"""sources の純ロジック: トピック whitelist / RSS パース / 時刻・文脈 / ナレーション。
ネットワークは叩かない（fetch_weather/fetch_topics は呼ばない）。"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
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
        n = sources.user_narration("元気？", sources.build_context(None))
        self.assertIn("元気？", n)

    def test_event_narration_has_silence_option(self):
        # 過剰発話抑制（「……」でよい）が必ず入る
        self.assertIn("……", sources.event_narration("雀が来た"))

    def test_guest_narration_wraps_line(self):
        n = sources.guest_narration("ごめんやす", first=True, last=False)
        self.assertIn("ごめんやす", n)


if __name__ == "__main__":
    unittest.main()
