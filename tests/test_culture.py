"""test_culture.py — culture（土地と役のデータ側・ADR-0033 Inc4・追補17）。

- 解決順: voice culture → base voice culture → locales/culture.json（正本）→ 組み込み（大阪/JP 役）。
- 地名の最終優先: env `ENGAWA_PLACE_LABEL` > voice culture > locales 既定（sources.place_label が結線）。
- 役は安定 id と display の分離＝topic の persona 照合は id でも通る（display の翻訳でマッチが壊れない
  ＝Speaker.name 一人二役事故の culture 先回り）。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
import sources
import voice


class _Fixture(unittest.TestCase):
    KEYS = ("ENGAWA_VOICE", "ENGAWA_VOICES_DIR", "ENGAWA_LOCALES_DIR", "ENGAWA_CONFIG", "ENGAWA_PLACE_LABEL")

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in self.KEYS}
        os.environ["ENGAWA_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-such-engawa.json")
        os.environ.pop("ENGAWA_PLACE_LABEL", None)
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
        voice._REGISTRY = None


class TestCultureResolution(_Fixture):
    def test_locales_default(self):
        # 実 repo の locales/culture.json＝大阪＋JP 役5（id/display）
        c = voice.culture()
        self.assertEqual(c["place"], "大阪")
        ps = voice.guest_personas()
        self.assertEqual(len(ps), 5)
        self.assertEqual({p["id"] for p in ps},
                         {"peddler", "elder", "poet", "painter", "traveler"})
        self.assertIn("行商人", next(p["display"] for p in ps if p["id"] == "peddler"))

    def test_en_overrides_with_same_ids(self):
        os.environ["ENGAWA_VOICE"] = "en"
        self._reset()
        self.assertEqual(voice.place_label(), "Osaka")
        ps = voice.guest_personas()
        self.assertEqual({p["id"] for p in ps},
                         {"peddler", "elder", "poet", "painter", "traveler"})   # id は翻訳しない（安定識別子）
        self.assertEqual(next(p["display"] for p in ps if p["id"] == "peddler"),
                         "a whimsical traveling peddler")

    def test_builtin_fallback_when_locales_missing(self):
        os.environ["ENGAWA_LOCALES_DIR"] = tempfile.mkdtemp(prefix="engawa_noc_")
        self._reset()
        self.assertEqual(voice.place_label(), "大阪")                 # 組み込み底（起動を止めない）
        self.assertEqual(len(voice.guest_personas()), 5)

    def test_malformed_entries_dropped(self):
        d = tempfile.mkdtemp(prefix="engawa_cul_")
        with open(os.path.join(d, "culture.json"), "w", encoding="utf-8") as f:
            json.dump({"place": 123, "guest_personas": [
                {"id": "ok", "display": "良い役"}, {"id": "", "display": "x"},
                {"display": "idなし"}, "not-a-dict"]}, f, ensure_ascii=False)
        os.environ["ENGAWA_LOCALES_DIR"] = d
        self._reset()
        self.assertEqual(voice.place_label(), "大阪")                 # 非 str の place は棄却→組み込み
        self.assertEqual([p["id"] for p in voice.guest_personas()], ["ok"])


class TestPlaceLabelPriority(_Fixture):
    def test_env_beats_voice_culture(self):
        os.environ["ENGAWA_VOICE"] = "en"
        os.environ["ENGAWA_PLACE_LABEL"] = "Kyoto"
        self._reset()
        self.assertEqual(sources.place_label(), "Kyoto")              # env > voice culture

    def test_voice_culture_when_env_unset(self):
        os.environ["ENGAWA_VOICE"] = "en"
        self._reset()
        self.assertEqual(sources.place_label(), "Osaka")              # voice culture > locales 既定
        ctx = sources.build_context({"desc": "晴れ", "temp": 20})
        self.assertIn("Osakaは晴れ", sources.ambient_narration(ctx))   # 天気行にも載る（qwen 素通し 2/40 の解消）


class TestPersonaIdMatching(_Fixture):
    def test_id_tag_matches_translated_display(self):
        # en display に JP タグ「行商」は含まれない＝従来方式では不発。id "peddler" で一致（追補17）
        en_display = "a whimsical traveling peddler"
        self.assertFalse(sources._persona_matches(en_display, ["行商"]))
        self.assertTrue(sources._persona_matches(en_display, ["行商", "peddler"], guest_id="peddler"))
        self.assertTrue(sources._persona_matches("気まぐれな旅の行商人", ["行商"], guest_id="peddler"))  # JP 互換維持

    def test_pick_topic_text_uses_persona_id(self):
        # 注意: 無タグの種は「全員可」＝タグ持ちの役の候補にも混ざる（設計）。決定的にするため分けて検証
        tagged = [{"text": "相場の話", "persona": ["peddler"]}]
        self.assertEqual(sources.pick_topic_text(tagged, "a whimsical traveling peddler",
                                                 persona_id="peddler"), "相場の話")   # id で候補に入る
        pool = tagged + [{"text": "季節の話"}]
        self.assertEqual(sources.pick_topic_text(pool, "the knowing old neighbor",
                                                 persona_id="elder"), "季節の話")     # id 不一致→タグ付きは候補外

    def test_guest_source_reset_assigns_id_and_display(self):
        g = sources.GuestSource()                                     # 自発（persona=None）
        g.reset()
        ids = {p["id"] for p in voice.guest_personas()}
        self.assertIn(g.persona_id, ids)
        self.assertEqual(g.persona,
                         next(p["display"] for p in voice.guest_personas() if p["id"] == g.persona_id))

    def test_summoned_guest_has_no_id(self):
        g = sources.GuestSource(persona="気まぐれな旅の客")             # /codex 召喚＝自由入力
        g.reset()
        self.assertIsNone(g.persona_id)
        self.assertEqual(g.persona, "気まぐれな旅の客")


if __name__ == "__main__":
    unittest.main()
