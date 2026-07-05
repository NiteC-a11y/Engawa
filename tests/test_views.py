"""views の純関数: collapse_ws（客人の声の1行化）と corner_xy（隅配置の座標）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
import daynight
import views


class TestCollapseWs(unittest.TestCase):
    def test_runs_collapse_to_single_space(self):
        self.assertEqual(views.collapse_ws("a  b\t c"), "a b c")

    def test_newlines_collapsed_and_trimmed(self):
        self.assertEqual(views.collapse_ws("  hello\n\nworld  "), "hello world")

    def test_fullwidth_space_preserved(self):
        # 全角空白(　)は ASCII 空白ではないので畳まず保つ
        self.assertEqual(views.collapse_ws("あ　い"), "あ　い")

    def test_all_whitespace_becomes_empty(self):
        self.assertEqual(views.collapse_ws("   \n\t "), "")

    def test_no_leading_space_emitted(self):
        self.assertEqual(views.collapse_ws("\n\n  x"), "x")


class TestCornerXy(unittest.TestCase):
    def test_bottom_right(self):
        self.assertEqual(
            views.corner_xy(1000, 800, 360, 480, "br", margin=16, taskbar=40),
            (1000 - 360 - 16, 800 - 480 - 16 - 40))

    def test_top_left(self):
        self.assertEqual(
            views.corner_xy(1000, 800, 360, 480, "tl", margin=16, taskbar=40),
            (16, 16))

    def test_bottom_left_and_top_right(self):
        self.assertEqual(views.corner_xy(1000, 800, 360, 480, "bl", 16, 40)[0], 16)
        self.assertEqual(views.corner_xy(1000, 800, 360, 480, "tr", 16, 40)[1], 16)

    def test_clamped_nonnegative_when_window_bigger_than_screen(self):
        x, y = views.corner_xy(100, 100, 360, 480, "br")
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)


class _FakeWindow:
    def __init__(self):
        self.destroyed = False
        self.resized = None

    def destroy(self):
        self.destroyed = True

    def resize(self, w, h):
        self.resized = (w, h)


class TestWebViewCloseClosesGameWindow(unittest.TestCase):
    """本窓の×（close）で観戦窓(第2窓)も畳む。残ると webview.start が返らず teardown に入れない。"""
    def test_close_destroys_both_windows(self):
        v = views.WebView()
        main, game = _FakeWindow(), _FakeWindow()
        v._window, v._game_window = main, game
        v.close()
        self.assertTrue(main.destroyed)        # 本窓
        self.assertTrue(game.destroyed)        # 観戦窓も
        self.assertIsNone(v._game_window)      # game_close が参照を外す

    def test_close_without_game_window_ok(self):
        v = views.WebView()
        main = _FakeWindow()
        v._window = main
        v.close()                              # 観戦窓なし → 例外なく本窓だけ
        self.assertTrue(main.destroyed)


class TestGameWindowAbort(unittest.TestCase):
    """観戦窓の×は窓を閉じるだけでなく、scheduler に『対局を畳んで縁側へ』を入力チャネルで伝える
    （View だけ閉じると Scheduler.game が残り「ゲームモードのまま復帰不能」になるのを防ぐ）。"""
    def test_game_api_close_aborts_and_signals(self):
        v = views.WebView()
        gw = _FakeWindow()
        v._game_window = gw
        views._GameApi(v).close()              # 観戦窓の×ボタン相当
        self.assertTrue(gw.destroyed)          # 窓は閉じる
        self.assertIsNone(v._game_window)
        self.assertEqual(v._inq.get_nowait(), views.GAME_CLOSE_REQUEST)  # scheduler への合図を積む


class TestBuildWebHtml(unittest.TestCase):
    """build_web_html: sprite 注入後にテンプレ印を残さない＋リサイズグリップを含む。"""
    def test_no_sprite_marker_leak(self):
        self.assertNotIn("/*SPRITE*/", views.build_web_html())

    def test_has_resize_grip(self):
        # frameless は掴む縁が無いので明示グリップ＋api.resize 配線が居ること（JS 挙動自体は GUI 目視）
        html = views.build_web_html()
        self.assertIn('id="grip"', html)
        self.assertIn("pywebview.api.resize", html)

    def test_font_scale_injected(self):
        # 文字倍率は --fz 変数で本文/入力に効かせる（窓全体 zoom は使わない＝入力欄を切らない）
        self.assertIn("--fz:1.5", views.build_web_html(1.5))

    def test_default_font_is_unity(self):
        self.assertIn("--fz:1.0", views.build_web_html())
        self.assertNotIn("/*FONT*/", views.build_web_html())   # プレースホルダは消費済み

    def test_input_bar_still_present(self):
        # 文字拡大で入力欄が消えないこと（zoom 事故の回帰ガード・大きめ倍率でもマークアップは在る）
        html = views.build_web_html(2.0)
        self.assertIn('id="in"', html)
        self.assertIn('id="bar"', html)


class TestWebViewResize(unittest.TestCase):
    """右下グリップ→窓リサイズの Python 側配線（JS ドラッグは GUI 目視・ここは api→window.resize＋クランプ）。"""
    def test_resize_calls_window(self):
        v = views.WebView()
        fw = _FakeWindow()
        v.bind_window(fw)
        ok = views._WebApi(v).resize(500, 400)
        self.assertTrue(ok)
        self.assertEqual(fw.resized, (500, 400))     # api→window.resize へ伝わる

    def test_resize_clamps_min(self):
        v = views.WebView()
        fw = _FakeWindow()
        v.bind_window(fw)
        views._WebApi(v).resize(10, 10)              # 極小 → 240 にクランプ（潰れ防止）
        self.assertEqual(fw.resized, (240, 240))

    def test_resize_noop_without_window(self):
        v = views.WebView()                          # bind 前 → 例外なく何もしない
        views._WebApi(v).resize(400, 400)            # 例外を投げないこと


class TestGameWindowFont(unittest.TestCase):
    """観戦窓(GAME_HTML)も本窓と同じ文字倍率で拡大（盤だけ拡大されない不整合の回帰ガード）。"""
    def test_game_html_font_injected(self):
        self.assertIn("--fz:1.6", views.build_game_html(1.6))

    def test_game_html_default_unity_no_marker(self):
        h = views.build_game_html()
        self.assertIn("--fz:1.0", h)
        self.assertNotIn("/*FONT*/", h)             # プレースホルダ消費済み

    def test_set_layout_stores_font_for_game_window(self):
        v = views.WebView()
        v.set_layout("br", 400, 520, 1.4)
        self.assertEqual(v._font, 1.4)              # game_open はこの倍率で観戦窓を建てる

    def test_set_layout_font_defaults_unity(self):
        v = views.WebView()
        v.set_layout("br", 400, 520)               # font 省略 → 等倍
        self.assertEqual(v._font, 1.0)


class TestFontLiveApply(unittest.TestCase):
    """/font のライブ適用（poll 方式）: set_font→current_font と poll/game_poll が font を運ぶ。
    JS の --fz 差し替えは GUI 目視（ここは Python 側の配線＝poll に font が載るか）。"""
    def test_set_and_get_font(self):
        v = views.WebView()
        self.assertTrue(v.set_font(1.5))
        self.assertEqual(v.current_font(), 1.5)

    def test_poll_carries_font(self):
        v = views.WebView()
        v.set_font(1.7)
        self.assertEqual(v.poll(0)["font"], 1.7)          # 本窓 poll に載る

    def test_game_poll_carries_font_even_when_rev_unchanged(self):
        v = views.WebView()
        v.set_font(1.3)
        self.assertEqual(v.game_poll(0)["font"], 1.3)     # rev 据置でも font はライブ反映

    def test_console_view_font_is_noop(self):
        c = views.ConsoleView()
        self.assertIsNone(c.current_font())               # 設定対象外（端末フォント依存）
        self.assertFalse(c.set_font(1.4))                 # no-op

    def test_web_html_has_apply_font(self):
        self.assertIn("applyFont", views.build_web_html())   # 本窓 JS が font を適用する口を持つ

    def test_game_html_has_apply_font(self):
        self.assertIn("applyFont", views.build_game_html())  # 観戦窓 JS も同様


class TestDayNightTint(unittest.TestCase):
    """背景の昼夜 tint（ADR-0028）: poll が {tint,glow} を運ぶ／ENGAWA_DAYNIGHT=0 で無効／
    HTML に膜2枚＋適用口。色の中身は daynight の純関数テスト側・見た目は目視。"""
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ.pop("ENGAWA_DAYNIGHT", None)
        config._CFG = {}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        config._CFG = None

    def test_poll_carries_day_when_enabled(self):
        v = views.WebView()                              # 既定オン
        day = v.poll(0)["day"]
        self.assertIsNotNone(day)
        self.assertIn("tint", day)
        self.assertIn("glow", day)
        self.assertTrue(day["tint"].startswith("rgb("))

    def test_poll_day_none_when_disabled(self):
        os.environ["ENGAWA_DAYNIGHT"] = "0"
        config._CFG = {}
        v = views.WebView()
        self.assertIsNone(v.poll(0)["day"])              # 無効＝JS は膜を素通し（背景そのまま）

    def test_web_html_has_tint_layers_and_apply(self):
        h = views.build_web_html()
        self.assertIn('id="tint"', h)                    # 染めの膜（乗算）
        self.assertIn('id="lamp"', h)                    # 室内灯の膜（障子ごしの暖色・screen）
        self.assertIn('id="glow"', h)                    # 光の膜（加算月光）
        self.assertIn("mix-blend-mode:multiply", h)
        self.assertIn("mix-blend-mode:screen", h)
        self.assertIn("applyDay", h)                     # poll の {tint,glow,lamp} を膜へ適用する口


class TestDayNightPreview(unittest.TestCase):
    """/daynight プレビューの View 配線（ADR-0028）: 固定(pin)/実時間へ戻す(off)/早送り終了で自動復帰。
    console は no-op。色の中身・時刻解釈は daynight の純関数テスト側。"""
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ.pop("ENGAWA_DAYNIGHT", None)          # 既定オンで
        config._CFG = {}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        config._CFG = None

    def test_console_daynight_is_noop(self):
        c = views.ConsoleView()
        self.assertIsNone(c.current_daynight())          # 対象外（背景が無い）
        self.assertIsNone(c.daynight_enabled())
        self.assertFalse(c.set_daynight({"mode": "pin", "minute": 100}))
        self.assertFalse(c.set_daynight_enabled(False))

    def test_pin_makes_poll_return_that_time(self):
        v = views.WebView()
        v.set_daynight({"mode": "pin", "minute": 18 * 60})
        self.assertEqual(v.poll(0)["day"], daynight.layers_for_minute(18 * 60))   # 実時間でなく 18:00 の色
        self.assertEqual(v.current_daynight(), {"mode": "pin", "minute": 18 * 60})

    def test_auto_clears_override_to_real_time(self):
        v = views.WebView()
        v.set_daynight({"mode": "pin", "minute": 0})
        v.set_daynight({"mode": "auto"})                 # プレビュー解除＝実時間へ
        self.assertEqual(v.current_daynight(), {"mode": "real"})
        self.assertIsNotNone(v.poll(0)["day"])           # 機能は有効のまま＝実時間の色

    def test_enabled_toggle_live_gates_poll(self):
        v = views.WebView()
        self.assertTrue(v.daynight_enabled())            # 既定オン
        self.assertIsNotNone(v.poll(0)["day"])
        v.set_daynight_enabled(False)                    # ライブ無効化＝再起動不要
        self.assertFalse(v.daynight_enabled())
        self.assertIsNone(v.poll(0)["day"])              # 無効＝背景固定
        v.set_daynight_enabled(True)
        self.assertIsNotNone(v.poll(0)["day"])           # 再び有効

    def test_enable_toggle_resets_preview_to_real(self):
        v = views.WebView()
        v.set_daynight({"mode": "pin", "minute": 0})     # 夜に固定中
        v.set_daynight_enabled(False)                    # トグルはプレビューを解除
        v.set_daynight_enabled(True)
        self.assertEqual(v.current_daynight(), {"mode": "real"})   # 前の固定を残さない

    def test_demo_self_clears_when_finished(self):
        v = views.WebView()
        v.set_daynight({"mode": "demo", "from": 960, "to": 1320, "secs": 0})   # secs=0＝即終了
        day = v.poll(0)["day"]                            # poll で expired 判定→override を外す
        self.assertIsNotNone(day)
        self.assertEqual(v.current_daynight(), {"mode": "real"})   # 自動で実時間へ戻った

    def test_disabled_feature_ignores_override(self):
        os.environ["ENGAWA_DAYNIGHT"] = "0"              # 機能オフ
        config._CFG = {}
        v = views.WebView()
        v.set_daynight({"mode": "pin", "minute": 18 * 60})
        self.assertIsNone(v.poll(0)["day"])              # オフなら override 有無に関わらず None


class TestUiWindowWiring(unittest.TestCase):
    """run_web から分離した窓オプション/設定解決（GUI 起動せずユニットで担保）。
    『窓が狭い』対策＝窓は resizable・サイズは config 由来（ハードコードでない）。"""
    def setUp(self):
        import engawa_main
        self.em = engawa_main
        self._saved = dict(os.environ)
        for k in ("ENGAWA_UI_W", "ENGAWA_UI_H", "ENGAWA_UI_FONT", "ENGAWA_UI_CORNER", "ENGAWA_UI_EASYDRAG"):
            os.environ.pop(k, None)
        config._CFG = {}                       # engawa.json を無視（テスト隔離）

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        config._CFG = None                     # 次回ロードで再読込

    def test_window_is_resizable_with_min_size(self):
        k = self.em._web_window_kwargs(400, 520, easy_drag=False)
        self.assertTrue(k["resizable"])               # 設定上は resizable（実挙動は GUI 目視）
        self.assertEqual(k["min_size"], (240, 240))   # 潰れ防止
        self.assertEqual((k["width"], k["height"]), (400, 520))   # サイズ passthrough
        self.assertTrue(k["frameless"])               # 枠なし隅窓は維持

    def test_size_and_font_from_config_env(self):
        os.environ["ENGAWA_UI_W"] = "500"
        os.environ["ENGAWA_UI_FONT"] = "1.4"
        _corner, _ed, w, _h, font = self.em._ui_config()
        self.assertEqual(w, 500)                       # env が効く＝ハードコードでない
        self.assertEqual(font, 1.4)

    def test_defaults_when_unset(self):
        corner, _ed, w, h, font = self.em._ui_config()
        self.assertEqual((w, h), (400, 520))           # 既定窓サイズ（少し広め）
        self.assertEqual(font, 1.0)                    # 既定 文字倍率（等倍）
        self.assertEqual(corner, "br")


class TestDebugConfig(unittest.TestCase):
    """_debug_config: ENGAWA_DEBUG/ENGAWA_LOG_FILE の解決（既定オフ・空パスは既定へ）。"""
    def setUp(self):
        import engawa_main
        self.em = engawa_main
        self._saved = dict(os.environ)
        for k in ("ENGAWA_DEBUG", "ENGAWA_LOG_FILE"):
            os.environ.pop(k, None)
        config._CFG = {}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        config._CFG = None

    def test_default_off_with_repo_log_path(self):
        debug, path = self.em._debug_config()
        self.assertFalse(debug)                        # 既定オフ
        self.assertTrue(path.endswith("engawa.log"))   # 既定はリポジトリ直下 engawa.log

    def test_env_enables_and_overrides_path(self):
        os.environ["ENGAWA_DEBUG"] = "1"
        os.environ["ENGAWA_LOG_FILE"] = "/tmp/x.log"
        debug, path = self.em._debug_config()
        self.assertTrue(debug)
        self.assertEqual(path, "/tmp/x.log")

    def test_empty_json_file_falls_back_to_default(self):
        config._CFG = {"debug": {"enabled": 1, "file": ""}}   # 空文字は既定パスへ（FileHandler("") 事故回避）
        debug, path = self.em._debug_config()
        self.assertTrue(debug)
        self.assertTrue(path.endswith("engawa.log"))


class TestAssetSwap(unittest.TestCase):
    """背景/スプライトを好みで差し替え（env > engawa.json[assets] > 既定・ADR-0010 の皮を背景にも拡張）。"""

    def setUp(self):
        self._env = os.environ.pop("ENGAWA_SCENE_BG", None)
        self._cfg = config._CFG
        config._CFG = {}                                   # engawa.json 相当を空に（決定的に）

    def tearDown(self):
        config._CFG = self._cfg
        if self._env is None:
            os.environ.pop("ENGAWA_SCENE_BG", None)
        else:
            os.environ["ENGAWA_SCENE_BG"] = self._env

    def test_default_falls_back_to_assets_dir(self):
        p = views._asset_path("ENGAWA_SCENE_BG", "scene_bg", "scene.png")
        self.assertEqual(os.path.basename(p), "scene.png")
        self.assertTrue(p.endswith(os.path.join("assets", "scene.png")))

    def test_engawa_json_overrides_default(self):
        config._CFG = {"assets": {"scene_bg": "C:/skins/my_room.png"}}
        self.assertEqual(views._asset_path("ENGAWA_SCENE_BG", "scene_bg", "scene.png"),
                         "C:/skins/my_room.png")

    def test_env_wins_over_engawa_json(self):
        config._CFG = {"assets": {"scene_bg": "from_json.png"}}
        os.environ["ENGAWA_SCENE_BG"] = "from_env.png"
        self.assertEqual(views._asset_path("ENGAWA_SCENE_BG", "scene_bg", "scene.png"), "from_env.png")

    def test_sprite_config_path_is_swappable(self):
        config._CFG = {"assets": {"sprite_config": "D:/skins/alt/sprite.json"}}
        self.assertEqual(views._sprite_config_path(), "D:/skins/alt/sprite.json")


class TestSceneBgInjection(unittest.TestCase):
    """背景 data URI が #scene に注入され .shoji/.floor が隠れる／無ければグラデにフォールバック
    （build_web_html を触った時の背景 marker 消し忘れ・placeholder 非表示漏れを検知・codexレビュー）。"""

    def setUp(self):
        self._orig = views._load_scene_bg

    def tearDown(self):
        views._load_scene_bg = self._orig

    def test_bg_present_injects_and_hides_placeholders(self):
        views._load_scene_bg = lambda: "data:image/png;base64,AAAA"
        h = views.build_web_html(1.0)
        self.assertNotIn("/*SCENEBG*/", h)                        # プレースホルダ消費
        self.assertIn("#scene{background:url(data:image/png;base64,AAAA)", h)
        self.assertIn(".shoji,.floor{display:none}", h)           # 板プレースホルダを隠す

    def test_bg_absent_falls_back_to_gradient(self):
        views._load_scene_bg = lambda: None
        h = views.build_web_html(1.0)
        self.assertNotIn("/*SCENEBG*/", h)                        # プレースホルダは空に消費
        self.assertNotIn("background:url(data:image/png", h)      # 画像注入なし
        self.assertNotIn(".shoji,.floor{display:none}", h)        # 板は残る
        self.assertIn("linear-gradient", h)                       # グラデ背景（フォールバック）


class TestResidentBackend(unittest.TestCase):
    """住人 backend の切替（ADR-0026・composition root）。既定=ACP／openai=OpenAIAgent／表示は sessionId 任意。"""

    def setUp(self):
        import engawa_main
        self.em = engawa_main
        self._env = os.environ.pop("ENGAWA_RESIDENT_BACKEND", None)
        self._cfg = config._CFG
        config._CFG = {}

    def tearDown(self):
        config._CFG = self._cfg
        if self._env is None:
            os.environ.pop("ENGAWA_RESIDENT_BACKEND", None)
        else:
            os.environ["ENGAWA_RESIDENT_BACKEND"] = self._env

    def test_default_is_acp(self):
        import acp
        self.assertEqual(self.em._resident_spawner(), acp.AcpAgent.spawn_resident)

    def test_openai_backend_selects_openai_agent(self):
        import agent_openai
        config._CFG = {"backend": {"resident": "openai"}}
        self.assertEqual(self.em._resident_spawner(), agent_openai.OpenAIAgent.spawn_resident)

    def test_resident_tag_without_sessionid(self):
        import agent_openai
        a = agent_openai.OpenAIAgent("http://x/v1", "qwen", "k", 30)
        tag = self.em._resident_tag(a)                            # sessionId 無し（OpenAIAgent）→ model だけ
        self.assertIn("茶々=qwen", tag)
        self.assertNotIn("session=", tag)


class TestGuestBackend(unittest.TestCase):
    """客人 backend の切替（ADR-0026・composition root）。既定=ACP(codex)／openai=OpenAIAgent。"""

    def setUp(self):
        import engawa_main
        self.em = engawa_main
        self._env = os.environ.pop("ENGAWA_GUEST_BACKEND", None)
        self._cfg = config._CFG
        config._CFG = {}

    def tearDown(self):
        config._CFG = self._cfg
        if self._env is None:
            os.environ.pop("ENGAWA_GUEST_BACKEND", None)
        else:
            os.environ["ENGAWA_GUEST_BACKEND"] = self._env

    def test_default_is_acp(self):
        import acp
        self.assertEqual(self.em._guest_spawner(), acp.AcpAgent.spawn_guest)

    def test_openai_backend_selects_openai_guest(self):
        import agent_openai
        config._CFG = {"backend": {"guest": "openai"}}
        self.assertEqual(self.em._guest_spawner(), agent_openai.OpenAIAgent.spawn_guest)


class TestEnterMode(unittest.TestCase):
    """入力欄の Enter の振る舞いを config で切替（ui.enter・send=Enter送信/newline=Enter改行）。build_web_html が注入。"""

    def setUp(self):
        self._env = os.environ.pop("ENGAWA_UI_ENTER", None)
        self._cfg = config._CFG
        config._CFG = {}

    def tearDown(self):
        config._CFG = self._cfg
        if self._env is None:
            os.environ.pop("ENGAWA_UI_ENTER", None)
        else:
            os.environ["ENGAWA_UI_ENTER"] = self._env

    def test_default_is_send(self):
        h = views.build_web_html(1.0)
        self.assertIn('const ENTER_MODE="send"', h)
        self.assertNotIn("/*ENTERMODE*/", h)                     # マーカー消費

    def test_newline_mode(self):
        config._CFG = {"ui": {"enter": "newline"}}
        self.assertIn('const ENTER_MODE="newline"', views.build_web_html(1.0))

    def test_invalid_falls_back_to_send(self):
        config._CFG = {"ui": {"enter": "bogus"}}
        self.assertIn('const ENTER_MODE="send"', views.build_web_html(1.0))


if __name__ == "__main__":
    unittest.main()
