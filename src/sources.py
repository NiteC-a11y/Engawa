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
import voice         # lang_note＝住人向け narration にも出力言語指示を後置（葉 import・ADR-0022・7/19 の穴）

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
TOPIC_PROB = config.get_float("ENGAWA_TOPIC_PROB", "topic", "prob", 0.7, lo=0, hi=1)               # 天気と一緒に“種”が場の空気に混じる確率（発話有無は LLM 判断・0で無効・ADR-0014）
TOPIC_COOLDOWN = config.get_int("ENGAWA_TOPIC_COOLDOWN", "topic", "cooldown", 2, lo=0)             # 種を置いたら次まで空ける客人ターン数（同じ話題への粘着＝毎ターン振るのを防ぐ・0で無効）
TOPIC_MAX_LEN = int(os.environ.get("ENGAWA_TOPIC_MAX_LEN", "120"))           # 1ネタの長さ上限
TOPIC_MAX_PER_SOURCE = int(os.environ.get("ENGAWA_TOPIC_MAX_PER_SOURCE", "5"))
TOPIC_MAX_BYTES = 512 * 1024                                                  # rss 取得サイズ上限
TOPIC_CONFIG = os.environ.get("ENGAWA_TOPIC_CONFIG", "")                     # 外部JSON で源を差し替え（配布時）

# 天気の観測地点（config 主導・env > engawa.json > 既定。未設定は大阪＝現行挙動を保つ）。
# lat/lon は範囲クランプ（壊れた値で世界の裏側の天気を拾わない）。place は茶々の発話ラベル、
# tz は Open-Meteo の timezone（当地の時刻で current を返す）。緯度経度・地名・TZ の3点が連動。
WEATHER_LAT = config.get_float("ENGAWA_WEATHER_LAT", "weather", "lat", 34.6937, lo=-90, hi=90)
WEATHER_LON = config.get_float("ENGAWA_WEATHER_LON", "weather", "lon", 135.5023, lo=-180, hi=180)
WEATHER_TZ = config.get_str("ENGAWA_WEATHER_TZ", "weather", "tz", "Asia/Tokyo")
PLACE_LABEL = config.get_str("ENGAWA_PLACE_LABEL", "weather", "place", "大阪")   # 茶々が「〜は晴れ」と言う地名

WEATHER_CODE = {
    0: "快晴", 1: "おおむね晴れ", 2: "ところどころ曇り", 3: "曇り", 45: "霧", 48: "霧氷の霧",
    51: "霧雨", 53: "霧雨", 55: "強い霧雨", 61: "小雨", 63: "雨", 65: "強い雨", 66: "凍る雨", 67: "強い凍る雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪", 80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪", 95: "雷雨", 96: "雹混じりの雷雨", 99: "激しい雹混じりの雷雨",
}


def _weather_url(lat=None, lon=None, tz=None):
    """Open-Meteo の現在天気 URL を組む（純関数＝ネット非依存でテスト可）。
    引数省略時は config 解決済みのモジュール値（既定=大阪/Asia/Tokyo）。tz は URL エンコード。"""
    lat = WEATHER_LAT if lat is None else lat
    lon = WEATHER_LON if lon is None else lon
    tz = WEATHER_TZ if tz is None else tz
    return ("https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,weather_code,wind_speed_10m"
            f"&timezone={urllib.parse.quote(tz, safe='')}")


def fetch_weather():
    try:
        with urllib.request.urlopen(_weather_url(), timeout=8) as r:
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

