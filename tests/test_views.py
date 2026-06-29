"""views の純関数: collapse_ws（客人の声の1行化）と corner_xy（隅配置の座標）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
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


if __name__ == "__main__":
    unittest.main()
