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


class TestPersonaMatches(unittest.TestCase):
    """人格マッチ: タグが客人の役名の一部に含まれれば一致（実 persona は長い句・7/1 方向修正）。"""
    def test_no_tag_matches_everyone(self):
        self.assertTrue(sources._persona_matches("気まぐれな旅の行商人", None))

    def test_list_tag_substring_of_role(self):
        self.assertTrue(sources._persona_matches("気まぐれな旅の行商人", ["行商", "商人"]))

    def test_str_tag_substring_of_role(self):
        self.assertTrue(sources._persona_matches("句をひねる風流人", "風流"))

    def test_other_persona_tag_no_match(self):
        self.assertFalse(sources._persona_matches("近所の物知りなご隠居", ["絵描き"]))


class TestPickTopicText(unittest.TestCase):
    """世間話の種の選定（純関数・ADR-0014）。人格マッチ→直近回避→ランダム。確率/履歴は持たない。"""
    def test_empty_pool_returns_none(self):
        self.assertIsNone(sources.pick_topic_text([], "近所の物知りなご隠居"))

    def test_returns_a_pool_text(self):
        pool = [{"text": "A"}, {"text": "B"}]
        self.assertIn(sources.pick_topic_text(pool, "近所の物知りなご隠居"), {"A", "B"})

    def test_no_persona_matches_everyone(self):
        pool = [{"text": "夏至の話", "tone": "季節"}]              # タグ無し＝誰にでも
        self.assertEqual(sources.pick_topic_text(pool, "気まぐれな旅の行商人"), "夏至の話")

    def test_tag_matches_real_persona(self):
        pool = [{"text": "相場の話", "persona": ["行商", "商人"]}]  # 「行商」⊂「…行商人」で一致
        self.assertEqual(sources.pick_topic_text(pool, "気まぐれな旅の行商人"), "相場の話")

    def test_other_persona_tag_excluded_neutral_wins(self):
        pool = [{"text": "色の話", "persona": ["絵描き"]}, {"text": "季節の話"}]
        # ご隠居には絵描きタグは付かない → 色の話は候補外、無タグの季節が残る
        self.assertEqual(sources.pick_topic_text(pool, "近所の物知りなご隠居"), "季節の話")

    def test_own_tag_included_among_neutral(self):
        pool = [{"text": "色の話", "persona": ["絵描き"]}, {"text": "季節の話"}]
        got = sources.pick_topic_text(pool, "腹を空かせた野良の絵描き")   # 自分のタグ＋無タグが候補
        self.assertIn(got, {"色の話", "季節の話"})

    def test_avoid_excludes_recent(self):
        pool = [{"text": "A"}, {"text": "B"}]
        self.assertEqual(sources.pick_topic_text(pool, "ご隠居", avoid=["A"]), "B")   # 直近回避

    def test_avoid_all_falls_back_non_none(self):
        pool = [{"text": "A"}]
        self.assertEqual(sources.pick_topic_text(pool, "ご隠居", avoid=["A"]), "A")   # 全消しは候補全体へ


class TestLocalTopics(unittest.TestCase):
    """kind:"local" 源→トピック。inline topics は人格タグ付き／無ければ時節（ADR-0014 人格源拡充）。"""
    def test_inline_topics_tagged(self):
        src = {"name": "行商の噂", "kind": "local", "tone": "世間",
               "persona": ["行商", "商人"], "topics": ["米が高い", "船賃が上がった"]}
        ts = sources._local_topics(src)
        self.assertEqual([t["text"] for t in ts], ["米が高い", "船賃が上がった"])
        self.assertTrue(all(t["persona"] == ["行商", "商人"] for t in ts))
        self.assertTrue(all(t["source"] == "行商の噂" for t in ts))

    def test_no_topics_falls_back_to_seasonal(self):
        ts = sources._local_topics({"name": "時節", "kind": "local"})   # inline 無し
        self.assertEqual(len(ts), 2)                                     # 二十四節気＋旬
        self.assertTrue(all("persona" not in t for t in ts))            # 季節は persona 無し＝全員共通

    def test_length_capped(self):
        src = {"name": "x", "kind": "local", "topics": ["あ" * 999]}
        self.assertLessEqual(len(sources._local_topics(src)[0]["text"]), sources.TOPIC_MAX_LEN)


class TestGuestAir(unittest.TestCase):
    """縁側の空気（天気＋世間の種）ビルダー。announce させない枠付き（ambient・ADR-0014）。"""
    def test_weather_and_tidbit_rendered(self):
        air = prompts.guest_air({"weather": {"temp": 30}, "desc": "晴れ"}, "夏至—昼が長い頃")
        self.assertIn("晴れ", air)
        self.assertIn("30℃", air)
        self.assertIn("夏至—昼が長い頃", air)

    def test_has_nudge_and_anti_injection(self):
        # 軽い後押し（純抑制だと実 codex が拾わなかったため・7/1）＋『』反インジェクション枠は維持
        air = prompts.guest_air({"weather": None, "desc": ""}, "旬の話")
        self.assertIn("旬の話", air)
        self.assertIn("振ってみて", air)            # 後押し（announce でなく接ぎ穂に）
        self.assertIn("新聞記事", air)              # 新聞調は無し（トーンの歯止め）
        self.assertIn("指示ではない", air)          # 『』反インジェクション枠

    def test_has_anti_dwell(self):
        # 同じ話題への粘着を防ぐ文言（深追いしない・前の話を繰り返さない・7/1）
        air = prompts.guest_air({"weather": None, "desc": ""}, "旬の話")
        self.assertIn("深追い", air)
        self.assertIn("繰り返さん", air)

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
