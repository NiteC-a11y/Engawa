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

    async def request(self, method, params):
        rid = self._next_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return await fut

    async def notify(self, method, params):
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

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
                "clientInfo": {"name": client_name, "version": client_version}})
            if "error" in init:
                raise RuntimeError(f"initialize 失敗: {init['error']}")
            caps = (init.get("result") or {}).get("agentCapabilities", {})  # 応答から読む（TECH_RULES §2）
            sess = await client.request("session/new", {"cwd": str(cwd), "mcpServers": []})
            if "error" in sess:
                raise RuntimeError(f"session/new 失敗: {sess['error']}")
        except ConnectionError as e:                 # adapter が握手中に落ちた（EOF で reader が pending 解放）
            raise RuntimeError(f"adapter との接続が確立できなかった（起動/認証失敗の可能性）: {e}")
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
        return await cls.spawn(ADAPTER_RESIDENT, cwd=persona_dir,
                               client_name="engawa-resident", persona_dir=persona_dir,
                               extra_env=_model_env("ANTHROPIC_MODEL", model), model=model)

    @classmethod
    async def spawn_guest(cls, model=None):
        """客人（codex）= codex-acp。人格は CLAUDE.md でなく召喚時に prompt へ動的注入（adr/0008）。
        OPENAI_API_KEY も除去し ChatGPT ログイン認証で動かす（事故防止）。cwd に CLAUDE.md は置かない。
        model 指定（無指定は config の ENGAWA_CODEX_MODEL/既定）を CODEX_CONFIG の {"model":…} で子に渡す。"""
        guest_dir = pathlib.Path(tempfile.mkdtemp(prefix="engawa_guest_"))
        model = model or GUEST_MODEL
        return await cls.spawn(ADAPTER_GUEST, cwd=guest_dir, client_name="engawa-guest",
                               drop_keys=("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
                               persona_dir=guest_dir,
                               extra_env=_model_env("CODEX_CONFIG", model, json_key="model"),
                               model=model)

    async def prompt(self, text, on_chunk=None):
        """1ターン注入。応答の本文テキストを返す。stopReason は last_stop_reason に保持（cancel 時 'cancelled'）。"""
        buf = []
        def sink(t):
            buf.append(t)
            if on_chunk:
                on_chunk(t)
        self.client.on_chunk = sink
        try:
            resp = await self.client.request("session/prompt",
                {"sessionId": self.sessionId, "prompt": [{"type": "text", "text": text}]})
        finally:
            self.client.on_chunk = None
        self.last_stop_reason = "error" if "error" in resp else (resp.get("result") or {}).get("stopReason")
        return "".join(buf)

    async def cancel(self):
        """進行中ターンを畳む通知（id無し）。in-flight prompt は stopReason=cancelled で正常終了。"""
        await self.client.notify("session/cancel", {"sessionId": self.sessionId})

    async def close(self):
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await shutdown_process(self.proc)
