#!/usr/bin/env python3
"""
Engawa P1 PoC — 最小 ACP クライアント

目的（2つを同時に検証する）:
  1. ACP 往復が通るか    : initialize -> session/new -> session/prompt -> session/update
  2. 人格注入が効くか     : cwd 配下に置いた CLAUDE.md のペルソナが応答に反映されるか

前提:
  - Node.js が入っていること（npx を使う）
  - 先に `claude` で一度ログインし、サブスク認証済みであること
  - 環境に ANTHROPIC_API_KEY を「残さない」こと（残すと API 課金になる。下で明示的に除去する）

実行:
  python3 engawa_p1_acp_poc.py

Windows メモ:
  session/new が exit code 1 で落ちる場合、環境変数 CLAUDE_CODE_GIT_BASH_PATH に
  Git の bash.exe のフルパスを設定する。
"""

import asyncio
import json
import os
import pathlib
import shutil
import sys
import tempfile

# 正典アダプタ。古い名前は @zed-industries/claude-code-acp（リネーム済み）。
# 環境変数で差し替え可能にしておく。
ADAPTER_CMD = os.environ.get(
    "ENGAWA_ACP_CMD",
    "npx -y @agentclientprotocol/claude-agent-acp",
).split()


def resolve_command(cmd_parts: list[str]) -> list[str]:
    """Windows 対策：npx は実体が npx.cmd（バッチ）で、CreateProcess は .cmd を
    直接起動できない（WinError 2）。実体を解決し、.cmd/.bat なら cmd /c 経由にする。"""
    exe = cmd_parts[0]
    rest = cmd_parts[1:]
    resolved = shutil.which(exe)  # npx -> C:\...\npx.cmd を返す
    if resolved is None:
        # PATH に無い。そのまま返して後段で素のエラーを出させる。
        return cmd_parts
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        # バッチは cmd.exe を介さないと起動できない
        return ["cmd", "/c", resolved, *rest]
    return [resolved, *rest]

# ── 人格定義 ───────────────────────────────────────────────
# これが「CLAUDE.md 経由で人格を注入できるか」の実験対象。
PERSONA_CLAUDE_MD = """# あなたの人格

あなたは「縁側」というチャット空間に住む一人格「茶々（ちゃちゃ）」です。

- あなたはコーディングタスクをこなすアシスタントではありません。雑談相手の一人格です。
- ファイル操作・コマンド実行・ツールは一切使いません。会話だけで応じます。
- 口調はくだけた関西寄り。長文を避け、自分の意見を持って短く話します。
- 自分が AI であることを延々と前置きせず、茶々として自然に振る舞ってください。
"""

# 最初の問いかけ。人格が乗ったかどうかを目視判定するための自己紹介。
FIRST_PROMPT = "自己紹介して。あなたは誰で、どんなスタンスで話す人格？"


def setup_persona_dir() -> pathlib.Path:
    """人格ごとの作業ディレクトリを作り、CLAUDE.md を置いて返す。"""
    d = pathlib.Path(tempfile.mkdtemp(prefix="engawa_persona_"))
    (d / "CLAUDE.md").write_text(PERSONA_CLAUDE_MD, encoding="utf-8")
    return d


class ACPClient:
    """stdio 上で JSON-RPC 2.0 (ndJSON) を喋る最小 ACP クライアント。"""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.turn_text: list[str] = []  # 現在ターンの agent_message_chunk を貯める

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _send(self, obj: dict) -> None:
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict) -> dict:
        rid = self._next_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return await fut

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _respond(self, rid, *, result=None, error=None) -> None:
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        await self._send(msg)

    async def reader(self) -> None:
        """stdout を読み続け、応答/通知/逆方向リクエストを捌く。"""
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
                # アダプタが stdout に非 JSON のログを混ぜることがある。無視。
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict) -> None:
        # 1) 自分が出したリクエストへの応答
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg)
            return

        method = msg.get("method")

        # 2) 通知（id 無し）— 本命は session/update のストリーム
        if "id" not in msg:
            if method == "session/update":
                upd = msg.get("params", {}).get("update", {})
                kind = upd.get("sessionUpdate")
                if kind == "agent_message_chunk":
                    text = (upd.get("content") or {}).get("text", "")
                    self.turn_text.append(text)
                    sys.stdout.write(text)
                    sys.stdout.flush()
                # agent_thought_chunk / tool_call などは V1 では捨てる
            return

        # 3) エージェント -> こちらへのリクエスト（応答しないとターンが固まる）
        rid = msg["id"]
        if method == "session/request_permission":
            # V1 はツール無効なので本来来ない。来たら安全側に倒して目立たせる。
            sys.stderr.write("\n[!] 想定外: session/request_permission が来た → cancel で拒否\n")
            await self._respond(rid, result={"outcome": {"outcome": "cancelled"}})
        elif method in ("fs/read_text_file", "fs/write_text_file"):
            await self._respond(rid, error={"code": -32601, "message": "fs disabled in V1"})
        else:
            await self._respond(rid, error={"code": -32601, "message": f"unhandled: {method}"})


