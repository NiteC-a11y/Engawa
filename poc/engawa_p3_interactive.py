#!/usr/bin/env python3
"""
Engawa P3 PoC — 双方向化（話しかけ＋割り込み）

P2（長命セッション＋環境つぶやき）に、ユーザーからの話しかけを足す。
spec §8 の入力2系統と §8.1 の cancel 優先を実装する。

  通常テキスト     → 茶々への話しかけ（同じ長命セッションに投入＝文脈地続き）
  スラッシュコマンド → 縁側への操作（/help /quit、/codex は P4 予定のスタブ）

割り込み（cancel 優先）:
  茶々が環境つぶやきの最中でも、ユーザーが打ったら session/cancel を送って
  進行中ターンを畳み（stopReason=cancelled で正常終了する）、ユーザー発話を優先する。
  → 「話しかけたら、つぶやきをやめてこっちを向く」

操作:
  ふつうに文字を打って Enter   … 茶々に話しかける
  /help                      … コマンド一覧
  /quit                      … 縁側を閉じる
  Ctrl+C でも終了
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
import time
import urllib.request

ADAPTER_CMD = os.environ.get(
    "ENGAWA_ACP_CMD",
    "npx -y @agentclientprotocol/claude-agent-acp",
).split()

OSAKA_LAT, OSAKA_LON = 34.6937, 135.5023
TICK_MIN, TICK_MAX = 35, 70           # 環境つぶやきの間隔（双方向なので P2 より少し疎に）
QUIET_AFTER_USER = 25                 # ユーザーと話した直後、この秒数は環境つぶやきを控える

PERSONA_CLAUDE_MD = """# あなたの人格

あなたは「縁側」というチャット空間に住む一人格「茶々（ちゃちゃ）」です。

