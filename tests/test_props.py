"""props.py（縁側の小物・ADR-0032 v2）: カタログ（entity+component 正規化・キャッシュ・フォールバック）と
条件レジストリ（when は全フィールド AND・未知フィールド無視＝前方互換）・narration_line を検証。
台帳は ENGAWA_PROPS_CONFIG の temp json で隔離。同梱台帳の実ファイル検証は test_views 側。"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
import props


def _july():
    return datetime.datetime(2026, 7, 15)


def _january():
    return datetime.datetime(2026, 1, 15)


class PropsBase(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ("ENGAWA_PROPS_CONFIG", "ENGAWA_CONFIG")}
        os.environ["ENGAWA_CONFIG"] = os.path.join(tempfile.gettempdir(), "no-such-engawa.json")
        self._reset()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._reset()

    @staticmethod
    def _reset():
        config._CFG = None
        props._CACHE = None

    def _use(self, data):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False)
        f.close()
        os.environ["ENGAWA_PROPS_CONFIG"] = f.name
        self._reset()
        return f.name


class TestCatalog(PropsBase):
    def test_missing_file_is_empty(self):
        os.environ["ENGAWA_PROPS_CONFIG"] = os.path.join(tempfile.gettempdir(), "no-such-props.json")
        self._reset()
        self.assertEqual(props.catalog(), [])

    def test_broken_json_is_empty(self):
        self._use("{broken")
        self.assertEqual(props.catalog(), [])

    def test_normalize_components(self):
        self._use({"props": [
            {"image": "a.png"},                                        # id 省略＝image 名・塊は空に矯正
            {"id": "b", "image": "b.png", "place": "junk", "effect": {"kind": "rise"},
             "narrate": "縁側にbがある"},
            {"id": "no-image"}, "junk",                                # image 無し/非 dict は落ちる
        ]})
        cat = props.catalog()
        self.assertEqual([p["id"] for p in cat], ["a.png", "b"])
        self.assertEqual(cat[0]["place"], {})
        self.assertEqual(cat[1]["place"], {})                          # 型不正の塊は空 dict へ
        self.assertEqual(cat[1]["effect"], {"kind": "rise"})
        self.assertEqual(cat[1]["narrate"], "縁側にbがある")

    def test_cache_reloads_on_path_change(self):
        self._use({"props": [{"id": "x", "image": "x.png"}]})
        self.assertEqual(props.catalog()[0]["id"], "x")
        self._use({"props": [{"id": "y", "image": "y.png"}]})          # 別パス＝読み直し（テストの単離）
        self.assertEqual(props.catalog()[0]["id"], "y")


class TestWhenRegistry(PropsBase):
    def test_month_gate_in_and_out(self):
        self._use({"props": [{"id": "k", "image": "k.png", "when": {"months": [6, 7, 8, 9]}}]})
        self.assertEqual(props.active_ids(_july()), ["k"])             # 夏＝出る
        self.assertEqual(props.active_ids(_january()), [])             # 冬＝出ない

    def test_no_when_is_always_on(self):
        self._use({"props": [{"id": "tea", "image": "tea.png"}]})
        self.assertEqual(props.active_ids(_january()), ["tea"])

    def test_unknown_when_field_is_ignored(self):
        # 前方互換: 未来の条件（hours 等）を古いコードが読んでも落ちない・無視して表示側に倒す
        self._use({"props": [{"id": "k", "image": "k.png", "when": {"hours": [18, 19], "months": [7]}}]})
        self.assertEqual(props.active_ids(_july()), ["k"])

    def test_string_months_cast_and_broken_fall_open(self):
        self._use({"props": [{"id": "a", "image": "a.png", "when": {"months": ["7"]}},
                             {"id": "b", "image": "b.png", "when": {"months": ["july", None]}}]})
        self.assertEqual(props.active_ids(_july()), ["a", "b"])        # 壊れた months は常時扱い
        self.assertEqual(props.active_ids(_january()), ["b"])


class TestNarration(PropsBase):
    def test_narration_joins_active_only(self):
        self._use({"props": [
            {"id": "k", "image": "k.png", "when": {"months": [7]}, "narrate": "縁側には蚊取り線香がある"},
            {"id": "t", "image": "t.png", "narrate": "湯呑みが置いてある。"},
            {"id": "mute", "image": "m.png"},                          # narrate 無し＝語らない
        ]})
        self.assertEqual(props.narration_line(_july()),
                         "縁側には蚊取り線香がある。湯呑みが置いてある。")   # 句点補完＋連結
        self.assertEqual(props.narration_line(_january()), "湯呑みが置いてある。")   # 冬は線香が消える

    def test_narration_empty_when_nothing(self):
        self._use({"props": []})
        self.assertEqual(props.narration_line(_july()), "")


if __name__ == "__main__":
    unittest.main()
