#!/usr/bin/env python3
"""engawa_main.py — 配線（composition root）。ADR-0013 の構成を組み立てて起動するだけ。

  AcpAgent.spawn_resident()         住人（茶々）を1接続
  sources.default_sources()         箱庭アーク＋自発来訪を registry に
  Scheduler(resident, sources, WeatherSource, View).run()

View は素の python 実行だと既定 ConsoleView、`ENGAWA_UI=web` で WebView（pywebview の隅窓・P5）。
ただし PyInstaller の exe(frozen) は --noconsole＝コンソールが無いので既定を web にする（_resolve_ui）。
web モードは webview をメインスレッド、Scheduler を別スレッド+loop で回す（webview.start がブロックするため）。
P3/P3.5 を発展させた現行の先端。基準点 engawa_p3_interactive.py は温存。
"""
import asyncio
import os
import sys

import acp
import config
import debuglog
import scheduler as sched
import sources
import views
import voice


def _resident_spawner():
    """住人（茶々）の Agent factory を backend で選ぶ（ADR-0026）。中座の再spawn もこの factory 経由なので
    差し替えは1点。ENGAWA_RESIDENT_BACKEND: 'acp'(既定・Claude Code) / 'openai'(ローカル OpenAI 互換 API＝
    LM Studio 等)。openai は任意経路＝選択時のみ agent_openai を import。"""
    backend = config.get_str("ENGAWA_RESIDENT_BACKEND", "backend", "resident", "acp").lower()
    if backend in ("openai", "api", "lmstudio", "local"):
        import agent_openai
        return agent_openai.OpenAIAgent.spawn_resident
    return acp.AcpAgent.spawn_resident


def _guest_spawner():
    """客人（codex）の Agent factory を backend で選ぶ（ADR-0026）。ENGAWA_GUEST_BACKEND: 'acp' 既定
    （codex-acp・ChatGPT 認証）/ 'openai'（ローカル OpenAI 互換 API＝住人と同じ endpoint を共有）。
    /codex 召喚も自発来訪も同経路。人格は毎ターン prompt 注入なので backend に依らない（ADR-0008）。"""
    backend = config.get_str("ENGAWA_GUEST_BACKEND", "backend", "guest", "acp").lower()
    if backend in ("openai", "api", "lmstudio", "local"):
        import agent_openai
        return agent_openai.OpenAIAgent.spawn_guest
    return acp.AcpAgent.spawn_guest


def _resident_tag(resident):
    """起動行の住人表示。sessionId は ACP 固有なので getattr で任意扱い（OpenAIAgent は持たない）。
    voice が既定以外なら `声=<label>` を添える（ADR-0022 Inc1・既定 ja-osaka では従来表示のまま）。
    ラベル（茶々=/既定/声=）も voice 追従＝外枠 boot_ok_* だけ鍵化して中身が混成日本語になる穴の修正（codex 7/19 [中]）。"""
    sid = getattr(resident, "sessionId", None)
    tag = (f"session={sid[:8]}… / " if sid else "") \
        + f"{voice.resident_name()}={resident.model or voice.loc('ui_model_default')}"
    if voice.current()["id"] != voice.DEFAULT_ID:
        tag += f" / {voice.loc('ui_voice_label')}={voice.label()}"
    return tag


def _build(resident, view):
    # rlcard ゲームを composition root で1度だけ登録（任意依存・未導入でも起動は妨げない・ADR-0017）。
    # ＝Scheduler(core) が adapter モジュールを参照しないための寄せ。register は lambda 登録のみで idempotent。
    try:
        import game_rlcard
        game_rlcard.register_rlcard_games()
    except ImportError:
        pass   # rlcard 未導入＝/game は遊べないが縁側は通常起動
    guest_spawn = _guest_spawner()                             # 客人も backend で acp/openai（ADR-0026）
    return sched.Scheduler(resident,
                           sources.default_sources(spawn_codex=guest_spawn),
                           sources.WeatherSource(), view,
                           spawn_codex=guest_spawn,
                           spawn_resident=_resident_spawner())   # timeout 段階回復の再起動用（backend で acp/openai）


async def run_console():
    print(voice.loc("boot_title"))
    print(voice.loc("boot_starting_console"))
    try:
        resident = await _resident_spawner()()
    except RuntimeError as e:
        print("[x]", e)
        print(voice.loc("boot_auth_hint"))
        return 1
    print(voice.loc("boot_ok_console").format(tag=_resident_tag(resident)))
    await _build(resident, views.ConsoleView()).run()
    print(voice.loc("boot_bye"))
    return 0


