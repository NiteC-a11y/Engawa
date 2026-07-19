"""test_voice_lint.py — voice_lint の判定純関数（ADR-0033 決定8・追補12/14）。

5状態分類（missing/inherited-from-base/same-as-default/translated/unknown）・base 一段限定の
異常系（自己参照/不在/多段）・placeholder 不一致・exit code 契約（0=完訳/1=指摘/2=不成立）を検証。
実 en バンドルの lint が通る（完訳・エラー無し）ことも smoke（同梱品質の回帰止め）。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import voice_lint

REG = {"greet": "こんにちは", "bye": "さようなら（{name}）"}


def _mk(root, vid, meta=None, strings=None, persona=False):
    d = os.path.join(root, vid)
    os.makedirs(d, exist_ok=True)
    if meta is not None:
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    if strings is not None:
        with open(os.path.join(d, "strings.json"), "w", encoding="utf-8") as f:
            json.dump(strings, f, ensure_ascii=False)
    if persona:
        with open(os.path.join(d, "persona.md"), "w", encoding="utf-8") as f:
            f.write("p")


class TestLintStates(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="engawa_lint_")

    def test_five_states(self):
        _mk(self.root, "base-v", meta={"label": "B"}, strings={"bye": "cya ({name})"}, persona=True)
        _mk(self.root, "kid", meta={"label": "K", "base": "base-v"},
            strings={"greet": "hello", "typo_key": "x", "same": "?"}, persona=True)
        # same-as-default を作るため registry に追加キー
        reg = dict(REG, same="そのまま")
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
            strings={"greet": "hi", "bye": "bye ({name})"}, persona=True)
        r2 = voice_lint.lint_bundle("full", self.root, REG)
        self.assertEqual(voice_lint.exit_code(r2), 0)              # 完訳

    def test_placeholder_mismatch(self):
        _mk(self.root, "ph", meta={"label": "P"}, strings={"bye": "bye"}, persona=True)   # {name} 落ち
        r = voice_lint.lint_bundle("ph", self.root, REG)
        self.assertEqual(len(r["placeholder_mismatch"]), 1)
        self.assertEqual(r["placeholder_mismatch"][0][0], "bye")
        self.assertEqual(voice_lint.exit_code(r), 1)


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
    """同梱 voices/en は lint 完訳・エラー無しを保つ（回帰止め。base 自己参照は 7/19 に lint が発見→修正）。"""

    def test_en_is_clean(self):
        import voice
        registry, state = voice._load_registry()
        self.assertEqual(state, "ok")
        r = voice_lint.lint_bundle("en", voice._voices_dir(), registry)
        self.assertEqual(r["errors"], [])
        self.assertEqual(voice_lint.exit_code(r), 0, voice_lint.render_report(r))


if __name__ == "__main__":
    unittest.main()
