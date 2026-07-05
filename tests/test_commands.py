"""commands: CommandRouter と /font /daynight の単体（ADR-0029 Phase 1）。

既存の統合テスト（test_scheduler の TestFontCommand/TestDayNightCommand）は緑のまま
＝振る舞い不変の characterization。ここは抽出した Command/Router を単体で確認する
（深い挙動＝save 永続化 等は統合テスト側に任せ、ここは Router 機構と配線に絞る）。
"""
import sys
import unittest

import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import commands
import views


def _systems(v):
    return [m for (t, m, _l) in v.events if t == "system"]


class TestCommandRouter(unittest.IsolatedAsyncioTestCase):
    def test_has_known_and_aliases_case_insensitive(self):
        r = commands.default_router()
        self.assertTrue(r.has("/font"))
        self.assertTrue(r.has("/daynight"))
        self.assertTrue(r.has("/tod"))          # 別名
        self.assertTrue(r.has("/FONT"))         # 大小無視
        self.assertFalse(r.has("/nope"))

    async def test_dispatch_unknown_returns_false(self):
        v = views.CaptureView()
        ctx = commands.CommandContext(v)
        handled = await commands.default_router().dispatch(ctx, "/nope", ["/nope"])
        self.assertFalse(handled)               # 未登録＝呼び側にフォールバックさせる
        self.assertEqual(_systems(v), [])       # 何も出さない

    async def test_dispatch_runs_registered(self):
        v = views.CaptureView()
        ctx = commands.CommandContext(v)
        handled = await commands.default_router().dispatch(ctx, "/font 1.4", ["/font", "1.4"])
        self.assertTrue(handled)
        self.assertEqual(v.current_font(), 1.4)


class TestFontCommandUnit(unittest.IsolatedAsyncioTestCase):
    async def test_no_arg_shows_current(self):
        v = views.CaptureView()
        await commands.FontCommand().run(commands.CommandContext(v), "/font", ["/font"])
        self.assertTrue(any("今の文字サイズ" in (m or "") for m in _systems(v)))

    async def test_number_applies_and_clamps(self):
        v = views.CaptureView()
        await commands.FontCommand().run(commands.CommandContext(v), "/font 9", ["/font", "9"])
        self.assertEqual(v.current_font(), commands.FONT_MAX)   # 上限へクランプ

    async def test_console_is_noop(self):
        v = views.ConsoleView()                 # current_font() is None ＝ web 専用
        await commands.FontCommand().run(commands.CommandContext(v), "/font 1.4", ["/font", "1.4"])
        # 例外なく早期 return（注記のみ）＝ここでは落ちないことを確認


class TestDayNightCommandUnit(unittest.IsolatedAsyncioTestCase):
    async def test_pin_sets_override(self):
        v = views.CaptureView()
        await commands.DayNightCommand().run(
            commands.CommandContext(v), "/daynight 18:30", ["/daynight", "18:30"])
        self.assertEqual(v.current_daynight(), {"mode": "pin", "minute": 18 * 60 + 30})

    async def test_alias_tod_shows_state(self):
        v = views.CaptureView()
        await commands.DayNightCommand().run(commands.CommandContext(v), "/tod", ["/tod"])
        self.assertTrue(any("実時間" in (m or "") for m in _systems(v)))

    async def test_console_is_noop(self):
        v = views.ConsoleView()                 # current_daynight() is None ＝ web 専用
        await commands.DayNightCommand().run(
            commands.CommandContext(v), "/daynight 18:30", ["/daynight", "18:30"])
        # 例外なく早期 return


if __name__ == "__main__":
    unittest.main()
