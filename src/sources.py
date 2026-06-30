#!/usr/bin/env python3
"""sources.py — イベント源（ADR-0011 / ADR-0013 ①）。

EventSource = ナレーションを産むプロデューサ。
  next_phase(ctx) -> Narration | SILENT | None
    Narration … 茶々に注入する1フェーズ
    SILENT    … 無言で状態だけ進めた（隙間 or react=False のビート）。終了ではない
    None      … 結了（Scheduler が reset()+cooldown）
天気は実天気が真実・箱庭はそれに従属（ADR-0012）。状態は実天気、瞬間の手触りは箱庭。
"""
import datetime
import json
import os
import random
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import config        # 設定解決（env > engawa.json > 既定）

# ── 源レベルのつまみ（env 上書き可・テスト容易化）─────────────
ARC_COOLDOWN = int(os.environ.get("ENGAWA_ARC_COOLDOWN", "5"))
MAX_ARC_TICKS = int(os.environ.get("ENGAWA_MAX_ARC_TICKS", "12"))
PHASE_GAP = (int(os.environ.get("ENGAWA_PHASE_GAP_MIN", "1")),
             int(os.environ.get("ENGAWA_PHASE_GAP_MAX", "2")))

# 自発来訪のつまみ（ADR-0008: 時刻×確率×クールダウン。「来ない日がある」を低確率で表現）
GUEST_VISIT_FROM_HOUR = config.get_int("ENGAWA_GUEST_FROM_HOUR", "guest", "from_hour", 15, lo=0, hi=23)   # 夕方以降のみ
GUEST_VISIT_PROB = config.get_float("ENGAWA_GUEST_PROB", "guest", "prob", 0.05, lo=0, hi=1)              # eligible 判定の素確率（低め）

# 客人の世間話トピック（ADR-0014: ホワイトリスト・時節土台＋やわらかRSS・確率注入）
TOPIC_REFRESH_MIN = config.get_int("ENGAWA_TOPIC_REFRESH_MIN", "topic", "refresh_min", 30, lo=1)   # キャッシュ更新間隔（分）
TOPIC_PROB = config.get_float("ENGAWA_TOPIC_PROB", "topic", "prob", 0.7, lo=0, hi=1)               # 世間ビートでネタを使う確率
TOPIC_MAX_LEN = int(os.environ.get("ENGAWA_TOPIC_MAX_LEN", "120"))           # 1ネタの長さ上限
TOPIC_MAX_PER_SOURCE = int(os.environ.get("ENGAWA_TOPIC_MAX_PER_SOURCE", "5"))
TOPIC_MAX_BYTES = 512 * 1024                                                  # rss 取得サイズ上限
TOPIC_CONFIG = os.environ.get("ENGAWA_TOPIC_CONFIG", "")                     # 外部JSON で源を差し替え（配布時）

OSAKA_LAT, OSAKA_LON = 34.6937, 135.5023   # TODO(Backlog): 利用者が変更できる仕様へ（地名ラベルも連動）

WEATHER_CODE = {
    0: "快晴", 1: "おおむね晴れ", 2: "ところどころ曇り", 3: "曇り", 45: "霧", 48: "霧氷の霧",
    51: "霧雨", 53: "霧雨", 55: "強い霧雨", 61: "小雨", 63: "雨", 65: "強い雨", 66: "凍る雨", 67: "強い凍る雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪", 80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪", 95: "雷雨", 96: "雹混じりの雷雨", 99: "激しい雹混じりの雷雨",
}


def fetch_weather():
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={OSAKA_LAT}&longitude={OSAKA_LON}"
           "&current=temperature_2m,weather_code,wind_speed_10m&timezone=Asia%2FTokyo")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            cur = json.loads(r.read().decode("utf-8")).get("current", {})
        return {"temp": cur.get("temperature_2m"), "wind": cur.get("wind_speed_10m"),
                "desc": WEATHER_CODE.get(cur.get("weather_code"), "よくわからない空")}
    except Exception:
        return None


def time_of_day(now):
    h = now.hour
    return ("夜明け" if 5 <= h < 8 else "朝" if 8 <= h < 11 else "昼" if 11 <= h < 15
            else "夕方" if 15 <= h < 18 else "宵" if 18 <= h < 22 else "夜更け")


# ── 客人の世間話トピック（ADR-0014）─────────────────────────────
# 取得先ホワイトリストは topic_sources.json（リポジトリ直下・env ENGAWA_TOPIC_CONFIG で差し替え）。
# kind=local はコード生成（無取得・常に在庫）、rss は取得（url+domain 必須・host が domain 一致＋https のみ）。
# 設定に載った源しか fetch しない＝設定そのものが whitelist。コードに URL は埋めない。

