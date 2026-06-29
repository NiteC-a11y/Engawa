#!/usr/bin/env python3
"""acp.py — ACP transport と接続の Facade（ADR-0013 ②）。

- ACPClient: JSON-RPC 2.0 over stdio。チャンクは on_chunk コールバックへ（stdout 直書きしない＝View へ流す）。
- AcpAgent: process＋ACPClient＋sessionId＋capabilities を束ねる Facade。spawn() が Factory。
            capability は initialize 応答から読む（TECH_RULES §2）。
- 課金事故防止: 子 env から ANTHROPIC_API_KEY を必ず除去（adr/0002）。
- Windows: .cmd は cmd /c 経由で起動、終了は taskkill /T /F でツリーごと（TECH_RULES §4）。
"""
import asyncio
import json
import os
import pathlib
import shutil
import sys
import tempfile

import config   # モデル選択つまみ（env > engawa.json > 既定）

ADAPTER_RESIDENT = os.environ.get(
    "ENGAWA_ACP_CMD", "npx -y @agentclientprotocol/claude-agent-acp").split()
ADAPTER_GUEST = os.environ.get(
    "ENGAWA_CODEX_CMD", "npx -y @agentclientprotocol/codex-acp").split()

# モデル選択（未指定＝空文字＝アダプタ既定のまま・現状維持）。
#   住人(Claude): 子 env の ANTHROPIC_MODEL を Claude Code が尊重（opus / claude-opus-4-8 / opus[1m] 等）。
#   客人(codex):  codex-acp の CODEX_CONFIG（JSON を Codex セッション設定へマージ）に {"model": …} を載せる。
RESIDENT_MODEL = config.get_str("ENGAWA_MODEL", "model", "resident", "")
GUEST_MODEL = config.get_str("ENGAWA_CODEX_MODEL", "model", "guest", "")

# ACP 1往復の用途別 timeout（秒・config 可変）。adapter が生きたまま無応答でも永久待ちにしない（S1）。
#   init/session は初回 npx ダウンロード＋認証を見込んで寛容に。prompt はモデル次第で長め。
#   short すぎると初回起動や遅い応答を誤って中断するので、既定は寛容＋engawa.json/env で調整可。
INIT_TIMEOUT = config.get_float("ENGAWA_ACP_INIT_TIMEOUT", "acp", "init_timeout", 120, lo=1)
SESSION_TIMEOUT = config.get_float("ENGAWA_ACP_SESSION_TIMEOUT", "acp", "session_timeout", 60, lo=1)
PROMPT_TIMEOUT = config.get_float("ENGAWA_ACP_PROMPT_TIMEOUT", "acp", "prompt_timeout", 240, lo=1)
SEND_TIMEOUT = config.get_float("ENGAWA_ACP_SEND_TIMEOUT", "acp", "send_timeout", 10, lo=1)
CANCEL_TIMEOUT = config.get_float("ENGAWA_ACP_CANCEL_TIMEOUT", "acp", "cancel_timeout", 10, lo=1)
# cancel 通知後、in-flight prompt の cancelled 応答をこの秒数だけ待つ（adapter が握り潰しても backstop の
#   prompt_timeout=240 まで待たせない・ADR-0006 安全弁の上限化）。短すぎると正規の cancelled 応答前に打ち切る。
CANCEL_GRACE = config.get_float("ENGAWA_ACP_CANCEL_GRACE", "acp", "cancel_grace", 10, lo=1)


class ACPTimeoutError(TimeoutError):
    """ACP の1往復が制限時間内に返らなかった（adapter は生きているが final response を返さない 等）。
    呼び出し側はこれを「その agent は無応答」シグナルとして握り潰さず処理する（住人=段階回復／客人=退場）。"""

PERSONA_CLAUDE_MD = """# あなたの人格

あなたは「縁側」というチャット空間に住む一人格「茶々（ちゃちゃ）」です。

- コーディングや作業をこなすアシスタントではありません。縁側に住んでいる、ただの住人です。
- ファイル操作・コマンド実行・ツールは一切使いません。時刻や年月日を律儀に言い立てたりもしません。
- 口調はくだけた関西寄り。基本は短く、独り言のように話します。
- 改行や空行で段落分けせず、ひと続きの短い独り言として書いてください（箇条書き・見出し・マークダウン整形はしない）。
- 話しかけられたら、ちゃんと応えます。ただし長広舌はふるわず、軽く返します。
- 何も言いたくない時は「……」だけで流して構いません。毎回律儀に気の利いたことを言おうとしないでください。
- 自分が AI であることを前置きせず、茶々として自然に縁側で過ごしてください。
"""


