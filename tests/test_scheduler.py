"""Scheduler のユーザー入力経路（CaptureView＋fake resident）と、
箱庭アーク／客人来訪の最小ライフサイクル。ネットワーク・実 ACP は使わない。"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import scheduler as sched
import sources
import views
from tests.test_game import FakeGame   # ゲーム配線テスト用の依存ゼロ・アダプタ


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


class TestParseAddr(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(sched._parse_addr("\x00guest\x00やあ"), ("guest", "やあ"))
        self.assertEqual(sched._parse_addr("\x00both\x00みな"), ("both", "みな"))

    def test_no_marker(self):
        self.assertEqual(sched._parse_addr("やあ"), (None, "やあ"))

    def test_empty_to_is_none(self):
        self.assertEqual(sched._parse_addr("\x00\x00やあ"), (None, "やあ"))


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

    async def test_explicit_addressee_marker_clean_body(self):
        # web チップ=客人（C方式）: 本文に「客人さん、」を混ぜずメタデータで客人へ振り分け
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        s.view.events.clear()
        await s.on_user_input("\x00guest\x00こんばんは")
        self.assertEqual([sp for sp, _ in self._says(s.view)],
                         ["近所の物知りなご隠居", "茶々"])         # 客人→もう片方（最大2手）
        self.assertTrue(any("こんばんは" in p for p in created[0].prompts))
        self.assertFalse(any("客人さん、" in p for p in created[0].prompts))   # 本文クリーン

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


class TestGameMode(unittest.IsolatedAsyncioTestCase):
    """ゲームモード（ADR-0017 Inc3/A）の配線: 実 rlcard/LLM 無しで FakeGame＋fake codex で検証。
    A＝基本 私＋茶々（観戦は茶々のみ）＝客人 codex を呼ばない。足りない時だけ客人で埋める。"""
    def _sched(self, created, make=None):
        s = sched.Scheduler(FakeResident(), [], sources.WeatherSource(),
                            views.CaptureView(), spawn_codex=_codex_factory(created))
        s._make_game = make or (lambda gid, n: FakeGame(n))   # rlcard を使わせない（人数は尊重）
        return s

    @staticmethod
    def _systems(v):
        return [m for (t, m, _l) in v.events if t == "system"]

    @staticmethod
    def _game_events(v):
        return [t for (t, _a, _b) in v.events if t in ("game_open", "game_update", "game_close")]

    async def test_game_window_opens_and_persists(self):
        # 開始で game_open→game_update（配り）。終局しても **窓は閉じない**（ユーザーが×で閉じる）
        created = []
        s = self._sched(created)
        await s._start_game("blackjack", watch=True)
        evs = self._game_events(s.view)
        self.assertEqual(evs[0], "game_open")
        self.assertIn("game_update", evs)
        for _ in range(6):
            if s.game is None:
                break
            await s._tick(sources.build_context(None, []))
        self.assertIsNone(s.game)                        # 対局自体は終わる
        self.assertNotIn("game_close", self._game_events(s.view))  # でも観戦窓は開いたまま

    async def test_play_is_human_plus_chacha_no_guest(self):
        created = []
        s = self._sched(created)
        await s._start_game("blackjack", watch=False)
        self.assertIsNotNone(s.game)
        self.assertEqual(len(created), 0)                # 私+茶々のみ＝客人 codex を呼ばない（A）
        self.assertEqual(s.game.adapter.num_players, 2)
        self.assertTrue(s.game.waiting_for_human)        # slot0=私 → 入力待ち
        await s.on_user_input("hi")                      # 私が hi（FakeGame の合法手）
        for _ in range(6):                               # tick で 茶々 が打って終局
            if s.game is None:
                break
            await s._tick(sources.build_context(None, []))
        self.assertIsNone(s.game)

    async def test_watch_is_chacha_solo_no_guest(self):
        created = []
        s = self._sched(created)
        await s._start_game("blackjack", watch=True)
        self.assertEqual(len(created), 0)                # 茶々がディーラーと＝客人なし
        self.assertEqual(s.game.adapter.num_players, 1)
        self.assertFalse(s.game.waiting_for_human)       # 全AI（茶々のみ）
        for _ in range(6):
            if s.game is None:
                break
            await s._tick(sources.build_context(None, []))
        self.assertIsNone(s.game)

    async def test_fills_guests_only_when_game_needs_more(self):
        # 人数が足りないゲーム（3人固定）の時だけ客人で埋め、終局で破棄
        created = []
        s = self._sched(created, make=lambda gid, n: FakeGame(3))
        await s._start_game("blackjack", watch=False)    # 私+茶々=2 だが 3人ゲーム → 客人1
        self.assertEqual(len(created), 1)
        await s.on_user_input("hi")
        for _ in range(8):
            if s.game is None:
                break
            await s._tick(sources.build_context(None, []))
        self.assertIsNone(s.game)
        self.assertTrue(created[0].closed)               # 埋めた客人 codex を破棄

    async def test_second_game_rejected(self):
        created = []
        s = self._sched(created)
        await s._start_game("blackjack", watch=True)
        s.view.events.clear()
        await s._start_game("blackjack", watch=True)     # 二重開始は拒否
        self.assertTrue(any("ゲーム中" in (m or "") for m in self._systems(s.view)))

    async def test_input_during_ai_turn_is_held(self):
        created = []
        s = self._sched(created)
        await s._start_game("blackjack", watch=True)     # 全AI＝人間の番でない
        s.view.events.clear()
        await s.on_user_input("hi")
        self.assertTrue(any("他のプレイヤーの番" in (m or "") for m in self._systems(s.view)))


if __name__ == "__main__":
    unittest.main()
