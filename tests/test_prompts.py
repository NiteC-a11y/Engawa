"""prompts.strip_resident_leak（住人=茶々 出力の染み出しガード）の純関数テスト。
注入プロンプトの復唱＋先頭の思考(英語/メタ)を表示前に削る。正常出力は無改変。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import prompts


class TestStripResidentLeak(unittest.TestCase):
    def test_passthrough_normal(self):
        s = "そうやなあ……ぼちぼちやで。"
        self.assertEqual(prompts.strip_resident_leak(s), s)

    def test_keeps_silence(self):
        # 「……」は茶々の有効な発話（沈黙）＝削らない
        self.assertEqual(prompts.strip_resident_leak("……"), "……")

    def test_empty_and_none(self):
        self.assertEqual(prompts.strip_resident_leak(""), "")
        self.assertIsNone(prompts.strip_resident_leak(None))

    def test_short_leading_latin_kept(self):
        # "OK、" 程度の軽い先頭は思考ではない＝残す（MIN 未満）
        s = "OK、ほなまた明日な。"
        self.assertEqual(prompts.strip_resident_leak(s), s)

    def test_strips_leading_english_reasoning(self):
        # 注入文の復唱が無い「思考だけ」の染み出しも削れる（原因を問わない＝root-cause-agnostic）
        leaked = ("The human is frustrated. As Chacha, I should be warm and short, Kansai."
                  "そうなんよなよな……淡々とやるのが正解や。")
        out = prompts.strip_resident_leak(leaked)
        self.assertTrue(out.startswith("そうなんよな"))
        self.assertNotIn("As Chacha", out)

    def test_strips_echoed_injection(self):
        injected = prompts.user_narration("評価むずいわ", ctx=None)
        leaked = injected + "そうなんよな……気にせんとき。"
        out = prompts.strip_resident_leak(leaked, injected)
        self.assertEqual(out, "そうなんよな……気にせんとき。")
        self.assertNotIn("茶々として", out)
        self.assertNotIn("話しかけられた", out)

    def test_full_leak_like_screenshot(self):
        # 実機のスクショと同型: [注入文の復唱] + [英語の思考] + [本物の台詞]
        injected = prompts.user_narration(
            "そうそう。だから淡々とやってればいいんだけど、なかなか評価にはむずぴつかんね", ctx=None)
        leaked = (injected +
                  "The human is talking about how their work doesn't translate to evaluation. "
                  "As Chacha, I should acknowledge the bind honestly, not cheerlead. Short, warm, Kansai."
                  "そうなんよなよな……淡々とやるのが正解や、頭では分かってても、"
                  "報われへんのはやっぱり効くわな。")
        out = prompts.strip_resident_leak(leaked, injected)
        self.assertTrue(out.startswith("そうなんよな"))
        self.assertNotIn("茶々として、自然にこたえて", out)
        self.assertNotIn("As Chacha", out)
        self.assertNotIn("評価にはむずぴつかんね", out)   # 復唱された注入の質問も消える

    def test_echo_without_injected_uses_builtin_markers(self):
        # injected を渡さなくても、既知の指示文 marker で復唱を検知できる
        leaked = ("茶々として、自然にこたえて。聞かれてもいないのに天気をいちいち言い立てない。"
                  "ほな、ぼちぼちいこか。")
        out = prompts.strip_resident_leak(leaked)
        self.assertEqual(out, "ほな、ぼちぼちいこか。")


class TestSanitizePersona(unittest.TestCase):
    """/codex 自由入力 persona の最小サニタイズ（制御文字/改行/長さ・公開前の最低線）。"""

    def test_passthrough_normal(self):
        self.assertEqual(prompts.sanitize_persona("近所の物知りなご隠居"), "近所の物知りなご隠居")

    def test_strips_newlines_tabs_control(self):
        out = prompts.sanitize_persona("旅の\n行商人\tだよ\x00")
        for bad in ("\n", "\t", "\x00"):
            self.assertNotIn(bad, out)
        self.assertEqual(out, "旅の 行商人 だよ")

    def test_collapses_whitespace(self):
        self.assertEqual(prompts.sanitize_persona("  絵   描き  "), "絵 描き")

    def test_caps_length(self):
        self.assertLessEqual(len(prompts.sanitize_persona("あ" * 200)), 60)

    def test_empty_falls_back_to_default(self):
        self.assertEqual(prompts.sanitize_persona(""), "気まぐれな旅の客")
        self.assertEqual(prompts.sanitize_persona("   \n\t "), "気まぐれな旅の客")


if __name__ == "__main__":
    unittest.main()
