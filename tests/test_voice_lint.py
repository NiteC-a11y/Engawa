"""test_voice_lint.py — voice_lint の判定純関数（ADR-0033 決定8・追補12/14・codex 7/19 4件反映）。

5状態分類・base 一段限定の異常系・placeholder 不一致（own/base 両方＝継承値も実行時に使われる）・
壊れた format 文字列での頑丈性（traceback せず診断に載る）・culture 検査（place/役 id の fallback 可視化）・
exit code 契約（0=voice 全体の完訳/1=指摘/2=不成立）を検証。
実 en バンドルの lint が通る（完訳・エラー無し）ことも smoke（同梱品質の回帰止め）。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import voice
import voice_lint

REG = {"greet": "こんにちは", "bye": "さようなら（{name}）"}
IDS = ("peddler", "elder", "poet", "painter", "traveler")     # locales/culture.json の基準 id
FULL_CULTURE = {"place": "テスト町",
                "guest_personas": [{"id": i, "display": f"role-{i}"} for i in IDS]}


def _mk(root, vid, meta=None, strings=None, persona=False, culture=None):
    d = os.path.join(root, vid)
    os.makedirs(d, exist_ok=True)
    if meta is not None:
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    if strings is not None:
        with open(os.path.join(d, "strings.json"), "w", encoding="utf-8") as f:
            json.dump(strings, f, ensure_ascii=False)
    if culture is not None:
        with open(os.path.join(d, "culture.json"), "w", encoding="utf-8") as f:
            json.dump(culture, f, ensure_ascii=False)
    if persona:
        with open(os.path.join(d, "persona.md"), "w", encoding="utf-8") as f:
            f.write("p")


class TestLintStates(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="engawa_lint_")

    def test_five_states(self):
        reg = dict(REG, same="そのまま")
        _mk(self.root, "base-v", meta={"label": "B"}, strings={"bye": "cya ({name})"}, persona=True)
        _mk(self.root, "kid2", meta={"label": "K2", "base": "base-v"},
            strings={"greet": "hello", "typo_key": "x", "same": "そのまま"}, persona=True)
        r = voice_lint.lint_bundle("kid2", self.root, reg)
        self.assertEqual(r["states"]["greet"], "translated")
        self.assertEqual(r["states"]["bye"], "inherited-from-base")
        self.assertEqual(r["states"]["same"], "same-as-default")
        self.assertEqual(r["states"]["typo_key"], "unknown")
        self.assertEqual(voice_lint.exit_code(r), 1)               # unknown あり＝指摘

    def test_missing_and_complete(self):
        _mk(self.root, "empty", meta={"label": "E"}, strings={}, persona=True)
        r = voice_lint.lint_bundle("empty", self.root, REG)
        self.assertEqual(set(r["states"].values()), {"missing"})
        self.assertEqual(voice_lint.exit_code(r), 1)
        _mk(self.root, "full", meta={"label": "F"},
            strings={"greet": "hi", "bye": "bye ({name})"}, persona=True, culture=FULL_CULTURE)
        r2 = voice_lint.lint_bundle("full", self.root, REG)
        self.assertEqual(r2["culture_findings"], [])
        self.assertEqual(voice_lint.exit_code(r2), 0)              # voice 全体の完訳（strings＋culture）

    def test_placeholder_mismatch_own(self):
        _mk(self.root, "ph", meta={"label": "P"}, strings={"bye": "bye"}, persona=True)   # {name} 落ち
        r = voice_lint.lint_bundle("ph", self.root, REG)
        self.assertEqual(len(r["placeholder_mismatch"]), 1)
        self.assertEqual(r["placeholder_mismatch"][0][0], "bye")
        self.assertEqual(r["placeholder_mismatch"][0][3], "own")
        self.assertEqual(voice_lint.exit_code(r), 1)

    def test_placeholder_mismatch_inherited_from_base(self):
        # base の訳が {name} を落としていても継承先で実行時に壊れる＝子の lint に載る（codex[中]①）
        _mk(self.root, "b", meta={"label": "B"}, strings={"bye": "cya"}, persona=True)
        _mk(self.root, "kid", meta={"label": "K", "base": "b"},
            strings={"greet": "hi"}, persona=True, culture=FULL_CULTURE)
        r = voice_lint.lint_bundle("kid", self.root, REG)
        self.assertEqual(r["states"]["bye"], "inherited-from-base")
        self.assertEqual([(m[0], m[3]) for m in r["placeholder_mismatch"]], [("bye", "base")])
        self.assertEqual(voice_lint.exit_code(r), 1)

    def test_broken_format_string_is_diagnosed_not_crash(self):
        # "{" 一個で lint が traceback したら著者ツール失格（codex[中]②）＝診断に載せて正常終了
        _mk(self.root, "fmt", meta={"label": "F"},
            strings={"greet": "hi {", "bye": "bye ({name})"}, persona=True, culture=FULL_CULTURE)
        r = voice_lint.lint_bundle("fmt", self.root, REG)
        self.assertEqual([(e[0], e[1]) for e in r["format_errors"]], [("greet", "own")])
        self.assertEqual(voice_lint.exit_code(r), 1)

    def test_broken_base_strings_is_bundle_error(self):
        _mk(self.root, "bb", meta={"label": "B"}, persona=True)
        with open(os.path.join(self.root, "bb", "strings.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        _mk(self.root, "kid", meta={"label": "K", "base": "bb"}, strings={}, persona=True)
        r = voice_lint.lint_bundle("kid", self.root, REG)
        self.assertTrue(any("bb" in e for e in r["errors"]))
        self.assertEqual(voice_lint.exit_code(r), 2)


class TestLintCulture(unittest.TestCase):
    """culture の解決後検査（codex[中]④）＝欠け・壊れ・id 異常の可視化。基準 id は実 repo の locales。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="engawa_lintc_")
        self._strings = {"greet": "hi", "bye": "bye ({name})"}

    def test_no_culture_falls_back_visibly(self):
        _mk(self.root, "v", meta={"label": "V"}, strings=self._strings, persona=True)
        r = voice_lint.lint_bundle("v", self.root, REG)
        self.assertTrue(any("place 未定義" in f for f in r["culture_findings"]))
        self.assertTrue(any("役名 display 未定義" in f for f in r["culture_findings"]))
        self.assertEqual(voice_lint.exit_code(r), 1)               # strings 完訳でも voice 完訳ではない

    def test_partial_culture_lists_missing_ids(self):
        c = {"place": "X", "guest_personas": [{"id": "peddler", "display": "d"}]}
        _mk(self.root, "v", meta={"label": "V"}, strings=self._strings, persona=True, culture=c)
        r = voice_lint.lint_bundle("v", self.root, REG)
        f = next(x for x in r["culture_findings"] if "役名 display 未定義" in x)
        for missing in ("elder", "poet", "painter", "traveler"):
            self.assertIn(missing, f)
        self.assertNotIn("peddler", f)

    def test_duplicate_and_unknown_ids_flagged(self):
        c = {"place": "X", "guest_personas": (
            [{"id": i, "display": "d"} for i in IDS]
            + [{"id": "peddler", "display": "d2"}, {"id": "ninja", "display": "x"}])}
        _mk(self.root, "v", meta={"label": "V"}, strings=self._strings, persona=True, culture=c)
        r = voice_lint.lint_bundle("v", self.root, REG)
        self.assertTrue(any("重複" in f and "peddler" in f for f in r["culture_findings"]))
        self.assertTrue(any("基準に無い役 id" in f and "ninja" in f for f in r["culture_findings"]))

    def test_broken_culture_is_bundle_error(self):
        _mk(self.root, "v", meta={"label": "V"}, strings=self._strings, persona=True)
        with open(os.path.join(self.root, "v", "culture.json"), "w", encoding="utf-8") as f:
            f.write("[broken")
        r = voice_lint.lint_bundle("v", self.root, REG)
        self.assertTrue(any("culture.json" in e for e in r["errors"]))
        self.assertEqual(voice_lint.exit_code(r), 2)


