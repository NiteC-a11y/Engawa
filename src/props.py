#!/usr/bin/env python3
"""props.py — 縁側の小物（props）のカタログ（ADR-0032 v2・エンティティ＋コンポーネントの台帳）。

「小物は増える」前提の設計（7/18 ユーザー要請）＝ゲーム開発の定石を縮小適用:
- **Entity + Component**: 台帳（`assets/props.json`）の1エントリ＝エンティティ。性質は独立したコンポーネント塊
  （`place`=置き場 / `when`=表示条件 / `effect`=演出 / `narrate`=茶々の環境行に載る一文）。消費者（system）は
  自分のコンポーネントだけ読む: View は place/effect、判定は when、prompts は narrate。**新しい小物はコード0行・台帳に1行**。
- **条件は Registry（Specification の縮小版）**: `when` の各フィールドに述語を1対1登録（`_WHEN`）・**全フィールド AND**・
  未知フィールドは無視（前方互換）。新しい条件（hours/weather…）は述語1個の登録＝分岐を増やさない。
- **Facade（単一正本）**: views（描画）と prompts（茶々が知る）が同じカタログを読む＝食い違いを構造で防ぐ。
  台帳は **renderer 非依存の契約**＝将来の canvas シーン化に台帳ごと引っ越す（Backlog 庭側ビュー最終形）。
- 「いま出ている集合」は純関数 `active(now)`（状態を持たない）＝poll が毎回配って表示が実日付に追従する。
  欠損/壊れは空に落として起動を止めない（皮の流儀）。キャッシュは `_CACHE=None` でリセット（テスト用）。
"""
import datetime
import json
import math
import os
import sys

import config

_CACHE = None      # (path, catalog) — 台帳は1プロセス1回読み（パスが変われば読み直し＝テスト向け）


def _config_path():
    """台帳のパス: env ENGAWA_PROPS_CONFIG > engawa.json[assets].props_config > assets/props.json
    （sprite/scene と同列の皮・ADR-0010。frozen 時は sys._MEIPASS 基準＝spec datas 同梱）。"""
    p = config.get_str("ENGAWA_PROPS_CONFIG", "assets", "props_config", "")
    if p:
        return p
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "props.json")


def base_dir():
    """台帳内の image 相対パスの基準（台帳と同じディレクトリ）。views の dataURI 化が使う。"""
    return os.path.dirname(_config_path())


def _num(v, default, lo, hi):
    """台帳の数値を有限数に矯正して [lo, hi] へクランプ（型不正/NaN/∞ は default）。
    壊れた台帳1件で常駐 GUI を劣化させない（例: period_ms 負値→setInterval ほぼ0ms の DOM 連打・codex レビュー 7/18）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return min(max(f, lo), hi)


def _normalize(p, base):
    """台帳エントリを component 形に正規化する**正本境界**（codex レビュー 7/18 反映）:
    - image は空でない str のみ（数値/list 等は entity ごと捨てる＝views の os.path が落ちない）
    - **画像ファイルが実在しない entity も捨てる**＝「画面に出せない物は世界にも無い」＝views（描画）と
      prompts（narrate）が同じ集合を見る（食い違いの構造防止・ADR-0032 の Facade を型でも守る）
    - 数値は _num で有限数＋範囲へ（0 は 0 のまま有効＝JS 側も ?? で受ける）"""
    if not isinstance(p, dict):
        return None
    img = p.get("image")
    if not isinstance(img, str) or not img.strip():
        return None
    img_path = img if os.path.isabs(img) else os.path.join(base, img)
    if not os.path.isfile(img_path):
        return None
    pid = p.get("id")
    place_in = p.get("place") if isinstance(p.get("place"), dict) else {}
    eff_in = p.get("effect") if isinstance(p.get("effect"), dict) else None
    effect = None
    if eff_in and isinstance(eff_in.get("kind"), str) and eff_in["kind"]:
        effect = {"kind": eff_in["kind"],
                  "x_pct": _num(eff_in.get("x_pct"), 50, 0, 100),
                  "y_pct": _num(eff_in.get("y_pct"), 0, 0, 100),
                  "color": eff_in.get("color") if isinstance(eff_in.get("color"), str) else "#c9c9c9",
                  "period_ms": _num(eff_in.get("period_ms"), 2600, 250, 60000)}
    return {"id": pid if (isinstance(pid, str) and pid.strip()) else img,
            "image": img,
            "image_path": img_path,
            "place": {"left_pct": _num(place_in.get("left_pct"), 10, 0, 100),
                      "bottom_pct": _num(place_in.get("bottom_pct"), 6, 0, 100),
                      "display_px": _num(place_in.get("display_px"), 40, 8, 400)},
            "when": p.get("when") if isinstance(p.get("when"), dict) else {},
            "effect": effect,
            "narrate": p.get("narrate") if isinstance(p.get("narrate"), str) else ""}


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("props") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        base = os.path.dirname(path)
        return [q for q in (_normalize(p, base) for p in items) if q]
    except Exception:
        return []          # 無い/壊れ → 小物なし（起動を止めない）


def catalog():
    global _CACHE
    path = _config_path()
    if _CACHE is None or _CACHE[0] != path:
        _CACHE = (path, _load(path))
    return _CACHE[1]


# ── 条件レジストリ（when の field → 述語・全フィールド AND・未知は無視＝前方互換）─────────
def _when_months(v, now):
    """months=出す月のリスト。空/壊れは常時（表示側に倒す）。"""
    if not isinstance(v, list) or not v:
        return True
    try:
        return now.month in {int(m) for m in v}
    except (TypeError, ValueError):
        return True

_WHEN = {"months": _when_months}


def is_active(entity, now):
    when = entity.get("when") or {}
    return all(_WHEN[k](v, now) for k, v in when.items() if k in _WHEN)


def active(now=None):
    """今出す小物（純関数・now 注入でテスト可能）。"""
    now = now or datetime.datetime.now()
    return [p for p in catalog() if is_active(p, now)]


def active_ids(now=None):
    """poll が毎回配る「いま出ている集合」（JS は show/hide するだけ・資産は起動時注入＝Flyweight）。"""
    return [p["id"] for p in active(now)]


def narration_line(now=None):
    """茶々の環境行に載せる一文（active な narrate を連結・無ければ空）。天気と同じ「持たせるだけ」＝
    言い立てさせない（原則#2）。prompts が使う。"""
    parts = [p["narrate"] for p in active(now) if p["narrate"]]
    return "".join(s if s.endswith("。") else s + "。" for s in parts)