# 二十四節気（おおよその開始日・暦年順）。今日に最も近い直近の節気を選ぶ。
SEKKI = [
    (1, 6, "小寒", "寒の入り、一年で最も寒さが厳しくなる頃"),
    (1, 20, "大寒", "寒さの底、もうすぐ春に向かう頃"),
    (2, 4, "立春", "暦の上で春が始まる頃"),
    (2, 19, "雨水", "雪が雨に変わり、氷が解け始める頃"),
    (3, 6, "啓蟄", "冬ごもりの虫が土から出てくる頃"),
    (3, 20, "春分", "昼と夜の長さがほぼ同じになる頃"),
    (4, 5, "清明", "草木が芽吹き、すべてが清々しい頃"),
    (4, 20, "穀雨", "春の雨が穀物を潤す頃"),
    (5, 6, "立夏", "暦の上で夏が始まる頃"),
    (5, 21, "小満", "草木が茂り、生命が満ち始める頃"),
    (6, 6, "芒種", "稲を植え、梅雨入りの頃"),
    (6, 21, "夏至", "一年で最も昼が長い頃"),
    (7, 7, "小暑", "梅雨明けが近く、暑さが増していく頃"),
    (7, 23, "大暑", "一年で最も暑さが厳しい頃"),
    (8, 7, "立秋", "暦の上で秋が始まる、残暑の頃"),
    (8, 23, "処暑", "暑さがようやく和らいでくる頃"),
    (9, 8, "白露", "草に朝露が宿り始める頃"),
    (9, 23, "秋分", "再び昼夜の長さが等しくなる頃"),
    (10, 8, "寒露", "冷たい露が結ぶ、秋が深まる頃"),
    (10, 24, "霜降", "霜が降り始める頃"),
    (11, 7, "立冬", "暦の上で冬が始まる頃"),
    (11, 22, "小雪", "わずかに雪が降り始める頃"),
    (12, 7, "大雪", "本格的に雪が降り積もる頃"),
    (12, 22, "冬至", "一年で最も昼が短い、柚子湯の頃"),
]

MONTH_SHUN = {
    1: "七草・蜜柑・寒鰤", 2: "蕗の薹・牡蠣・梅のつぼみ", 3: "蛤・若布・菜の花",
    4: "筍・桜鯛・新若布", 5: "鰹・新茶・豆ご飯", 6: "梅・初鰹・さくらんぼ",
    7: "鮎・西瓜・茄子", 8: "桃・とうもろこし・冷奴", 9: "秋刀魚・葡萄・新米",
    10: "栗・松茸・秋刀魚", 11: "蜜柑・牡蠣・銀杏", 12: "柚子・蟹・大根",
}


def _seasonal_topics(now=None):
    now = now or datetime.datetime.now()
    md = (now.month, now.day)
    cur = SEKKI[-1]                       # 1/6 より前なら前年末の冬至
    for m, d, name, phrase in SEKKI:
        if (m, d) <= md:
            cur = (m, d, name, phrase)
    return [
        {"text": f"{cur[2]}—{cur[3]}", "tone": "季節", "source": "時節"},
        {"text": f"今の旬は {MONTH_SHUN[now.month]} あたり", "tone": "季節", "source": "旬"},
    ]


def _host_allowed(url, domain):
    """rss は『そのソースが自己申告した domain』に host が一致＋https のときだけ許可（whitelist 強制）。"""
    if not domain:
        return False
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    if u.scheme != "https" or not u.hostname:
        return False
    h = u.hostname
    return h == domain or h.endswith("." + domain)


def _parse_rss_titles(data):
    """RSS<item>/Atom<entry> の <title> だけ取る（見出し限定＝注入面を最小化）。"""
    try:
        root = ET.fromstring(data)
    except Exception:
        return []
    titles = []
    for it in root.iter():
        if not (it.tag.endswith("item") or it.tag.endswith("entry")):
            continue
        for ch in it:
            if ch.tag.endswith("title") and (ch.text or "").strip():
                titles.append(ch.text.strip())
                break
    return titles