class TestLintBaseAndErrors(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="engawa_lint_")

    def test_base_self_reference_is_error(self):
        _mk(self.root, "selfy", meta={"label": "S", "base": "selfy"}, strings={}, persona=True)
        r = voice_lint.lint_bundle("selfy", self.root, REG)
        self.assertTrue(any("自己参照" in e for e in r["errors"]))
        self.assertEqual(voice_lint.exit_code(r), 2)

    def test_base_missing_is_error_and_grandbase_warns(self):
        _mk(self.root, "orphan", meta={"label": "O", "base": "no-such"}, strings={}, persona=True)
        r = voice_lint.lint_bundle("orphan", self.root, REG)
        self.assertTrue(any("no-such" in e for e in r["errors"]))
        _mk(self.root, "gp", meta={"label": "GP"}, strings={}, persona=True)
        _mk(self.root, "mid", meta={"label": "M", "base": "gp"}, strings={}, persona=True)
        _mk(self.root, "leaf", meta={"label": "L", "base": "mid"}, strings={}, persona=True)
        r2 = voice_lint.lint_bundle("leaf", self.root, REG)
        self.assertTrue(any("一段限定" in w for w in r2["warnings"]))   # 多段は警告（追補14）

    def test_bundle_not_found(self):
        r = voice_lint.lint_bundle("ghost", self.root, REG)
        self.assertTrue(r["errors"])
        self.assertEqual(voice_lint.exit_code(r), 2)


class TestShippedEnBundle(unittest.TestCase):
    """同梱 voices/en は lint 完訳（strings＋culture）・エラー無しを保つ（回帰止め）。"""

    def test_en_is_clean(self):
        registry, state = voice._load_registry()
        self.assertEqual(state, "ok")
        r = voice_lint.lint_bundle("en", voice._voices_dir(), registry)
        self.assertEqual(r["errors"], [])
        self.assertEqual(voice_lint.exit_code(r), 0, voice_lint.render_report(r))


if __name__ == "__main__":
    unittest.main()
