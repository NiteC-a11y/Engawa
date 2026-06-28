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

    async def test_model_command_guest_live_reported(self):
        s, r, v = _make()

        class _LiveGuest:                      # 来訪中の客人を擬似（key=guest・live agent が報告あり）
            key = "guest"

            def __init__(self):
                self.agent = type("A", (), {"reported_model": "gpt-5-codex（Codex）"})()

        s.active = _LiveGuest()
        await s.on_user_input("/model")
        systems = _systems(v)
        self.assertTrue(any("来訪中・アダプタ報告" in (m or "") for m in systems))  # live 報告を優先
        self.assertTrue(any("gpt-5-codex" in (m or "") for m in systems))


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


def _codex_factory(created, line="ごめんやす"):
    class FakeCodex:
        def __init__(self):
            self.closed = False
            self.reported_model = None
            self.prompts = []

        async def prompt(self, text, on_chunk=None):
            self.prompts.append(text)
            return line

        async def close(self):
            self.closed = True

    async def spawn():
        c = FakeCodex()
        created.append(c)
        return c
    return spawn


class TestThreeWayRoom(unittest.IsolatedAsyncioTestCase):
    """3人会話の部屋（ADR-0015 Inc2）: Scheduler↔Room の統合。fake codex＋CaptureView で実 ACP 不要。"""
    def _scheduler(self, created):
        return sched.Scheduler(FakeResident(), [], sources.WeatherSource(),
                               views.CaptureView(), spawn_codex=_codex_factory(created))

    @staticmethod
    def _says(v):
        return [(sp, t) for (typ, sp, t) in v.events if typ == "say"]

    async def test_summon_opens_room_and_greets(self):
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        self.assertIsNotNone(s.room)
        self.assertFalse(s.room.closed)
        speakers = [sp for sp, _ in self._says(s.view)]
        self.assertIn("近所の物知りなご隠居", speakers)        # 客人の到着挨拶
        self.assertIn("茶々", speakers)                        # 茶々の反応
        self.assertEqual(len(created), 1)                      # codex 1体だけ spawn

    async def test_human_to_guest_two_turns(self):
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        s.view.events.clear()
        await s.on_user_input("ご隠居どう思う?")
        self.assertEqual([sp for sp, _ in self._says(s.view)],
                         ["近所の物知りなご隠居", "茶々"])      # 宛先=客人→もう片方=茶々（最大2手）
        self.assertFalse(s.room.closed)

    async def test_human_default_to_resident(self):
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        s.view.events.clear()
        await s.on_user_input("ええ天気やね")                  # 名前なし＝既定は茶々
        self.assertEqual([sp for sp, _ in self._says(s.view)],
                         ["茶々", "近所の物知りなご隠居"])

    async def test_codex_hears_human_and_chacha(self):
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        await s.on_user_input("ご隠居、最近どう?")
        prompts = created[0].prompts
        self.assertTrue(any("最近どう" in p for p in prompts))  # 双方向: codex に人間の発話が届く
        self.assertTrue(any("ここまでのやり取り" in p for p in prompts))  # transcript 同梱

    async def test_room_closes_during_lock_wait_no_crash(self):
        # 辞去レース回帰: on_user_input が drive_lock 待ちの間に tick が部屋を閉じても落ちず通常入力へ
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        before = len(s.resident.prompts)
        gate = asyncio.Event()

        async def holder():                       # tick の「沈黙→辞去」を擬似（ロックを握って部屋を閉じる）
            async with s.drive_lock:
                gate.set()
                await asyncio.sleep(0.03)         # on_user_input を drive_lock 待ちに入らせる隙
                await s._end_visit()              # self.room=None・codex 破棄

        h = asyncio.create_task(holder())
        await gate.wait()                         # holder がロックを保持したのを確認
        await s.on_user_input("おーい")           # outer check は通過→ロック待ち→内側で room=None を検知して落とす
        await h
        self.assertIsNone(s.room)                 # 部屋は閉じている
        self.assertTrue(created[0].closed)        # codex 破棄済み
        self.assertGreater(len(s.resident.prompts), before)   # 通常の茶々への話しかけに落ちた（例外なし）

    async def test_silence_makes_guest_leave_and_dispose(self):
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        for _ in range(8):
            await s._tick(sources.build_context(None, []))
            if s.room is None:
                break
        self.assertIsNone(s.room)                              # 辞去して部屋が閉じた（有界）
        self.assertIsNone(s.active)
        self.assertTrue(created[0].closed)                    # codex 破棄（使い捨て・ADR-0008）


if __name__ == "__main__":
    unittest.main()