def _fetch_rss(source):
    if not _host_allowed(source.get("url", ""), source.get("domain")):   # whitelist 強制（domain＋https）
        return []
    req = urllib.request.Request(source["url"], headers={"User-Agent": "engawa/0.5"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = r.read(TOPIC_MAX_BYTES)               # サイズ上限
    out = []
    for t in _parse_rss_titles(data)[:TOPIC_MAX_PER_SOURCE]:
        t = " ".join(t.split())[:TOPIC_MAX_LEN]      # 整形＋長さ上限
        if t:
            out.append({"text": t, "tone": source.get("tone", "暮らし"),
                        "persona": source.get("persona"), "source": source["name"]})
    return out


def _topic_config_path():
    # topic_sources.json はリポジトリ直下。src/ から見て親ディレクトリ。
    return TOPIC_CONFIG or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "topic_sources.json")


def _load_topic_sources():
    """topic_sources.json から源リストを読む。欠損/壊れは時節(local)のみへフォールバック。"""
    try:
        with open(_topic_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        srcs = data.get("sources") if isinstance(data, dict) else data
        if isinstance(srcs, list):
            return srcs
    except Exception:
        pass
    return [{"name": "時節", "kind": "local", "enabled": True}]


def fetch_topics():
    """有効＆ホワイト合格の源からトピック・プールを作る（weather と同型・失敗は握りつぶす）。"""
    pool = []
    for s in _load_topic_sources():
        if not s.get("enabled"):
            continue
        try:
            if s.get("kind") == "local":
                pool += _seasonal_topics()
            elif s.get("kind") == "rss":
                pool += _fetch_rss(s)
        except Exception:
            pass
    return pool


def build_context(weather, topics=None):
    now = datetime.datetime.now()
    desc = (weather or {}).get("desc", "")
    return {"weather": weather, "desc": desc,
            "raining": any(k in desc for k in ("雨", "雷", "霧雨")),
            "tod": time_of_day(now), "hour": now.hour, "now": now,
            "topics": topics or []}


# ── Narration（value）と SILENT 番兵 ──────────────────────────
class Narration:
    __slots__ = ("text", "kind", "label", "voice")
    def __init__(self, text, kind, label=None, voice=None):
        # voice: 表示用の「客人の生セリフ」。text(茶々への注入)と別に画面へ出す（客人来訪のみ）
        self.text, self.kind, self.label, self.voice = text, kind, label, voice


SILENT = object()   # next_phase が「無言で進めた・終了ではない」を表す番兵


def _suppressor():
    return "何か言うならひとこと。何も言いたくなければ「……」でよい。"


def event_narration(text):
    # 箱庭イベント。時刻は入れない（茶々がツール地金=now を漏らす誘発になる。原則#2）
    return f"[縁側の外]\n{text}\n茶々は縁側からそれを見ている。\n{_suppressor()}"


def ambient_narration(ctx):
    now = ctx["now"]; w = ctx["weather"]
    parts = [f"時刻: {now.strftime('%H:%M')}（{ctx['tod']}）"]
    if w:
        s = f"大阪は{ctx['desc']}"
        if w.get("temp") is not None:
            s += f"、{w['temp']}℃"
        if isinstance(w.get("wind"), (int, float)) and w["wind"] >= 20:
            s += "、風が強い"
        parts.append(s)
    return ("[縁側の外]\n" + "\n".join(parts)
            + "\nあなた（茶々）は縁側に座って外を眺めている。\n"
            + "独り言を漏らすなら、ひとこと。何も言いたくなければ「……」だけでよい。")


def transition_narration(prev, ctx):
    now = ctx["now"]
    return (f"[縁側の外]\n時刻 {now.strftime('%H:%M')}（{ctx['tod']}）。"
            f"さっきまで「{prev}」やったのが、いま「{ctx['desc']}」に変わってきた。\n"
            f"茶々は空の移ろいを眺めている。\n{_suppressor()}")


# ── EventSource 基底 ─────────────────────────────────────────
class EventSource:
    key = "?"
    cooldown_ticks = ARC_COOLDOWN
    def eligible(self, ctx):
        return True
    async def next_phase(self, ctx):
        return None
    def reset(self):
        pass
    async def close(self):
        pass


# ── 箱庭アーク（雀/猫/風） ───────────────────────────────────
class Phase:
    __slots__ = ("tag", "narrate", "react")
    def __init__(self, tag, narrate, react=True):
        self.tag, self.narrate, self.react = tag, narrate, react


class BoxGardenArc(EventSource):
    def __init__(self, key, gate, phases, cooldown_ticks=ARC_COOLDOWN, phase_gap=PHASE_GAP):
        self.key = key
        self._gate = gate
        self.phases = phases
        self.cooldown_ticks = cooldown_ticks
        self.phase_gap = phase_gap
        self.idx = 0
        self.gap = 0
        self.age = 0

    def eligible(self, ctx):
        return self._gate(ctx)

    def reset(self):
        self.idx = self.gap = self.age = 0

    async def next_phase(self, ctx):
        if self.age > MAX_ARC_TICKS:          # 安全弁
            return None
        if self.gap > 0:                       # フェーズ間の隙間＝呼吸
            self.gap -= 1; self.age += 1
            return SILENT
        if self.idx >= len(self.phases):
            return None
        ph = self.phases[self.idx]
        self.idx += 1; self.age += 1
        if self.idx < len(self.phases):
            self.gap = random.randint(*self.phase_gap)
        text = ph.narrate(ctx) if callable(ph.narrate) else ph.narrate
        if not ph.react:                       # 無言ビート（ティア分け）
            return SILENT
        return Narration(event_narration(text), "arc", f"箱庭〔{self.key}〕[{ph.tag}]")


# ── 天気源（idle/fallback＋前ティック差分）────────────────────
class WeatherSource(EventSource):
    key = "weather"
    cooldown_ticks = 0
    def __init__(self):
        self.prev_desc = None

    async def next_phase(self, ctx):
        # idle は Scheduler が one-shot で使う。prev_desc は毎観測で更新（移ろい検出）
        prev, desc = self.prev_desc, ctx["desc"]
        self.prev_desc = desc
        if prev and desc and prev != desc:
            return Narration(transition_narration(prev, ctx), "transition", "縁側の外")
        return Narration(ambient_narration(ctx), "ambient", "縁側の外")


# ── 客人源（ADR-0008/0015）───────────────────────────────────
# 自発来訪で着せる役（召喚=/codex はユーザー指定なので使わない）。来訪ごとに1つ抽選。
GUEST_PERSONAS = [
    "気まぐれな旅の行商人",
    "近所の物知りなご隠居",
    "句をひねる風流人",
    "腹を空かせた野良の絵描き",
    "夕暮れに道を訪ねてきた旅人",
]


class GuestSource(EventSource):
    """客人の「源」＝来訪の判定と codex エージェントの生命周期（ADR-0008/0011/0015）。
    live 来訪は 3人会話の部屋（conversation.Room）が駆動し、本クラスは eligible（自発来訪の抽選）・
    persona・ensure_agent/close（codex の spawn/dispose）を担う。人格は prompt へ動的注入（CLAUDE.md でなく）。
    2系統（ADR-0008）:
      - 召喚（/codex <人格>）: persona 指定で生成。eligible=False（registry でなく即 active 化）。
      - 自発来訪: persona=None で registry に常駐。eligible が夕方×低確率で True、来訪ごとに役を抽選。"""
    key = "guest"
    cooldown_ticks = 20
    def __init__(self, persona=None, spawn_codex=None):
        self.persona = persona
        self._autonomous = persona is None       # persona 未指定＝自発来訪（役は reset で抽選）
        self._spawn_codex = spawn_codex          # async factory () -> AcpAgent（codex）
        self.agent = None

    def eligible(self, ctx):
        # 召喚専用インスタンスは抽選に乗らない。自発は夕方以降×低確率（cooldown は Scheduler 側）。
        if not self._autonomous:
            return False
        if ctx["hour"] < GUEST_VISIT_FROM_HOUR:
            return False
        return random.random() < GUEST_VISIT_PROB

    def reset(self):
        if self._autonomous:
            self.persona = random.choice(GUEST_PERSONAS)   # 来訪ごとに役を着せ替え

    async def close(self):
        await self._dispose()

    async def _dispose(self):
        if self.agent is not None:
            try:
                await self.agent.close()
            except Exception:
                pass
            self.agent = None

    async def ensure_agent(self):
        """codex を必要時に1度だけ spawn して返す（3人会話の部屋が使う・ADR-0015）。失敗は例外で上げる。"""
        if self.agent is None:
            self.agent = await self._spawn_codex()
        return self.agent


def _neko_ten(ctx):
    return "雨脚が強まって、猫が耳を伏せた。" if ctx["raining"] \
        else "どこかで物音がして、猫がぴたりと固まった。"


def default_sources(spawn_codex=None):
    """源の registry。追加＝ここに源を1つ足すだけ（Open-Closed）。
    spawn_codex を渡すと自発来訪（GuestSource・夕方×低確率）も registry に乗る。"""
    arcs = [
        BoxGardenArc("雀", gate=lambda c: (not c["raining"]) and 7 <= c["hour"] < 17, phases=[
            Phase("起", "雀が一羽、ひょいと縁側の手すりに止まった。"),
            Phase("承", "雀は首をかしげて、板の間のあたりをちょんちょんとついばんでいる。"),
            Phase("転", "どこかで物音がして、雀がびくっと身をすくめた。"),
            Phase("結", "雀はぱっと羽ばたいて、軒の向こうへ飛んでいった。"),
        ]),
        BoxGardenArc("猫", gate=lambda c: True, phases=[
            Phase("起", "塀の上を、近所の三毛猫がそろりと歩いてきた。"),
            Phase("承", "猫は縁側の方をちらりと見て、足を止めた。", react=False),  # 無言ビート
            Phase("転", _neko_ten),
            Phase("結", "猫は身をひるがえして、軒下のどこかへ消えていった。"),
        ]),
        BoxGardenArc("風", gate=lambda c: True, phases=[
            Phase("単", "一陣の風が、軒先のなにかをことりと鳴らした。"),  # 単発＝1フェーズ
        ]),
    ]
    if spawn_codex is not None:
        arcs.append(GuestSource(spawn_codex=spawn_codex))   # 自発来訪（ADR-0008）
    return arcs
