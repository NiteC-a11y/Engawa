"""props.py（縁側の小物・ADR-0032）: 台帳読込のフォールバックと月ゲート（純関数）を検証。
実ファイルは temp で隔離。views 側の dataURI 化・注入は test_views の TestProps が担う。"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import props


def _july():
    return datetime.datetime(2026, 7, 15)


def _january():
    return datetime.datetime(2026, 1, 15)


class TestLoadConfig(unittest.TestCase):
    def _write(self, data):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_missing_file_is_empty(self):
        self.assertEqual(props.load_config(os.path.join(tempfile.gettempdir(), "no-such-props.json")), [])

    def test_broken_json_is_empty(self):
        self.assertEqual(props.load_config(self._write("{broken")), [])

    def test_non_list_props_is_empty(self):
        self.assertEqual(props.load_config(self._write({"props": {"id": "x"}})), [])

    def test_items_without_image_are_dropped(self):
        path = self._write({"props": [{"id": "a", "image": "a.png"}, {"id": "no-image"}, "junk"]})
        out = props.load_config(path)
        self.assertEqual([p["id"] for p in out], ["a"])


class TestActive(unittest.TestCase):
    def test_month_gate_in_and_out(self):
        items = [{"image": "k.png", "months": [6, 7, 8, 9]}]
        self.assertEqual(len(props.active(items, _july())), 1)      # 夏＝出る
        self.assertEqual(props.active(items, _january()), [])       # 冬＝出ない

    def test_no_months_is_always_on(self):
        items = [{"image": "tea.png"}]
        self.assertEqual(len(props.active(items, _january())), 1)

    def test_string_months_are_cast(self):
        items = [{"image": "k.png", "months": ["7"]}]
        self.assertEqual(len(props.active(items, _july())), 1)

    def test_broken_months_fall_back_to_always(self):
        items = [{"image": "k.png", "months": ["july", None]}]      # 壊れた台帳＝表示側に倒す
        self.assertEqual(len(props.active(items, _january())), 1)


if __name__ == "__main__":
    unittest.main()
