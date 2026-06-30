#!/usr/bin/env python3
"""views.py — View ポート（ADR-0013 ③）。core を console から剥がす。

core（Scheduler）は turn_start / chunk / turn_end / system を emit するだけ。
ConsoleView を今、WebView を P5 で差す。テストは CaptureView（stdout parse を廃止）。
"""
import asyncio
import base64
import datetime
import json
import os
import queue
import sys
import threading

SPRITE_CONFIG = os.environ.get("ENGAWA_SPRITE_CONFIG", "")   # 茶々スプライト設定（既定 sprite.json）

# 観戦窓×→入力チャネルに流す制御トークン（scheduler が「対局を畳んで縁側へ戻す」合図に使う）。
#   入力キュー(_inq)経由＝スレッド安全。ESC 始まりで通常入力/宛先マーカー(\x00)と衝突しない。
GAME_CLOSE_REQUEST = "\x1b__engawa_game_close__"


def collapse_ws(s):
    """ASCII 空白・改行の連なりを半角1個に畳み前後を削る（全角空白は保つ）。客人の声の1行化用。"""
    out, gap = [], False
    for ch in s:
        if ch in " \t\r\n":
            if out:
                gap = True
            continue
        if gap:
            out.append(" "); gap = False
        out.append(ch)
    return "".join(out)


def corner_xy(sw, sh, ww, wh, corner="br", margin=16, taskbar=40):
    """画面隅へ置く左上座標を返す（P5 Inc2・ADR-0009）。corner: br/bl/tr/tl。下辺はタスクバー分よける。"""
    x = sw - ww - margin if corner.endswith("r") else margin
    y = (sh - wh - margin - taskbar) if corner.startswith("b") else margin
    return (max(0, x), max(0, y))


class View:
    def turn_start(self, who, kind, label=None, voice=None): ...
    def chunk(self, text): ...
    def turn_end(self): ...
    def system(self, msg): ...
    def say(self, speaker, text): ...          # 3人会話の確定発話を1行で出す（茶々/客人を一様に・ADR-0015）
    # ゲーム観戦（ADR-0017 Inc4）。console=テキスト / web=隣の観戦窓。snapshot は構造化状態、lines は文字表現
    def game_open(self, title): ...            # 対局開始（web は観戦窓を開く）
    def game_update(self, snapshot, lines): ...  # 局面更新（web は札を描く／console は lines を出す）
    def game_close(self): ...                  # 終局（web は観戦窓を閉じる）
    async def inputs(self):
        if False:
            yield None


class ConsoleView(View):
    """茶々は「ひと続きの短い独り言」（persona 準拠）。チャンク跨ぎで改行/連続空白を
    半角1個に畳み、行頭の空白は食って強制1行化する。ヘッダは遅延出力＝最初の可視文字で
    初めて出す（初トークン前に割り込まれた等で何も流れなければ空「茶々 › 」行を残さない）。
    客人来訪時は voice（生セリフ）を即表示してから茶々の反応を出す（茶々が黙っても客人は見せる）。
    ストリーミングと cancel 時の partial 表示は維持（状態は turn ごとにリセット）。"""
    def __init__(self):
        self._pending = None     # 未出力ヘッダ。最初の可視文字で出す（空ターンには出さない）
        self._started = False    # この turn で可視文字を出したか（行頭の空白を食う）
        self._gap = False        # 直前に空白/改行を見た（次の可視文字の前に半角1個）

    def turn_start(self, who, kind, label=None, voice=None):
        if voice is not None:                       # 客人の声は即表示（茶々が黙っても見せる）
            sys.stdout.write(self._voice_block(label, voice)); sys.stdout.flush()
            self._pending = "   茶々 › "             # 茶々の prefix だけ遅延
        else:
            self._pending = self._header(kind, label)   # ここでは出さない（遅延）
        self._started = self._gap = False

    @staticmethod
    def _voice_block(label, voice):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        return f"\n──[{now}] {label or '客人'} ───────────────\n   客人 › {collapse_ws(voice)}\n"

    @staticmethod
    def _header(kind, label):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        if kind == "user":
            return "   茶々 › "
        if kind == "arc":
            return f"\n──[{label}] {now} ─────\n   茶々 › "
        suffix = "（移ろい）" if kind == "transition" else ""
        return f"\n──[{now}] {label or '縁側の外'}{suffix} ───────────────\n   茶々 › "

    def chunk(self, text):
        buf = []
        for ch in text:
            if ch in " \t\r\n":          # 改行・連続空白は畳む（行頭は食う）
                if self._started:
                    self._gap = True
                continue
            if not self._started:        # 最初の可視文字＝ここで初めてヘッダを出す
                if self._pending:
                    buf.append(self._pending); self._pending = None
            elif self._gap:              # 空白を跨いだ＝語の区切りに半角1個だけ
                buf.append(" "); self._gap = False
            buf.append(ch); self._started = True
        if buf:
            sys.stdout.write("".join(buf)); sys.stdout.flush()

    def turn_end(self):
        if self._started:                # 何か出した時だけ閉じ改行
            sys.stdout.write("\n"); sys.stdout.flush()
        self._pending = None             # 空ターンはヘッダごと捨てる（空「茶々 › 」行を残さない）
        self._started = self._gap = False

    def system(self, msg):
        print(msg)

    def say(self, speaker, text):              # 3人会話: 確定した発話を1行で（茶々/客人を一様に）
        sys.stdout.write(f"   {speaker} › {collapse_ws(text)}\n"); sys.stdout.flush()

    def game_open(self, title):                # console は窓を出さない（開始バナーは system 済み）
        pass

    def game_update(self, snapshot, lines):    # console は文字表現をそのまま出す
        for ln in (lines or []):
            print(ln)

    def game_close(self):
        pass

    async def inputs(self):
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                yield None
                return
            yield line


