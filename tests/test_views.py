"""views の純関数: collapse_ws（客人の声の1行化）と corner_xy（隅配置の座標）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
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

    def destroy(self):
        self.destroyed = True


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
    """build_web_html の注入: UI 拡大率(zoom)を html{zoom:N} として埋め、テンプレ印を残さない。
    窓の resizable/サイズは run_web（GUI 起動＝ユニット対象外）が config 値を pywebview に渡す。"""
    def test_zoom_injected(self):
        self.assertIn("html{zoom:1.25}", views.build_web_html(1.25))

    def test_default_zoom_is_unity(self):
        # 引数なし＝等倍（run_web は config 既定 1.1 を渡すが、関数単体の既定は 1.0）
        self.assertIn("html{zoom:1.0}", views.build_web_html())

    def test_no_template_markers_leak(self):
        html = views.build_web_html(1.2)
        self.assertNotIn("/*ZOOM*/", html)     # zoom プレースホルダは消費済み
        self.assertNotIn("/*SPRITE*/", html)   # sprite プレースホルダも消費済み


class TestUiWindowWiring(unittest.TestCase):
    """run_web から分離した窓オプション/設定解決（GUI 起動せずユニットで担保）。
    『窓が狭い/文字が小さい』対策＝窓は resizable・サイズ/zoom は config 由来（ハードコードでない）。"""
    def setUp(self):
        import engawa_main
        self.em = engawa_main
        self._saved = dict(os.environ)
        for k in ("ENGAWA_UI_W", "ENGAWA_UI_H", "ENGAWA_UI_ZOOM", "ENGAWA_UI_CORNER", "ENGAWA_UI_EASYDRAG"):
            os.environ.pop(k, None)
        config._CFG = {}                       # engawa.json を無視（テスト隔離）

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        config._CFG = None                     # 次回ロードで再読込

    def test_window_is_resizable_with_min_size(self):
        k = self.em._web_window_kwargs(400, 520, easy_drag=False)
        self.assertTrue(k["resizable"])               # ドラッグで広げられる（狭い対策）
        self.assertEqual(k["min_size"], (240, 240))   # 潰れ防止
        self.assertEqual((k["width"], k["height"]), (400, 520))   # サイズ passthrough
        self.assertTrue(k["frameless"])               # 枠なし隅窓は維持

    def test_size_and_zoom_from_config_env(self):
        os.environ["ENGAWA_UI_W"] = "500"
        os.environ["ENGAWA_UI_ZOOM"] = "1.3"
        _corner, _ed, w, _h, zoom = self.em._ui_config()
        self.assertEqual(w, 500)                       # env が効く＝ハードコードでない
        self.assertEqual(zoom, 1.3)

    def test_defaults_when_unset(self):
        corner, _ed, w, h, zoom = self.em._ui_config()
        self.assertEqual((w, h), (400, 520))           # 既定窓サイズ（少し広め）
        self.assertEqual(zoom, 1.1)                    # 既定 zoom（少し大きめ）
        self.assertEqual(corner, "br")

    def test_zoom_clamped_out_of_range(self):
        os.environ["ENGAWA_UI_ZOOM"] = "9"             # 壊れた大値 → 上限 2.5 へクランプ
        _c, _ed, _w, _h, zoom = self.em._ui_config()
        self.assertEqual(zoom, 2.5)


if __name__ == "__main__":
    unittest.main()
