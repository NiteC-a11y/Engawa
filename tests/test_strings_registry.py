"""test_strings_registry.py — UI 文言台帳（locales/strings.json・ADR-0033）の静的照合と契約。

守る不変条件（ADR-0033 決定3/4/13・追補12/13/18/19）:
1. src の全 `loc()` 呼び出しはリテラルキーで、キーは台帳に存在する（typo は決定10で「キー名表示」に
   化けて JP テストでは見落としやすい＝ここで機械検出）。
2. インライン既定は禁止（台帳が正本）。例外は**動的既定**（JP 既定が random プール）の3キーのみ＝理由付き EXEMPT。
3. 台帳に未使用キーを溜めない（死んだ文言の温床）。
4. placeholder 集合は台帳と全バンドルで一致（書式は str.format。翻訳者が {tag} を落とす/増やすと実行時だけ壊れる）。
5. 台帳ローダーは失敗を識別する（ok/missing/malformed/wrong-shape・空/非文字列は欠損・欠損はキー名表示）。
6. 雛形 voices/_template/strings.json は台帳から再生成した結果と一致（意味＋バイトの二段・追補19）。
"""
import ast
import json
import os
import string
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import config
import voice

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(ROOT, "locales", "strings.json")

# インライン default を許す唯一の例外＝JP 既定が random プール（prompts.py の定型群）で台帳に静的値を
# 置けないキー。台帳には en 等の訳の受け皿として代表値を置く（解決順で呼び側 default が勝つ＝挙動不変）。
DYNAMIC_DEFAULT_EXEMPT = {"absence_leave", "absence_return", "guest_timeout_leave"}


