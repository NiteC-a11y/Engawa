#!/usr/bin/env python3
"""debuglog.py — デバッグ用ログ（stdlib logging の薄いラッパ）。

`ENGAWA_DEBUG=1`（config・既定オフ）で `engawa.log` に主要イベントを吐く（縁側の窓/console 本文は
汚さない＝別ファイル）。off の時は NullHandler＝`log.debug(...)` は実質 no-op（本番の負荷ゼロ）。
自前ロガーは作らず stdlib logging を使う（原則: 車輪の再発明をしない）。

使い方: 各モジュールで `import debuglog; log = debuglog.get("scheduler")` → `log.debug("種を空気へ: %s", t)`。
`setup(debug, path)` は composition root（engawa_main）が1度だけ呼ぶ。テストは `assertLogs("engawa.scheduler")`
で個々の debug 出力を検証できる（setup 不要＝assertLogs が一時ハンドラを付ける）。
"""
import logging

_ROOT = "engawa"     # 全モジュールの親ロガー。ハンドラはここに1つだけ付ける（第三者ログを巻き込まない）


def get(name):
    """モジュール用の子ロガー（"engawa.<name>"）。親 "engawa" のハンドラへ propagate する。"""
    return logging.getLogger(f"{_ROOT}.{name}")


def setup(debug, path):
    """デバッグログを設定して有効なら True。debug=False は NullHandler＝何も書かない。
    - 親ロガー "engawa" にだけ FileHandler を付け、真のルートへは propagate させない（他ログと混ざらない）。
    - 何度呼んでも既存ハンドラを畳んでから付け直す（多重出力を防ぐ）。"""
    root = logging.getLogger(_ROOT)
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    root.propagate = False
    if not debug:
        root.addHandler(logging.NullHandler())
        root.setLevel(logging.WARNING)
        return False
    handler = logging.FileHandler(path, encoding="utf-8")
    # 日付＋ミリ秒（定量分析用＝会話タイミングを msec 精度で追える）。%(msecs)03d は asctime の秒に足す。
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    return True
