"""prompts.strip_resident_leak（住人=茶々 出力の染み出しガード）の純関数テスト。
注入プロンプトの復唱＋先頭の思考(英語/メタ)を表示前に削る。正常出力は無改変。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import prompts


class TestIsErrorPayload(unittest.TestCase):
    """backend が API エラーを本文として流した時の門番（生 JSON を縁側に出さない）。"""
    def test_real_codex_400_error(self):
        # 実際に縁側へ漏れたペイロード（codex モデル非対応 400・2026-07-09 報告）
        s = ('{ "type": "error", "error": { "type": "invalid_request_error", '
             '"code": "unsupported_value", "message": "This model is not supported when '
             'using X-OpenAI-Internal-Codex-Responses-Lite.", "param": "model" }, "status": 400 }')
        self.assertTrue(prompts.is_error_payload(s))

    def test_error_object_shape(self):
        self.assertTrue(prompts.is_error_payload('{"error": {"message": "boom"}}'))
        self.assertTrue(prompts.is_error_payload('{"type": "error", "status": 500}'))

    def test_truncated_stream_with_signature(self):
        # 途中で切れて JSON パース不能でも、明白なシグネチャがあれば弾く
        self.assertTrue(prompts.is_error_payload('{ "type": "error", "error": { "type": "invalid_requ'))

    def test_normal_speech_is_not_error(self):
        for s in ("そうやなあ、ぼちぼちやで。", "……", "「ふむ」と客人が笑った。", ""):
            self.assertFalse(prompts.is_error_payload(s))

    def test_none_is_not_error(self):
        self.assertFalse(prompts.is_error_payload(None))

    def test_plain_json_without_error_key_is_not_error(self):
        # error/type シグネチャの無い素の dict は弾かない（過剰検出防止）
        self.assertFalse(prompts.is_error_payload('{"mood": "ねむい"}'))


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

    def test_reasoning_strip_skipped_for_non_jp_voice(self):
        # 英語 voice（jp=False）: 英文中に和語（夏至等）を引用しても手前の正当な英文を削らない（ADR-0022）
        out = "Mm, the summer solstice—夏至—came around already, didn't it."
        self.assertEqual(prompts.strip_resident_leak(out, jp=False), out)

    def test_reasoning_strip_applies_for_jp_voice(self):
        # 日本語 voice（jp=True）: 従来どおり先頭の非日本語塊（思考）は削られる
        leaked = ("I should reply as Chacha in a casual Kansai tone without markdown. "
                  "ええ天気やなあ")
        self.assertEqual(prompts.strip_resident_leak(leaked, jp=True), "ええ天気やなあ")


class TestUserNarrationInterrupted(unittest.TestCase):
    """barge-in（喋りかけ cancel）の事実を注入に語る（UI の「[茶々がこちらを向いた]」演出と文脈の一致）。"""
    def test_interrupted_adds_turned_around_fact(self):
        n = prompts.user_narration("おーい", ctx=None, interrupted=True)
        self.assertIn("こちらを向いたところ", n)
        # 事実の行は「話しかけられた」枠より前＝振り向いてから聞く、の順
        self.assertLess(n.index("こちらを向いたところ"), n.index("話しかけられた"))

    def test_default_has_no_turned_around_fact(self):
        n = prompts.user_narration("おーい", ctx=None)
        self.assertNotIn("こちらを向いた", n)

    def test_echoed_interrupted_narration_is_stripped(self):
        # 注入した事実の行を復唱されても、既存の echo-strip（injected marker）で消える
        injected = prompts.user_narration("おーい", ctx=None, interrupted=True)
        out = prompts.strip_resident_leak(injected + "なんや、おったんかいな。", injected)
        self.assertEqual(out, "なんや、おったんかいな。")


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
