"""daynight.layers の純関数（ADR-0028）: 時刻→背景の昼夜 tint/glow。

見た目（乗算/加算の膜の絵）はユーザー目視。ここは色を出す純関数の値域・キーフレーム・連続性を担保。
"""
import datetime
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import daynight


def at(h, m=0, s=0):
    return datetime.datetime(2026, 7, 4, h, m, s)


class TestDayNightKeyframes(unittest.TestCase):
    def test_noon_is_white_and_dark_glow_off(self):
        d = daynight.layers(at(12))
        self.assertEqual(d["tint"], "rgb(255,255,255)")   # 昼=白＝乗算しても無変化
        self.assertEqual(d["glow"], 0.0)                  # 月明かり消灯
        self.assertEqual(d["lamp"], 0.0)                  # 昼は室内灯も消灯

    def test_midnight_is_blue_grey_and_glow_full(self):
        d = daynight.layers(at(0))
        self.assertEqual(d["tint"], "rgb(99,130,163)")    # 夜=青灰（寒色）
        self.assertEqual(d["glow"], 1.0)                  # 月明かり満
        self.assertEqual(d["lamp"], 1.0)                  # 夜は室内灯も満（部屋から明かり）

    def test_lamp_lights_at_dusk_and_off_midday(self):
        self.assertEqual(daynight.layers(at(14))["lamp"], 0.0)     # 昼は消灯
        self.assertGreater(daynight.layers(at(18))["lamp"], 0.0)   # 夕＝灯り点き始め
        self.assertGreater(daynight.layers(at(20))["lamp"],        # 夜に向けて強まる
                           daynight.layers(at(18))["lamp"])

    def test_night_2100_matches_midnight(self):
        self.assertEqual(daynight.layers(at(21)), daynight.layers(at(0)))

    def test_sunset_is_warm_peach(self):
        d = daynight.layers(at(18))
        self.assertEqual(d["tint"], "rgb(252,217,191)")   # 夕焼け=桃の暖色
        self.assertEqual(d["glow"], 0.2)