class CaptureView(View):
    """テスト用。出力イベントを記録し、入力はキュー（feed）から流す。"""
    def __init__(self):
        self.events = []          # ("start",kind,label) / ("chunk-end",kind,text) / ("system",msg)
        self.voices = []          # 客人の生セリフ（voice）を記録
        self._kind = None
        self._buf = []
        self._q = asyncio.Queue()

    def turn_start(self, who, kind, label=None, voice=None):
        self._kind, self._buf = kind, []
        self.events.append(("start", kind, label))
        if voice is not None:
            self.voices.append(voice)

    def chunk(self, text):
        self._buf.append(text)

    def turn_end(self):
        self.events.append(("end", self._kind, "".join(self._buf)))

    def system(self, msg):
        self.events.append(("system", msg, None))

    def say(self, speaker, text):              # 3人会話の確定発話を記録（テスト用）
        self.events.append(("say", speaker, text))

    def game_open(self, title):
        self.events.append(("game_open", title, None))

    def game_update(self, snapshot, lines):
        self.events.append(("game_update", snapshot, lines))

    def game_close(self):
        self.events.append(("game_close", None, None))

    def feed(self, line):
        self._q.put_nowait(line)

    async def inputs(self):
        while True:
            line = await self._q.get()
            yield line
            if line is None:
                return

    # テスト用ヘルパ
    def arc_turns(self):
        return [(k, l) for (t, k, l) in self.events if t == "start" and k == "arc"]

    def texts(self, kind):
        return [txt for (t, k, txt) in self.events if t == "end" and k == kind]


