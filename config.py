#!/usr/bin/env python3
"""config.py — アプリ挙動の設定解決（env > engawa.json > コード既定）。

- つまみの優先順位: `ENGAWA_*` 環境変数があれば最優先 → `engawa.json` の値 → コード既定。
  既存の env 運用を壊さず、engawa.json で「永続的なデフォルト」を置けるようにするための薄い層。
- ファイルは `engawa.json`（リポジトリ直下・env `ENGAWA_CONFIG` でパス差し替え。topic/sprite と同じ流儀）。
- **API キーは入れない**（adr/0002：キーは子 env から除去する思想と分離。ここは挙動つまみ専用）。
- JSON 欠損/壊れ/型不一致は静かにコード既定へフォールバック（起動を止めない）。
"""
import json
import os

_CFG = None   # engawa.json を一度だけ読んでキャッシュ（テストは _CFG=None でリセット）


def _path():
    return os.environ.get("ENGAWA_CONFIG") or \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "engawa.json")


def _load():
    global _CFG
    if _CFG is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                data = json.load(f)
            _CFG = data if isinstance(data, dict) else {}
        except Exception:
            _CFG = {}        # 無い/壊れ → 全部コード既定へ
    return _CFG


def _from_json(section, key):
    sec = _load().get(section)
    if isinstance(sec, dict) and key in sec:
        return sec[key]
    return None


def _clamp(v, lo, hi):
    """範囲外なら端へ寄せる（壊れた設定値で極端な挙動＝常時来訪/毎tick取得 等にならないように）。"""
    if lo is not None and v < lo:
        return lo
    if hi is not None and v > hi:
        return hi
    return v


def get(env, section, key, default, cast, lo=None, hi=None):
    """env(ENGAWA_*) → engawa.json[section][key] → default の順で解決して cast。
    lo/hi 指定時は範囲にクランプ（確率は 0..1、間隔は正数 等）。無効値は既定へ。"""
    if env in os.environ:
        try:
            return _clamp(cast(os.environ[env]), lo, hi)
        except (TypeError, ValueError):
            pass                          # 壊れた env は無視して次へ
    v = _from_json(section, key)
    if v is not None:
        try:
            return _clamp(cast(v), lo, hi)
        except (TypeError, ValueError):
            pass                          # 壊れた json 値は無視して既定へ
    return _clamp(default, lo, hi)


def get_int(env, section, key, default, lo=None, hi=None):
    return get(env, section, key, default, int, lo, hi)


def get_float(env, section, key, default, lo=None, hi=None):
    return get(env, section, key, default, float, lo, hi)