class TestDayNightRanges(unittest.TestCase):
    _rgb = re.compile(r"^rgb\((\d+),(\d+),(\d+)\)$")

    def test_all_minutes_valid(self):
        """1日の全分で tint∈0..255・glow/lamp∈0..1・書式が壊れない（補間の破綻検知）。"""
        for total in range(0, 24 * 60):
            d = daynight.layers(at(total // 60, total % 60))
            for ch in ("glow", "lamp"):
                self.assertTrue(0.0 <= d[ch] <= 1.0, (total, ch, d[ch]))
            mo = self._rgb.match(d["tint"])
            self.assertIsNotNone(mo, (total, d["tint"]))
            for c in mo.groups():
                self.assertTrue(0 <= int(c) <= 255, (total, c))

    def test_continuous_across_midnight(self):
        """23:59 と 0:00 が同色＝日付をまたいでも段差なし（キーフレーム両端が同じ夜色）。"""
        self.assertEqual(daynight.layers(at(23, 59)), daynight.layers(at(0, 0)))

    def test_seconds_interpolate(self):
        """秒でも連続的に混ざる（正午ちょうどとその手前で glow はどちらも 0＝午後へ向け滑らか）。"""
        self.assertEqual(daynight.layers(at(14))["glow"], 0.0)   # 昼〜午後は消灯のまま
        # 夜明け前（3:00）は真夜中(glow1.0)と朝(0.08)の間＝0..1 に収まる中間値
        g = daynight.layers(at(3))["glow"]
        self.assertTrue(0.0 < g < 1.0, g)


class TestParseTime(unittest.TestCase):
    def test_hh_mm(self):
        self.assertEqual(daynight.parse_time("18:30"), 18 * 60 + 30)

    def test_hh_only(self):
        self.assertEqual(daynight.parse_time("18"), 18 * 60)

    def test_zero(self):
        self.assertEqual(daynight.parse_time("0:00"), 0)

    def test_invalid(self):
        for bad in ("", "24:00", "18:60", "abc", "-1", "9:9x", None):
            self.assertIsNone(daynight.parse_time(bad), bad)


class TestParseOverride(unittest.TestCase):
    def test_empty_is_show(self):
        self.assertEqual(daynight.parse_override("")["mode"], "show")
        self.assertEqual(daynight.parse_override(None)["mode"], "show")

    def test_enable_aliases(self):
        for s in ("on", "enable", "ON", "有効"):
            self.assertEqual(daynight.parse_override(s)["mode"], "enable", s)

    def test_disable_aliases(self):
        for s in ("off", "disable", "OFF", "無効"):
            self.assertEqual(daynight.parse_override(s)["mode"], "disable", s)

    def test_auto_aliases(self):
        for s in ("auto", "now", "real", "解除"):
            self.assertEqual(daynight.parse_override(s)["mode"], "auto", s)

    def test_pin(self):
        spec = daynight.parse_override("18:30")
        self.assertEqual(spec, {"mode": "pin", "minute": 18 * 60 + 30})

    def test_bad(self):
        self.assertEqual(daynight.parse_override("25:00")["mode"], "bad")
        self.assertEqual(daynight.parse_override("xyz")["mode"], "bad")

    def test_demo_defaults(self):
        spec = daynight.parse_override("demo")
        self.assertEqual(spec["mode"], "demo")
        self.assertEqual((spec["from"], spec["to"]), (daynight.DEMO_FROM, daynight.DEMO_TO))
        self.assertEqual(spec["secs"], daynight.DEMO_SECS)

    def test_demo_custom_secs_only(self):
        spec = daynight.parse_override("demo 60")
        self.assertEqual(spec["secs"], 60)
        self.assertEqual((spec["from"], spec["to"]), (daynight.DEMO_FROM, daynight.DEMO_TO))

    def test_demo_custom_range_and_secs(self):
        spec = daynight.parse_override("demo 16:00 22:00 50")
        self.assertEqual((spec["from"], spec["to"], spec["secs"]), (960, 1320, 50))


class TestOverrideMinute(unittest.TestCase):
    def test_progresses_from_start_to_end(self):
        spec = {"mode": "demo", "from": 960, "to": 1320, "secs": 40}
        self.assertEqual(daynight.override_minute(spec, 0), 960)          # 開始＝from
        self.assertEqual(daynight.override_minute(spec, 20), 1140)        # 半分＝中間(19:00)
        self.assertAlmostEqual(daynight.override_minute(spec, 39.9), 1320, delta=2)

    def test_expires_returns_none(self):
        spec = {"mode": "demo", "from": 960, "to": 1320, "secs": 40}
        self.assertIsNone(daynight.override_minute(spec, 40))             # ちょうど＝終了
        self.assertIsNone(daynight.override_minute(spec, 999))

    def test_non_demo_is_none(self):
        self.assertIsNone(daynight.override_minute({"mode": "pin", "minute": 100}, 0))
        self.assertIsNone(daynight.override_minute(None, 0))


class TestEffectiveLayers(unittest.TestCase):
    def test_no_spec_is_real_time(self):
        day, expired = daynight.effective_layers(None, at(12), 0)
        self.assertEqual(day, daynight.layers(at(12)))
        self.assertFalse(expired)

    def test_pin_uses_minute_not_now(self):
        day, expired = daynight.effective_layers({"mode": "pin", "minute": 18 * 60}, at(9), 0)
        self.assertEqual(day, daynight.layers_for_minute(18 * 60))        # now=9時でも 18:00 の色
        self.assertFalse(expired)

    def test_demo_midway_uses_virtual_minute(self):
        spec = {"mode": "demo", "from": 960, "to": 1320, "secs": 40}
        day, expired = daynight.effective_layers(spec, at(9), 20)
        self.assertEqual(day, daynight.layers_for_minute(1140))           # 経過半分＝19:00
        self.assertFalse(expired)

    def test_demo_expired_falls_back_to_real_with_flag(self):
        spec = {"mode": "demo", "from": 960, "to": 1320, "secs": 40}
        day, expired = daynight.effective_layers(spec, at(7), 40)
        self.assertEqual(day, daynight.layers(at(7)))                     # 実時間(朝)の色へ
        self.assertTrue(expired)                                         # View に「戻せ」の合図


class TestLayersForMinuteAndFormat(unittest.TestCase):
    def test_wraps_1440_to_zero(self):
        self.assertEqual(daynight.layers_for_minute(1440), daynight.layers_for_minute(0))

    def test_format_minute(self):
        self.assertEqual(daynight.format_minute(0), "00:00")
        self.assertEqual(daynight.format_minute(18 * 60 + 30), "18:30")
        self.assertEqual(daynight.format_minute(1440), "00:00")          # 折り返し


if __name__ == "__main__":
    unittest.main()