# ── P5: pywebview 用 View（poll 方式・ADR-0009/0010/0013）───────────
class WebView(View):
    """pywebview 用 View。出力は `_log` に積み JS が poll、入力は queue 経由（スレッド安全）。
    スレッド跨ぎの evaluate_js を使わない堅い経路（app.py 実績の踏襲）。茶々の lazy 表示
    （空ターンは出さない）と改行畳みは ConsoleView と揃える。"""
    def __init__(self):
        self._log = []                  # [{id,rev,type,...}]（asyncio スレッドが書く / poll が読む）
        self._id = 0                    # 安定キー（DOM要素・表示順）
        self._rev = 0                   # 変更版数。chunk/確定ごとに上げ poll は rev>since で差分配信
        self._cur = None                # 進行中ターン
        self._lock = threading.Lock()
        self._inq = queue.Queue()       # JS send → inputs() （ConsoleView の stdin 相当）
        self._window = None             # frameless の×ボタン用（close のみ）
        self.api = _WebApi(self)        # JS から見える窓口（poll/send/close）
        # 観戦窓（ADR-0017 Inc4b）。対局中だけ隣に出す第2窓
        self._game_window = None
        self._game_api = None
        self._game_snap = None          # 最新スナップショット（カード描画用）
        self._game_lines = []           # snapshot 無しゲームのテキスト用
        self._game_rev = 0
        self._corner = "br"             # 観戦窓の配置（run_web が set_layout で設定）
        self._main_wh = (340, 360)      # メイン窓サイズ（配置計算用・既定）

    def set_layout(self, corner, main_w, main_h):
        self._corner = corner
        self._main_wh = (int(main_w), int(main_h))

    def _bump(self):
        self._rev += 1
        return self._rev

    # ---- View ポート（asyncio スレッドから）----
    def turn_start(self, who, kind, label=None, voice=None):
        with self._lock:
            self._id += 1
            self._cur = {"id": self._id, "rev": self._bump(), "type": "turn", "kind": kind,
                         "label": label, "voice": voice, "text": "", "done": False}
            self._log.append(self._cur)
            self._trim()

    def chunk(self, text):
        with self._lock:
            if self._cur is not None:
                self._cur["text"] += text
                self._cur["rev"] = self._bump()

    def turn_end(self):
        with self._lock:
            cur = self._cur
            if cur is not None:
                if not collapse_ws(cur["text"]) and not cur["voice"]:
                    self._log.remove(cur)       # 空ターン（茶々が黙った/初トークン前割り込み）は捨てる
                else:
                    cur["done"] = True
                    cur["rev"] = self._bump()   # 確定（最後のチャンク込み）を必ず配信
                self._cur = None

    def system(self, msg):
        with self._lock:
            self._id += 1
            self._log.append({"id": self._id, "rev": self._bump(), "type": "system",
                              "text": str(msg), "done": True})
            self._trim()

    def say(self, speaker, text):              # 3人会話: 確定発話を1件のログ行として積む（poll が配る）
        with self._lock:
            self._id += 1
            self._log.append({"id": self._id, "rev": self._bump(), "type": "say",
                              "speaker": str(speaker), "text": str(text), "done": True})
            self._trim()

    def game_open(self, title):
        """対局開始: 隣に観戦窓（第2窓）を生成。失敗してもゲームは継続（lines は本窓ログへ落ちる）。"""
        if self._game_window is not None:
            return
        with self._lock:
            self._game_snap = None
            self._game_lines = []
            self._game_rev = 0
        self._game_api = _GameApi(self)
        gw, gh = 380, 320
        x, y = self._game_xy(gw, gh)
        try:
            import webview
            self._game_window = webview.create_window(
                str(title), html=GAME_HTML, js_api=self._game_api,
                width=gw, height=gh, x=x, y=y,
                frameless=True, on_top=True, easy_drag=True, resizable=True)
        except Exception:
            self._game_window = None           # 第2窓を作れない環境では本窓ログにフォールバック

    def _game_xy(self, gw, gh, gap=12):
        try:
            import webview
            scr = webview.screens[0]
            sw, sh = int(scr.width), int(scr.height)
        except Exception:
            sw, sh = 1920, 1080
        mw, mh = self._main_wh
        mx, my = corner_xy(sw, sh, mw, mh, self._corner)
        x = (mx - gw - gap) if self._corner.endswith("r") else (mx + mw + gap)   # 右隅→左へ / 左隅→右へ
        y = (my + mh - gh) if self._corner.startswith("b") else my               # 下隅→下端合わせ
        return (max(0, x), max(0, y))

    def game_update(self, snapshot, lines):
        with self._lock:
            if snapshot is not None:
                self._game_snap = snapshot
            if lines:
                self._game_lines.extend(lines)
                if len(self._game_lines) > 60:
                    del self._game_lines[:len(self._game_lines) - 60]
            self._game_rev += 1
        if self._game_window is None:          # 第2窓が無ければ本窓ログへ（フォールバック）
            for ln in (lines or []):
                self.system(ln)

    def game_close(self):
        w = self._game_window
        self._game_window = None
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass

    def request_game_abort(self):
        """観戦窓の×（ユーザー操作）: 窓を閉じ、scheduler にも対局終了を伝える（対局中なら お開き＝縁側へ戻す）。
        単に窓を destroy するだけだと Scheduler.game が残り「ゲームモードのまま復帰不能」になるのを防ぐ。"""
        self.game_close()
        self._inq.put(GAME_CLOSE_REQUEST)   # 入力チャネル経由でスレッド安全に scheduler へ

    def game_poll(self, since):                # 観戦窓の JS から（_GameApi 経由）
        with self._lock:
            if self._game_rev <= since:
                return {"rev": self._game_rev}
            return {"rev": self._game_rev, "snap": self._game_snap, "lines": list(self._game_lines)}

    def _trim(self, cap=120):
        if len(self._log) > cap:
            del self._log[:len(self._log) - cap]

    async def inputs(self):
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, self._inq.get)
            if line is None:
                yield None
                return
            yield line

    # ---- pywebview スレッドから（_WebApi 経由）----
    def poll(self, since):
        """since(rev) より新しい変更のみ差分で返す。client は id をキーに upsert・id 順に並べる。"""
        with self._lock:
            items = []
            for i in self._log:
                if i["rev"] <= since:
                    continue                    # 変化なし
                if i["type"] == "turn" and not i["done"] \
                        and not collapse_ws(i["text"]) and not i["voice"]:
                    continue                    # 空の live ターンはまだ出さない（lazy）
                items.append(self._serialize(i))
            return {"items": items, "cursor": self._rev}

    @staticmethod
    def _serialize(i):
        if i["type"] in ("system", "you"):
            return {"id": i["id"], "type": i["type"], "text": i["text"]}
        if i["type"] == "say":                  # 3人会話の確定発話（speaker＋text）
            return {"id": i["id"], "type": "say", "speaker": i["speaker"],
                    "text": collapse_ws(i["text"])}
        return {"id": i["id"], "type": "turn", "kind": i["kind"], "label": i["label"],
                "voice": collapse_ws(i["voice"]) if i["voice"] else None,
                "text": collapse_ws(i["text"]), "done": i["done"]}

    def send(self, text, to=""):
        text = (text or "").strip()
        if not text:
            return
        with self._lock:                # 自分の発言を「本文クリーンで」エコー（宛先はチップが示す・C方式）
            self._id += 1
            self._log.append({"id": self._id, "rev": self._bump(), "type": "you",
                              "text": text, "done": True})
            self._trim()
        # 宛先(to)は本文に混ぜず制御マーカーで scheduler へ運ぶ（'\x00<to>\x00<text>'・console は無印）
        self._inq.put(("\x00" + to + "\x00" + text) if to else text)

    def bind_window(self, window):
        self._window = window           # frameless の×ボタンから閉じるため

    def resize_window(self, w, h):
        """frameless 窓を JS の右下グリップから広げる（pywebview window.resize）。min=240 で潰れ防止。"""
        if self._window is None:
            return
        try:
            self._window.resize(max(240, int(w)), max(240, int(h)))
        except Exception:
            pass

    def close(self):
        self.game_close()               # 観戦窓(第2窓)が残ると webview.start が返らず teardown に入れない → 先に畳む
        if self._window is not None:    # ×ボタン → 本窓を閉じる（両窓 destroy で webview.start が返り teardown へ）
            try:
                self._window.destroy()
            except Exception:
                pass

    def signal_close(self):
        self._inq.put(None)             # inputs() が None を吐き run() を畳む


