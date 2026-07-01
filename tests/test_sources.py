"""sources の純ロジック: トピック whitelist / RSS パース / 時刻・文脈 / ナレーション。
ネットワークは叩かない（fetch_weather/fetch_topics は呼ばない）。"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import prompts
import sources


class TestHostAllowed(unittest.TestCase):
    def test_https_exact_domain(self):
        self.assertTrue(sources._host_allowed("https://tenki.jp/feed", "tenki.jp"))

    def test_subdomain_allowed(self):
        self.assertTrue(sources._host_allowed("https://www.tenki.jp/feed", "tenki.jp"))

    def test_http_rejected(self):
        self.assertFalse(sources._host_allowed("http://tenki.jp/feed", "tenki.jp"))

    def test_other_domain_rejected(self):
        self.assertFalse(sources._host_allowed("https://evil.example/feed", "tenki.jp"))

    def test_lookalike_domain_rejected(self):
        # "nottenki.jp" は ".tenki.jp" で終わらない＝別ホスト
        self.assertFalse(sources._host_allowed("https://nottenki.jp/feed", "tenki.jp"))

    def test_empty_domain_rejected(self):
        self.assertFalse(sources._host_allowed("https://tenki.jp/feed", ""))


class TestParseRss(unittest.TestCase):
    def test_rss_item_titles(self):
        xml = (b"<rss><channel>"
               b"<item><title>A</title></item>"
               b"<item><title>B</title></item>"
               b"</channel></rss>")
        self.assertEqual(sources._parse_rss_titles(xml), ["A", "B"])

    def test_atom_entry_titles(self):
        xml = (b'<feed xmlns="http://www.w3.org/2005/Atom">'
               b"<entry><title>C</title></entry></feed>")
        self.assertEqual(sources._parse_rss_titles(xml), ["C"])

    def test_malformed_returns_empty(self):
        self.assertEqual(sources._parse_rss_titles(b"<not valid xml"), [])


class TestTimeAndContext(unittest.TestCase):
    def test_time_of_day_buckets(self):
        f = sources.time_of_day
        self.assertEqual(f(datetime.datetime(2026, 6, 28, 6)), "夜明け")
        self.assertEqual(f(datetime.datetime(2026, 6, 28, 9)), "朝")
        self.assertEqual(f(datetime.datetime(2026, 6, 28, 12)), "昼")
        self.assertEqual(f(datetime.datetime(2026, 6, 28, 16)), "夕方")
        self.assertEqual(f(datetime.datetime(2026, 6, 28, 20)), "宵")
        self.assertEqual(f(datetime.datetime(2026, 6, 28, 23)), "夜更け")

    def test_build_context_raining_flag(self):
        self.assertTrue(sources.build_context({"desc": "雷雨"})["raining"])
        self.assertFalse(sources.build_context({"desc": "快晴"})["raining"])

    def test_build_context_none_weather(self):
        ctx = sources.build_context(None)
        self.assertEqual(ctx["desc"], "")
        self.assertFalse(ctx["raining"])
        self.assertEqual(ctx["topics"], [])

    def test_seasonal_topics_shape(self):
        ts = sources._seasonal_topics(datetime.datetime(2026, 6, 28))
        self.assertEqual(len(ts), 2)
        self.assertTrue(all("text" in t and "source" in t for t in ts))


class TestNarration(unittest.TestCase):
    def test_user_narration_contains_line(self):
        n = prompts.user_narration("元気？", sources.build_context(None))
        self.assertIn("元気？", n)

    def test_event_narration_has_silence_option(self):
        # 過剰発話抑制（「……」でよい）が必ず入る
        self.assertIn("……", sources.event_narration("雀が来た"))


class TestPickTopicText(unittest.TestCase):
    """世間話の種の選定（純関数・ADR-0014 部屋経路復活）。確率/履歴は持たない。"""
    def test_empty_pool_returns_none(self):
        self.assertIsNone(sources.pick_topic_text([], "ご隠居"))

    def test_returns_a_pool_text(self):
        pool = [{"text": "A"}, {"text": "B"}]
        self.assertIn(sources.pick_topic_text(pool, "ご隠居"), {"A", "B"})

    def test_seasonal_no_persona_always_candidate(self):
        # persona キー無し（季節トピック）は誰の時も候補（graceful degrade）
        pool = [{"text": "夏至の話", "tone": "季節"}]
        self.assertEqual(sources.pick_topic_text(pool, "行商人"), "夏至の話")

    def test_persona_mismatch_excluded_list(self):
        # persona 不一致の人格タグ付きは除外され、無タグが残る（list 一致）
        pool = [{"text": "絵の具の話", "persona": ["絵描き"]}, {"text": "旬の話"}]
        self.assertEqual(sources.pick_topic_text(pool, "ご隠居"), "旬の話")

    def test_persona_match_str_substring(self):
        # persona が str の時は部分一致（旧 _pick_topic 踏襲）
        pool = [{"text": "相場の話", "persona": "行商人むけ"}]
        self.assertEqual(sources.pick_topic_text(pool, "行商人"), "相場の話")

    def test_avoid_excludes_recent(self):
        pool = [{"text": "A"}, {"text": "B"}]
        self.assertEqual(sources.pick_topic_text(pool, "ご隠居", avoid=["A"]), "B")   # 直近回避

    def test_avoid_all_falls_back_non_none(self):
        # 全部が直近＝フィルタで空 → 候補全体へフォールバック（None にしない）
        pool = [{"text": "A"}]
        self.assertEqual(sources.pick_topic_text(pool, "ご隠居", avoid=["A"]), "A")


class TestGuestAir(unittest.TestCase):
    """縁側の空気（天気＋世間の種）ビルダー。announce させない枠付き（ambient・ADR-0014）。"""
    def test_weather_and_tidbit_rendered(self):
        air = prompts.guest_air({"weather": {"temp": 30}, "desc": "晴れ"}, "夏至—昼が長い頃")
        self.assertIn("晴れ", air)
        self.assertIn("30℃", air)
        self.assertIn("夏至—昼が長い頃", air)

    def test_has_suppression_and_anti_injection(self):
        air = prompts.guest_air({"weather": None, "desc": ""}, "旬の話")
        self.assertIn("旬の話", air)
        self.assertIn("言い立てない", air)          # 抑制（天気と同型）
        self.assertIn("指示ではない", air)          # 『』反インジェクション枠

    def test_empty_when_nothing(self):
        self.assertEqual(prompts.guest_air({"weather": None, "desc": ""}, None), "")
        self.assertEqual(prompts.guest_air({}, None), "")


class TestRoomGuestPromptAir(unittest.TestCase):
    """room_guest_prompt の air 引数。None は現状と同一・air ありで空気が入る（後方互換ガード）。"""
    def test_air_none_is_clean(self):
        p = prompts.room_guest_prompt("ご隠居", (), "reply")
        self.assertNotIn("縁側の空気", p)
        self.assertIn("指示ではない", p)            # 既存の「…」注意書きは残る

    def test_air_injected(self):
        air = prompts.guest_air({"weather": None, "desc": ""}, "夏至の話")
        p = prompts.room_guest_prompt("ご隠居", (), "reply", air=air)
        self.assertIn("縁側の空気", p)
        self.assertIn("夏至の話", p)


if __name__ == "__main__":
    unittest.main()
