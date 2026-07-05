#!/usr/bin/env python3
"""daynight.py — 時刻→背景の昼夜レイヤ（tint 乗算＋glow 加算月光）を返す純関数（ADR-0028）。

世界の定番（Godot CanvasModulate 等）に倣い、画像差し替え(B案)でなく **1枚の色膜を時刻で
lerp** する方式(A案)。#scene に膜を2枚重ねるだけで、絵1枚から朝昼夕夜＋月明かりを出す。
- tint: `mix-blend-mode:multiply` 用の環境色。昼=白(無変化)/夕=桃(暖色)/夜=青灰(寒色)。
  ベタ塗りでなく乗算で敷く＝ハイライトを残して自然に暗くなる。
- glow: `mix-blend-mode:screen` 用の月明かりの強さ 0..1（昼0・夕薄・夜1.0）。空の隅・寒色。
- lamp: `mix-blend-mode:screen` 用の室内灯（障子ごしに漏れる暖色）の強さ 0..1（昼0・夕点灯・夜1.0）。
  暗くする tint の上に足す＝夜に「部屋から明かりが漏れる」。glow が寒色の月なら lamp は暖色の家の灯り。

実数値の起点は実時間 day-night の癒しゲー Usagi Shima（夕 rgb(252,217,191)/夜 rgb(99,130,163)）＋
色理論（暖色光→寒色影）。純関数＝unittest 可（原則6）。View が datetime.now() を渡す。
"""

# (分, (r,g,b) 乗算色, 月明かり0..1, 室内灯0..1)。分は 0..1440（0:00 と 24:00 は同色＝境目で連続）。
# 昼=白(255,255,255)＝乗算しても無変化。夕=桃の暖色・夜=青灰の寒色。glow(月)/lamp(家の灯り) は昼0→夜1。
# lamp は「日が暮れたら灯りをつける」＝夕に点き始め（glow=月より少し早く立ち上がる）。
_KEYS = [
    #  分     tint(乗算色)       glow(月) lamp(室内灯)
    (0,    (99, 130, 163), 1.00, 1.00),   # 0:00 深夜（青灰・月満・家も点灯）
    (300,  (120, 132, 168), 0.55, 0.70),  # 5:00 夜明け前（薄青・まだ灯り）
    (420,  (232, 228, 225), 0.08, 0.00),  # 7:00 朝（ほぼ素・消灯）
    (720,  (255, 255, 255), 0.00, 0.00),  # 12:00 正午（白＝無変化・消灯）
    (960,  (255, 246, 232), 0.00, 0.00),  # 16:00 午後（わずかに暖色・消灯）
    (1080, (252, 217, 191), 0.20, 0.35),  # 18:00 夕焼け（桃の暖色・灯り点き始め）
    (1170, (196, 182, 186), 0.55, 0.75),  # 19:30 薄暮（暖→寒へ渡る・灯り強まる）
    (1260, (99, 130, 163), 1.00, 1.00),   # 21:00 夜（青灰・月満・全灯）
    (1440, (99, 130, 163), 1.00, 1.00),   # 24:00 = 0:00（境目で連続）
]


def _lerp(a, b, t):
    return a + (b - a) * t


def _minute_of_day(now):
    """0..1440 未満の「その日の分」（秒を小数で含む＝連続的に色がにじむ）。"""
    return now.hour * 60 + now.minute + now.second / 60.0


def _layers_from_minute(m):
    """分(0..1440)→ {"tint": "rgb(r,g,b)", "glow": 0..1, "lamp": 0..1}。キーフレーム間を線形補間。"""
    if m <= _KEYS[0][0]:
        rgb, glow, lamp = _KEYS[0][1], _KEYS[0][2], _KEYS[0][3]
    else:
        rgb, glow, lamp = _KEYS[-1][1], _KEYS[-1][2], _KEYS[-1][3]
        for i in range(1, len(_KEYS)):
            m0, c0, g0, l0 = _KEYS[i - 1]
            m1, c1, g1, l1 = _KEYS[i]
            if m <= m1:
                t = 0.0 if m1 == m0 else (m - m0) / (m1 - m0)
                rgb = tuple(int(round(_lerp(c0[k], c1[k], t))) for k in range(3))
                glow = _lerp(g0, g1, t)
                lamp = _lerp(l0, l1, t)
                break
    glow = max(0.0, min(1.0, glow))
    lamp = max(0.0, min(1.0, lamp))
    return {"tint": "rgb(%d,%d,%d)" % rgb, "glow": round(glow, 3), "lamp": round(lamp, 3)}


