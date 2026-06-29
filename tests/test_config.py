"""config の解決順（env > engawa.json > 既定）と範囲クランプ（S4 回帰）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config


class TestConfigResolve(unittest.TestCase):
    def setUp(self):
        self._saved_env = dict(os.environ)
        config._CFG = {}          # engawa.json を無視（テスト隔離）

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        config._CFG = None        # 次回ロードで再読込

    # ── 解決順 ──
    def test_env_takes_precedence(self):
        os.environ["E_X"] = "0.4"
        config._CFG = {"s": {"k": 0.9}}
        self.assertEqual(config.get_float("E_X", "s", "k", 0.1), 0.4)

    def test_json_used_when_no_env(self):
        config._CFG = {"s": {"k": 0.55}}
        self.assertEqual(config.get_float("ABSENT_ENV", "s", "k", 0.1), 0.55)

    def test_default_when_absent(self):
        self.assertEqual(config.get_float("ABSENT_ENV", "s", "k", 0.3), 0.3)

    def test_broken_env_falls_back_to_default(self):
        os.environ["E_X"] = "abc"
        self.assertEqual(config.get_float("E_X", "s", "k", 0.3), 0.3)

    # ── クランプ（S4）──
    def test_clamp_high(self):
        os.environ["E_X"] = "5"
        self.assertEqual(config.get_float("E_X", "s", "k", 0.5, lo=0, hi=1), 1.0)

    def test_clamp_low(self):
        os.environ["E_X"] = "-0.3"
        self.assertEqual(config.get_float("E_X", "s", "k", 0.5, lo=0, hi=1), 0.0)

    def test_in_range_unchanged(self):
        os.environ["E_X"] = "0.42"
        self.assertEqual(config.get_float("E_X", "s", "k", 0.5, lo=0, hi=1), 0.42)

    def test_no_bounds_means_no_clamp(self):
        os.environ["E_X"] = "999"
        self.assertEqual(config.get_float("E_X", "s", "k", 0.5), 999.0)

    def test_json_value_also_clamped(self):
        config._CFG = {"guest": {"prob": 9}}
        self.assertEqual(config.get_float("ABSENT", "guest", "prob", 0.05, lo=0, hi=1), 1.0)

    def test_get_int_clamp(self):
        os.environ["E_N"] = "-10"
        self.assertEqual(config.get_int("E_N", "topic", "refresh_min", 30, lo=1), 1)

    # ── 文字列（モデル選択つまみ）──
    def test_get_str_from_env(self):
        os.environ["E_M"] = "claude-opus-4-8"
        self.assertEqual(config.get_str("E_M", "model", "resident", ""), "claude-opus-4-8")

    def test_get_str_default_empty(self):
        # 未指定は空文字＝アダプタ既定のまま（現状維持）
        self.assertEqual(config.get_str("ABSENT", "model", "resident", ""), "")

    def test_get_str_from_json(self):
        config._CFG = {"model": {"guest": "gpt-5-codex"}}
        self.assertEqual(config.get_str("ABSENT", "model", "guest", ""), "gpt-5-codex")


if __name__ == "__main__":
    unittest.main()
