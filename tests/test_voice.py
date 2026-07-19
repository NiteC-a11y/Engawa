"""voice.py（ADR-0022 Inc1）: voice 解決（env > engawa.json > 既定）・バンドル読込・base 継承・
欠損フォールバック（組み込み ja-osaka＝現状維持）・loc の未訳フォールバックを検証。
実ファイルは temp dir（ENGAWA_VOICES_DIR/ENGAWA_CONFIG）で隔離＝個人設定に依存しない。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
import persona
import voice


def _write_bundle(root, vid, meta=None, persona_md=None, strings=None):
    d = os.path.join(root, vid)
    os.makedirs(d, exist_ok=True)
    if meta is not None:
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    if persona_md is not None:
        with open(os.path.join(d, "persona.md"), "w", encoding="utf-8") as f:
            f.write(persona_md)
    if strings is not None:
        with open(os.path.join(d, "strings.json"), "w", encoding="utf-8") as f:
            json.dump(strings, f, ensure_ascii=False)


class TestVoice(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.pop(k, None)
                     for k in ("ENGAWA_VOICE", "ENGAWA_VOICES_DIR", "ENGAWA_CONFIG")}
        os.environ["ENGAWA_VOICES_DIR"] = self._tmp.name
        os.environ["ENGAWA_CONFIG"] = os.path.join(self._tmp.name, "engawa.json")  # 個人 engawa.json を読まない
        config._CFG = None
        voice._CACHE = None

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config._CFG = None
        voice._CACHE = None
        self._tmp.cleanup()

    def _reset(self):
        config._CFG = None
        voice._CACHE = None

    def test_default_is_builtin_osaka(self):
        v = voice.current()
        self.assertEqual(v["id"], "ja-osaka")
        self.assertEqual(voice.persona_text(), persona.RESIDENT_PERSONA)   # ゼロ設定＝現状維持
        self.assertIsNone(voice.llm_lang())
        self.assertEqual(voice.loc("help_quit", "縁側を閉じる"), "縁側を閉じる")   # 未訳＝コード内の日本語へ

    def test_env_selects_bundle(self):
        _write_bundle(self._tmp.name, "en",
                      meta={"base": "en", "label": "English", "llm_lang": "en"},
                      persona_md="You are Chacha, a resident of the engawa.",
                      strings={"help_quit": "close the engawa"})
        os.environ["ENGAWA_VOICE"] = "en"
        self._reset()
        self.assertEqual(voice.label(), "English")
        self.assertEqual(voice.llm_lang(), "en")
        self.assertIn("You are Chacha", voice.persona_text())
        self.assertEqual(voice.loc("help_quit", "縁側を閉じる"), "close the engawa")
        self.assertEqual(voice.loc("unknown_key", "既定"), "既定")          # 未訳キーは底へ落ちる

    def test_json_selects_and_env_wins(self):
        _write_bundle(self._tmp.name, "en", meta={"base": "en", "label": "English"},
                      persona_md="You are Chacha.")
        with open(os.environ["ENGAWA_CONFIG"], "w", encoding="utf-8") as f:
            json.dump({"voice": {"id": "en"}}, f)
        self._reset()
        self.assertEqual(voice.current()["id"], "en")                       # json で選択
        os.environ["ENGAWA_VOICE"] = "ja-osaka"
        self._reset()
        self.assertEqual(voice.current()["id"], "ja-osaka")                 # env が json に勝つ

    def test_missing_bundle_falls_back_builtin(self):
        os.environ["ENGAWA_VOICE"] = "no-such-voice"
        self._reset()
        v = voice.current()
        self.assertEqual(v["id"], "ja-osaka")                               # 起動は止めない
        self.assertIn("no-such-voice", v["label"])                          # ただし痕跡は残す（黙らない）
        self.assertEqual(voice.persona_text(), persona.RESIDENT_PERSONA)

    def test_base_inheritance_strings_and_persona(self):
        _write_bundle(self._tmp.name, "en",
                      meta={"base": "en", "label": "English"},
                      persona_md="You are Chacha.",
                      strings={"a": "A", "b": "B"})
        _write_bundle(self._tmp.name, "en-uk",
                      meta={"base": "en", "label": "English (UK)"},
                      strings={"b": "B-UK"})                                # persona.md 無し＝base から継承
        os.environ["ENGAWA_VOICE"] = "en-uk"
        self._reset()
        self.assertIn("You are Chacha", voice.persona_text())               # persona は base から
        self.assertEqual(voice.loc("a", "х"), "A")                          # base の敷き
        self.assertEqual(voice.loc("b", "х"), "B-UK")                       # voice が上書き

    def test_persona_only_bundle(self):
        _write_bundle(self._tmp.name, "ja-kyoto", persona_md="うちは茶々どす。")   # meta 無し＝方言の最小形
        os.environ["ENGAWA_VOICE"] = "ja-kyoto"
        self._reset()
        self.assertIn("どす", voice.persona_text())
        self.assertEqual(voice.label(), "ja-kyoto")                         # label は id に既定
        self.assertIsNone(voice.llm_lang())


class TestEnBundleAndWiring(unittest.IsolatedAsyncioTestCase):
    """同梱 voices/en の実ファイル検証＋Inc2 配線（prompts の言語ノブ・views のラベル差し替え・
    scheduler の loc）。既定 voice（ja-osaka）では全出力が従来どおり＝英語は en 選択時だけ。"""

    def setUp(self):
        self._env = {k: os.environ.pop(k, None)
                     for k in ("ENGAWA_VOICE", "ENGAWA_VOICES_DIR", "ENGAWA_CONFIG")}
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        os.environ["ENGAWA_VOICES_DIR"] = os.path.join(root, "voices")
        os.environ["ENGAWA_CONFIG"] = os.path.join(root, "no-such-engawa.json")
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

    def _use_default_voice(self):
        os.environ["ENGAWA_VOICE"] = "ja-osaka"
        self._reset()

    def test_en_bundle_loads(self):
        self.assertEqual(voice.label(), "English")
        self.assertEqual(voice.llm_lang(), "en")
        self.assertIn("Chacha", voice.persona_text())
        self.assertNotIn("関西", voice.persona_text())              # 翻訳でなく別の声（transcreation）
        self.assertEqual(voice.loc("ui_send", "送信"), "Send")
        self.assertEqual(voice.loc("help_quit", "x"), "  /quit            → close the engawa")

    def test_prompts_lang_note_english(self):
        import prompts
        out = prompts.user_narration("hello")
        self.assertIn("Respond in English", out)                    # llm_lang=en → 言語ノブが乗る
        self.assertIn("Respond in English", prompts.room_resident_prompt((), "reply"))
        self.assertIn("Respond in English", prompts.room_guest_prompt("traveler", (), "reply"))   # 客人も英語で応じる

    def test_leak_guard_spares_english_speech(self):
        # en voice（llm_lang=en）では jp 省略でも思考除去ヒューリスティックを跳ばす＝英文を壊さない
        import prompts
        out = "Mm, the summer solstice—夏至—came around already."
        self.assertEqual(prompts.strip_resident_leak(out), out)

    def test_prompts_lang_note_absent_by_default(self):
        self._use_default_voice()
        import prompts
        self.assertNotIn("Respond in", prompts.user_narration("やあ"))   # JP では注入文は不変（ADR-0022）

    def test_web_html_localized(self):
        import views
        html = views.build_web_html()
        self.assertIn(">Send<", html)
        self.assertNotIn(">送信<", html)
        self.assertIn("Say something…", html)
        self.assertIn(">Meow<", html)
        self.assertIn(">Chacha<", html)                              # 宛先チップ

    def test_web_html_default_unchanged(self):
        self._use_default_voice()
        import views
        html = views.build_web_html()
        self.assertIn(">送信<", html)
        self.assertIn("話しかける…", html)
        self.assertIn(">ニャー<", html)

    def test_resident_name_and_addr_english(self):
        # 表示名 茶々→Chacha（transcript/チップの画面内不一致の解消・7/19）＋宛先バー見出しの鍵化漏れ修正
        import views
        self.assertEqual(voice.resident_name(), "Chacha")
        html = views.build_web_html()
        self.assertIn('<span class="al">To</span>', html)
        self.assertIn('const RESIDENT="Chacha";', html)          # JS のソロ転写ラベル/色分け/在室判定も追従（7/19 実機バグ）
        self.assertEqual(views.ConsoleView._header("Chacha", "user", None), "   Chacha › ")  # console も who 追従

    def test_room_speaker_display_split(self):
        # room の注入窓（LLM が読む側）は「茶々」のまま・画面表示だけ Chacha（Speaker.display 分離・7/19）
        import room_speakers
        f = room_speakers.RoomSpeakerFactory("旅人", resident_speak=None,
                                             guest_agent_provider=lambda: None,
                                             context_provider=lambda: None,
                                             topics_provider=lambda: [], log=None)
        res, guest = f.speakers()
        self.assertEqual((res.name, res.display), ("茶々", "Chacha"))
        self.assertEqual((guest.name, guest.display), ("旅人", "旅人"))

    def test_resident_tag_english(self):
        # 起動 tag のラベルも voice 追従（外枠 boot_ok_* だけ鍵化で中身が混成日本語になる穴・codex 7/19 [中]1）
        import agent_openai
        import engawa_main
        a = agent_openai.OpenAIAgent("http://x/v1", "qwen", "k", 30)
        self.assertEqual(engawa_main._resident_tag(a), "Chacha=qwen / voice=English")
        a2 = agent_openai.OpenAIAgent("http://x/v1", None, "k", 30)
        self.assertIn("Chacha=default", engawa_main._resident_tag(a2))   # モデル未指定の「既定」も英語

    def test_resident_name_and_addr_default_unchanged(self):
        self._use_default_voice()
        import views
        self.assertEqual(voice.resident_name(), "茶々")
        html = views.build_web_html()
        self.assertIn('<span class="al">宛先</span>', html)
        self.assertIn('const RESIDENT="茶々";', html)            # JP でも定数注入（値は同じ＝挙動不変）
        self.assertEqual(views.ConsoleView._header("茶々", "user", None), "   茶々 › ")

    async def test_scheduler_help_localized(self):
        import scheduler as sched
        import sources
        import views

        class _R:
            model = reported_model = None

            async def prompt(self, text, on_chunk=None):
                return ""

            async def cancel(self):
                pass

            async def close(self):
                pass

        s = sched.Scheduler(_R(), [], sources.WeatherSource(), views.CaptureView())
        await s._command("/help")
        msgs = [m for (t, m, _x) in s.view.events if t == "system"]
        self.assertTrue(any("close the engawa" in m for m in msgs))  # /quit 行が英語
        self.assertFalse(any("縁側を閉じる" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
