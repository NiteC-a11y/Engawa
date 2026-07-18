"""props.py（縁側の小物・ADR-0032 v2）: カタログ（entity+component 正規化＝正本境界・型/範囲の矯正・
画像実在チェック・キャッシュ・フォールバック）と条件レジストリ（when は全AND・未知フィールド無視）・
narration_line を検証。台帳は temp dir（json＋ダミー画像）で隔離。views 側は test_views の TestProps。"""
import datetime
import json
import os
import shutil
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

    def _use(self, data, images=()):
        """temp dir に台帳 json＋ダミー画像を作って ENGAWA_PROPS_CONFIG を向ける（実在チェック対応）。"""
        d = tempfile.mkdtemp(prefix="engawa_props_")
        self.addCleanup(shutil.rmtree, d, True)
        for name in images:
            p = os.path.join(d, name)
            os.makedirs(os.path.dirname(p) or d, exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"\x89PNG-fake")
        path = os.path.join(d, "props.json")
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f, ensure_ascii=False)
        os.environ["ENGAWA_PROPS_CONFIG"] = path
        self._reset()
        return path


class TestCatalog(PropsBase):
    def test_missing_file_is_empty(self):
        os.environ["ENGAWA_PROPS_CONFIG"] = os.path.join(tempfile.gettempdir(), "no-such-props.json")
        self._reset()
        self.assertEqual(props.catalog(), [])

    def test_broken_json_is_empty(self):
        self._use("{broken")
        self.assertEqual(props.catalog(), [])

    def test_broken_image_types_are_dropped(self):
        # codex レビュー【高】: image が非 str（123/[]）だと views の os.path が TypeError で落ちていた
        # → 正本境界（_normalize）で entity ごと捨てる。id 非 str は image 名へフォールバック。
        self._use({"props": [
            {"id": "ok", "image": "ok.png"},
            {"id": "bad1", "image": 123},
            {"image": []},
            {"id": {}, "image": "ok2.png"},
        ]}, images=("ok.png", "ok2.png"))
        self.assertEqual([p["id"] for p in props.catalog()], ["ok", "ok2.png"])

    def test_missing_image_file_is_dropped_everywhere(self):
        # codex レビュー【中】: 画像欠損＝「画面に出せない物は世界にも無い」＝narrate も語らない（views/prompts の一致）
        self._use({"props": [{"id": "ghost", "image": "ghost.png", "narrate": "縁側に幽霊の壺がある"}]})
        self.assertEqual(props.catalog(), [])
        self.assertEqual(props.narration_line(_july()), "")

    def test_normalize_components_and_clamps(self):
        self._use({"props": [
            {"image": "a.png", "place": "junk"},                       # id 省略＝image 名・型不正の塊は既定へ
            {"id": "b", "image": "b.png",
             "place": {"left_pct": 0, "bottom_pct": 250, "display_px": 99999},
             "effect": {"kind": "rise", "period_ms": -5, "x_pct": "junk"},
             "narrate": "縁側にbがある"},
        ]}, images=("a.png", "b.png"))
        cat = props.catalog()
        self.assertEqual([p["id"] for p in cat], ["a.png", "b"])
        self.assertEqual(cat[0]["place"], {"left_pct": 10, "bottom_pct": 6, "display_px": 40})
        self.assertEqual(cat[1]["place"]["left_pct"], 0)                # 0 は有効な端位置（既定に化けない）
        self.assertEqual(cat[1]["place"]["bottom_pct"], 100)            # 範囲クランプ
        self.assertEqual(cat[1]["place"]["display_px"], 400)
        self.assertEqual(cat[1]["effect"]["period_ms"], 250)            # 負値→下限（setInterval 連打防止）
        self.assertEqual(cat[1]["effect"]["x_pct"], 50)                 # 型不正→既定
        self.assertEqual(cat[1]["narrate"], "縁側にbがある")

    def test_cache_reloads_on_path_change(self):
        self._use({"props": [{"id": "x", "image": "x.png"}]}, images=("x.png",))
        self.assertEqual(props.catalog()[0]["id"], "x")
        self._use({"props": [{"id": "y", "image": "y.png"}]}, images=("y.png",))
        self.assertEqual(props.catalog()[0]["id"], "y")


class TestWhenRegistry(PropsBase):
    def test_month_gate_in_and_out(self):
        self._use({"props": [{"id": "k", "image": "k.png", "when": {"months": [6, 7, 8, 9]}}]},
                  images=("k.png",))
        self.assertEqual(props.active_ids(_july()), ["k"])              # 夏＝出る
        self.assertEqual(props.active_ids(_january()), [])              # 冬＝出ない

    def test_no_when_is_always_on(self):
        self._use({"props": [{"id": "tea", "image": "tea.png"}]}, images=("tea.png",))
        self.assertEqual(props.active_ids(_january()), ["tea"])

    def test_unknown_when_field_is_ignored(self):
        # 前方互換: 未来の条件（hours 等）を古いコードが読んでも落ちない・無視して表示側に倒す
        self._use({"props": [{"id": "k", "image": "k.png", "when": {"hours": [18], "months": [7]}}]},
                  images=("k.png",))
        self.assertEqual(props.active_ids(_july()), ["k"])

    def test_string_months_cast_and_broken_fall_open(self):
        self._use({"props": [{"id": "a", "image": "a.png", "when": {"months": ["7"]}},
                             {"id": "b", "image": "b.png", "when": {"months": ["july", None]}}]},
                  images=("a.png", "b.png"))
        self.assertEqual(props.active_ids(_july()), ["a", "b"])         # 壊れた months は常時扱い
        self.assertEqual(props.active_ids(_january()), ["b"])


class TestNarration(PropsBase):
    def test_narration_joins_active_only(self):
        self._use({"props": [
            {"id": "k", "image": "k.png", "when": {"months": [7]}, "narrate": "縁側には蚊取り線香がある"},
            {"id": "t", "image": "t.png", "narrate": "湯呑みが置いてある。"},
            {"id": "mute", "image": "m.png"},                           # narrate 無し＝語らない
        ]}, images=("k.png", "t.png", "m.png"))
        self.assertEqual(props.narration_line(_july()),
                         "縁側には蚊取り線香がある。湯呑みが置いてある。")   # 句点補完＋連結
        self.assertEqual(props.narration_line(_january()), "湯呑みが置いてある。")

    def test_narration_empty_when_nothing(self):
        self._use({"props": []})
        self.assertEqual(props.narration_line(_july()), "")


if __name__ == "__main__":
    unittest.main()
