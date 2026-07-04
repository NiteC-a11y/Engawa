"""debuglog: ENGAWA_DEBUG のログ設定（既定オフ＝no-op / on＝engawa.log へ書く）。
実ファイル書き込みは一時パスで検証（実 engawa.log を汚さない）。"""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import debuglog


class TestDebugLogSetup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        self._tmp.close()

    def tearDown(self):
        debuglog.setup(False, self._tmp.name)      # ハンドラを畳んでファイルを解放
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    def test_off_returns_false_and_writes_nothing(self):
        self.assertFalse(debuglog.setup(False, self._tmp.name))
        debuglog.get("scheduler").debug("出ないはず")
        with open(self._tmp.name, encoding="utf-8") as f:
            self.assertEqual(f.read(), "")          # off＝no-op（本番の負荷ゼロ）

    def test_on_returns_true_and_writes(self):
        self.assertTrue(debuglog.setup(True, self._tmp.name))
        debuglog.get("scheduler").debug("種を空気へ: 夏至")
        logging.getLogger("engawa").handlers[0].flush()
        with open(self._tmp.name, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("種を空気へ: 夏至", body)
        self.assertIn("engawa.scheduler", body)     # 子ロガー名が出る

    def test_timestamp_has_date_and_milliseconds(self):
        # 定量分析用: 各行が「YYYY-MM-DD HH:MM:SS.mmm」で始まる（msec 精度で会話タイミングを追える）
        debuglog.setup(True, self._tmp.name)
        debuglog.get("scheduler").debug("next beat +6.0s (active=True)")
        logging.getLogger("engawa").handlers[0].flush()
        with open(self._tmp.name, encoding="utf-8") as f:
            first = f.readline()
        self.assertRegex(first, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} ")

    def test_setup_twice_no_duplicate_handlers(self):
        debuglog.setup(True, self._tmp.name)
        debuglog.setup(True, self._tmp.name)        # 付け直し＝多重出力しない
        self.assertEqual(len(logging.getLogger("engawa").handlers), 1)

    def test_child_name_and_no_root_leak(self):
        # 子は "engawa" 配下＝親のハンドラへ流れる。setup 後は真のルートへ漏らさない（propagate=False）
        self.assertEqual(debuglog.get("acp").name, "engawa.acp")
        debuglog.setup(True, self._tmp.name)
        self.assertFalse(logging.getLogger("engawa").propagate)


if __name__ == "__main__":
    unittest.main()
