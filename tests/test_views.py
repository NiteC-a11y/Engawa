"""views の純関数: collapse_ws（客人の声の1行化）と corner_xy（隅配置の座標）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


if __name__ == "__main__":
    unittest.main()
