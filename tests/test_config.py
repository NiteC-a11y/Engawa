"""config の解決順（env > engawa.json > 既定）と範囲クランプ（S4 回帰）＋書き戻し（/font save）。"""
import json
import os
import sys
import tempfile
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


class TestConfigSetValue(unittest.TestCase):
    """set_value: engawa.json[section][key] へ書き戻し（/font save の永続化）。
    ENGAWA_CONFIG で一時ファイルに向けて実 engawa.json を汚さない。"""
    def setUp(self):
        self._saved_env = dict(os.environ)
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8")
        self._tmp.close()
        os.environ["ENGAWA_CONFIG"] = self._tmp.name
        config._CFG = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        config._CFG = None
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    def _read(self):
        with open(self._tmp.name, encoding="utf-8") as f:
            return json.load(f)

    def test_writes_new_section_to_missing_file(self):
        os.remove(self._tmp.name)                 # ファイル無し → 新規作成
        self.assertTrue(config.set_value("ui", "font", 1.4))
        self.assertEqual(self._read(), {"ui": {"font": 1.4}})

    def test_preserves_existing_keys_and_comments(self):
        with open(self._tmp.name, "w", encoding="utf-8") as f:
            json.dump({"_comment": "手書き", "model": {"resident": "opus"},
                       "ui": {"corner": "br", "font": 1.0}}, f, ensure_ascii=False)
        self.assertTrue(config.set_value("ui", "font", 1.6))
        data = self._read()
        self.assertEqual(data["ui"]["font"], 1.6)
        self.assertEqual(data["ui"]["corner"], "br")       # 同セクションの他キー保持
        self.assertEqual(data["model"]["resident"], "opus")  # 他セクション保持
        self.assertEqual(data["_comment"], "手書き")          # コメント保持

    def test_updates_cache_so_get_reflects(self):
        config.set_value("ui", "font", 1.8)
        # env 無し → json（＝書いた値）が効く
        self.assertEqual(config.get_float("ABSENT_FONT_ENV", "ui", "font", 1.0), 1.8)

    def test_broken_json_not_overwritten(self):
        with open(self._tmp.name, "w", encoding="utf-8") as f:
            f.write("{ this is not json ]")
        self.assertFalse(config.set_value("ui", "font", 1.4))   # 壊れた設定は潰さない
        with open(self._tmp.name, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ this is not json ]")    # 中身そのまま


if __name__ == "__main__":
    unittest.main()