class _WebApi:
    """JS から呼べる窓口を poll/send/close だけに絞る（View の内部メソッドを露出しない）。"""
    def __init__(self, view):
        self._v = view
    def poll(self, since):
        return self._v.poll(since)
    def send(self, text, to=""):
        self._v.send(text, to); return True
    def close(self):
        self._v.close(); return True
    def resize(self, w, h):
        self._v.resize_window(w, h); return True


class _GameApi:
    """観戦窓の JS から呼べる窓口（poll と、×ボタンの close）。"""
    def __init__(self, view):
        self._v = view
    def poll(self, since):
        return self._v.game_poll(since)
    def close(self):
        self._v.request_game_abort(); return True   # 窓を閉じ scheduler に対局終了も伝える


# 観戦窓（ADR-0017 Inc4b）。snapshot を poll してカードを描く小窓。札卓っぽい緑フェルト。
GAME_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;background:#33503f;color:#f0ece2;overflow:hidden;
    font-family:system-ui,"Yu Gothic UI",sans-serif;user-select:none}
  #app{height:100vh;box-sizing:border-box;border:1px solid #20382b;padding:8px 10px;cursor:move}
  .label{font-size:12px;opacity:.8;margin:0 0 6px;text-align:center;letter-spacing:2px}
  .row{display:flex;align-items:center;gap:4px;margin:7px 0;min-height:38px}
  .row.dealer{border-bottom:1px dashed rgba(255,255,255,.22);padding-bottom:9px;margin-bottom:10px}
  .who{width:84px;font-size:13px;flex:0 0 auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .val{margin-left:6px;font-size:13px;opacity:.85;min-width:34px}
  .row.cur{background:rgba(255,255,255,.10);border-radius:7px}
  .card{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;
    width:26px;height:36px;background:#fbfaf6;border-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.4)}
  .card.red{color:#c83b2e}.card.blk{color:#23201c}
  .card b{font-weight:700;font-size:13px;line-height:1}.card i{font-style:normal;font-size:11px;line-height:1}
  .card.back{background:repeating-linear-gradient(45deg,#7a8fae 0 4px,#67809f 4px 8px)}
  .badge{margin-left:8px;font-size:12px;padding:1px 8px;border-radius:10px}
  .badge.win{background:#2e7d4f}.badge.lose{background:#9a3b32}.badge.draw{background:#6b6256}
  #txt{font-size:12px;white-space:pre-wrap;opacity:.9}
  #gclose{position:absolute;top:4px;right:6px;z-index:20;width:20px;height:20px;padding:0;
    line-height:18px;text-align:center;border:0;border-radius:4px;cursor:pointer;
    background:rgba(0,0,0,.30);color:#f0ece2;font-size:14px}
  #gclose:hover{background:rgba(150,50,40,.85)}
</style></head><body><div id="app">
  <button id="gclose" title="閉じる">×</button>
  <div class="label" id="title">観戦</div>
  <div id="table"></div>
  <div id="txt"></div>
</div>
<script>
document.getElementById('gclose').onclick=()=>{window.pywebview&&window.pywebview.api.close();};
let since=0;
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function cardEl(c){
  const suit=c[0], rank=c.slice(1), red=(suit==='♥'||suit==='♦');
  return '<span class="card '+(red?'red':'blk')+'"><b>'+esc(rank)+'</b><i>'+esc(suit)+'</i></span>';
}
function back(){return '<span class="card back"></span>';}
function render(s){
  document.getElementById('title').textContent='観戦：'+s.label;
  document.getElementById('txt').textContent='';
  const d=s.dealer;
  let dc=d.cards.map(cardEl).join('');
  if(d.hidden) dc+=back();
  let h='<div class="row dealer"><span class="who">ディーラー</span>'+dc
       +'<span class="val">'+(d.hidden?'':('= '+d.value))+'</span></div>';
  for(const p of s.players){
    const bcls=p.outcome==='勝ち'?'win':(p.outcome==='負け'?'lose':'draw');
    h+='<div class="row player'+(p.current?' cur':'')+'">'
      +'<span class="who">'+esc(p.name)+'</span>'
      +p.cards.map(cardEl).join('')
      +'<span class="val">'+p.value+'</span>'
      +(p.outcome?'<span class="badge '+bcls+'">'+esc(p.outcome)+'</span>':'')
      +'</div>';
  }
  document.getElementById('table').innerHTML=h;
}
function renderText(lines){
  document.getElementById('table').innerHTML='';
  document.getElementById('txt').textContent=(lines||[]).join('\n');
}
async function tick(){
  try{
    const r=await window.pywebview.api.poll(since);
    if(r && r.rev>since){
      since=r.rev;
      if(r.snap) render(r.snap);
      else if(r.lines) renderText(r.lines);
    }
  }catch(e){}
}
setInterval(tick,250);
</script></body></html>"""


WEB_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;background:#2a2320;color:#f0e9e0;overflow:hidden;
    font-family:system-ui,"Yu Gothic UI",sans-serif;user-select:none}
  #app{display:flex;flex-direction:column;height:100vh;position:relative;
    border:1px solid #1a1410;box-sizing:border-box}
  #scene{position:relative;flex:0 0 200px;overflow:hidden;cursor:move;
    background:linear-gradient(#bcd6e6 0%,#dbe7ec 54%,#e8dcc8 54%,#decba8 100%)}
  #close{position:absolute;top:3px;right:5px;z-index:20;width:20px;height:20px;padding:0;
    line-height:18px;text-align:center;border:0;border-radius:4px;cursor:pointer;
    background:rgba(40,30,24,.45);color:#f0e9e0;font-size:14px}
  #close:hover{background:rgba(150,50,40,.85)}
  /* 右下リサイズグリップ（frameless は掴む縁が無いので明示ハンドル・ドラッグで window.resize） */
  #grip{position:absolute;right:0;bottom:0;width:15px;height:15px;z-index:30;cursor:nwse-resize;touch-action:none;
    background:linear-gradient(135deg,transparent 46%,rgba(240,233,224,.5) 46% 60%,transparent 60% 73%,rgba(240,233,224,.5) 73% 88%,transparent 88%)}
  /* 客人来訪の気配＝庭先に木の葉がそよぐ（客人は画面外＝庭側にいる扱い・Inc4） */
  #kehai{position:absolute;bottom:8%;left:58%;width:7px;height:4px;border-radius:60% 0 60% 0;
    background:#7a9a5a;opacity:0;pointer-events:none;z-index:14}
  #kehai.on{animation:drift 2.4s ease-in-out}
  @keyframes drift{0%{opacity:0;transform:translate(10px,-4px) rotate(0)}
    25%{opacity:.85}100%{opacity:0;transform:translate(-48px,12px) rotate(-200deg)}}
  .shoji{position:absolute;top:0;left:0;right:0;height:54%;opacity:.5;
    background:repeating-linear-gradient(90deg,#b9a988 0 2px,transparent 2px 25%),
      repeating-linear-gradient(0deg,#b9a988 0 2px,transparent 2px 33%),#efe7d6}
  .floor{position:absolute;bottom:0;left:0;right:0;height:46%;
    background:repeating-linear-gradient(90deg,#caa46b 0 17px,#bd9a5c 17px 19px)}
  #cha{position:absolute;left:50%;bottom:24px;transform:translateX(-50%);z-index:2;
    image-rendering:pixelated;width:118px;height:118px}
  /* 単一フレームのスプライト用：下端固定の縦伸縮＝呼吸（座布団は動かさず猫がふくらむ） */
  #cha.breathe{transform-origin:bottom center;animation:breathe 4s ease-in-out infinite}
  @keyframes breathe{0%,100%{transform:translateX(-50%) scaleY(1)}50%{transform:translateX(-50%) scaleY(.98)}}
  /* 茶々をダブルクリックした時の「ニャー」吹き出し（頭上にふわっと出て消える） */
  #nya{position:absolute;left:50%;bottom:140px;transform:translateX(-50%);z-index:30;pointer-events:none;
    opacity:0;font-size:15px;font-weight:bold;color:#3a2a1a;background:rgba(255,255,255,.88);
    padding:1px 9px;border-radius:11px;white-space:nowrap}
  #nya.show{animation:nyaPop 1.1s ease-out}
  @keyframes nyaPop{0%{opacity:0;transform:translate(-50%,6px) scale(.7)}
    20%{opacity:1;transform:translate(-50%,0) scale(1.06)}40%{transform:translate(-50%,0) scale(1)}
    100%{opacity:0;transform:translate(-50%,-26px) scale(1)}}
  /* 接地影：浮き感を消す（スプライトの足元に敷く） */
  #chashadow{position:absolute;left:50%;transform:translateX(-50%);height:9px;display:none;z-index:1;
    background:rgba(0,0,0,.22);border-radius:50%;filter:blur(3px)}
  #log{flex:1;overflow-y:auto;padding:8px 11px;font-size:15px;line-height:1.5;background:#2a2320}
  .item{margin:3px 0;word-break:break-word}
  .sys{color:#9c8e84;font-size:12px}
  .guest{color:#9fd2e2}.cha{color:#f3e8c9}
  .you{color:#bfd99a;text-align:right}
  .who{opacity:.55;margin-right:4px;font-size:11px}
  #bar{display:flex;gap:6px;padding:8px;background:#1f1916}
  /* 宛先ドロップダウン（左・3人会話の入力補助。@ は日本語IMEで打ちにくいのでセレクトで選ぶ） */
  /* 宛先チップ（入力欄の上・来訪中だけ表示・タップで次の発言の宛先を選ぶ） */
  #addrbar{display:none;gap:5px;align-items:center;padding:5px 8px 0;background:#1f1916}
  #addrbar.on{display:flex}
  #addrbar .al{font-size:11px;opacity:.5}
  #addrbar .ac{font-size:12px;padding:3px 10px;border:1px solid #5a4a3a;border-radius:12px;
    background:#2e2620;color:#cdbfae;cursor:pointer}
  #addrbar .ac.sel{background:#caa46b;color:#2a2320;border-color:#caa46b}
  #in{flex:1;padding:8px;border:1px solid #5a4a3a;border-radius:6px;background:#2e2620;color:#f0e9e0;font-size:13px}
  #send{padding:8px 13px;border:0;border-radius:6px;background:#caa46b;color:#2a2320;cursor:pointer}
</style></head>
<body><div id="app">
  <div id="scene" class="pywebview-drag-region"><div class="shoji"></div><div class="floor"></div>
    <div id="kehai"></div><div id="chashadow"></div>
    <canvas id="cha" width="74" height="74"></canvas><div id="nya">ニャー</div></div>
  <button id="close" title="閉じる">×</button>
  <div id="grip" title="ドラッグでリサイズ"></div>
  <div id="log"></div>
  <div id="addrbar"><span class="al">宛先</span>
    <button class="ac sel" data-p="">茶々</button>
    <button class="ac" data-p="guest">客人</button>
    <button class="ac" data-p="both">二人とも</button></div>
  <div id="bar"><input id="in" placeholder="話しかける…（/help /codex /quit）" autocomplete="off">
    <button id="send">送信</button></div>
</div>
<script>
const log=document.getElementById('log'), inp=document.getElementById('in');
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let since=0; const seen={}; let busy=false;
// 茶々の state（poll から推定して canvas の動きへ）
const chacha={lastGuest:-1e9,lastUser:-1e9}; const liveSet=new Set(); let lastTurnId=0;
let guestName='', lastGuestSeen=-1e9;   // @メニュー用: 来訪中の客人名と最終発話時刻
function render(it){
  if(it.type==='system') return '<div class="sys">'+esc(it.text)+'</div>';
  if(it.type==='you') return '<div class="you"><span class="who">あなた ›</span>'+esc(it.text)+'</div>';
  if(it.type==='say'){                    // 3人会話の確定発話（茶々 or 客人）
    const cls=(it.speaker==='茶々')?'cha':'guest';
    return '<div class="'+cls+'"><span class="who">'+esc(it.speaker)+' ›</span>'+esc(it.text)+'</div>';
  }
  let h='';
  if(it.voice) h+='<div class="guest"><span class="who">客人 ›</span>'+esc(it.voice)+'</div>';
  h+='<div class="cha"><span class="who">茶々 ›</span>'+esc(it.text)+'</div>';
  return h;
}
async function tick(){
  if(busy||!window.pywebview) return; busy=true;
  try{
    const r=await window.pywebview.api.poll(since);
    // append 前に「下端付近にいるか」を見る。上へスクロールして履歴を見ている時は引き戻さない
    const stick=log.scrollHeight-log.scrollTop-log.clientHeight<48;
    for(const it of r.items){
      let el=seen[it.id];
      if(!el){el=document.createElement('div');el.className='item';log.appendChild(el);seen[it.id]=el;}
      el.innerHTML=render(it);
      if(it.type==='you') chacha.lastUser=performance.now();          // 話しかけたら即こっち見る
      if(it.type==='say'&&it.speaker!=='茶々'){chacha.lastGuest=performance.now();guestName=it.speaker;lastGuestSeen=performance.now();}  // 客人が喋った＝耳ピン/気配＋@候補
      if(it.type==='turn'){
        if(it.done) liveSet.delete(it.id); else liveSet.add(it.id);   // 話してる最中か
        if(it.id>lastTurnId){
          lastTurnId=it.id;
          if(it.voice) chacha.lastGuest=performance.now();            // 客人来訪＝耳ピン
          else if(it.kind==='user') chacha.lastUser=performance.now(); // 話しかけられた＝こっち見る
        }
      }
    }
    since=r.cursor;
    if(r.items.length&&stick) log.scrollTop=log.scrollHeight;   // 新着があり下端追従中の時だけ最下部へ
  }catch(e){}finally{busy=false;}
  updateGuest(); refreshAddr();
}
// 客人来訪の気配（Inc4）：客人は庭側＝画面外。到着時に庭先の葉がそよぐ＋茶々は耳ピン(既存 listen)
let guestShown=false;
function kehai(){const k=document.getElementById('kehai');k.classList.remove('on');void k.offsetWidth;k.classList.add('on');}
function updateGuest(){
  const present=(performance.now()-chacha.lastGuest)<12000;   // voice 由来の在/不在
  if(present&&!guestShown){guestShown=true;kehai();}            // 来訪の立ち上がりで気配
  else if(!present&&guestShown){guestShown=false;}
}
// ── 宛先チップ（入力欄の上・来訪中だけ表示）。タップで次の発言の宛先を選び、送ると茶々へ戻る（一回限り） ──
const addrbar=document.getElementById('addrbar');
const chips=()=>addrbar.querySelectorAll('.ac');
let addrTo='';                                               // 次の発言の宛先キー（''=茶々/既定・guest・both）
function selChip(btn){addrTo=btn.dataset.p;chips().forEach(c=>c.classList.toggle('sel',c===btn));}
function resetChip(){addrTo='';chips().forEach((c,i)=>c.classList.toggle('sel',i===0));}
chips().forEach(c=>c.onclick=()=>selChip(c));
function send(){
  const v=inp.value.trim(); if(!v||!window.pywebview) return;
  window.pywebview.api.send(v, addrTo);                      // 本文はクリーン・宛先は別引数で渡す（C方式）
  inp.value='';                                             // 宛先(チップ)は保持＝変えるまで同じ相手（B方式・客人退場時のみ refreshAddr が茶々へ戻す）
}
document.getElementById('send').onclick=send;
document.getElementById('close').onclick=()=>{window.pywebview&&window.pywebview.api.close();};
inp.addEventListener('keydown',e=>{if(e.key==='Enter')send();});
let addrShown=null;
function refreshAddr(){
  const present=(performance.now()-lastGuestSeen)<90000;     // 来訪中らしい間だけチップ行を出す
  if(present===addrShown) return; addrShown=present;
  addrbar.classList.toggle('on',present);
  if(!present) resetChip();                                  // 客人が去ったら茶々へ戻す
}
setInterval(tick,150);
// ── 茶々の描画（ADR-0010 骨/皮）: SPRITE シートがあればコマ送り、無ければ procedural ──
const SPRITE = /*SPRITE*/null;
const cv=document.getElementById('cha'), g=cv.getContext('2d'); g.imageSmoothingEnabled=false;
let blinkNext=0,blinkEnd=0,earNext=0,earEnd=0;
function chaState(t){                              // state→アニメ選択（sheet 用）
  if(t>blinkNext){blinkEnd=t+120;blinkNext=t+1800+Math.random()*3500;}
  if(liveSet.size>0) return 'talk';
  if((t-chacha.lastUser)<4000) return 'attentive';
  if((t-chacha.lastGuest)<6000) return 'listen';
  if(t<blinkEnd) return 'blink';
  return 'idle';
}
let spriteImg=null;
if(SPRITE&&SPRITE.dataUri){spriteImg=new Image();spriteImg.src=SPRITE.dataUri;}
function drawSheet(t){
  g.clearRect(0,0,cv.width,cv.height);
  if(spriteImg&&spriteImg.complete&&spriteImg.naturalWidth){
    const a=(SPRITE.animations&&(SPRITE.animations[chaState(t)]||SPRITE.animations.idle))||{frames:[0]};
    const fw=SPRITE.frame_w, fh=SPRITE.frame_h, cols=Math.max(1,Math.floor(spriteImg.naturalWidth/fw));
    const fr=a.frames||[0], fps=a.fps||2, idx=fr[Math.floor(t/1000*fps)%fr.length]||0;
    g.drawImage(spriteImg,(idx%cols)*fw,Math.floor(idx/cols)*fh,fw,fh,0,0,cv.width,cv.height);
  }
  requestAnimationFrame(drawSheet);
}
function drawChacha(t){
  g.clearRect(0,0,74,74);
  const talk=liveSet.size>0;                         // 話してる最中
  const listen=(t-chacha.lastGuest)<6000;            // 客人来訪を聞いてる
  const attn=(t-chacha.lastUser)<4000;               // 話しかけられた
  if(t>blinkNext){blinkEnd=t+120;blinkNext=t+1800+Math.random()*3500;}
  const blink=t<blinkEnd;
  if(t>earNext){earEnd=t+170;earNext=t+2600+Math.random()*4200;}
  const tw=t<earEnd?Math.sin((earEnd-t)/26)*3:0;     // 耳ピクッ
  const breath=Math.sin(t/650)*1.3;
  const bob=talk?Math.abs(Math.sin(t/150))*3.2:0;    // 話す＝ぴょこぴょこ
  const sway=Math.sin(t/900)*1.5;                    // 胴体ゆらり
  const lean=talk?2.2:0;                             // 前のめり
  const earUp=listen?5:0;                            // 客人＝耳ピン
  const headDn=attn?2:0;                             // 話しかけ＝こっち見る
  const tAmp=(talk||attn)?5:2.5;                     // 尻尾の振り幅
  const cx=37+sway+lean, by=34-breath-bob;
  g.strokeStyle='#d9a25a';g.lineWidth=6;g.lineCap='round';   // しっぽ
  g.beginPath();g.moveTo(cx+16,by+24);
  g.quadraticCurveTo(cx+27,by+18+Math.sin(t/300)*tAmp,cx+24,by+5+Math.sin(t/300)*tAmp);g.stroke();
  g.fillStyle='#e8b06a';                             // からだ
  g.beginPath();(g.roundRect?g.roundRect(cx-16,by,32,30+breath,11):g.rect(cx-16,by,32,30));g.fill();
  const hx=cx,hy=by-2+headDn;
  g.beginPath();g.arc(hx,hy,15,0,7);g.fill();        // あたま
  g.beginPath();g.moveTo(hx-11,hy-10);g.lineTo(hx-15+tw,hy-23-earUp);g.lineTo(hx-4,hy-13);g.fill();  // 耳L
  g.beginPath();g.moveTo(hx+11,hy-10);g.lineTo(hx+15-tw,hy-23-earUp);g.lineTo(hx+4,hy-13);g.fill();  // 耳R
  g.fillStyle='#3a2a1a';                             // 目（まばたき＝横線）
  if(blink){g.fillRect(hx-8,hy-3,5,1);g.fillRect(hx+3,hy-3,5,1);}
  else{const ew=attn?3.4:2.6,eh=attn?4.6:4;g.fillRect(hx-8,hy-4,ew,eh);g.fillRect(hx+5-ew,hy-4,ew,eh);}
  g.fillStyle='#caa46b';g.fillRect(hx-1,hy+2,3,2);   // 鼻
  requestAnimationFrame(drawChacha);
}
if(SPRITE&&SPRITE.frame_w){                       // 表示サイズ・位置を scene 寸法から自動算出（どんなコマ寸法でも収まる）
  const scene=document.getElementById('scene'), sh=scene.clientHeight||200;
  const botM=Math.round(sh*0.05), topR=Math.round(sh*0.12);   // 床際まで下げて接地感／空の余白
  const fit=Math.max(1,Math.floor((sh-botM-topR)/SPRITE.frame_h));
  const sc=Math.max(1,Math.min(SPRITE.scale||fit,fit));       // 指定倍率を fit でクランプ（整数倍＝クリスプ）
  const dw=SPRITE.frame_w*sc;
  cv.width=SPRITE.frame_w; cv.height=SPRITE.frame_h;           // 内部は等倍
  cv.style.width=dw+'px'; cv.style.height=(SPRITE.frame_h*sc)+'px';
  cv.style.bottom=botM+'px';
  cv.classList.add('breathe');                                 // 猫だけの絵（座布団なし）＝呼吸を戻す
  const shd=document.getElementById('chashadow');             // 足元に接地影＝浮き感を消す
  shd.style.width=Math.round(dw*0.7)+'px'; shd.style.bottom=Math.max(0,botM-2)+'px'; shd.style.display='block';
  g.imageSmoothingEnabled=false;
}
requestAnimationFrame(SPRITE?drawSheet:drawChacha);   // シートがあればコマ送り、無ければ procedural
// 茶々をダブルクリック → ニャー（吹き出し＋こっち見てにっこり）。LLM/客人を介さないクライアント完結の小ネタ＝トークン消費なし・即反応
function meow(){
  const nya=document.getElementById('nya'), scene=document.getElementById('scene');
  nya.style.bottom=(scene.clientHeight-cv.offsetTop+2)+'px';   // 茶々の頭の真上に出す（procedural/sprite どちらの寸法でも追従）
  nya.classList.remove('show'); void nya.offsetWidth; nya.classList.add('show');   // 連打でも頭から再生
  chacha.lastUser=performance.now();                            // 反応＝既存 attentive（こっち見てにっこり）
}
cv.addEventListener('dblclick',meow);
// 右下グリップ → frameless 窓のドラッグ・リサイズ（pywebview window.resize を api 経由で呼ぶ）。
// 画面座標(screenX/Y)で差分を取り、窓が伸びても基準がぶれないように。min は Python 側でも 240 にクランプ。
(function(){
  const g=document.getElementById('grip'); if(!g) return;
  let on=false, sx=0, sy=0, sw=0, sh=0;
  g.addEventListener('pointerdown',e=>{on=true; sx=e.screenX; sy=e.screenY; sw=window.innerWidth; sh=window.innerHeight;
    try{g.setPointerCapture(e.pointerId);}catch(_){} e.preventDefault();});
  g.addEventListener('pointermove',e=>{ if(!on) return;
    const w=Math.max(240,Math.round(sw+(e.screenX-sx))), h=Math.max(240,Math.round(sh+(e.screenY-sy)));
    if(window.pywebview&&pywebview.api&&pywebview.api.resize) pywebview.api.resize(w,h);});
  const end=e=>{on=false; try{g.releasePointerCapture(e.pointerId);}catch(_){}};
  g.addEventListener('pointerup',end); g.addEventListener('pointercancel',end);
})();
</script></body></html>
"""


def _sprite_config_path():
    # sprite.json と chacha.png は assets/。src/ から見て親の assets/（シートは sprite.json 隣で解決）。
    return SPRITE_CONFIG or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "sprite.json")


def _load_sprite():
    """sprite.json を読み、enabled かつシート PNG があれば {frame_w,frame_h,scale,animations,dataUri} を返す。
    無効/欠損/読めない時は None（→ JS は procedural にフォールバック）。ADR-0010 の皮の差し替え口。"""
    path = _sprite_config_path()
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        if not cfg.get("enabled"):
            return None
        sheet = cfg.get("sheet") or ""
        sheet_path = sheet if os.path.isabs(sheet) else os.path.join(os.path.dirname(path), sheet)
        with open(sheet_path, "rb") as f:
            raw = f.read()
        cfg["dataUri"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        cfg.pop("sheet", None)
        return cfg
    except Exception:
        return None


def build_web_html():
    """WEB_HTML に sprite 設定を注入して返す（run_web が使う）。シート無しなら SPRITE=null のまま。"""
    sprite = _load_sprite()
    return WEB_HTML.replace("/*SPRITE*/null",
                            json.dumps(sprite, ensure_ascii=False) if sprite else "null")