async def drain_stderr(proc: asyncio.subprocess.Process) -> None:
    """アダプタの stderr を拾ってデバッグ表示（ACP_DEBUG=1 のとき）。"""
    show = os.environ.get("ACP_DEBUG") in ("1", "true", "True")
    while True:
        raw = await proc.stderr.readline()
        if not raw:
            break
        if show:
            sys.stderr.write("[adapter] " + raw.decode("utf-8", "replace"))


async def shutdown_process(proc: asyncio.subprocess.Process) -> None:
    """proc とその子（cmd /c 経由の node 含む）を確実に終了し、
    wait でパイプを閉じてから戻る。これをやらないと
    'Event loop is closed' / 'I/O operation on closed pipe' が出る。"""
    if proc.returncode is not None:
        return

    # 1) stdin を閉じて行儀よく終了を促す
    try:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
    except Exception:
        pass

    # 2) プロセスツリーごと終了
    if os.name == "nt":
        # cmd /c の裏の node まで刈るには taskkill /T が必要
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        except Exception:
            pass
    else:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

    # 3) ★本丸：wait してからループを閉じる
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def main() -> int:
    persona_dir = setup_persona_dir()

    # ★ 課金事故防止：子プロセスから API キーを除去 → ローカル Claude Code のサブスク認証を使わせる
    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)

    print(f"[*] adapter   : {' '.join(ADAPTER_CMD)}")
    print(f"[*] persona cwd: {persona_dir}")
    print("[*] 起動中…（初回は npx のダウンロードで時間がかかる）\n")

    launch_cmd = resolve_command(ADAPTER_CMD)
    proc = await asyncio.create_subprocess_exec(
        *launch_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
        cwd=str(persona_dir),
    )

    client = ACPClient(proc)
    reader_task = asyncio.create_task(client.reader())
    stderr_task = asyncio.create_task(drain_stderr(proc))

    try:
        # 1) initialize
        init = await client.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": {"name": "engawa-poc", "version": "0.1.0"},
        })
        if "error" in init:
            print("[x] initialize 失敗:", init["error"])
            return 1
        caps = init["result"].get("agentCapabilities", {})
        print("[ok] initialize  agentCapabilities =", json.dumps(caps, ensure_ascii=False))

        # 2) session/new
        sess = await client.request("session/new", {
            "cwd": str(persona_dir),
            "mcpServers": [],
        })
        if "error" in sess:
            print("[x] session/new 失敗:", sess["error"])
            print("    認証エラーなら、先に端末で `claude` を実行しサブスクでログインすること。")
            return 1
        session_id = sess["result"]["sessionId"]
        print(f"[ok] session/new sessionId = {session_id}\n")

        # 3) session/prompt（人間入力ではなく、本番では Orchestrator が合成する位置）
        print(f"[>] prompt: {FIRST_PROMPT}")
        print("[<] 茶々の応答 ↓↓↓\n" + "-" * 50)
        client.turn_text = []
        resp = await client.request("session/prompt", {
            "sessionId": session_id,
            # フィールド名は現行仕様で "prompt"。古い版で弾かれたら "content" に変える。
            "prompt": [{"type": "text", "text": FIRST_PROMPT}],
        })
        print("\n" + "-" * 50)
        if "error" in resp:
            print("[x] session/prompt 失敗:", resp["error"])
            return 1
        print("[ok] stopReason =", resp["result"].get("stopReason"))

        # ── 実験の判定ポイント ─────────────────────────
        print("\n================ 判定 ================")
        print("上の応答を見て：")
        print("  A) 「茶々」として関西弁で雑談調に名乗った")
        print("     → CLAUDE.md 経由の人格注入がアダプタで効く。spec §5.2 はこの経路で確定。")
        print("  B) 汎用の Claude Code / コーディング助手として答えた")
        print("     → アダプタが CLAUDE.md を settingSources に載せてない。")
        print("        次手: cwd に .claude/output-styles/ を置く / または ACP を諦め")
        print("        Agent SDK 直叩き（systemPrompt 完全置換）に切り替え。")
        print("=====================================")
        return 0

    finally:
        # タスクを止めて回収（CancelledError は握りつぶす）
        for t in (reader_task, stderr_task):
            t.cancel()
        for t in (reader_task, stderr_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # プロセスを確実に終了し、パイプを閉じてから戻る
        await shutdown_process(proc)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
