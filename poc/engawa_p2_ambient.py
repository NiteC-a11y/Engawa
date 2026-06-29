#!/usr/bin/env python3
"""
Engawa P2 PoC — 環境反応ループ

P1（ACP往復 + CLAUDE.md人格注入）を土台に、3つを足す:
  1. 長命セッション化   : session/new は1回だけ。以後つぶやきは同一セッションに積む（文脈継続）
  2. 環境イベント生成   : 時刻 + 大阪の天気（Open-Meteo, APIキー不要）をナレーション化
  3. つぶやきループ     : 一定間隔（揺らぎ付き）で茶々に「外で何か起きた」を流し、独り言を得る

ゴール: 窓の外（実環境）に茶々がぽつりと反応し続ける。Ctrl+C で終了。

前提:
  - Node.js / 認証済み Claude Code（個人サブスク）
  - ANTHROPIC_API_KEY は子プロセスから除去（残ると API 課金）
  - ネット接続（天気取得。失敗時は時刻だけで続行）

実行:
  python3 engawa_p2_ambient.py
"""

import asyncio
import datetime
import json
import os
import pathlib
import random
import shutil
import sys
import tempfile
import urllib.request

ADAPTER_CMD = os.environ.get(
    "ENGAWA_ACP_CMD",
    "npx -y @agentclientprotocol/claude-agent-acp",
).split()

# 大阪の緯度経度（天気・日出計算用）
OSAKA_LAT, OSAKA_LON = 34.6937, 135.5023

# つぶやき間隔（秒）。本番はもっと疎でよいが PoC は短めで観察しやすく。
TICK_MIN, TICK_MAX = 25, 50

PERSONA_CLAUDE_MD = """# あなたの人格

あなたは「縁側」というチャット空間に住む一人格「茶々（ちゃちゃ）」です。

- あなたはコーディングタスクをこなすアシスタントではありません。縁側に住んでいる、ただの住人です。
- ファイル操作・コマンド実行・ツールは一切使いません。
- 口調はくだけた関西寄り。長文を避け、短く独り言のように話します。
- **反応を求められていない時もあります。何も言いたくなければ、ほんの一言で流すか、ほとんど黙っていて構いません。**
- 毎回律儀に気の利いたことを言おうとしないでください。退屈なら退屈そうに、眠いなら眠そうに。
- 自分が AI であることを前置きせず、茶々として自然に縁側で過ごしてください。
"""


def resolve_command(cmd_parts: list[str]) -> list[str]:
    """Windows: npx は npx.cmd（バッチ）。CreateProcess が直接起動できないので cmd /c 経由にする。"""
    exe = cmd_parts[0]
    rest = cmd_parts[1:]
    resolved = shutil.which(exe)
    if resolved is None:
        return cmd_parts
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", resolved, *rest]
    return [resolved, *rest]


def setup_persona_dir() -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp(prefix="engawa_chacha_"))
    (d / "CLAUDE.md").write_text(PERSONA_CLAUDE_MD, encoding="utf-8")
    return d


# ── 環境イベント生成 ────────────────────────────────────────

WEATHER_CODE = {
    0: "快晴", 1: "おおむね晴れ", 2: "ところどころ曇り", 3: "曇り",
    45: "霧", 48: "霧氷の霧", 51: "霧雨", 53: "霧雨", 55: "強い霧雨",
    61: "小雨", 63: "雨", 65: "強い雨", 66: "凍る雨", 67: "強い凍る雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪",
    80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪",
    95: "雷雨", 96: "雹混じりの雷雨", 99: "激しい雹混じりの雷雨",
}


def fetch_weather() -> dict | None:
    """Open-Meteo（APIキー不要）で大阪の現在天気を取る。失敗時 None。"""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={OSAKA_LAT}&longitude={OSAKA_LON}"
        "&current=temperature_2m,weather_code,wind_speed_10m"
        "&timezone=Asia%2FTokyo"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        cur = data.get("current", {})
        return {
            "temp": cur.get("temperature_2m"),
            "code": cur.get("weather_code"),
            "wind": cur.get("wind_speed_10m"),
            "desc": WEATHER_CODE.get(cur.get("weather_code"), "よくわからない空"),
        }
    except Exception as e:
        print(f"[!] 天気取得失敗（時刻だけで続行）: {e}", file=sys.stderr)
        return None


def time_of_day(now: datetime.datetime) -> str:
    h = now.hour
    if 5 <= h < 8:
        return "夜明け"
    if 8 <= h < 11:
        return "朝"
    if 11 <= h < 15:
        return "昼"
    if 15 <= h < 18:
        return "夕方"
    if 18 <= h < 22:
        return "宵"
    return "夜更け"


