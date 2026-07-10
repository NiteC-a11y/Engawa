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
import time

import config   # アセット(皮)の差し替えパス解決（env(ENGAWA_*) > engawa.json[assets] > 既定・ADR-0010 の皮を背景にも拡張）
import daynight  # 時刻→背景の昼夜レイヤ（tint 乗算＋glow 加算月光）の純関数（ADR-0028）

def _base_dir():
    """アセット(皮)の基準ディレクトリ。PyInstaller の exe(frozen) は展開先 sys._MEIPASS、
    素の python 実行は src/ の親（リポジトリ直下）。onefile では __file__ が展開先を指さないため、
    frozen 時は同梱アセット(assets/ を --add-data で束ねた場所)を sys._MEIPASS 基準で解決する。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_ASSETS_DIR = os.path.join(_base_dir(), "assets")


def _asset_path(env, key, default_name):
    """アセット（皮＝スプライト設定/背景画像）のパスを解決: env > engawa.json[assets][key] > assets/<default_name>。
    好みの絵に丸ごと差し替えられる差し替え口（ADR-0010）。空/未指定は既定 assets/ を使う。"""
    p = config.get_str(env, "assets", key, "")
    return p if p else os.path.join(_ASSETS_DIR, default_name)

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
    def set_font(self, scale): return False    # 文字倍率をライブ適用（/font・web のみ True／console は no-op）
    def current_font(self): return None        # 今の文字倍率（web=float／console=None＝設定対象外）
    def set_absent(self, absent): return False  # 中座の in/out（web は茶々スプライトを消す/戻す＝空っぽの縁側・ADR-0027／console は no-op）
    def set_daynight(self, spec): return False  # 昼夜プレビュー override（/daynight＝固定/早送り/実時間・web のみ True／console は no-op・ADR-0028）
    def current_daynight(self): return None      # 今の昼夜override（web=dict{"mode":...}／console=None＝背景が無い＝対象外）
    def set_daynight_enabled(self, on): return False  # 昼夜 tint 機能そのものの on/off をライブ適用（/daynight on|off・web のみ／保存は呼び側）
    def daynight_enabled(self): return None      # 機能が有効か（web=bool／console=None＝対象外）
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
        self._font = 1.0          # 文字倍率（/font テスト用・web と同じく設定対象扱い）
        self._absent = False      # 中座の in/out（テスト記録用）
        self._dn = None           # 昼夜プレビューの override（/daynight テスト用・web の代役）
        self._daynight = True     # 昼夜 tint 機能が有効か（/daynight on|off テスト用・既定オン）
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

    def set_font(self, scale):                 # /font のライブ適用を記録（web の代役）
        self._font = float(scale)
        self.events.append(("set_font", self._font, None))

    def set_absent(self, absent):              # 中座の in/out を記録（web の代役・スプライト消灯/点灯）
        self._absent = bool(absent)
        self.events.append(("set_absent", self._absent, None))
        return True
        return True

    def current_font(self):
        return self._font

    def set_daynight(self, spec):              # /daynight プレビューの override を記録（web の代役・pin/demo だけ張る）
        self._dn = dict(spec) if (spec and spec.get("mode") in ("pin", "demo")) else None
        self.events.append(("set_daynight", self._dn, None))
        return True

    def current_daynight(self):
        return dict(self._dn) if self._dn else {"mode": "real"}

    def set_daynight_enabled(self, on):        # /daynight on|off のライブ適用を記録（web の代役）
        self._daynight = bool(on)
        self._dn = None                        # トグルは実時間へリセット
        self.events.append(("set_daynight_enabled", self._daynight, None))
        return True

    def daynight_enabled(self):
        return self._daynight

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
        self._font = 1.0                # 文字倍率（ENGAWA_UI_FONT・観戦窓にも一貫適用）
        self._absent = False            # 茶々が中座中か（poll で JS へ→スプライトを消す/戻す・ADR-0027）
        # 背景の昼夜 tint（ADR-0028）。既定オン・0 で無効＝固定背景。poll が font/absent と同じく毎回載せる
        self._daynight = config.get_int("ENGAWA_DAYNIGHT", "ui", "daynight", 1, 0, 1) == 1
        self._dn = None                 # 昼夜プレビューの override（/daynight）。None=実時間／pin/demo spec。demo は _t0(monotonic) を持つ

    def set_layout(self, corner, main_w, main_h, font=1.0):
        self._corner = corner
        self._main_wh = (int(main_w), int(main_h))
        self._font = float(font)

    def set_font(self, scale):
        """文字倍率をライブ適用（/font）。poll が返す font を JS が拾って --fz を差し替える
        （スレッド跨ぎの evaluate_js を使わない poll 方式・本窓と観戦窓の両方に効く）。"""
        with self._lock:
            self._font = float(scale)
        return True

    def current_font(self):
        with self._lock:
            return self._font

    def set_absent(self, absent):
        """中座の in/out（poll が返す absent を JS が拾い、茶々スプライトをフェードで消す/戻す＝
        空っぽの縁側・ADR-0027）。font と同じく poll に毎回載せる持続フラグ方式（cross-thread evaluate_js 回避）。"""
        with self._lock:
            self._absent = bool(absent)
        return True

    def set_daynight(self, spec):
        """昼夜プレビューの override を張る/外す（/daynight・ADR-0028）。pin/demo だけが override を作り、
        それ以外（auto/real/None 等）は解除＝実時間へ。demo は開始時刻(_t0=monotonic)を刻んで poll が
        経過秒から仮想時刻を出す。判断は daynight の純関数側。"""
        with self._lock:
            mode = spec.get("mode") if spec else None
            if mode == "demo":
                self._dn = dict(spec, _t0=time.monotonic())
            elif mode == "pin":
                self._dn = dict(spec)
            else:
                self._dn = None            # auto/real/その他＝override 解除（実時間へ）
        return True

    def current_daynight(self):
        """今の override（web は常に dict を返す＝{"mode":"real"} も含む・console は None＝対象外）。"""
        with self._lock:
            return dict(self._dn) if self._dn else {"mode": "real"}

    def set_daynight_enabled(self, on):
        """昼夜 tint 機能そのものを live に on/off（/daynight on|off）。off＝poll が day=None＝背景固定。
        プレビュー override はトグルで解除（前の固定を残さず実時間から）。永続保存は呼び側（scheduler→config）。"""
        with self._lock:
            self._daynight = bool(on)
            self._dn = None                # トグルは実時間へリセット（プレビュー固定を残さない）
        return True

    def daynight_enabled(self):
        with self._lock:
            return self._daynight

    def _resolve_day(self):
        """poll が返す背景の昼夜 {tint,glow}（override 反映）。無効なら None＝JS は素通し。
        demo の再生が終わったら override を外して実時間へ戻す（純関数 effective_layers の合図に従う）。"""
        if not self._daynight:
            return None
        now = datetime.datetime.now()
        with self._lock:
            spec = self._dn
        elapsed = (time.monotonic() - spec["_t0"]) if (spec and spec.get("mode") == "demo") else 0.0
        day, expired = daynight.effective_layers(spec, now, elapsed)
        if expired:
            with self._lock:
                if self._dn is spec:            # 別の override に差し替わってなければ実時間へ戻す
                    self._dn = None
        return day

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
        gw, gh = int(380 * self._font), int(320 * self._font)   # 文字倍率に合わせ観戦窓も拡大（盤がはみ出さない）
        x, y = self._game_xy(gw, gh)
        try:
            import webview
            self._game_window = webview.create_window(
                str(title), html=build_game_html(self._font), js_api=self._game_api,
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
                return {"rev": self._game_rev, "font": self._font}   # rev 据置でも font はライブ反映
            return {"rev": self._game_rev, "snap": self._game_snap,
                    "lines": list(self._game_lines), "font": self._font}

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
            out = {"items": items, "cursor": self._rev, "font": self._font, "absent": self._absent}
        # 昼夜 tint（ADR-0028）: 大阪時刻を単一情報源に毎回配る（無効時 None＝JS は素通し）。
        # /daynight の override（固定/早送り）も含めて _resolve_day が解決（判断は daynight の純関数）。
        out["day"] = self._resolve_day()
        return out

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
  :root{--fz:/*FONT*/1}   /* 文字倍率（ENGAWA_UI_FONT）。盤=文字+カード箱+窓を揃えて拡大（zoom は 100vh で切れるので不使用） */
  html,body{margin:0;height:100%;background:#33503f;color:#f0ece2;overflow:hidden;
    font-family:system-ui,"Yu Gothic UI",sans-serif;user-select:none}
  #app{height:100vh;box-sizing:border-box;border:1px solid #20382b;padding:8px 10px;cursor:move;overflow-y:auto}
  .label{font-size:calc(12px * var(--fz));opacity:.8;margin:0 0 6px;text-align:center;letter-spacing:2px}
  .row{display:flex;align-items:center;gap:4px;margin:7px 0;min-height:calc(38px * var(--fz))}
  .row.dealer{border-bottom:1px dashed rgba(255,255,255,.22);padding-bottom:9px;margin-bottom:10px}
  .who{width:calc(84px * var(--fz));font-size:calc(13px * var(--fz));flex:0 0 auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .val{margin-left:6px;font-size:calc(13px * var(--fz));opacity:.85;min-width:calc(34px * var(--fz))}
  .row.cur{background:rgba(255,255,255,.10);border-radius:7px}
  .card{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;
    width:calc(26px * var(--fz));height:calc(36px * var(--fz));background:#fbfaf6;border-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.4)}
  .card.red{color:#c83b2e}.card.blk{color:#23201c}
  .card b{font-weight:700;font-size:calc(13px * var(--fz));line-height:1}.card i{font-style:normal;font-size:calc(11px * var(--fz));line-height:1}
  .card.back{background:repeating-linear-gradient(45deg,#7a8fae 0 4px,#67809f 4px 8px)}
  .badge{margin-left:8px;font-size:calc(12px * var(--fz));padding:1px 8px;border-radius:10px}
  .badge.win{background:#2e7d4f}.badge.lose{background:#9a3b32}.badge.draw{background:#6b6256}
  #txt{font-size:calc(12px * var(--fz));white-space:pre-wrap;opacity:.9}
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
// 文字倍率（/font のライブ適用）: 本窓と揃えて観戦窓の --fz も poll で差し替える
let curFont=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fz'))||1;
function applyFont(f){ if(typeof f==='number'&&f>0&&f!==curFont){curFont=f;document.documentElement.style.setProperty('--fz',f);} }
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
    if(r) applyFont(r.font);                                    // /font のライブ適用（rev 据置でも効く）
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
  :root{--fz:/*FONT*/1}   /* 本文/入力の文字倍率（ENGAWA_UI_FONT・既定1）。窓全体 zoom は使わない＝入力欄を切らない */
  html,body{margin:0;height:100%;background:#2a2320;color:#f0e9e0;overflow:hidden;
    font-family:system-ui,"Yu Gothic UI",sans-serif;user-select:none}
  #app{display:flex;flex-direction:column;height:100vh;position:relative;
    border:1px solid #1a1410;box-sizing:border-box}
  #scene{position:relative;flex:0 0 200px;overflow:hidden;cursor:move;isolation:isolate;
    background:linear-gradient(#bcd6e6 0%,#dbe7ec 38%,#e8dcc8 38%,#decba8 100%)}
  /* 背景の昼夜 tint（ADR-0028）: #scene 内に閉じた膜2枚。染め=乗算で暗く＋色付け、光=加算で月明かり。
     isolation:isolate で乗算/加算が窓外(暗い地)へ漏れない。z は UI(×20/grip30/nya30)より後ろ＝UIは染めない */
  #tint{position:absolute;inset:0;z-index:15;pointer-events:none;mix-blend-mode:multiply;
    background-color:rgb(255,255,255);transition:background-color 1.2s linear}   /* 昼=白＝乗算しても無変化 */
  /* 室内灯（障子ごしに漏れる暖色・夜だけ点灯）: tint(乗算=暗く)の上に screen で足す＝夜に部屋の灯りがにじむ。
     上端(部屋=画面上=障子側)から下へフェード。scene.png 差し替えでも壊れない位置非依存の出方（ADR-0010）。 */
  #lamp{position:absolute;left:0;right:0;top:0;height:56%;z-index:16;pointer-events:none;mix-blend-mode:screen;opacity:0;
    background:radial-gradient(150% 100% at 50% -12%,rgba(255,198,122,.62),rgba(255,182,104,.18) 44%,transparent 68%);
    transition:opacity 1.2s linear}                                             /* 強さは opacity=lamp */
  #glow{position:absolute;inset:0;z-index:17;pointer-events:none;mix-blend-mode:screen;opacity:0;
    background:radial-gradient(120% 80% at 80% 12%,rgba(200,220,255,.55),transparent 60%);
    transition:opacity 1.2s linear}                                              /* 隅の月明かり・強さは opacity */
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
  .shoji{position:absolute;top:0;left:0;right:0;height:38%;opacity:.5;
    background:repeating-linear-gradient(90deg,#b9a988 0 2px,transparent 2px 25%),
      repeating-linear-gradient(0deg,#b9a988 0 2px,transparent 2px 33%),#efe7d6}
  .floor{position:absolute;bottom:0;left:0;right:0;height:62%;
    background:repeating-linear-gradient(90deg,#caa46b 0 17px,#bd9a5c 17px 19px)}
  #cha{position:absolute;left:50%;bottom:24px;transform:translateX(-50%);z-index:2;
    image-rendering:pixelated;width:118px;height:118px;transition:opacity .55s ease}
  /* 中座＝茶々が席を外す：スプライトと接地影をフェードで消す＝空っぽの縁側（ADR-0027） */
  #cha.gone,#chashadow.gone{opacity:0}
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
  /* ダブルクリックで茶々の頭上にハートがふわっと舞う（クライアント完結・トークン0・--dx で横ゆらぎ） */
  .heart{position:absolute;z-index:29;pointer-events:none;color:#ff7a9c;
    text-shadow:0 1px 2px rgba(0,0,0,.35);will-change:transform,opacity;
    animation:floatUp 1s ease-out forwards}
  @keyframes floatUp{0%{opacity:0;transform:translate(0,4px) scale(.5)}
    25%{opacity:1;transform:translate(calc(var(--dx) * .35),-16px) scale(1)}
    100%{opacity:0;transform:translate(var(--dx),-64px) scale(.9)}}
  /* 接地影：浮き感を消す（スプライトの足元に敷く） */
  #chashadow{position:absolute;left:50%;transform:translateX(-50%);height:9px;display:none;z-index:1;
    background:rgba(0,0,0,.22);border-radius:50%;filter:blur(3px)}
  #log{flex:1;overflow-y:auto;padding:8px 11px;font-size:calc(15px * var(--fz));line-height:1.5;background:#2a2320}
  .item{margin:3px 0;word-break:break-word}
  .sys{color:#9c8e84;font-size:calc(12px * var(--fz))}
  .guest{color:#9fd2e2}.cha{color:#f3e8c9}
  .you{color:#bfd99a;text-align:right}
  .who{opacity:.55;margin-right:4px;font-size:calc(11px * var(--fz))}
  #bar{display:flex;gap:6px;padding:8px;background:#1f1916;align-items:flex-end}
  /* 宛先ドロップダウン（左・3人会話の入力補助。@ は日本語IMEで打ちにくいのでセレクトで選ぶ） */
  /* 宛先チップ（入力欄の上・来訪中だけ表示・タップで次の発言の宛先を選ぶ） */
  #addrbar{display:none;gap:5px;align-items:center;padding:5px 8px 0;background:#1f1916}
  #addrbar.on{display:flex}
  #addrbar .al{font-size:11px;opacity:.5}
  #addrbar .ac{font-size:12px;padding:3px 10px;border:1px solid #5a4a3a;border-radius:12px;
    background:#2e2620;color:#cdbfae;cursor:pointer}
  #addrbar .ac.sel{background:#caa46b;color:#2a2320;border-color:#caa46b}
  #in{flex:1;padding:8px;border:1px solid #5a4a3a;border-radius:6px;background:#2e2620;color:#f0e9e0;font-size:calc(13px * var(--fz));font-family:inherit;line-height:1.4;resize:none;overflow-y:auto;max-height:8.5em;box-sizing:border-box}
  #send{padding:8px 13px;border:0;border-radius:6px;background:#caa46b;color:#2a2320;cursor:pointer}
  /*SCENEBG*/
</style></head>
<body><div id="app">
  <div id="scene" class="pywebview-drag-region"><div class="shoji"></div><div class="floor"></div>
    <div id="kehai"></div><div id="chashadow"></div>
    <canvas id="cha" width="74" height="74"></canvas><div id="nya">ニャー</div>
    <div id="tint"></div><div id="lamp"></div><div id="glow"></div></div>
  <button id="close" title="閉じる">×</button>
  <div id="grip" title="ドラッグでリサイズ"></div>
  <div id="log"></div>
  <div id="addrbar"><span class="al">宛先</span>
    <button class="ac sel" data-p="">茶々</button>
    <button class="ac" data-p="guest">客人</button>
    <button class="ac" data-p="both">二人とも</button></div>
  <div id="bar"><textarea id="in" rows="1" placeholder="話しかける…" autocomplete="off"></textarea>
    <button id="send">送信</button></div>
</div>
<script>
const log=document.getElementById('log'), inp=document.getElementById('in');
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let since=0; const seen={}; let busy=false;
// 文字倍率（/font のライブ適用）: poll が返す font を --fz に反映（再起動不要・入力欄を切らない本文拡大）
let curFont=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fz'))||1;
function applyFont(f){ if(typeof f==='number'&&f>0&&f!==curFont){curFont=f;document.documentElement.style.setProperty('--fz',f);} }
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
let absent=false;
function setAbsent(a){                                          // 中座＝茶々スプライト（＋接地影）をフェードで消す/戻す（ADR-0027）
  if(a===absent) return; absent=a;
  document.getElementById('cha').classList.toggle('gone',a);
  const sh=document.getElementById('chashadow'); if(sh) sh.classList.toggle('gone',a);
}
// 背景の昼夜 tint（ADR-0028）: poll の {tint,glow,lamp}（大阪時刻→純関数）を膜3枚へ。null=無効なら素通し
const tintEl=document.getElementById('tint'), glowEl=document.getElementById('glow'), lampEl=document.getElementById('lamp');
function applyDay(d){
  if(!d){                                // 昼夜 tint 無効（/daynight off）→ 膜を中立化＝素の明るい背景へ戻す
    tintEl.style.backgroundColor='#ffffff';  // 乗算で白＝無変化（前の夜色を残さない）
    glowEl.style.opacity=0; lampEl.style.opacity=0; return;
  }
  tintEl.style.backgroundColor=d.tint;   // 乗算色（昼=白で無変化／夕=桃／夜=青灰）
  glowEl.style.opacity=d.glow;           // 月明かりの強さ 0..1（空の隅・寒色）
  lampEl.style.opacity=d.lamp;           // 室内灯の強さ 0..1（障子ごしの暖色・夜だけ）
}
async function tick(){
  if(busy||!window.pywebview) return; busy=true;
  try{
    const r=await window.pywebview.api.poll(since);
    applyFont(r.font);                                          // /font のライブ適用（--fz 差し替え）
    setAbsent(!!r.absent);                                      // 中座＝空っぽの縁側（poll の absent を反映）
    applyDay(r.day);                                            // 背景の昼夜 tint（poll の {tint,glow}・ADR-0028）
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
function autogrow(){ inp.style.height='auto'; inp.style.height=inp.scrollHeight+'px'; }  // 内容に応じ高さ伸縮（CSS max-height で上限・超過は内部スクロール）
function send(){
  const v=inp.value.trim(); if(!v||!window.pywebview) return;
  window.pywebview.api.send(v, addrTo);                      // 本文はクリーン・宛先は別引数で渡す（C方式）
  inp.value=''; autogrow();                                  // 宛先(チップ)は保持＝変えるまで同じ相手（B方式・客人退場時のみ refreshAddr が茶々へ戻す）
}
inp.addEventListener('input', autogrow);                     // 幅で折り返し＋行数に応じ縦に伸ばす＝Enter前に見直せる
document.getElementById('send').onclick=send;
document.getElementById('close').onclick=()=>{window.pywebview&&window.pywebview.api.close();};
const ENTER_MODE=/*ENTERMODE*/"send";                          // ui.enter: 'send'=Enter送信/Shift+Enter改行 ／ 'newline'=Enter改行/Ctrl(⌘)+Enter送信
inp.placeholder=(ENTER_MODE==='send')?'話しかける…（Enterで送信・Shift+Enterで改行）':'話しかける…（改行OK・送信は Ctrl+Enter か 送信ボタン）';
inp.addEventListener('keydown',e=>{
  if(e.key!=='Enter'||e.isComposing||e.keyCode===229) return;  // IME変換確定は素通し（確定のみ＝送信も改行もしない）
  const go=(ENTER_MODE==='send')?!e.shiftKey:(e.ctrlKey||e.metaKey);  // send:素Enter送信 ／ newline:Ctrl/⌘+Enter送信
  if(go){e.preventDefault();send();}                           // それ以外（send の Shift+Enter / newline の素Enter）は default＝改行
});
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
  cv.width=SPRITE.frame_w; cv.height=SPRITE.frame_h;           // buffer=絵の実寸（drawImage は 1:1）
  let dw, dh;
  if(SPRITE.display_px){                                       // 縮尺方式: 表示pxを指定→CSSがスムーズ縮小（非ドット絵向け・画像を作り直さずサイズ調整）
    dh=SPRITE.display_px; dw=Math.round(dh*SPRITE.frame_w/SPRITE.frame_h);
    cv.style.imageRendering='auto';                           // スムーズ（CSS の pixelated を上書き）
  }else{                                                       // 従来: 整数倍アップスケール＋pixelated（ドット絵向け）
    const fit=Math.max(1,Math.floor((sh-botM-topR)/SPRITE.frame_h));
    const sc=Math.max(1,Math.min(SPRITE.scale||fit,fit));     // 指定倍率を fit でクランプ（整数倍＝クリスプ）
    dw=SPRITE.frame_w*sc; dh=SPRITE.frame_h*sc;
  }
  cv.style.width=dw+'px'; cv.style.height=dh+'px';
  cv.style.bottom=botM+'px';
  cv.classList.add('breathe');                                 // 猫だけの絵（座布団なし）＝呼吸を戻す
  const shd=document.getElementById('chashadow');             // 足元に接地影＝浮き感を消す（幅/上下/高さは sprite.json で調整）
  const shw=(SPRITE.shadow_w??0.7), shdy=(SPRITE.shadow_dy??-2), shh=(SPRITE.shadow_h??9);
  shd.style.width=Math.round(dw*shw)+'px'; shd.style.height=shh+'px';
  shd.style.bottom=Math.max(0,botM+shdy)+'px'; shd.style.display='block';
  g.imageSmoothingEnabled=false;
}
requestAnimationFrame(SPRITE?drawSheet:drawChacha);   // シートがあればコマ送り、無ければ procedural
// 茶々をダブルクリック → ニャー（吹き出し＋こっち見てにっこり）＋ハートがふわっと舞う。
// LLM/客人を介さないクライアント完結の小ネタ＝トークン消費なし・即反応
function hearts(){                                              // 頭上に数個♥を撒いて浮かせ、終わったら除去
  const scene=document.getElementById('scene');
  const r=cv.getBoundingClientRect(), sr=scene.getBoundingClientRect();   // translateX(-50%) 込みの実描画位置
  const cx=r.left-sr.left+r.width/2, cy=r.top-sr.top+r.height*0.26;       // 茶々の頭あたり（真上中央）
  for(let i=0;i<5;i++){
    const h=document.createElement('span'); h.className='heart'; h.textContent='♥';
    h.style.left=(cx+(Math.random()*24-12))+'px';
    h.style.top=cy+'px';
    h.style.fontSize=(10+Math.random()*7)+'px';
    h.style.setProperty('--dx',(Math.random()*36-18).toFixed(1)+'px');
    h.style.animationDelay=(Math.random()*0.18).toFixed(2)+'s';
    scene.appendChild(h);
    setTimeout(()=>h.remove(),1300);                           // アニメ後に DOM 掃除（溜めない）
  }
}
function meow(){
  const nya=document.getElementById('nya'), scene=document.getElementById('scene');
  nya.style.bottom=(scene.clientHeight-cv.offsetTop+2)+'px';   // 茶々の頭の真上に出す（procedural/sprite どちらの寸法でも追従）
  nya.classList.remove('show'); void nya.offsetWidth; nya.classList.add('show');   // 連打でも頭から再生
  hearts();                                                    // ♥ を舞わせる
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
    # 茶々スプライト設定。env ENGAWA_SPRITE_CONFIG > engawa.json[assets].sprite_config > assets/sprite.json
    # （シート PNG は sprite.json 隣で解決＝設定ごと差し替えれば絵も丸ごと替わる）。
    return _asset_path("ENGAWA_SPRITE_CONFIG", "sprite_config", "sprite.json")


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


def _load_scene_bg():
    """縁側の背景画像を dataURI で返す。パスは env ENGAWA_SCENE_BG > engawa.json[assets].scene_bg >
    assets/scene.png。無ければ None＝CSS のグラデ背景（＋.shoji/.floor プレースホルダ）にフォールバック
    ＝好みの背景に丸ごと差し替え可能（ADR-0010 の皮を背景にも拡張）。"""
    try:
        with open(_asset_path("ENGAWA_SCENE_BG", "scene_bg", "scene.png"), "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return None


def build_web_html(font=1.0):
    """WEB_HTML に sprite 設定・縁側背景・文字倍率(font)を注入して返す（run_web が使う）。
    font は本文/入力のフォントだけを calc(BASE * var(--fz)) で拡大＝スクロール領域のみ＝入力欄を押し出さない
    （窓全体の zoom は使わない・6/30 の事故の教訓）。シート無しなら SPRITE=null／背景無しならグラデのまま。"""
    sprite = _load_sprite()
    html = WEB_HTML.replace("/*SPRITE*/null",
                            json.dumps(sprite, ensure_ascii=False) if sprite else "null")
    bg = _load_scene_bg()                                  # 縁側背景画像があれば #scene を差し替え＋プレースホルダ .shoji/.floor を隠す
    html = html.replace("/*SCENEBG*/",
                        ("#scene{background:url(" + bg + ") center/cover no-repeat}.shoji,.floor{display:none}"
                         if bg else ""))
    enter_mode = config.get_str("ENGAWA_UI_ENTER", "ui", "enter", "send")    # Enter の振る舞い（send=送信/newline=改行・ui.enter）
    if enter_mode not in ("send", "newline"):
        enter_mode = "send"
    html = html.replace('/*ENTERMODE*/"send"', json.dumps(enter_mode))
    return html.replace("/*FONT*/1", str(font))


def build_game_html(font=1.0):
    """GAME_HTML（観戦窓）に文字倍率(font)を注入。盤=文字+カード箱+行を calc(BASE * var(--fz)) で揃えて拡大し、
    窓サイズも font 倍で広げる（はみ出さない）。本窓と同じ ENGAWA_UI_FONT で一貫させる。"""
    return GAME_HTML.replace("/*FONT*/1", str(font))