# 七十二候（略本暦・本朝七十二候）。二十四節気を約5日ずつ3候に分けた、さらに細かい季節の刻み。
# (月, 候の入り日≒, 名, 読み, 一言)。暦年順＝1/1(冬至末候)に始まり 12/27(冬至次候)で終わる。
# 日付は略本暦のおおよそ（年で±1〜2日ずれる・SEKKI と同じ近似）。今日に最も近い直近の候を選ぶ。
KOU = [
    (1, 1, "雪下出麦", "ゆきわたりてむぎのびる", "雪の下で麦が芽を出す頃"),
    (1, 6, "芹乃栄", "せりすなわちさかう", "芹が生い茂る頃"),
    (1, 11, "水泉動", "しみずあたたかをふくむ", "地中で凍った泉が動き出す頃"),
    (1, 16, "雉始雊", "きじはじめてなく", "雄の雉が鳴き始める頃"),
    (1, 20, "款冬華", "ふきのはなさく", "蕗の薹が咲き出す頃"),
    (1, 25, "水沢腹堅", "さわみずこおりつめる", "沢の水が厚く凍る頃"),
    (1, 30, "鶏始乳", "にわとりはじめてとやにつく", "鶏が卵を産み始める頃"),
    (2, 4, "東風解凍", "はるかぜこおりをとく", "春風が氷を解かし始める頃"),
    (2, 9, "黄鶯睍睆", "うぐいすなく", "鶯が里で鳴き始める頃"),
    (2, 14, "魚上氷", "うおこおりをいずる", "割れた氷から魚が跳ねる頃"),
    (2, 19, "土脉潤起", "つちのしょううるおいおこる", "雨が土を潤し始める頃"),
    (2, 24, "霞始靆", "かすみはじめてたなびく", "霞がたなびき始める頃"),
    (3, 1, "草木萌動", "そうもくめばえいずる", "草木が芽吹き始める頃"),
    (3, 6, "蟄虫啓戸", "すごもりむしとをひらく", "冬ごもりの虫が出てくる頃"),
    (3, 11, "桃始笑", "ももはじめてさく", "桃の花が咲き始める頃"),
    (3, 16, "菜虫化蝶", "なむしちょうとなる", "青虫が羽化して蝶になる頃"),
    (3, 21, "雀始巣", "すずめはじめてすくう", "雀が巣を作り始める頃"),
    (3, 26, "桜始開", "さくらはじめてひらく", "桜が咲き始める頃"),
    (3, 31, "雷乃発声", "かみなりすなわちこえをはっす", "遠くで雷が鳴り始める頃"),
    (4, 5, "玄鳥至", "つばめきたる", "燕が南から渡ってくる頃"),
    (4, 10, "鴻雁北", "こうがんかえる", "雁が北へ帰っていく頃"),
    (4, 15, "虹始見", "にじはじめてあらわる", "雨上がりに虹が見え始める頃"),
    (4, 20, "葭始生", "あしはじめてしょうず", "葦が芽を吹き始める頃"),
    (4, 25, "霜止出苗", "しもやみてなえいずる", "霜が終わり稲の苗が育つ頃"),
    (4, 30, "牡丹華", "ぼたんはなさく", "牡丹の花が咲く頃"),
    (5, 5, "蛙始鳴", "かわずはじめてなく", "蛙が鳴き始める頃"),
    (5, 10, "蚯蚓出", "みみずいずる", "蚯蚓が地上に這い出る頃"),
    (5, 15, "竹笋生", "たけのこしょうず", "筍が生えてくる頃"),
    (5, 21, "蚕起食桑", "かいこおきてくわをはむ", "蚕が桑を盛んに食べる頃"),
    (5, 26, "紅花栄", "べにばなさかう", "紅花が盛んに咲く頃"),
    (5, 31, "麦秋至", "むぎのときいたる", "麦が実り金色になる頃"),
    (6, 6, "螳螂生", "かまきりしょうず", "蟷螂が生まれ出る頃"),
    (6, 11, "腐草為螢", "くされたるくさほたるとなる", "朽ちた草から蛍が舞う頃"),
    (6, 16, "梅子黄", "うめのみきばむ", "梅の実が黄ばんでくる頃"),
    (6, 21, "乃東枯", "なつかれくさかるる", "靫草が枯れていく頃"),
    (6, 26, "菖蒲華", "あやめはなさく", "菖蒲の花が咲く頃"),
    (7, 1, "半夏生", "はんげしょうず", "半夏が生える頃"),
    (7, 7, "温風至", "あつかぜいたる", "夏の熱い風が吹き始める頃"),
    (7, 12, "蓮始開", "はすはじめてひらく", "蓮の花が開き始める頃"),
    (7, 17, "鷹乃学習", "たかすなわちわざをならう", "鷹の幼鳥が飛び方を覚える頃"),
    (7, 23, "桐始結花", "きりはじめてはなをむすぶ", "桐が実を結び始める頃"),
    (7, 28, "土潤溽暑", "つちうるおうてむしあつし", "土が湿って蒸し暑い頃"),
    (8, 2, "大雨時行", "たいうときどきふる", "時に大雨が降る頃"),
    (8, 7, "涼風至", "すずかぜいたる", "涼しい風が立ち始める頃"),
    (8, 12, "寒蝉鳴", "ひぐらしなく", "蜩が鳴き始める頃"),
    (8, 17, "蒙霧升降", "ふかききりまとう", "深い霧が立ちこめる頃"),
    (8, 23, "綿柎開", "わたのはなしべひらく", "綿を包む萼が開く頃"),
    (8, 28, "天地始粛", "てんちはじめてさむし", "暑さがようやく鎮まる頃"),
    (9, 2, "禾乃登", "こくものすなわちみのる", "稲が実る頃"),
    (9, 8, "草露白", "くさのつゆしろし", "草に降りた露が白く光る頃"),
    (9, 13, "鶺鴒鳴", "せきれいなく", "鶺鴒が鳴き始める頃"),
    (9, 18, "玄鳥去", "つばめさる", "燕が南へ帰っていく頃"),
    (9, 23, "雷乃収声", "かみなりすなわちこえをおさむ", "雷が鳴らなくなる頃"),
    (9, 28, "蟄虫坏戸", "むしかくれてとをふさぐ", "虫が土に隠れ戸を塞ぐ頃"),
    (10, 3, "水始涸", "みずはじめてかるる", "田の水を落とし刈入れに備える頃"),
    (10, 8, "鴻雁来", "こうがんきたる", "雁が北から渡ってくる頃"),
    (10, 13, "菊花開", "きくのはなひらく", "菊の花が開く頃"),
    (10, 18, "蟋蟀在戸", "きりぎりすとにあり", "蟋蟀が戸口で鳴く頃"),
    (10, 23, "霜始降", "しもはじめてふる", "霜が降り始める頃"),
    (10, 28, "霎時施", "こさめときどきふる", "小雨がしとしと降る頃"),
    (11, 2, "楓蔦黄", "もみじつたきばむ", "紅葉や蔦が色づく頃"),
    (11, 7, "山茶始開", "つばきはじめてひらく", "山茶花が咲き始める頃"),
    (11, 12, "地始凍", "ちはじめてこおる", "大地が凍り始める頃"),
    (11, 17, "金盞香", "きんせんかさく", "水仙の花が香り出す頃"),
    (11, 22, "虹蔵不見", "にじかくれてみえず", "虹を見かけなくなる頃"),
    (11, 27, "朔風払葉", "きたかぜこのはをはらう", "北風が木の葉を払う頃"),
    (12, 2, "橘始黄", "たちばなはじめてきばむ", "橘の実が黄ばみ始める頃"),
    (12, 7, "閉塞成冬", "そらさむくふゆとなる", "空が塞がり冬本番になる頃"),
    (12, 12, "熊蟄穴", "くまあなにこもる", "熊が冬眠のため穴に入る頃"),
    (12, 17, "鱖魚群", "さけのうおむらがる", "鮭が群れて川を上る頃"),
    (12, 22, "乃東生", "なつかれくさしょうず", "靫草が芽を出す頃"),
    (12, 27, "麋角解", "さわしかのつのおつる", "大鹿の角が抜け落ちる頃"),
]