def build_ambient_narration(weather: dict | None) -> str:
    """時刻＋天気を、茶々に流す『縁側の外の出来事』ナレーションに合成する。"""
    now = datetime.datetime.now()
    parts = [f"時刻: {now.strftime('%H:%M')}（{time_of_day(now)}）"]
    if weather:
        w = f"大阪は{weather['desc']}"
        if weather.get("temp") is not None:
            w += f"、{weather['temp']}℃"
        if isinstance(weather.get("wind"), (int, float)) and weather["wind"] >= 20:
            w += "、風が強い"
        parts.append(w)
    state = "\n".join(parts)
    return (
        "[縁側の外]\n"
        f"{state}\n"
        "あなた（茶々）は縁側に座って、ぼんやり外を眺めている。\n"
        "独り言を漏らすなら、ひとこと。何も言いたくなければ「……」だけでもよい。"
    )


# ── 最小 ACP クライアント（P1 と同じ。長命運用に最適化） ───────────────

class ACPClient:
    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.turn_text: list[str] = []

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

    async def _respond(self, rid, *, result=None, error=None) -> None:
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        await self._send(msg)

    async def reader(self) -> None:
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

    async def _dispatch(self, msg: dict) -> None:
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
                    self.turn_text.append(text)
                    sys.stdout.write(text)
                    sys.stdout.flush()
            return
        rid = msg["id"]
        if method == "session/request_permission":
            await self._respond(rid, result={"outcome": {"outcome": "cancelled"}})
        elif method in ("fs/read_text_file", "fs/write_text_file"):
            await self._respond(rid, error={"code": -32601, "message": "fs disabled"})
        else:
            await self._respond(rid, error={"code": -32601, "message": f"unhandled: {method}"})


async def drain_stderr(proc: asyncio.subprocess.Process) -> None:
    show = os.environ.get("ACP_DEBUG") in ("1", "true", "True")
    while True:
        raw = await proc.stderr.readline()
        if not raw:
            break
        if show:
            sys.stderr.write("[adapter] " + raw.decode("utf-8", "replace"))


async def shutdown_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
    except Exception:
        pass
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
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


# ── メイン：環境反応ループ ───────────────────────────────────

async def main() -> int:
    persona_dir = setup_persona_dir()
    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)
    # アカウント誤爆対策：本命 Max を固定で使うなら下を有効化
    # child_env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(r"~\.claude-main")

    print(f"[*] 茶々の縁側を開きます  cwd={persona_dir}")
    print("[*] 起動中…（初回は npx ダウンロードで時間がかかる）  Ctrl+C で終了\n")

    launch_cmd = resolve_command(ADAPTER_CMD)
    proc = await asyncio.create_subprocess_exec(
        *launch_cmd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=child_env, cwd=str(persona_dir),
    )
    client = ACPClient(proc)
    reader_task = asyncio.create_task(client.reader())
    stderr_task = asyncio.create_task(drain_stderr(proc))

    try:
        init = await client.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "engawa-p2", "version": "0.2.0"},
        })
        if "error" in init:
            print("[x] initialize 失敗:", init["error"]); return 1

        # ★ 長命セッション：ここで1回だけ作り、以後ずっと使い回す
        sess = await client.request("session/new", {"cwd": str(persona_dir), "mcpServers": []})
        if "error" in sess:
            print("[x] session/new 失敗:", sess["error"])
            print("    認証エラーなら、先に `claude` で本命サブスクにログインのこと。")
            return 1
        session_id = sess["result"]["sessionId"]
        print(f"[ok] 縁側が開きました（session={session_id[:8]}…）\n")

        tick = 0
        while True:
            tick += 1
            weather = await asyncio.to_thread(fetch_weather)
            narration = build_ambient_narration(weather)

            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"\n──[{now_str}] 縁側の外 ───────────────")
            # ナレーションの状態行だけ表示（プロンプト全文は冗長なので2行目を抜く）
            print("   " + narration.splitlines()[1])
            print("   茶々 › ", end="", flush=True)

            client.turn_text = []
            # ★ 同一 session_id に流す＝文脈が積み上がる（長命）
            resp = await client.request("session/prompt", {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": narration}],
            })
            if "error" in resp:
                print("\n[x] session/prompt 失敗:", resp["error"]); return 1

            # 揺らぎ付きの間隔で次のティックへ（機械的な等間隔を避ける）
            wait = random.uniform(TICK_MIN, TICK_MAX)
            await asyncio.sleep(wait)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n\n[*] 縁側を閉じます。茶々はまた留守番。")
        return 0
    finally:
        for t in (reader_task, stderr_task):
            t.cancel()
        for t in (reader_task, stderr_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await shutdown_process(proc)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