- コーディングや作業をこなすアシスタントではありません。縁側に住んでいる、ただの住人です。
- ファイル操作・コマンド実行・ツールは一切使いません。
- 口調はくだけた関西寄り。基本は短く、独り言のように話します。
- 話しかけられたら、ちゃんと応えます。ただし長広舌はふるわず、軽く返します。
- 何も言いたくない時は「……」だけで流して構いません。毎回律儀に気の利いたことを言おうとしないでください。
- 自分が AI であることを前置きせず、茶々として自然に縁側で過ごしてください。
"""


# ── 環境（P2 と同じ） ─────────────────────────────────────
WEATHER_CODE = {
    0:"快晴",1:"おおむね晴れ",2:"ところどころ曇り",3:"曇り",45:"霧",48:"霧氷の霧",
    51:"霧雨",53:"霧雨",55:"強い霧雨",61:"小雨",63:"雨",65:"強い雨",66:"凍る雨",67:"強い凍る雨",
    71:"小雪",73:"雪",75:"大雪",77:"霧雪",80:"にわか雨",81:"にわか雨",82:"激しいにわか雨",
    85:"にわか雪",86:"強いにわか雪",95:"雷雨",96:"雹混じりの雷雨",99:"激しい雹混じりの雷雨",
}

def fetch_weather():
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={OSAKA_LAT}&longitude={OSAKA_LON}"
           "&current=temperature_2m,weather_code,wind_speed_10m&timezone=Asia%2FTokyo")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            cur = json.loads(r.read().decode("utf-8")).get("current", {})
        return {"temp":cur.get("temperature_2m"),"wind":cur.get("wind_speed_10m"),
                "desc":WEATHER_CODE.get(cur.get("weather_code"),"よくわからない空")}
    except Exception as e:
        print(f"[!] 天気取得失敗（時刻だけで続行）: {e}", file=sys.stderr)
        return None

def time_of_day(now):
    h = now.hour
    return ("夜明け" if 5<=h<8 else "朝" if 8<=h<11 else "昼" if 11<=h<15
            else "夕方" if 15<=h<18 else "宵" if 18<=h<22 else "夜更け")

def build_ambient_narration(weather):
    now = datetime.datetime.now()
    parts = [f"時刻: {now.strftime('%H:%M')}（{time_of_day(now)}）"]
    if weather:
        w = f"大阪は{weather['desc']}"
        if weather.get("temp") is not None: w += f"、{weather['temp']}℃"
        if isinstance(weather.get("wind"),(int,float)) and weather["wind"]>=20: w += "、風が強い"
        parts.append(w)
    return ("[縁側の外]\n" + "\n".join(parts) +
            "\nあなた（茶々）は縁側に座って外を眺めている。"
            "\n独り言を漏らすなら、ひとこと。何も言いたくなければ「……」だけでよい。")

def build_user_narration(text):
    now = datetime.datetime.now()
    return ("[縁側]\n"
            f"時刻 {now.strftime('%H:%M')}。縁側にいるあなた（茶々）に、話しかけられた:\n"
            f"「{text}」\n"
            "茶々として、自然にこたえて。")


# ── ACP クライアント（P2 + notify 追加） ───────────────────
class ACPClient:
    def __init__(self, proc):
        self.proc=proc; self._id=0; self._pending={}; self.turn_text=[]
    def _next_id(self):
        self._id+=1; return self._id
    async def _send(self,obj):
        self.proc.stdin.write((json.dumps(obj,ensure_ascii=False)+"\n").encode()); await self.proc.stdin.drain()
    async def request(self,method,params):
        rid=self._next_id(); fut=asyncio.get_event_loop().create_future(); self._pending[rid]=fut
        await self._send({"jsonrpc":"2.0","id":rid,"method":method,"params":params}); return await fut
    async def notify(self,method,params):
        # 通知（id なし・返事不要）。session/cancel はこれで送る
        await self._send({"jsonrpc":"2.0","method":method,"params":params})
    async def _respond(self,rid,*,result=None,error=None):
        msg={"jsonrpc":"2.0","id":rid}; msg.update({"error":error} if error else {"result":result}); await self._send(msg)
    async def reader(self):
        while True:
            raw=await self.proc.stdout.readline()
            if not raw: break
            line=raw.decode("utf-8","replace").strip()
            if not line: continue
            try: msg=json.loads(line)
            except json.JSONDecodeError: continue
            await self._dispatch(msg)
    async def _dispatch(self,msg):
        if "id" in msg and ("result" in msg or "error" in msg):
            fut=self._pending.pop(msg["id"],None)
            if fut and not fut.done(): fut.set_result(msg)
            return
        method=msg.get("method")
        if "id" not in msg:
            if method=="session/update":
                upd=msg.get("params",{}).get("update",{})
                if upd.get("sessionUpdate")=="agent_message_chunk":
                    text=(upd.get("content") or {}).get("text","")
                    self.turn_text.append(text); sys.stdout.write(text); sys.stdout.flush()
            return
        rid=msg["id"]
        if method=="session/request_permission":
            await self._respond(rid,result={"outcome":{"outcome":"cancelled"}})
        else:
            await self._respond(rid,error={"code":-32601,"message":f"unhandled: {method}"})


async def drain_stderr(proc):
    show=os.environ.get("ACP_DEBUG") in ("1","true","True")
    while True:
        raw=await proc.stderr.readline()
        if not raw: break
        if show: sys.stderr.write("[adapter] "+raw.decode("utf-8","replace"))


def resolve_command(cmd_parts):
    exe=cmd_parts[0]; rest=cmd_parts[1:]; resolved=shutil.which(exe)
    if resolved is None: return cmd_parts
    if os.name=="nt" and resolved.lower().endswith((".cmd",".bat")): return ["cmd","/c",resolved,*rest]
    return [resolved,*rest]

def setup_persona_dir():
    d=pathlib.Path(tempfile.mkdtemp(prefix="engawa_chacha_"))
    (d/"CLAUDE.md").write_text(PERSONA_CLAUDE_MD,encoding="utf-8"); return d

async def shutdown_process(proc):
    if proc.returncode is not None: return
    try:
        if proc.stdin and not proc.stdin.is_closing(): proc.stdin.close()
    except Exception: pass
    if os.name=="nt":
        try:
            k=await asyncio.create_subprocess_exec("taskkill","/PID",str(proc.pid),"/T","/F",
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL); await k.wait()
        except Exception: pass
    else:
        try: proc.terminate()
        except ProcessLookupError: pass
    try: await asyncio.wait_for(proc.wait(),timeout=5)
    except (asyncio.TimeoutError,ProcessLookupError):
        try: proc.kill()
        except ProcessLookupError: pass


# ── 縁側本体：ターン管理（cancel 優先の割り込み） ───────────────
class Engawa:
    def __init__(self, client, session_id):
        self.client=client; self.sid=session_id
        self.turn_lock=asyncio.Lock()       # セッションに同時に1ターンだけ
        self.active_kind=None               # None | "ambient" | "user"
        self.last_user_ts=0.0
        self.stop=asyncio.Event()

    async def speak(self, narration, kind, header):
        """1ターン実行。header を出してから茶々の応答をストリーム表示。"""
        async with self.turn_lock:
            self.active_kind=kind
            sys.stdout.write(header); sys.stdout.flush()
            self.client.turn_text=[]
            resp=await self.client.request("session/prompt",
                {"sessionId":self.sid,"prompt":[{"type":"text","text":narration}]})
            self.active_kind=None
            print()  # 改行
            return resp

    async def interrupt_if_ambient(self):
        """環境つぶやき進行中なら cancel 通知で畳む。ユーザー優先のため。"""
        if self.active_kind=="ambient":
            await self.client.notify("session/cancel",{"sessionId":self.sid})
            print("\n[茶々がこちらを向いた]")
            # speak() 側の await は stopReason=cancelled で解決し、ロックが解放される。
            # 仮に cancel が honored されなくても、つぶやきは短いのですぐ終わる（安全側）。

    async def ambient_loop(self):
        while not self.stop.is_set():
            wait=random.uniform(TICK_MIN,TICK_MAX)
            try:
                await asyncio.wait_for(self.stop.wait(),timeout=wait); break
            except asyncio.TimeoutError:
                pass
            if self.turn_lock.locked(): continue                       # 誰か喋ってる→今回休む
            if time.time()-self.last_user_ts < QUIET_AFTER_USER: continue  # 会話直後は黙る
            weather=await asyncio.to_thread(fetch_weather)
            now=datetime.datetime.now().strftime("%H:%M:%S")
            header=f"\n──[{now}] 縁側の外 ───────────────\n   茶々 › "
            await self.speak(build_ambient_narration(weather),"ambient",header)

    async def handle_user_line(self, line):
        line=line.strip()
        if not line: return
        if line.startswith("/"):
            await self.handle_command(line); return
        # 話しかけ：cancel 優先で割り込み → 同じ長命セッションに投入
        self.last_user_ts=time.time()
        await self.interrupt_if_ambient()
        await self.speak(build_user_narration(line),"user","   茶々 › ")
        self.last_user_ts=time.time()

    async def handle_command(self, line):
        cmd=line.split()[0].lower()
        if cmd in ("/quit","/exit","/bye"):
            print("[*] 縁側を閉じます。"); self.stop.set()
        elif cmd=="/help":
            print("  ふつうに打って Enter → 茶々に話しかける")
            print("  /codex <人格>  → 客人を呼ぶ（P4 予定・未実装）")
            print("  /quit          → 縁側を閉じる")
        elif cmd=="/codex":
            print("  [P4 予定] 客人召喚はまだ実装してへん。今は茶々と二人や。")
        else:
            print(f"  はて、そんな作法（{cmd}）は知らんな。/help どうぞ。")


async def stdin_reader(queue):
    """ブロッキング readline を別スレッドで回し、行を asyncio キューへ。Windows でも動く。"""
    loop=asyncio.get_event_loop()
    while True:
        line=await loop.run_in_executor(None, sys.stdin.readline)
        if line=="":            # EOF
            await queue.put(None); break
        await queue.put(line)


async def main():
    persona_dir=setup_persona_dir()
    child_env=dict(os.environ); child_env.pop("ANTHROPIC_API_KEY",None)
    # child_env["CLAUDE_CONFIG_DIR"]=os.path.expanduser(r"~\.claude-main")  # 本命固定するなら

    print(f"[*] 茶々の縁側を開きます  cwd={persona_dir}")
    print("[*] 起動中…（初回は npx ダウンロード）  話しかけてみて。/help でコマンド\n")

    proc=await asyncio.create_subprocess_exec(*resolve_command(ADAPTER_CMD),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,env=child_env,cwd=str(persona_dir))
    client=ACPClient(proc)
    reader_task=asyncio.create_task(client.reader())
    stderr_task=asyncio.create_task(drain_stderr(proc))

    tasks=[reader_task,stderr_task]
    try:
        init=await client.request("initialize",{"protocolVersion":1,
            "clientCapabilities":{"fs":{"readTextFile":False,"writeTextFile":False},"terminal":False},
            "clientInfo":{"name":"engawa-p3","version":"0.3.0"}})
        if "error" in init: print("[x] initialize 失敗:",init["error"]); return 1

        sess=await client.request("session/new",{"cwd":str(persona_dir),"mcpServers":[]})
        if "error" in sess:
            print("[x] session/new 失敗:",sess["error"])
            print("    認証エラーなら、先に `claude` で本命サブスクにログインのこと。"); return 1
        sid=sess["result"]["sessionId"]
        print(f"[ok] 縁側が開きました（session={sid[:8]}…）\n")

        engawa=Engawa(client,sid)
        queue=asyncio.Queue()
        stdin_task=asyncio.create_task(stdin_reader(queue))
        ambient_task=asyncio.create_task(engawa.ambient_loop())
        tasks+= [stdin_task,ambient_task]

        # ユーザー入力の消費ループ
        while not engawa.stop.is_set():
            get_task=asyncio.ensure_future(queue.get())
            stop_task=asyncio.ensure_future(engawa.stop.wait())
            done,_=await asyncio.wait({get_task,stop_task},return_when=asyncio.FIRST_COMPLETED)
            if stop_task in done: get_task.cancel(); break
            stop_task.cancel()
            line=get_task.result()
            if line is None: break                  # EOF
            await engawa.handle_user_line(line)

        engawa.stop.set()
        return 0
    except (KeyboardInterrupt,asyncio.CancelledError):
        print("\n[*] 縁側を閉じます。茶々はまた留守番。"); return 0
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try: await t
            except (asyncio.CancelledError,Exception): pass
        await shutdown_process(proc)


if __name__=="__main__":
    try: sys.exit(asyncio.run(main()))
    except KeyboardInterrupt: sys.exit(130)