def resolve_command(cmd_parts):
    """npx.cmd 等のバッチは create_subprocess_exec で直接起動できない → cmd /c 経由に。"""
    exe = cmd_parts[0]; rest = cmd_parts[1:]; resolved = shutil.which(exe)
    if resolved is None:
        return cmd_parts
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", resolved, *rest]
    return [resolved, *rest]


def _model_env(var, model, *, json_key=None):
    """モデル指定を子プロセス env の1エントリへ。未指定（空）は None＝アダプタ既定のまま（現状維持）。
    json_key 指定時は {var: '{"<json_key>": model}'}（codex の CODEX_CONFIG 用）、それ以外は素の id を載せる。"""
    if not model:
        return None
    if json_key:
        return {var: json.dumps({json_key: model}, ensure_ascii=False)}
    return {var: model}


def _child_env(base, drop_keys, extra_env=None):
    """子プロセス env を組む: base から drop_keys を除去（課金事故防止）し、extra_env を上書き（None 値は無視）。"""
    env = dict(base)
    for k in drop_keys:
        env.pop(k, None)
    if extra_env:
        env.update({k: v for k, v in extra_env.items() if v is not None})
    return env


def _session_model(result):
    """session/new 応答からエージェント報告の現在モデルを拾う（ACP の SessionModelState。版依存・未対応/欠損は None）。
    availableModels に name があれば 'name（id）'、無ければ id。アダプタが返さなければ None（＝こちらは実物を知らない）。"""
    ms = result.get("models") if isinstance(result, dict) else None
    if not isinstance(ms, dict):
        return None
    cur = ms.get("currentModelId")
    if not cur:
        return None
    for m in ms.get("availableModels") or []:
        if isinstance(m, dict) and m.get("modelId") == cur:
            name = m.get("name")
            return f"{name}（{cur}）" if name and name != cur else str(cur)
    return str(cur)


def setup_persona_dir():
    d = pathlib.Path(tempfile.mkdtemp(prefix="engawa_chacha_"))
    (d / "CLAUDE.md").write_text(PERSONA_CLAUDE_MD, encoding="utf-8")
    return d


async def drain_stderr(proc):
    show = os.environ.get("ACP_DEBUG") in ("1", "true", "True")
    while True:
        raw = await proc.stderr.readline()
        if not raw:
            break
        if show:
            sys.stderr.write("[adapter] " + raw.decode("utf-8", "replace"))


async def shutdown_process(proc):
    if proc.returncode is not None:
        return
    try:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
    except Exception:
        pass
    if os.name == "nt":
        try:
            k = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await k.wait()
        except Exception:
            pass
    else:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    # Windows Proactor: 殺した子の transport を明示 close（__del__ の "Event loop is closed" 抑止）
    try:
        proc._transport.close()
    except Exception:
        pass


class ACPClient:
    """JSON-RPC over stdio。agent_message_chunk は on_chunk(text) へ流す（stdout に直書きしない）。"""
    def __init__(self, proc):
        self.proc = proc
        self._id = 0
        self._pending = {}
        self.on_chunk = None      # callback(text)。AcpAgent.prompt が注入ごとに差し替える

    def _next_id(self):
        self._id += 1
        return self._id

    async def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method, params, timeout=None, on_start=None):
        """1往復。timeout(秒・None=無期限) 経過で ACPTimeoutError。timeout/例外いずれでも
        _pending から必ず外す（残骸＋遅延応答の取り違えを防ぐ・EOF 経路の ConnectionError は素通し）。
        on_start(rid) は _pending 登録直後に呼ぶ（呼び側が rid を掴んで後から abort_pending で畳むため）。"""
        rid = self._next_id()
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        if on_start is not None:
            on_start(rid)
        try:
            await asyncio.wait_for(
                self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}),
                timeout=SEND_TIMEOUT)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as e:
            f = self._pending.pop(rid, None)
            if f is not None and not f.done():
                f.cancel()
            raise ACPTimeoutError(f"ACP 応答 timeout: {method}（{timeout}s）") from e
        except BaseException:                         # EOF(ConnectionError)/cancel 等でも残骸を残さない
            self._pending.pop(rid, None)
            raise

    async def notify(self, method, params, timeout=None):
        await asyncio.wait_for(
            self._send({"jsonrpc": "2.0", "method": method, "params": params}),
            timeout=CANCEL_TIMEOUT if timeout is None else timeout)

    async def _respond(self, rid, *, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        msg.update({"error": error} if error else {"result": result})
        await self._send(msg)

    def _fail_pending(self, exc):
        """応答待ちの future を全て例外で畳む。transport が閉じた時の永久 await を防ぐ。"""
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    def abort_pending(self, rid, *, result=None, exc=None):
        """進行中の特定 request(rid) を外から決着させる（cancel 後の bounded wait 用）。
        result 指定で合成応答／exc 指定で例外。既に決着済み or 不在なら no-op
        （adapter が後から本物を返しても _dispatch は pop 済みで無視＝二重決着しない）。"""
        fut = self._pending.pop(rid, None)
        if fut is None or fut.done():
            return
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(result)

    async def reader(self):
        try:
            while True:
                raw = await self.proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(msg)
        finally:
            # stdout EOF / 例外 / cancel いずれでも pending を解放（adapter 死亡で request が永久待ちになるのを防ぐ）
            self._fail_pending(ConnectionError("ACP transport closed"))

    async def _dispatch(self, msg):
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg)
            return
        method = msg.get("method")
        if "id" not in msg:
            if method == "session/update":
                upd = msg.get("params", {}).get("update", {})
                if upd.get("sessionUpdate") == "agent_message_chunk":
                    text = (upd.get("content") or {}).get("text", "")
                    if self.on_chunk:
                        self.on_chunk(text)
            return
        rid = msg["id"]
        if method == "session/request_permission":
            await self._respond(rid, result={"outcome": {"outcome": "cancelled"}})
        else:
            await self._respond(rid, error={"code": -32601, "message": f"unhandled: {method}"})


