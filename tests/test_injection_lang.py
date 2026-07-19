"""test_injection_lang.py — 層またぎ不変条件の合成テスト（ADR-0022・7/19 のソロ経路穴の再発防止）。

不変条件: 「llm_lang が立つ voice では、LLM に届く注入ビルダーの**全経路**に出力言語指示（lang note）が載る。
JP 既定 voice では全経路が1バイトも変わらない」。

背景: lang note が prompts.py のビルダーだけに付き sources.py の narration（ambient/arc/transition）に
無かった＝en モードの起動直後ソロ独り言が日本語になるバグ（80発話の実測で「note 有り=40/40 英語／
無し=ほぼ100%日本語」の二値・7/19）。ユニットは各モジュール緑のまま継ぎ目に落ちた（ADR-0031 ARRIVE 穴と
同型）ため、ここで**ビルダーを明示列挙**して継ぎ目ごと張る。列挙の完全性は命名 canary
（`*_narration`/`*_prompt` の公開関数は「検証済み ∪ 除外明記」に必ず分類）で守る＝新ビルダーを足すと
このテストが分類を迫る。実 LLM での一巡は tests/e2e/leak_probe.py（opt-in・層B）。
"""
import datetime
import inspect
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
import conversation
import prompts
import sources
import voice

NOTE = "(Respond in English"


def _ctx():
    now = datetime.datetime(2026, 7, 19, 9, 30)
    w = {"desc": "時々曇り", "temp": 28.6, "wind": 6}
    return {"weather": w, "desc": w["desc"], "raining": False,
            "tod": sources.time_of_day(now), "hour": now.hour, "now": now, "topics": []}


# LLM に届く注入ビルダーの列挙（住人向け＋客人向け）。新ビルダーは必ずここか EXEMPT に入れる。
COVERED = {
    "user_narration": lambda: prompts.user_narration("hello", _ctx()),
    "room_resident_prompt": lambda: prompts.room_resident_prompt((), conversation.REPLY, _ctx()),
    "room_guest_prompt": lambda: prompts.room_guest_prompt("traveler", (), conversation.REPLY, _ctx()),
    "ambient_narration": lambda: sources.ambient_narration(_ctx()),
    "event_narration": lambda: sources.event_narration("雀が一羽、手すりに止まった。"),
    "transition_narration": lambda: sources.transition_narration("晴れ", _ctx()),
}

# 意図的な除外（理由必須）:
# - game_move_prompt: 出力契約が「手の語だけ」（rlcard の英語トークン・parse_move 照合）＝言語中立で、
#   note を足すと "staying in character" が余計な口上を誘発しうる。会話ではなく手番プロトコル。
EXEMPT = {"game_move_prompt"}


def _named_builders(mod):
    """命名規約（*_narration / *_prompt）に載る公開関数＝注入ビルダー候補の機械列挙。"""
    return {n for n, f in vars(mod).items()
            if inspect.isfunction(f) and not n.startswith("_")
            and (n.endswith("_narration") or n.endswith("_prompt"))}


class TestInjectionLangNote(unittest.TestCase):
    """en voice（実 voices/en バンドル）で全経路に note・JP 既定で全経路不変。"""

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("ENGAWA_VOICE", "ENGAWA_VOICES_DIR", "ENGAWA_CONFIG")}
        os.environ["ENGAWA_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-such-engawa.json")
        os.environ.pop("ENGAWA_VOICES_DIR", None)        # 実 repo の voices/en を使う
        os.environ["ENGAWA_VOICE"] = "en"
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
        voice._CACHE = None

    def test_en_voice_all_paths_carry_note(self):
        self.assertEqual(voice.llm_lang(), "en")         # 前提: 実 en バンドルが解決できている
        for name, build in COVERED.items():
            with self.subTest(builder=name):
                self.assertIn(NOTE, build(), f"{name} に言語指示が無い（7/19 の穴の再発）")

    def test_default_voice_all_paths_unchanged(self):
        """JP 既定は「1バイトも変わらない」（ADR-0022）＝note 機構を強制無効化した基準出力とバイト一致で検証。
        文言不在（"Respond in" が無い）だけでは JP 限定の空白/改行/別表現の混入を見逃す（codex 7/19 [低]）。"""
        os.environ["ENGAWA_VOICE"] = "ja-osaka"
        self._reset()
        self.assertIsNone(voice.llm_lang())
        self.assertEqual(voice.lang_note(), "")              # 失敗理由を明瞭に（空文字契約そのもの）
        for name, build in COVERED.items():
            with self.subTest(builder=name):
                actual = build()
                # 呼び出し名でパッチ: prompts は module global `_lang_note`・sources は属性 `voice.lang_note`
                with mock.patch.object(prompts, "_lang_note", return_value=""), \
                     mock.patch.object(voice, "lang_note", return_value=""):
                    baseline = build()
                self.assertEqual(actual, baseline, f"{name} が JP 既定で note 機構由来の差分を持つ（1バイト不変則）")
                self.assertNotIn("Respond in", actual)

    def test_enumeration_is_complete(self):
        """命名規約に載る全ビルダーが「検証済み ∪ 除外明記」に分類されている（未分類＝ここで落ちる）。"""
        found = _named_builders(prompts) | _named_builders(sources)
        unclassified = found - set(COVERED) - EXEMPT
        self.assertFalse(unclassified,
                         f"未分類の注入ビルダー: {sorted(unclassified)}（COVERED か EXEMPT へ・理由を書くこと）")
        self.assertTrue(set(COVERED) <= found, "COVERED に実在しないビルダー名がある（改名の追従漏れ）")


if __name__ == "__main__":
    unittest.main()