def _registry():
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _loc_calls():
    """src 全体の loc() 呼び出しを AST で列挙 → (file, lineno, key_node, has_default, default_is_literal)。"""
    out = []
    src = os.path.join(ROOT, "src")
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(src, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if name == "loc" and node.args:
                out.append((fn, node.lineno, node.args[0], len(node.args) > 1,
                            len(node.args) > 1 and isinstance(node.args[1], ast.Constant)))
    return out


def _placeholders(text):
    """str.format の placeholder 名集合（位置引数は使わない前提＝名前だけ拾う）。"""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class TestLocConformance(unittest.TestCase):
    """src の loc() 呼び出し規約（キーはリテラル・台帳に存在・インライン既定は EXEMPT のみ）。"""

    def test_all_keys_literal_and_in_registry(self):
        reg = set(_registry()) - {"_comment"}
        for fn, ln, key_node, _, _ in _loc_calls():
            with self.subTest(site=f"{fn}:{ln}"):
                self.assertIsInstance(key_node, ast.Constant,
                                      f"{fn}:{ln} loc() のキーがリテラルでない（動的キーは原則禁止・追補18）")
                self.assertIn(key_node.value, reg,
                              f"{fn}:{ln} キー '{key_node.value}' が台帳に無い（typo はキー名表示に化ける）")

    def test_inline_defaults_only_for_dynamic_exempt(self):
        for fn, ln, key_node, has_default, is_literal in _loc_calls():
            if not has_default:
                continue
            key = key_node.value if isinstance(key_node, ast.Constant) else "?"
            with self.subTest(site=f"{fn}:{ln}"):
                self.assertIn(key, DYNAMIC_DEFAULT_EXEMPT,
                              f"{fn}:{ln} '{key}' がインライン既定を渡している（静的文言の正本は台帳・ADR-0033）")
                self.assertFalse(is_literal,
                                 f"{fn}:{ln} '{key}' の既定がリテラル（EXEMPT は動的既定＝random プール専用）")

    def test_registry_has_no_unused_keys(self):
        used = {k.value for _, _, k, _, _ in _loc_calls() if isinstance(k, ast.Constant)}
        unused = (set(_registry()) - {"_comment"}) - used
        self.assertFalse(unused, f"台帳に未使用キー: {sorted(unused)}（消すか使うか）")


class TestPlaceholders(unittest.TestCase):
    """placeholder 集合の一致（台帳 vs repo 内の全バンドル＝翻訳が {tag} を落とす/増やすのを機械検出）。"""

    def test_bundles_match_registry_placeholders(self):
        reg = _registry()
        vdir = os.path.join(ROOT, "voices")
        for vid in sorted(os.listdir(vdir)):
            sp = os.path.join(vdir, vid, "strings.json")
            if not os.path.exists(sp):
                continue
            with open(sp, encoding="utf-8") as f:
                bundle = json.load(f)
            for key, val in bundle.items():
                if key == "_comment" or key not in reg or not isinstance(val, str):
                    continue
                with self.subTest(voice=vid, key=key):
                    self.assertEqual(_placeholders(val), _placeholders(reg[key]),
                                     f"voices/{vid} の '{key}' の placeholder が台帳と不一致")

    def test_bundles_have_no_unknown_keys(self):
        """バンドルの未知キー（台帳に無い＝訳しても使われない typo）を検出。_template も対象。"""
        reg = set(_registry()) - {"_comment"}
        vdir = os.path.join(ROOT, "voices")
        for vid in sorted(os.listdir(vdir)):
            sp = os.path.join(vdir, vid, "strings.json")
            if not os.path.exists(sp):
                continue
            with open(sp, encoding="utf-8") as f:
                unknown = (set(json.load(f)) - {"_comment"}) - reg
            with self.subTest(voice=vid):
                self.assertFalse(unknown, f"voices/{vid} に台帳に無いキー: {sorted(unknown)}")


class TestRegistryLoader(unittest.TestCase):
    """専用ローダーの失敗識別（ADR-0033 決定13）と loc の解決3段。"""

    def setUp(self):
        self._env = os.environ.get("ENGAWA_LOCALES_DIR")
        self._tmp = tempfile.mkdtemp(prefix="engawa_loc_")
        self._reset()

    def tearDown(self):
        if self._env is None:
            os.environ.pop("ENGAWA_LOCALES_DIR", None)
        else:
            os.environ["ENGAWA_LOCALES_DIR"] = self._env
        self._reset()

    @staticmethod
    def _reset():
        config._CFG = None
        voice._CACHE = None
        voice._REGISTRY = None

    def _use(self, content=None):
        os.environ["ENGAWA_LOCALES_DIR"] = self._tmp
        if content is not None:
            with open(os.path.join(self._tmp, "strings.json"), "w", encoding="utf-8") as f:
                f.write(content)
        self._reset()

    def test_ok_and_resolution(self):
        self._use('{"_comment": "x", "closing", "invalid"}')   # まず malformed を踏んでから
        self.assertEqual(voice.registry_state(), "malformed")
        self._use('{"_comment": "x", "closing": "とじる", "empty_key": "", "bad": 1}')
        self.assertEqual(voice.registry_state(), "ok")
        self.assertEqual(voice.loc("closing"), "とじる")           # 台帳既定
        self.assertEqual(voice.loc("empty_key"), "empty_key")      # 空文字は欠損＝キー名表示
        self.assertEqual(voice.loc("bad"), "bad")                  # 非文字列も欠損
        self.assertEqual(voice.loc("no_such"), "no_such")          # 未定義＝キー名表示（決定10）
        self.assertEqual(voice.loc("closing", "呼び側既定"), "呼び側既定")   # 明示 default > 台帳（動的既定の契約）

    def test_missing_and_wrong_shape(self):
        self._use()                                                # ファイル無し
        self.assertEqual(voice.registry_state(), "missing")
        self.assertEqual(voice.loc("closing"), "closing")          # 全キーがキー名表示＝同梱漏れが一目でバレる
        self._use('["not", "a", "dict"]')
        self.assertEqual(voice.registry_state(), "wrong-shape")

    def test_real_registry_loads_ok(self):
        self._reset()                                              # 実 repo の locales/ を読む
        self.assertEqual(voice.registry_state(), "ok")
        self.assertEqual(voice.loc("closing"), "[*] 縁側を閉じます。")   # 代表キーの解決（frozen smoke 相当）


class TestTemplateSync(unittest.TestCase):
    """雛形＝台帳から再生成した結果と一致（意味＋バイトの二段・ADR-0033 追補19）。"""

    def test_template_matches_generator(self):
        import gen_voice_template as gen
        reg = _registry()
        want = gen.render(reg)
        path = os.path.join(ROOT, "voices", "_template", "strings.json")
        with open(path, encoding="utf-8", newline="") as f:
            got = f.read().replace("\r\n", "\n")                   # EOL 非依存（.gitattributes と二重防御）
        got_d, want_d = json.loads(got), json.loads(want)
        self.assertEqual(set(got_d) - {"_comment"}, set(reg) - {"_comment"},
                         "雛形のキー集合が台帳と不一致（tools/gen_voice_template.py を再実行してコミット）")
        self.assertEqual({k: v for k, v in got_d.items() if k != "_comment"},
                         {k: v for k, v in want_d.items() if k != "_comment"}, "雛形の値が台帳と不一致")
        self.assertEqual(got, want, "雛形がバイト不一致（生成器の出力形式が変わった？ 再生成してコミット）")


if __name__ == "__main__":
    unittest.main()