async def _serve_web(view):
    view.system(voice.loc("boot_starting"))
    try:
        resident = await _resident_spawner()()
    except RuntimeError as e:
        view.system(f"[x] {e}")
        view.system(voice.loc("boot_auth_hint_web"))
        return
    view.system(voice.loc("boot_ok_web").format(tag=_resident_tag(resident)))
    await _build(resident, view).run()


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


def _ui_config():
    """web 隅窓の設定を config から解決（env ENGAWA_UI_* > engawa.json[ui] > 既定。ADR-0020 流）。
    戻り: (corner, easy_drag, w, h)。run_web とテストが使う＝GUI 起動せず配線を検証可能に。"""
    corner    = config.get_str("ENGAWA_UI_CORNER", "ui", "corner", "br")              # br/bl/tr/tl
    easy_drag = config.get_str("ENGAWA_UI_EASYDRAG", "ui", "easydrag", "0") in ("1", "true", "True")
    w = config.get_int("ENGAWA_UI_W", "ui", "w", 400, lo=240, hi=1400)                # 窓幅（既定 400・少し広め）
    h = config.get_int("ENGAWA_UI_H", "ui", "h", 520, lo=240, hi=1600)               # 窓高（既定 520）
    font = config.get_float("ENGAWA_UI_FONT", "ui", "font", 1.0, lo=0.8, hi=2.2)     # 本文/入力の文字倍率（目が悪い人向け・既定1.0・窓全体でなく本文だけ拡大）
    return corner, easy_drag, w, h, font


def _web_window_kwargs(w, h, easy_drag):
    """pywebview.create_window へ渡す窓オプション（html/js_api 以外）。
    resizable=True ＝ frameless でもドラッグで広げられる（『窓が狭い』対策）。min_size で潰れ防止。"""
    return dict(width=w, height=h, frameless=True, easy_drag=easy_drag,
                on_top=True, resizable=True, min_size=(240, 240))


def run_web():
    import threading
    import webview                                    # 遅延 import（console/テストで不要）
    corner, easy_drag, web_w, web_h, font = _ui_config()
    loop = asyncio.new_event_loop()
    view = views.WebView()
    view.set_layout(corner, web_w, web_h, font)       # 観戦窓(第2窓)を本窓の隣へ＋同じ文字倍率で
    sx, sy = _screen_size()                           # 生成時から隅の座標で作る＝中央に一瞬出て隅へ飛ぶ「フラッシュ」を消す
    px, py = views.corner_xy(sx, sy, web_w, web_h, corner)
    window = webview.create_window("茶々の縁側", html=views.build_web_html(font),
                                   js_api=view.api, x=px, y=py,
                                   **_web_window_kwargs(web_w, web_h, easy_drag))
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
            window.move(*views.corner_xy(sw, sh, web_w, web_h, corner))
        except Exception:
            pass

    webview.start(place)                             # 窓を閉じるまでブロック（メインスレッド）
    view.signal_close()                              # 閉じた → inputs() が None → scheduler 終了
    t.join(timeout=8)
    return 0


def _debug_config():
    """デバッグログ設定を config から解決（env ENGAWA_DEBUG/ENGAWA_LOG_FILE > engawa.json[debug] > 既定）。
    戻り: (debug: bool, path: str)。log ファイルは既定でリポジトリ直下 engawa.log（gitignore）。純関数＝テスト可能。"""
    debug = config.get_str("ENGAWA_DEBUG", "debug", "enabled", "0") in ("1", "true", "True", "on")
    default_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engawa.log")
    path = config.get_str("ENGAWA_LOG_FILE", "debug", "file", default_path) or default_path  # 空文字は既定へ
    return debug, path


def _resolve_ui(environ, frozen):
    """UI モードを決める（純関数＝テスト可能）。ENGAWA_UI 明示が最優先。未指定なら
    frozen(PyInstaller の exe)=web / 素の python 実行=console を既定に。
    理由: exe は --noconsole でビルドする常駐GUI＝コンソール窓が無く、console UI の stdin/stdout が
    死んでいる（run_console が print/sys.stdin.readline で即死＝『窓が出ない/一瞬で消える』）。
    ので exe をダブルクリックした時は web 窓を出すのが正しい既定（env で明示上書きは可能）。"""
    ui = environ.get("ENGAWA_UI")
    if ui:
        return ui.lower()
    return "web" if frozen else "console"


def main():
    on = debuglog.setup(*_debug_config())          # デバッグログ（既定オフ＝no-op・ENGAWA_DEBUG=1 で engawa.log へ）
    ui = _resolve_ui(os.environ, getattr(sys, "frozen", False))
    if on:
        debuglog.get("main").debug("起動 ui=%s (frozen=%s)", ui, getattr(sys, "frozen", False))
    if ui == "web":
        return run_web()
    return asyncio.run(run_console())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