def _current_kou(now):
    """七十二候のうち今日に最も近い直近の候（月,日,名,読み,一言）を返す。SEKKI と同じ「直近手前」選び。
    KOU は 1/1(冬至末候)始まりの暦年昇順＝1/1 より前の日は無いので既定 KOU[-1] で足りる。"""
    md = (now.month, now.day)
    cur = KOU[-1]
    for row in KOU:
        if (row[0], row[1]) <= md:
            cur = row
    return cur


def _seasonal_topics(now=None):
    now = now or datetime.datetime.now()
    md = (now.month, now.day)
    cur = SEKKI[-1]                       # 1/6 より前なら前年末の冬至
    for m, d, name, phrase in SEKKI:
        if (m, d) <= md:
            cur = (m, d, name, phrase)
    kou = _current_kou(now)              # 七十二候＝節気より細かい5日刻み（rotate が速い＝種に厚み・ADR-0014）
    return [
        {"text": f"{cur[2]}—{cur[3]}", "tone": "季節", "source": "時節"},
        {"text": f"七十二候は今「{kou[2]}（{kou[3]}）」—{kou[4]}", "tone": "季節", "source": "七十二候"},
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


def _local_topics(source):
    """kind:"local" の源→トピック。inline "topics"（配列）があれば人格タグ付きの世間ネタとして返す
    （行商人→相場・絵描き→色 等・ADR-0014 人格マッチ源の拡充）。無ければ時節（二十四節気＋七十二候＋旬・persona 無し）。
    ネット不要・自作テキスト＝トーンを制御でき whitelist の心配も無い（in-repo config）。"""
    items = source.get("topics")
    if not items:
        return _seasonal_topics()
    tone = source.get("tone", "世間")
    name = source.get("name", "local")
    persona = source.get("persona")          # str/list/None（_persona_matches が役名と突き合わせ）
    out = []
    for t in items:
        t = " ".join(str(t).split())[:TOPIC_MAX_LEN]     # 整形＋長さ上限（rss と同じ安全側）
        if not t:
            continue
        d = {"text": t, "tone": tone, "source": name}
        if persona:
            d["persona"] = persona
        out.append(d)
    return out


def fetch_topics():
    """有効＆ホワイト合格の源からトピック・プールを作る（weather と同型・失敗は握りつぶす）。"""
    pool = []
    for s in _load_topic_sources():
        if not s.get("enabled"):
            continue
        try:
            if s.get("kind") == "local":
                pool += _local_topics(s)         # inline topics（人格タグ）or 時節
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


def _persona_matches(guest, tag):
    """トピックの persona タグが客人の役名にマッチするか。タグ無し(None/空)は全員可（graceful degrade
    ＝季節ネタは誰にでも）。タグ（str または list）のいずれかが**役名の一部に含まれれば**一致。
    実 persona は長い句（例「気まぐれな旅の行商人」）なので "行商" のような短いタグを役名側から拾う
    （逆向き `guest in tag` は長い役名がタグに収まらず効かない・7/1 修正）。"""
    if not tag:
        return True
    tags = [tag] if isinstance(tag, str) else tag
    guest = guest or ""
    return any(str(t) in guest for t in tags)


def pick_topic_text(pool, persona, avoid=()):
    """世間話の“種”を1つ選ぶ（人格マッチ→直近回避→ランダム）。無ければ None。
    確率ゲート(TOPIC_PROB)も履歴も持たない＝純関数（呼び側＝scheduler が握る・ADR-0014 の
    部屋経路復活）。人格マッチは _persona_matches（タグが役名の一部に含まれれば一致・タグ無しは全員）。"""
    if not pool:
        return None
    matched = [t for t in pool if _persona_matches(persona, t.get("persona"))]
    cands = matched or pool
    cands = [t for t in cands if t["text"] not in avoid] or cands   # 全消しなら候補全体へ（None にしない）
    return random.choice(cands)["text"]


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
    return f"[縁側の外]\n{text}\n茶々は縁側からそれを見ている。\n{_suppressor()}" + voice.lang_note()


def ambient_narration(ctx):
    now = ctx["now"]; w = ctx["weather"]
    parts = [f"時刻: {now.strftime('%H:%M')}（{ctx['tod']}）"]
    if w:
        s = f"{PLACE_LABEL}は{ctx['desc']}"
        if w.get("temp") is not None:
            s += f"、{w['temp']}℃"
        if isinstance(w.get("wind"), (int, float)) and w["wind"] >= 20:
            s += "、風が強い"
        parts.append(s)
    return ("[縁側の外]\n" + "\n".join(parts)
            + "\nあなた（茶々）は縁側に座って外を眺めている。\n"
            + "独り言を漏らすなら、ひとこと。何も言いたくなければ「……」だけでよい。"
            + voice.lang_note())


def transition_narration(prev, ctx):
    now = ctx["now"]
    return (f"[縁側の外]\n時刻 {now.strftime('%H:%M')}（{ctx['tod']}）。"
            f"さっきまで「{prev}」やったのが、いま「{ctx['desc']}」に変わってきた。\n"
            f"茶々は空の移ろいを眺めている。\n{_suppressor()}" + voice.lang_note())


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