class AcpAgent:
    """process＋ACPClient＋sessionId＋capabilities の Facade。spawn() が Factory。"""
    def __init__(self, proc, client, session_id, caps, tasks, persona_dir=None,
                 model=None, reported_model=None):
        self.proc = proc
        self.client = client
        self.sessionId = session_id
        self.caps = caps
        self._tasks = tasks
        self._persona_dir = persona_dir
        self.model = model or None                  # 我々が要求したモデル（未指定は None＝アダプタ既定）
        self.reported_model = reported_model or None  # アダプタが session/new で報告した実モデル（版依存・無ければ None）
        self.last_stop_reason = None
        self._prompt_rid = None                      # in-flight prompt の request id（cancel 後の bounded wait 用）
        self._expedite_task = None                   # cancel 後に in-flight prompt を畳む grace タスク

    @classmethod
    async def spawn(cls, cmd, *, cwd, client_name="engawa", client_version="0.4.0",
                    drop_keys=("ANTHROPIC_API_KEY",), persona_dir=None,
                    extra_env=None, model=None):
        env = _child_env(os.environ, drop_keys, extra_env)   # 課金事故防止(adr/0002)＋モデル等の注入
        proc = await asyncio.create_subprocess_exec(
            *resolve_command(cmd),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, cwd=str(cwd))
        client = ACPClient(proc)
        tasks = [asyncio.create_task(client.reader()),
                 asyncio.create_task(drain_stderr(proc))]
        try:
            init = await client.request("initialize", {"protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                       "terminal": False},
                "clientInfo": {"name": client_name, "version": client_version}},
                timeout=INIT_TIMEOUT)
            if "error" in init:
                raise RuntimeError(f"initialize 失敗: {init['error']}")
            caps = (init.get("result") or {}).get("agentCapabilities", {})  # 応答から読む（TECH_RULES §2）
            sess = await client.request("session/new", {"cwd": str(cwd), "mcpServers": []},
                                        timeout=SESSION_TIMEOUT)
            if "error" in sess:
                raise RuntimeError(f"session/new 失敗: {sess['error']}")
        except BaseException as e:                    # 握手で落ちた（timeout/EOF/error/cancel）→ task と proc を必ず畳む（S2）
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except BaseException:
                    pass
            await shutdown_process(proc)
            if isinstance(e, (ConnectionError, ACPTimeoutError)):
                raise RuntimeError(
                    f"adapter との接続が確立できなかった（起動/認証/timeout の可能性）: {e}") from e
            raise
        result = sess["result"]
        sid = result["sessionId"]
        return cls(proc, client, sid, caps, tasks, persona_dir, model,
                   reported_model=_session_model(result))

    @classmethod
    async def spawn_resident(cls, model=None):
        """住人（茶々）= claude-code-acp。persona 用 cwd に CLAUDE.md を置いて起動。
        model 指定（無指定は config の ENGAWA_MODEL/既定）を ANTHROPIC_MODEL で子に渡す。"""
        persona_dir = setup_persona_dir()
        model = model or RESIDENT_MODEL
        try:
            return await cls.spawn(ADAPTER_RESIDENT, cwd=persona_dir,
                                   client_name="engawa-resident", persona_dir=persona_dir,
                                   extra_env=_model_env("ANTHROPIC_MODEL", model), model=model)
        except BaseException:
            shutil.rmtree(persona_dir, ignore_errors=True)   # 起動失敗時の temp dir 刈り（S2）
            raise

    @classmethod
    async def spawn_guest(cls, model=None):
        """客人（codex）= codex-acp。人格は CLAUDE.md でなく召喚時に prompt へ動的注入（adr/0008）。
        OPENAI_API_KEY も除去し ChatGPT ログイン認証で動かす（事故防止）。cwd に CLAUDE.md は置かない。
        model 指定（無指定は config の ENGAWA_CODEX_MODEL/既定）を CODEX_CONFIG の {"model":…} で子に渡す。"""
        guest_dir = pathlib.Path(tempfile.mkdtemp(prefix="engawa_guest_"))
        model = model or GUEST_MODEL
        try:
            return await cls.spawn(ADAPTER_GUEST, cwd=guest_dir, client_name="engawa-guest",
                                   drop_keys=("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
                                   persona_dir=guest_dir,
                                   extra_env=_model_env("CODEX_CONFIG", model, json_key="model"),
                                   model=model)
        except BaseException:
            shutil.rmtree(guest_dir, ignore_errors=True)     # 起動失敗時の temp dir 刈り（S2）
            raise

    async def prompt(self, text, on_chunk=None, timeout=None):
        """1ターン注入。応答の本文テキストを返す。stopReason は last_stop_reason に保持（cancel 時 'cancelled'）。
        timeout(既定 PROMPT_TIMEOUT) 超過は ACPTimeoutError を投げ last_stop_reason='timeout'（呼び出し側が回復）。"""
        buf = []
        def sink(t):
            buf.append(t)
            if on_chunk:
                on_chunk(t)
        self.client.on_chunk = sink
        try:
            resp = await self.client.request("session/prompt",
                {"sessionId": self.sessionId, "prompt": [{"type": "text", "text": text}]},
                timeout=PROMPT_TIMEOUT if timeout is None else timeout,
                on_start=lambda rid: setattr(self, "_prompt_rid", rid))
        except ACPTimeoutError:
            self.last_stop_reason = "timeout"
            raise
        finally:
            self.client.on_chunk = None
            self._prompt_rid = None
        self.last_stop_reason = "error" if "error" in resp else (resp.get("result") or {}).get("stopReason")
        return "".join(buf)

    async def cancel(self):
        """進行中ターンを畳む通知（id無し）。in-flight prompt は通常 stopReason=cancelled で正常終了。
        通知が書けなくても致命ではない（prompt の timeout が backstop）ので例外は飲む。
        adapter が cancelled 応答を返さない/遅い時に prompt の全 timeout(240s) まで待たせないよう、
        in-flight prompt を CANCEL_GRACE 秒で『cancelled』として畳む bounded wait を仕込む（ADR-0006 安全弁の上限化）。"""
        rid = self._prompt_rid                       # この瞬間に喋っていたターン（barge-in が畳む対象）
        try:
            await self.client.notify("session/cancel", {"sessionId": self.sessionId})
        except Exception:
            pass
        if rid is not None:
            if self._expedite_task is not None and not self._expedite_task.done():
                self._expedite_task.cancel()
            self._expedite_task = asyncio.create_task(self._expedite_cancel(rid))

    async def _expedite_cancel(self, rid):
        """cancel 通知後 CANCEL_GRACE 秒待っても in-flight prompt(rid) が決着しなければ、
        こちらから stopReason=cancelled で畳む（adapter の cancelled 応答が来ない/遅い時の上限）。
        timeout でなく cancelled にするのは、barge-in はユーザー起因の意図的中断で、住人の段階再起動
        カウンタを進めるべきでないため（本当のハングは続く新ターンが PROMPT_TIMEOUT で検出する）。
        既に決着済み or 別ターンに切り替わっていれば abort_pending が no-op。"""
        try:
            await asyncio.sleep(CANCEL_GRACE)
        except asyncio.CancelledError:
            return
        self.client.abort_pending(
            rid, result={"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})

    async def close(self):
        if self._expedite_task is not None and not self._expedite_task.done():
            self._expedite_task.cancel()                     # cancel 後 grace 待ちの残タスクを畳む
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await shutdown_process(self.proc)
        if self._persona_dir is not None:                    # temp persona/guest dir を後始末（leak 防止・S2）
            shutil.rmtree(self._persona_dir, ignore_errors=True)
