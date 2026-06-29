#!/usr/bin/env python3
"""engawa_main.py — 配線（composition root）。ADR-0013 の構成を組み立てて起動するだけ。

  AcpAgent.spawn_resident()         住人（茶々）を1接続
  sources.default_sources()         箱庭アーク＋自発来訪を registry に
  Scheduler(resident, sources, WeatherSource, View).run()

View は既定で ConsoleView、`ENGAWA_UI=web` で WebView（pywebview の隅窓・P5）。
web モードは webview をメインスレッド、Scheduler を別スレッド+loop で回す（webview.start がブロックするため）。
P3/P3.5 を発展させた現行の先端。基準点 engawa_p3_interactive.py は温存。
"""
import asyncio
import os
import sys

import acp
import scheduler as sched
import sources
import views


def _build(resident, view):
    return sched.Scheduler(resident,
                           sources.default_sources(spawn_codex=acp.AcpAgent.spawn_guest),
                           sources.WeatherSource(), view,
                           spawn_codex=acp.AcpAgent.spawn_guest,
                           spawn_resident=acp.AcpAgent.spawn_resident)   # timeout 段階回復の再起動用


async def run_console():
    print("[*] 茶々の縁側を開きます（箱庭アーク / event-source 構成）")
    print("[*] 起動中…（初回は npx ダウンロード）  話しかけてみて。/help、/arc で試写\n")
    try:
        resident = await acp.AcpAgent.spawn_resident()
    except RuntimeError as e:
        print("[x]", e)
        print("    認証エラーなら、先に `claude` で本命サブスクにログインのこと。")
        return 1
    print(f"[ok] 縁側が開きました（session={resident.sessionId[:8]}… / 茶々={resident.model or '既定'}）\n")
    await _build(resident, views.ConsoleView()).run()
    print("[*] 縁側を閉じます。茶々はまた留守番。")
    return 0


async def _serve_web(view):
    view.system("[*] 起動中…（初回は npx ダウンロード）")
    try:
        resident = await acp.AcpAgent.spawn_resident()
    except RuntimeError as e:
        view.system(f"[x] {e}")
        view.system("認証エラーなら、先に `claude` で本命サブスクにログインのこと。")
        return
    view.system(f"[ok] 縁側が開きました（{resident.sessionId[:8]}… / 茶々={resident.model or '既定'}）話しかけてみて")
    await _build(resident, view).run()


WEB_W, WEB_H = 360, 480


def _screen_size():
    """画面サイズ（隅配置用）。webview.screens → ctypes(Windows) → 既定 の順でフォールバック。"""
    try:
        import webview
        s = webview.screens[0]
        if s.width and s.height:
            return int(s.width), int(s.height)
    except Exception:
        pass
    try:
        import ctypes
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def run_web():
    import threading
    import webview                                    # 遅延 import（console/テストで不要）
    corner = os.environ.get("ENGAWA_UI_CORNER", "br")          # br/bl/tr/tl
    easy_drag = os.environ.get("ENGAWA_UI_EASYDRAG", "0") in ("1", "true", "True")
    loop = asyncio.new_event_loop()
    view = views.WebView()
    view.set_layout(corner, WEB_W, WEB_H)             # 観戦窓(第2窓)を本窓の隣へ置くため
    window = webview.create_window("茶々の縁側", html=views.build_web_html(), js_api=view.api,
                                   width=WEB_W, height=WEB_H, frameless=True,
                                   easy_drag=easy_drag, on_top=True, resizable=False)
    view.bind_window(window)                          # ×ボタン / scheduler 終了で閉じるため

    def bg():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve_web(view))
        finally:
            loop.close()
            try:
                window.destroy()                     # /quit 等で scheduler 終了 → 窓も閉じる
            except Exception:
                pass

    t = threading.Thread(target=bg, daemon=True)
    t.start()

    def place():                                     # GUI 起動後に画面隅へ
        try:
            sw, sh = _screen_size()
            window.move(*views.corner_xy(sw, sh, WEB_W, WEB_H, corner))
        except Exception:
            pass

    webview.start(place)                             # 窓を閉じるまでブロック（メインスレッド）
    view.signal_close()                              # 閉じた → inputs() が None → scheduler 終了
    t.join(timeout=8)
    return 0


def main():
    if os.environ.get("ENGAWA_UI") == "web":
        return run_web()
    return asyncio.run(run_console())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
