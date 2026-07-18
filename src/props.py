#!/usr/bin/env python3
"""props.py — 縁側の小物（props）の台帳解決とゲート判定（ADR-0032・葉モジュール）。

台帳（`assets/props.json`）が「何を・どこに・いつ・どんな演出で」を持ち、コードは描き方だけを知る
（ADR-0019 の分業）。台帳は **renderer 非依存の契約**＝今は WebView の DOM 重ねだが、将来の canvas
シーン化（庭側ビュー構想の最終形・7/18 ユーザー着想）でも台帳ごと引っ越せる。

- 表示条件は**実日付の月ゲート**（months）＝実天気(ADR-0012)・実時刻の昼夜(ADR-0028)に続く
  「実日付＝季節」の環境反応。LLM 非経由・トークン0。
- 演出（effect）は**汎用語彙のみ**（今は rise=粒が立ちのぼる の1語）。小物専用の演出コードは書かない＝
  新しい小物はコード0行・台帳に1行（増殖の蓋・ADR-0032）。
- 判定は純関数（now 注入）＝テスト可能。欠損/壊れは空に落として起動を止めない（皮の流儀）。
"""
import json


def load_config(path):
    """props.json を読んで台帳リストを返す。無い/壊れ/型不一致は []（起動を止めない）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("props") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [p for p in items if isinstance(p, dict) and p.get("image")]
    except Exception:
        return []


def active(items, now):
    """今日出す小物だけに絞る（months=出す月のリスト・省略や壊れは常時＝表示側に倒す）。純関数。"""
    out = []
    for p in items:
        months = p.get("months")
        if isinstance(months, list) and months:
            try:
                if now.month not in {int(m) for m in months}:
                    continue
            except (TypeError, ValueError):
                pass                               # 壊れた months は常時扱い
        out.append(p)
    return out