def layers(now):
    """時刻(datetime)→ {"tint": "rgb(r,g,b)", "glow": 0..1} を返す純関数。

    tint は multiply 用の環境色（昼=白/夕=桃/夜=青灰）、glow は screen 用の月明かりの強さ。
    View は poll でこれを毎回 JS へ渡し、膜2枚に適用する。
    """
    return _layers_from_minute(_minute_of_day(now))


def layers_for_minute(m):
    """指定分(0..1440)の {tint,glow}。/daynight プレビューが仮想時刻で使う（純関数）。"""
    return _layers_from_minute(m % 1440)


# ── /daynight プレビュー（アプリ内で夕→夜の移ろいを待たず確認・ADR-0028／デバッグ再生は /arc と同筋） ──
# 既定の早送り: 16:00→22:00 を 40 秒で（夕焼け→薄暮→夜＋月明かりの立ち上がりが通しで見える）。
DEMO_FROM, DEMO_TO, DEMO_SECS = 960, 1320, 40


def format_minute(m):
    """分(0..1440)→'HH:MM'（純関数・/daynight の表示用）。"""
    m = int(round(m)) % 1440
    return "%02d:%02d" % (m // 60, m % 60)


def parse_time(s):
    """'18' / '18:30' → 分(0..1439)。不正は None（純関数）。"""
    s = (s or "").strip()
    if not s:
        return None
    hh, _, mm = s.partition(":") if ":" in s else (s, "", "0")
    if not (hh.isdigit() and mm.isdigit()):
        return None
    h, mi = int(hh), int(mm)
    return h * 60 + mi if (0 <= h <= 23 and 0 <= mi <= 59) else None


def parse_override(arg):
    """/daynight 引数(str) → spec（純関数・副作用なし）。機能 on/off（永続）と プレビュー override を1本で解釈。

    - ''             → {"mode": "show"}    … 今の状態を表示
    - on/enable      → {"mode": "enable"}  … 機能を有効化（呼び側が engawa.json に保存）
    - off/disable    → {"mode": "disable"} … 機能を無効化（同上・保存）
    - auto/now/real  → {"mode": "auto"}    … プレビューを解除して実時間へ戻す（保存しない）
    - 'demo [from] [to] [secs]' → {"mode":"demo","from":分,"to":分,"secs":秒}  … 夕→夜を早送り
      （時刻は HH:MM・省略で既定 16:00→22:00／秒は末尾のただの数値・省略で既定 40）
    - 'HH:MM'        → {"mode": "pin", "minute": 分}   … その時刻に固定（プレビュー）
    - それ以外        → {"mode": "bad"}
    """
    s = (arg or "").strip().lower()
    if s == "":
        return {"mode": "show"}
    if s in ("on", "enable", "有効"):
        return {"mode": "enable"}
    if s in ("off", "disable", "無効"):
        return {"mode": "disable"}
    if s in ("auto", "now", "real", "stop", "解除"):
        return {"mode": "auto"}
    parts = s.split()
    if parts[0] in ("demo", "sweep", "移ろい"):
        rest = parts[1:]
        times = [t for t in (parse_time(p) for p in rest if ":" in p) if t is not None]
        bare = [p for p in rest if ":" not in p and p.isdigit()]
        f, t = (times + [DEMO_FROM, DEMO_TO])[:2] if len(times) >= 2 else (DEMO_FROM, DEMO_TO)
        secs = int(bare[0]) if bare and int(bare[0]) > 0 else DEMO_SECS
        return {"mode": "demo", "from": f, "to": t, "secs": secs}
    m = parse_time(s)
    return {"mode": "pin", "minute": m} if m is not None else {"mode": "bad"}


def override_minute(spec, elapsed_sec):
    """demo spec の経過秒→仮想分。再生が終わったら None（実時間へ戻す合図）。純関数。"""
    if not spec or spec.get("mode") != "demo":
        return None
    secs = spec.get("secs", DEMO_SECS)
    if secs <= 0 or elapsed_sec >= secs:
        return None
    f, t = spec.get("from", DEMO_FROM), spec.get("to", DEMO_TO)
    return f + (t - f) * (elapsed_sec / secs)


def effective_layers(spec, now, elapsed_sec):
    """override を反映した (day, expired) を返す純関数。

    day＝{tint,glow}。expired＝demo が終わって View が実時間へ戻すべきか。
    spec が None/off/show なら実時間（layers(now)）＝素通し。View は clock を持たず
    「real な now」と「demo 開始からの経過秒」だけ渡せばよい＝ここが唯一の判断点。
    """
    if spec:
        mode = spec.get("mode")
        if mode == "pin":
            return layers_for_minute(spec["minute"]), False
        if mode == "demo":
            m = override_minute(spec, elapsed_sec)
            if m is None:
                return layers(now), True         # 再生終了→実時間色＋戻す合図
            return layers_for_minute(m), False
    return layers(now), False
