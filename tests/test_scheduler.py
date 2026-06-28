"""Scheduler のユーザー入力経路（CaptureView＋fake resident）と、
箱庭アーク／客人来訪の最小ライフサイクル。ネットワーク・実 ACP は使わない。"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scheduler as sched
import sources
import views


class FakeResident:
    """AcpAgent の代役。prompt/cancel/close を記録するだけ。"""
    def __init__(self):
        self.prompts = []
        self.cancels = 0
        self.closed = False
        self.model = None           # 我々が要求したモデル（/model 表示で使う）
        self.reported_model = None  # アダプタ報告の実モデル（同上・実 AcpAgent と同じ属性）

    async def prompt(self, text, on_chunk=None):
        self.prompts.append(text)
        if on_chunk:
            on_chunk("ええ天気やな")
        return "ええ天気やな"

    async def cancel(self):
        self.cancels += 1

    async def close(self):
        self.closed = True


def _make():
    resident = FakeResident()
    view = views.CaptureView()
    s = sched.Scheduler(resident, [], sources.WeatherSource(), view)
    return s, resident, view


def _starts(view):
    return [k for (t, k, _l) in view.events if t == "start"]


def _systems(view):
    return [m for (t, m, _l) in view.events if t == "system"]


class TestUserInput(unittest.IsolatedAsyncioTestCase):
    async def test_plain_text_injects_user_turn(self):
        s, r, v = _make()
        await s.on_user_input("こんにちは")
        self.assertEqual(len(r.prompts), 1)
        self.assertIn("こんにちは", r.prompts[0])
        self.assertIn("user", _starts(v))

    async def test_empty_input_ignored(self):
        s, r, v = _make()
        await s.on_user_input("   ")
        self.assertEqual(len(r.prompts), 0)

    async def test_cancel_priority_when_speaking(self):
        s, r, v = _make()
        s.speaking = True                 # つぶやき進行中を擬似
        await s.on_user_input("おーい")
        self.assertEqual(r.cancels, 1)    # cancel 優先（ADR-0006）

    async def test_quit_sets_stop(self):
        s, r, v = _make()
        await s.on_user_input("/quit")
        self.assertTrue(s.stop.is_set())

    async def test_help_emits_system(self):
        s, r, v = _make()
        await s.on_user_input("/help")
        self.assertTrue(_systems(v))

    async def test_unknown_command_is_handled(self):
        s, r, v = _make()
        await s.on_user_input("/nope")
        self.assertTrue(any("作法" in (m or "") for m in _systems(v)))
        self.assertEqual(len(r.prompts), 0)   # 茶々には流さない

    async def test_model_command_shows_requested(self):
        s, r, v = _make()
        r.model = "claude-opus-4-8"            # ENGAWA_MODEL 指定相当・アダプタ報告は無し
        await s.on_user_input("/model")
        systems = _systems(v)
        self.assertTrue(any("claude-opus-4-8" in (m or "") for m in systems))  # 指定値を表示
        self.assertTrue(any("codex" in (m or "") for m in systems))            # 客人(codex)行も
        self.assertEqual(len(r.prompts), 0)   # 縁側操作＝茶々には流さない

    async def test_model_command_prefers_reported(self):
        s, r, v = _make()
        r.model = "claude-opus-4-8"
        r.reported_model = "Claude Opus（opus）"   # アダプタ報告（真実）があれば優先
        await s.on_user_input("/model")
        self.assertTrue(any("アダプタ報告" in (m or "") for m in _systems(v)))

    async def test_model_command_unknown_when_unset(self):
        s, r, v = _make()                      # model/reported とも None
        await s.on_user_input("/model")
        self.assertTrue(any("不明" in (m or "") for m in _systems(v)))


class TestArcAndGuest(unittest.IsolatedAsyncioTestCase):
    async def test_single_phase_arc_concludes(self):
        arc = sources.BoxGardenArc("風", gate=lambda c: True,
                                   phases=[sources.Phase("単", "風が鳴った")])
        ctx = sources.build_context(None)
        first = await arc.next_phase(ctx)
        self.assertIsInstance(first, sources.Narration)
        self.assertEqual(first.kind, "arc")
        self.assertIsNone(await arc.next_phase(ctx))   # 次は結了(None)

    async def test_guest_visit_three_beats_then_dispose(self):
        created = []

        class FakeCodex:
            def __init__(self):
                self.closed = False

            async def prompt(self, text, on_chunk=None):
                return "ごめんやす"

            async def close(self):
                self.closed = True

        async def spawn():
            c = FakeCodex()
            created.append(c)
            return c

        g = sources.GuestSource(persona="ご隠居", spawn_codex=spawn, max_turns=3)
        g.reset()
        ctx = sources.build_context(None, topics=[])
        beats = []
        for _ in range(6):
            res = await g.next_phase(ctx)
            if res is None:
                break
            beats.append(res)
        self.assertEqual(len(beats), 3)                       # 到着/世間/辞去
        self.assertTrue(all(b.kind == "guest" for b in beats))
        self.assertTrue(all(b.voice for b in beats))          # 生セリフを表示へ載せる
        self.assertTrue(created and created[0].closed)        # 辞去で codex 破棄（使い捨て）


if __name__ == "__main__":
    unittest.main()
