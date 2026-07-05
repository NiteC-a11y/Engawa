"""Scheduler のユーザー入力経路（CaptureView＋fake resident）と、
箱庭アーク／客人来訪の最小ライフサイクル。ネットワーク・実 ACP は使わない。"""
import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import acp
import config
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


class TimeoutResident:
    """prompt が必ず ACPTimeoutError を投げる住人（無応答 adapter の代役）。"""
    def __init__(self):
        self.closed = False
        self.model = None
        self.reported_model = None

    async def prompt(self, text, on_chunk=None):
        raise acp.ACPTimeoutError("resident timeout")

    async def cancel(self):
        pass

    async def close(self):
        self.closed = True


class ErrorResident:
    """prompt が timeout 以外の例外を投げる住人（adapter 死亡=ConnectionError／不正状態 の代役）。"""
    def __init__(self, exc=None):
        self.closed = False
        self.model = None
        self.reported_model = None
        self._exc = exc or RuntimeError("boom")

    async def prompt(self, text, on_chunk=None):
        raise self._exc

    async def cancel(self):
        pass

    async def close(self):
        self.closed = True


class FakeArc:
    """箱庭アークの代役。起→承→転→結を順に返す（key で /arc から選べる）。"""
    def __init__(self, key="雀"):
        self.key = key
        self.cooldown_ticks = 3
        self.reset_calls = 0
        self._phases = ["起", "承", "転", "結"]
        self._i = 0

    def reset(self):
        self.reset_calls += 1
        self._i = 0

    def eligible(self, ctx):
        return True

    async def next_phase(self, ctx):
        if self._i >= len(self._phases):
            return None
        ph = self._phases[self._i]; self._i += 1
        return sources.Narration(ph, "arc")

    async def close(self):
        pass


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


class TestFontCommand(unittest.IsolatedAsyncioTestCase):
    """/font: アプリ内で文字サイズをライブ調整（明示保存方式）。縁側操作＝茶々に流さない（ADR-0007）。"""
    async def test_font_no_arg_shows_current(self):
        s, r, v = _make()
        await s.on_user_input("/font")
        self.assertTrue(any("今の文字サイズ" in (m or "") for m in _systems(v)))
        self.assertEqual(len(r.prompts), 0)                 # 茶々には流さない

    async def test_font_number_applies_live(self):
        s, r, v = _make()
        await s.on_user_input("/font 1.4")
        self.assertEqual(v.current_font(), 1.4)             # view にライブ適用
        self.assertIn(("set_font", 1.4, None), v.events)
        self.assertEqual(len(r.prompts), 0)

    async def test_font_clamped_to_bounds(self):
        s, r, v = _make()
        await s.on_user_input("/font 9")                    # 上限 2.2 に寄せる
        self.assertEqual(v.current_font(), sched.UI_FONT_MAX)

    async def test_font_non_number_rejected(self):
        s, r, v = _make()
        await s.on_user_input("/font おおきく")
        self.assertEqual(v.current_font(), 1.0)             # 変わらない
        self.assertTrue(any("数字で" in (m or "") for m in _systems(v)))

    async def test_font_save_persists(self):
        import tempfile
        tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False); tf.close()
        saved_env, saved_cfg = os.environ.get("ENGAWA_CONFIG"), config._CFG
        os.environ["ENGAWA_CONFIG"] = tf.name
        os.environ.pop("ENGAWA_UI_FONT", None)              # env 優先の注記を出さない条件で確認
        config._CFG = None
        try:
            s, r, v = _make()
            await s.on_user_input("/font 1.6")
            await s.on_user_input("/font save")
            self.assertEqual(config.get_float("ABSENT", "ui", "font", 1.0), 1.6)   # 書き戻し確認
            self.assertTrue(any("保存した" in (m or "") for m in _systems(v)))
        finally:
            if saved_env is None:
                os.environ.pop("ENGAWA_CONFIG", None)
            else:
                os.environ["ENGAWA_CONFIG"] = saved_env
            config._CFG = saved_cfg
            os.remove(tf.name)


class TestDayNightCommand(unittest.IsolatedAsyncioTestCase):
    """/daynight: 背景の昼夜プレビュー（固定/早送り/実時間へ・ADR-0028）。縁側操作＝茶々に流さない（ADR-0007）。"""
    async def test_pin_sets_view_and_does_not_prompt_chacha(self):
        s, r, v = _make()
        await s.on_user_input("/daynight 18:30")
        self.assertEqual(v.current_daynight(), {"mode": "pin", "minute": 18 * 60 + 30})
        self.assertTrue(any("18:30" in (m or "") for m in _systems(v)))
        self.assertEqual(len(r.prompts), 0)                 # 茶々には流さない

    async def test_demo_announces_sweep(self):
        s, r, v = _make()
        await s.on_user_input("/daynight demo")
        self.assertEqual(v.current_daynight()["mode"], "demo")
        self.assertTrue(any("移ろい" in (m or "") for m in _systems(v)))

    async def test_auto_returns_to_real(self):
        s, r, v = _make()
        await s.on_user_input("/daynight 21:00")
        await s.on_user_input("/daynight auto")          # プレビュー解除
        self.assertEqual(v.current_daynight(), {"mode": "real"})

    async def test_no_arg_shows_state(self):
        s, r, v = _make()
        await s.on_user_input("/daynight")
        self.assertTrue(any("実時間" in (m or "") for m in _systems(v)))

    async def test_bad_arg_rejected(self):
        s, r, v = _make()
        await s.on_user_input("/daynight 25:00")
        self.assertEqual(v.current_daynight(), {"mode": "real"})   # 固定されない
        self.assertTrue(any("使い方" in (m or "") for m in _systems(v)))

    async def test_on_off_toggles_live_and_persists(self):
        import tempfile
        tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False); tf.close()
        saved_env, saved_cfg = os.environ.get("ENGAWA_CONFIG"), config._CFG
        os.environ["ENGAWA_CONFIG"] = tf.name
        os.environ.pop("ENGAWA_DAYNIGHT", None)          # env 優先の注記を出さない条件で
        config._CFG = None
        try:
            s, r, v = _make()
            await s.on_user_input("/daynight off")
            self.assertFalse(v.daynight_enabled())                       # ライブ反映
            self.assertEqual(config.get_int("ABSENT", "ui", "daynight", 1), 0)   # 永続保存
            self.assertTrue(any("無効" in (m or "") for m in _systems(v)))
            await s.on_user_input("/daynight on")
            self.assertTrue(v.daynight_enabled())
            self.assertEqual(config.get_int("ABSENT", "ui", "daynight", 0), 1)
        finally:
            if saved_env is None:
                os.environ.pop("ENGAWA_CONFIG", None)
            else:
                os.environ["ENGAWA_CONFIG"] = saved_env
            config._CFG = saved_cfg
            os.remove(tf.name)

    async def test_preview_when_disabled_prompts_enable(self):
        s, r, v = _make()
        v.set_daynight_enabled(False)                    # 機能オフ
        await s.on_user_input("/daynight 18:30")         # 無効中のプレビュー
        self.assertEqual(v.current_daynight(), {"mode": "real"})   # 固定しない
        self.assertTrue(any("無効" in (m or "") for m in _systems(v)))

    async def test_font_console_is_noop(self):
        # console（set_font 非対応）は端末フォント依存＝no-op で注記のみ
        r = FakeResident()
        v = views.ConsoleView()
        s = sched.Scheduler(r, [], sources.WeatherSource(), v)
        printed = []
        v.system = lambda m: printed.append(m)
        await s.on_user_input("/font 1.4")
        self.assertTrue(any("web 表示だけ" in (m or "") for m in printed))


class TestArcAndGuest(unittest.IsolatedAsyncioTestCase):
    async def test_single_phase_arc_concludes(self):
        arc = sources.BoxGardenArc("風", gate=lambda c: True,
                                   phases=[sources.Phase("単", "風が鳴った")])
        ctx = sources.build_context(None)
        first = await arc.next_phase(ctx)
        self.assertIsInstance(first, sources.Narration)
        self.assertEqual(first.kind, "arc")
        self.assertIsNone(await arc.next_phase(ctx))   # 次は結了(None)


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


class TestAutonomousGuestVisit(unittest.IsolatedAsyncioTestCase):
    """自発来訪は arc 抽選から独立に判定（prob が実効 per-tick 率・arc と競合しない）。
    以前は arc_prob × prob × 競合 で三重に間引かれ『殆ど来ない』だった（ユーザー報告 6/29）。"""
    def _sched(self, created):
        spawn = _codex_factory(created)
        return sched.Scheduler(FakeResident(), sources.default_sources(spawn_codex=spawn),
                               sources.WeatherSource(), views.CaptureView(), spawn_codex=spawn)

    def _force(self, prob, from_hour, arc_prob):
        saved = (sources.GUEST_VISIT_PROB, sources.GUEST_VISIT_FROM_HOUR, sched.ARC_START_PROB)
        sources.GUEST_VISIT_PROB = prob
        sources.GUEST_VISIT_FROM_HOUR = from_hour
        sched.ARC_START_PROB = arc_prob
        return saved

    def _restore(self, saved):
        sources.GUEST_VISIT_PROB, sources.GUEST_VISIT_FROM_HOUR, sched.ARC_START_PROB = saved

    async def test_visits_independent_of_arc_lottery(self):
        created = []
        s = self._sched(created)
        saved = self._force(prob=1.0, from_hour=0, arc_prob=0.0)   # arc 抽選は絶対外す
        try:
            ctx = sources.build_context(None, []); ctx["hour"] = 12
            await s._tick(ctx)                       # arc_prob=0 でも来訪する＝抽選と無関係に独立判定
            self.assertIsNotNone(s.room)             # 部屋が開いた
            self.assertEqual(len(created), 1)        # codex を spawn
        finally:
            self._restore(saved)
            if s.room is not None:
                await s._end_visit()                 # 後始末（使い捨て codex を破棄）

    async def test_respects_from_hour(self):
        created = []
        s = self._sched(created)
        saved = self._force(prob=1.0, from_hour=15, arc_prob=0.0)
        try:
            ctx = sources.build_context(None, []); ctx["hour"] = 9   # from_hour 前 → 来ない
            await s._tick(ctx)
            self.assertIsNone(s.room)
            self.assertEqual(len(created), 0)
        finally:
            self._restore(saved)

    async def test_respects_cooldown(self):
        created = []
        s = self._sched(created)
        s.cooldowns["guest"] = 5                     # 来訪直後相当（クールダウン中）
        saved = self._force(prob=1.0, from_hour=0, arc_prob=0.0)
        try:
            ctx = sources.build_context(None, []); ctx["hour"] = 12
            await s._tick(ctx)                       # prob=1 でも cooldown 中は来ない（連続来訪を防ぐ）
            self.assertIsNone(s.room)
            self.assertEqual(len(created), 0)
        finally:
            self._restore(saved)


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

    async def test_topic_rides_in_air_on_guest_reply(self):
        # ADR-0014 部屋経路復活: 会話中(REPLY/CHIME)は“種”が空気に混じり codex prompt に届く。
        # 到着挨拶(ARRIVE)には入らない（CHIME/REPLY 限定ゲート）。
        created = []
        s = self._scheduler(created)
        s.weather = {"desc": "晴れ", "temp": 30}
        s.topics = [{"text": "夏至—一年で最も昼が長い頃", "tone": "季節", "source": "時節"}]
        saved = sources.TOPIC_PROB
        sources.TOPIC_PROB = 1.0                                   # 必ず種を空気へ（発火判定は fake なので確定）
        try:
            await s._summon_guest("近所の物知りなご隠居")           # ARRIVE（種なし）
            arrive_prompt = created[0].prompts[0]
            self.assertNotIn("夏至", arrive_prompt)                # 到着挨拶はクリーン
            await s.on_user_input("\x00guest\x00最近どう?")         # 客人へ→REPLY（種が空気に）
        finally:
            sources.TOPIC_PROB = saved
        self.assertTrue(any("夏至" in p for p in created[0].prompts))       # 種が届いた
        self.assertTrue(any("縁側の空気" in p for p in created[0].prompts))  # ambient ブロックとして

    async def test_debug_logs_seed_placed_and_skipped(self):
        # デバッグモード: 種を置いた/見送った判断が engawa.scheduler ログに出る（assertLogs で検証）。
        # 「LLM が拾うか」は目視だが「種を入れたか」は自動テストできる＝切り分けの土台。
        created = []
        s = self._scheduler(created)
        s.weather = {"desc": "晴れ"}
        s.topics = [{"text": "夏至—昼が長い頃", "tone": "季節", "source": "時節"}]
        await s._summon_guest("近所の物知りなご隠居")
        saved = (sources.TOPIC_PROB, sources.TOPIC_COOLDOWN)
        try:
            sources.TOPIC_PROB, sources.TOPIC_COOLDOWN = 1.0, 0    # cooldown 無しで prob 判定を素に見る
            with self.assertLogs("engawa.scheduler", level="DEBUG") as cm:
                await s.on_user_input("\x00guest\x00最近どう?")     # guest REPLY → 種を空気へ
            self.assertTrue(any("種を空気へ" in m and "夏至" in m for m in cm.output))
            sources.TOPIC_PROB = 0.0
            with self.assertLogs("engawa.scheduler", level="DEBUG") as cm2:
                await s.on_user_input("\x00guest\x00ほな")          # prob=0 → 種見送り
            self.assertTrue(any("種見送り: prob外れ" in m for m in cm2.output))
        finally:
            sources.TOPIC_PROB, sources.TOPIC_COOLDOWN = saved

    async def test_room_prompt_carries_current_time(self):
        # 時間感覚のズレ対策: 部屋プロンプトに「いまの縁側」（実時刻）が必ず入る（夜に夕暮れ発言を防ぐ）。
        created = []
        s = self._scheduler(created)
        await s._summon_guest("夕暮れに道を訪ねてきた旅人")
        self.assertTrue(any("いまの縁側" in p for p in created[0].prompts))   # 客人に実時刻アンカー
        self.assertTrue(any("今を優先" in p for p in created[0].prompts))     # persona の時間帯より今
        self.assertTrue(any("いまの縁側" in p for p in s.resident.prompts))   # 茶々にも同じ時刻

    async def test_topic_cooldown_spaces_out_seeds(self):
        # 同じ話題への粘着対策: 種を置いたら cooldown 分の客人ターンは種を見送る（毎ターン振らない）。
        created = []
        s = self._scheduler(created)
        s.weather = {"desc": "晴れ"}
        s.topics = [{"text": "夏至の話", "tone": "季節", "source": "時節"},
                    {"text": "旬の話", "tone": "季節", "source": "旬"}]
        await s._summon_guest("近所の物知りなご隠居")
        saved = (sources.TOPIC_PROB, sources.TOPIC_COOLDOWN)
        try:
            sources.TOPIC_PROB, sources.TOPIC_COOLDOWN = 1.0, 2      # 必ず種→以後2ターンは空ける
            with self.assertLogs("engawa.scheduler", level="DEBUG") as cm:
                for _ in range(3):                                   # 客人 REPLY を3回駆動
                    await s.on_user_input("\x00guest\x00どう?")
            placed = [m for m in cm.output if "種を空気へ" in m]
            cooled = [m for m in cm.output if "cooldown" in m]
            self.assertEqual(len(placed), 1)                         # 3ターンで種は1回だけ（毎ターンでない）
            self.assertEqual(len(cooled), 2)                         # 残り2ターンは cooldown で見送り
        finally:
            sources.TOPIC_PROB, sources.TOPIC_COOLDOWN = saved

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

    def _leave_bound(self):
        # 沈黙→辞去までの上限tick数。代打（ADR-0025）は回ごとに間隔が延びる（fill_after+n×slowdown）ので
        # その総和 ＋ 予算枯渇後の idle_leave ＋ 余裕。減速で長引く分を織り込んでも「必ず終端に着く（有界）」を保証。
        cap = sched.GUEST_FILL_CAP
        ramp = cap * sched.GUEST_FILL_AFTER + sched.GUEST_FILL_SLOWDOWN * cap * cap   # Σ(fill_after+n×slowdown) の安全な上界
        return ramp + sched.GUEST_IDLE_LEAVE_TICKS + 3

    async def test_silence_makes_guest_leave_and_dispose(self):
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        for _ in range(self._leave_bound()):                  # 沈黙が続けば（代打で長引いても）必ず辞去＝有界
            await s._tick(sources.build_context(None, []))
            if s.room is None:
                break
        self.assertIsNone(s.room)                              # 辞去して部屋が閉じた（有界）
        self.assertIsNone(s.active)
        self.assertTrue(created[0].closed)                    # codex 破棄（使い捨て・ADR-0008）

    async def test_guest_lingers_before_leaving(self):
        # すぐ帰らない＝沈黙しても代打で場をつなぎつつ居座る（せわしなさの解消・有界は維持・ADR-0025）
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        for _ in range(sched.GUEST_IDLE_LEAVE_TICKS):         # 旧しきい値ぶん沈黙しても
            await s._tick(sources.build_context(None, []))
        self.assertIsNotNone(s.room)                          # まだ居る（代打が入るので即辞去しない）
        for _ in range(self._leave_bound()):                  # 予算を使い切るまで続ければ最後は辞去
            await s._tick(sources.build_context(None, []))
            if s.room is None:
                break
        self.assertIsNone(s.room)

    async def test_resident_fills_in_during_silence(self):
        # 代打（ADR-0025）: 人間が黙っていても茶々が場を回す＝茶々↔客人の一言が実プロンプトで流れる
        created = []
        s = self._scheduler(created)
        await s._summon_guest("近所の物知りなご隠居")
        r_before, g_before = len(s.resident.prompts), len(created[0].prompts)
        for _ in range(sched.GUEST_FILL_AFTER + 1):           # 沈黙が代打しきい値を越える
            await s._tick(sources.build_context(None, []))
        self.assertTrue(any("席を外して" in p for p in s.resident.prompts))   # 茶々に MUSE（人間役の代打）が届いた
        self.assertGreater(len(s.resident.prompts), r_before)                 # 茶々が代打で喋った
        self.assertGreater(len(created[0].prompts), g_before)                 # 客人が茶々に返した
        self.assertIsNotNone(s.room)                                          # 1往復で人間待ちへ戻り、まだ在室

    async def test_logs_user_input_and_resident_inject(self):
        # 定量ログ（半日観測の土台）: 人間の入力と茶々ソロの発話が時刻付きで engawa.scheduler に出る
        created = []
        s = self._scheduler(created)
        with self.assertLogs("engawa.scheduler", level="DEBUG") as cm:
            await s.on_user_input("ええ天気やね")
        self.assertTrue(any("user input: ええ天気やね" in m for m in cm.output))   # 人間の入力時刻
        self.assertTrue(any("inject 茶々 (user)" in m for m in cm.output))         # 茶々ソロ応答の起点時刻


class TestGameMode(unittest.IsolatedAsyncioTestCase):
    """ゲームモード（ADR-0017 Inc3/A）の配線: 実 rlcard/LLM 無しで FakeGame＋fake codex で検証。
    A＝基本 私＋茶々（観戦は茶々のみ）＝客人 codex を呼ばない。足りない時だけ客人で埋める。"""
    def setUp(self):
        # ゲーム登録は composition root（engawa_main._build）の責務（A3・ADR-0017）。
        # テストは Scheduler を直接組むので、ここで registry を live と同じ状態に整える
        # （rlcard 未導入でも register は lambda 登録のみで成功。factory は _make_game 差し替えで呼ばれない）。
        import game_rlcard
        game_rlcard.register_rlcard_games()

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

    # ── 汎用 /game <id> [見る]（UNO/leduc を登録済みアダプタから起動）─────────────
    def _capture_make(self, s):
        calls = []
        s._make_game = lambda gid, n: (calls.append((gid, n)), FakeGame(n))[1]
        return calls

    async def test_game_command_starts_uno(self):
        s = self._sched([])
        calls = self._capture_make(s)
        await s.on_user_input("/game uno")            # 参加＝私+茶々（uno は2人固定）
        self.assertIsNotNone(s.game)
        self.assertEqual(calls[0], ("uno", 2))        # 登録済み uno が起動

    async def test_game_command_unknown_lists(self):
        s = self._sched([])
        await s.on_user_input("/game shogi")          # 未登録
        sys = self._systems(s.view)
        self.assertTrue(any("知らん" in (m or "") for m in sys))
        self.assertTrue(any("遊べるの" in (m or "") for m in sys))
        self.assertIsNone(s.game)                     # 起動しない

    async def test_game_command_no_id_lists(self):
        s = self._sched([])
        await s.on_user_input("/game")                # id 省略 → 一覧
        self.assertTrue(any("遊べるの" in (m or "") for m in self._systems(s.view)))
        self.assertIsNone(s.game)

    async def test_blackjack_alias_still_works(self):
        s = self._sched([])
        calls = self._capture_make(s)
        await s.on_user_input("/blackjack")
        self.assertIsNotNone(s.game)
        self.assertEqual(calls[0][0], "blackjack")    # 別名でも blackjack が起動

    async def test_watch_clamps_to_min_players(self):
        # leduc は最少2人。観戦でも want=1 にせず min を割らない（1人で rlcard を作って落ちるのを防ぐ）
        s = self._sched([])
        calls = self._capture_make(s)
        await s.on_user_input("/game leduc 見る")
        self.assertEqual(calls[0], ("leduc", 2))

    async def test_game_window_close_aborts_game(self):
        # 観戦窓×（GAME_CLOSE_REQUEST）で対局を畳んで縁側へ戻る（ゲームモードのまま固まらない）
        s = self._sched([])
        await s._start_game("blackjack", watch=False)
        self.assertIsNotNone(s.game)
        await s.on_user_input(views.GAME_CLOSE_REQUEST)
        self.assertIsNone(s.game)                          # お開き＝縁側へ復帰
        self.assertIn("game_close", [t for (t, _a, _b) in s.view.events])   # 観戦窓も閉じる
        self.assertTrue(any("お開き" in (m or "") for m in self._systems(s.view)))

    async def test_game_close_request_noop_without_game(self):
        s = self._sched([])
        await s.on_user_input(views.GAME_CLOSE_REQUEST)    # ゲーム中でない（結果表示の×等）
        self.assertIsNone(s.game)                          # 何も起きない（例外なし・メッセージも出さない）
        self.assertFalse(any("お開き" in (m or "") for m in self._systems(s.view)))

    # ── 異常系: 対局中のエラーで tick ループを殺さない（永久停止を防ぐ）────────────
    async def test_tick_survives_ai_error_in_game(self):
        # AI の手番で prompt が timeout 以外の例外 → お開き（_tick は例外を投げ返さない＝ループは死なない）
        s = sched.Scheduler(ErrorResident(ValueError("boom")), [], sources.WeatherSource(),
                            views.CaptureView())
        s._make_game = lambda gid, n: FakeGame(n)
        await s._start_game("blackjack", watch=True)       # 全AI（茶々のみ）
        self.assertIsNotNone(s.game)
        await s._tick(sources.build_context(None, []))     # ここで例外が漏れたらテストは ERROR で落ちる
        self.assertIsNone(s.game)                          # お開きで縁側へ復帰
        self.assertIn("game_close", [t for (t, _a, _b) in s.view.events])
        self.assertTrue(any("お開き" in (m or "") for m in self._systems(s.view)))

    async def test_tick_survives_adapter_error_in_game(self):
        # adapter 側（rlcard 相当）が手の適用で例外 → 同じくお開き（AI 経由でなく盤側の異常）
        class BrokenGame(FakeGame):
            def play(self, move):
                raise RuntimeError("adapter broke")
        s = sched.Scheduler(FakeResident(), [], sources.WeatherSource(), views.CaptureView())
        s._make_game = lambda gid, n: BrokenGame(n)
        await s._start_game("blackjack", watch=True)
        self.assertIsNotNone(s.game)
        await s._tick(sources.build_context(None, []))     # adapter.play が raise → お開き
        self.assertIsNone(s.game)
        self.assertTrue(any("お開き" in (m or "") for m in self._systems(s.view)))

    # ── 異常系: 対局中の /codex は弾く（room と game の同時成立を防ぐ）─────────────
    async def test_codex_refused_during_game(self):
        created = []
        s = self._sched(created)
        await s._start_game("blackjack", watch=False)      # 私+茶々（codex 不要）
        self.assertIsNotNone(s.game)
        await s.on_user_input("/codex 近所のご隠居")
        self.assertIsNone(s.room)                          # 部屋は立たない
        self.assertIsNotNone(s.game)                       # 対局は継続
        self.assertEqual(len(created), 0)                  # codex を spawn しない
        self.assertTrue(any("対局中" in (m or "") for m in self._systems(s.view)))

    async def test_codex_allowed_when_no_game(self):
        # 正常系: 対局中でなければ従来どおり客人が上がる（ガードが過剰に弾かない）
        created = []
        s = self._sched(created)
        await s._summon_guest("近所のご隠居")
        self.assertIsNotNone(s.room)                       # 部屋が立つ
        self.assertEqual(len(created), 1)                  # codex を1体 spawn
        await s._end_visit()                               # 後始末（使い捨て codex を破棄）

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


class TestTimeoutRecovery(unittest.IsolatedAsyncioTestCase):
    """ACP timeout（adapter 無応答）の段階回復: 住人=ターン破棄→再起動→閉じる／客人=急用退場／ゲーム=お開き。"""

    @staticmethod
    def _systems(v):
        return [m for (t, m, _l) in v.events if t == "system"]

    async def test_resident_first_timeout_abandons_turn_keeps_session(self):
        old = TimeoutResident()
        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView())
        await s.on_user_input("おーい")               # 1回目 → ターン破棄（< 閾値2・落とさない）
        self.assertIs(s.resident, old)                # session 維持（再起動しない＝文脈温存）
        self.assertFalse(s.stop.is_set())
        self.assertEqual(s._resident_timeouts, 1)
        self.assertTrue(any("黙り込んだ" in (m or "") for m in self._systems(s.view)))

    async def test_resident_restart_after_threshold(self):
        old = TimeoutResident()
        healthy = FakeResident()

        async def spawn_resident():
            return healthy

        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView(),
                            spawn_resident=spawn_resident)
        await s.on_user_input("おーい")               # 1
        await s.on_user_input("おーい")               # 2 → 閾値で再起動
        self.assertIs(s.resident, healthy)            # 新しい住人に差し替わった
        self.assertTrue(old.closed)                   # 旧住人は close
        self.assertEqual(s._resident_timeouts, 0)     # カウンタ復帰

    async def test_resident_closes_engawa_when_no_restart_factory(self):
        old = TimeoutResident()
        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView())  # spawn_resident=None
        await s.on_user_input("おーい")               # 1（まだ閉じない）
        self.assertFalse(s.stop.is_set())
        await s.on_user_input("おーい")               # 2 → 閾値・再起動不可 → 縁側を閉じる
        self.assertTrue(s.stop.is_set())

    async def test_guest_timeout_leaves_gracefully(self):
        created = []

        class TimeoutCodex:
            def __init__(self):
                self.closed = False
                self.reported_model = None
                self.prompts = []

            async def prompt(self, text, on_chunk=None):
                raise acp.ACPTimeoutError("guest timeout")

            async def close(self):
                self.closed = True

        async def spawn():
            c = TimeoutCodex()
            created.append(c)
            return c

        s = sched.Scheduler(FakeResident(), [], sources.WeatherSource(),
                            views.CaptureView(), spawn_codex=spawn)
        await s._summon_guest("ご隠居")
        self.assertIsNone(s.room)                     # 急用退場で部屋は閉じた
        self.assertIsNone(s.active)
        self.assertTrue(created[0].closed)            # ハングした codex も close（taskkill 相当）
        self.assertTrue(any("客人は" in (m or "") for m in self._systems(s.view)))  # 去り際の定型ナレ

    async def test_game_aborts_on_ai_timeout(self):
        s = sched.Scheduler(TimeoutResident(), [], sources.WeatherSource(), views.CaptureView())
        s._make_game = lambda gid, n: FakeGame(n)
        await s._start_game("blackjack", watch=True)  # 全AI（茶々のみ）
        self.assertIsNotNone(s.game)
        await s._tick(sources.build_context(None, []))    # 茶々が打とうとして timeout → お開き
        self.assertIsNone(s.game)
        self.assertIn("game_close", [t for (t, _a, _b) in s.view.events])  # 観戦窓も閉じる


class TestArcInterruptible(unittest.IsolatedAsyncioTestCase):
    """/arc は完走までブロックせず active に載せて即 return する＝再生中も barge-in が通る。"""
    def _systems(self, view):
        return [m for (t, m, _l) in view.events if t == "system"]

    def _make_with_arc(self, key="雀"):
        resident = FakeResident()
        view = views.CaptureView()
        arc = FakeArc(key)
        s = sched.Scheduler(resident, [arc], sources.WeatherSource(), view)
        return s, resident, view, arc

    async def asyncSetUp(self):
        self._fw = sources.fetch_weather
        sources.fetch_weather = lambda: None          # ネットワークを叩かない（天気 None）

    async def asyncTearDown(self):
        sources.fetch_weather = self._fw

    async def test_arc_loads_active_and_returns(self):
        s, r, v, arc = self._make_with_arc("雀")
        await s.on_user_input("/arc 雀")
        self.assertIs(s.active, arc)                  # tick 駆動の active に載った
        self.assertEqual(s.active.key, "雀")
        # 完走ブロックしていた頃は起→承→転→結を全部 inject していた。今は1本も inject しない（tick が前進させる）
        self.assertEqual(len(r.prompts), 0)
        # デバッグ表記は出さない＝成功時は無言で active に載るだけ（窓を汚さない）
        self.assertFalse(any("debug" in (m or "") for m in self._systems(v)))

    async def test_bargein_works_during_arc(self):
        s, r, v, arc = self._make_with_arc("雀")
        await s.on_user_input("/arc 雀")
        s.speaking = True                             # 起を喋っている最中を擬似
        await s.on_user_input("おーい")               # 再生中の話しかけ
        self.assertEqual(r.cancels, 1)                # cancel優先で畳む（ADR-0006）
        self.assertTrue(any("こちらを向いた" in (m or "") for m in self._systems(v)))
        self.assertIn("おーい", " ".join(r.prompts))  # ユーザー発話が茶々へ届く
        self.assertIs(s.active, arc)                  # active(source) は触らない＝QUIET 明けに背景継続

    async def test_arc_refused_when_busy(self):
        s, r, v, arc = self._make_with_arc("雀")
        s.active = FakeArc("猫")                       # 既に別アーク進行中
        await s.on_user_input("/arc 雀")
        self.assertEqual(s.active.key, "猫")           # 載せ替えない
        self.assertTrue(any("別のこと" in (m or "") for m in self._systems(v)))


class TestRestartAndGuard(unittest.IsolatedAsyncioTestCase):
    """/restart（住人セッション張り直し・染み出し/不調時）と、_inject の染み出しガード。"""

    @staticmethod
    def _systems(v):
        return [m for (t, m, _l) in v.events if t == "system"]

    @staticmethod
    def _bodies(v):
        return [text for (t, _k, text) in v.events if t == "end"]

    async def test_restart_command_respawns_resident(self):
        old = FakeResident()
        healthy = FakeResident()

        async def spawn_resident():
            return healthy

        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView(),
                            spawn_resident=spawn_resident)
        await s.on_user_input("/restart")
        self.assertIs(s.resident, healthy)             # 新セッションに差し替わった
        self.assertTrue(old.closed)                    # 旧は close
        self.assertTrue(any("戻ってきた" in (m or "") for m in self._systems(s.view)))

    async def test_restart_without_factory_keeps_resident(self):
        old = FakeResident()
        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView())  # spawn_resident=None
        await s.on_user_input("/restart")
        self.assertIs(s.resident, old)                 # 呼び直せない＝現状維持
        self.assertFalse(old.closed)

    async def test_restart_failure_keeps_current_resident(self):
        old = FakeResident()

        async def spawn_resident():
            raise RuntimeError("spawn failed")

        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView(),
                            spawn_resident=spawn_resident)
        await s.on_user_input("/restart")
        self.assertIs(s.resident, old)                 # 失敗時は今の茶々を生かす
        self.assertFalse(old.closed)
        self.assertFalse(s.stop.is_set())              # /restart 失敗で縁側は閉じない

    async def test_inject_strips_leak_before_display(self):
        class LeakyResident(FakeResident):
            async def prompt(self, text, on_chunk=None):
                self.prompts.append(text)
                out = text + "As Chacha, I should be short and warm, Kansai.そうやで、ぼちぼちやろ。"
                if on_chunk:
                    on_chunk(out)
                return out

        r = LeakyResident()
        v = views.CaptureView()
        s = sched.Scheduler(r, [], sources.WeatherSource(), v)
        guard = sched.RESIDENT_GUARD
        sched.RESIDENT_GUARD = 1
        try:
            await s.on_user_input("評価むずいわ")
        finally:
            sched.RESIDENT_GUARD = guard
        body = " ".join(self._bodies(v))
        self.assertIn("ぼちぼち", body)                 # 本物の台詞は残る
        self.assertNotIn("As Chacha", body)            # 地の思考は消える
        self.assertNotIn("茶々として", body)            # 注入文の復唱も消える

    async def test_guard_off_streams_raw(self):
        class LeakyResident(FakeResident):
            async def prompt(self, text, on_chunk=None):
                self.prompts.append(text)
                out = "As Chacha reasoning.ぼちぼちやろ。"
                if on_chunk:
                    on_chunk(out)
                return out

        r = LeakyResident()
        v = views.CaptureView()
        s = sched.Scheduler(r, [], sources.WeatherSource(), v)
        guard = sched.RESIDENT_GUARD
        sched.RESIDENT_GUARD = 0
        try:
            await s.on_user_input("よお")
        finally:
            sched.RESIDENT_GUARD = guard
        body = " ".join(self._bodies(v))
        self.assertIn("As Chacha", body)               # guard=0 は素通し（従来挙動）


class TestAbsenceRefresh(unittest.IsolatedAsyncioTestCase):
    """茶々の「中座」＝世界観に溶かした定期セッション更新（ADR-0027）。"""

    @staticmethod
    def _says(v):
        return [text for (t, _sp, text) in v.events if t == "say"]

    def _sched(self, spawn=None):
        return sched.Scheduler(FakeResident(), [], sources.WeatherSource(),
                               views.CaptureView(), spawn_resident=spawn)

    async def test_step_away_when_pressure_met(self):
        s = self._sched()
        g = sched.ABSENCE_AFTER_TURNS
        sched.ABSENCE_AFTER_TURNS = 5
        try:
            s._turns_since_refresh, s._absence_target = 5, 5
            stepped = s._maybe_step_away()
        finally:
            sched.ABSENCE_AFTER_TURNS = g
        self.assertTrue(stepped)
        self.assertTrue(s._absent)
        self.assertTrue(self._says(s.view))            # 中座の一言が出る（ローカル定型）
        self.assertIn(("set_absent", True, None), s.view.events)   # web は空っぽの縁側へ

    async def test_no_step_away_before_pressure(self):
        s = self._sched()
        g = sched.ABSENCE_AFTER_TURNS
        sched.ABSENCE_AFTER_TURNS = 5
        try:
            s._turns_since_refresh, s._absence_target = 3, 5   # まだ溜まってない
            self.assertFalse(s._maybe_step_away())
        finally:
            sched.ABSENCE_AFTER_TURNS = g
        self.assertFalse(s._absent)

    async def test_disabled_never_steps_away(self):
        s = self._sched()
        g = sched.ABSENCE_AFTER_TURNS
        sched.ABSENCE_AFTER_TURNS = 0                   # 中座オフ
        try:
            s._turns_since_refresh, s._absence_target = 999, 0
            self.assertFalse(s._maybe_step_away())
        finally:
            sched.ABSENCE_AFTER_TURNS = g
        self.assertFalse(s._absent)

    async def test_inject_counts_turns(self):
        s = self._sched()
        before = s._turns_since_refresh
        await s._inject(sources.Narration("よお", "ambient"))
        self.assertEqual(s._turns_since_refresh, before + 1)   # 発話ごとに圧が溜まる

    async def test_return_refreshes_session_and_resets_pressure(self):
        old = FakeResident()
        healthy = FakeResident()

        async def spawn():
            return healthy

        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView(),
                            spawn_resident=spawn)
        s._absent, s._turns_since_refresh = True, 42
        await s._return_from_away()
        self.assertIs(s.resident, healthy)             # 裏で新セッションに若返り
        self.assertTrue(old.closed)
        self.assertFalse(s._absent)
        self.assertEqual(s._turns_since_refresh, 0)    # 圧リセット
        self.assertTrue(self._says(s.view))            # 戻りの一言
        self.assertIn(("set_absent", False, None), s.view.events)  # 茶々スプライト復帰

    async def test_tick_returns_after_gap(self):
        old = FakeResident()
        healthy = FakeResident()

        async def spawn():
            return healthy

        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView(),
                            spawn_resident=spawn)
        s._absent, s._away_until = True, time.time() - 1   # 戻り時刻を過ぎている
        await s._tick(sources.build_context(None, []))
        self.assertFalse(s._absent)
        self.assertIs(s.resident, healthy)

    async def test_tick_stays_absent_before_gap(self):
        s = self._sched()
        s._absent, s._away_until = True, time.time() + 100   # まだ戻らない
        await s._tick(sources.build_context(None, []))
        self.assertTrue(s._absent)

    async def test_user_input_ends_absence(self):
        old = FakeResident()
        healthy = FakeResident()

        async def spawn():
            return healthy

        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView(),
                            spawn_resident=spawn)
        s._absent = True
        await s.on_user_input("茶々おるか")
        self.assertFalse(s._absent)                    # 話しかけられたら戻る
        self.assertIs(s.resident, healthy)             # 新セッションで応じる
        self.assertIn("茶々おるか", " ".join(healthy.prompts))

    async def test_no_ambient_fetch_during_absence_or_game(self):
        # 中座中/対局中は天気・ネタを取得しない（無駄回避＋中座の戻りを gap 通りに締める）
        s = self._sched()
        self.assertTrue(s._should_fetch_ambient())     # idle＝取得する
        s._absent = True
        self.assertFalse(s._should_fetch_ambient())    # 中座中＝取得しない
        s._absent = False
        s.game = object()                              # 対局中＝取得しない（遅延回避）
        self.assertFalse(s._should_fetch_ambient())


class TestAgentPort(unittest.IsolatedAsyncioTestCase):
    """ADR-0026: Scheduler は acp を import せず、中立 agent.AgentTimeoutError だけで回復する
    ＝実体(ACP/API)を差し替え可能。将来 OpenAIAgent が中立例外を投げても同じ段階回復が効くことを担保。"""

    def test_scheduler_uses_neutral_port_not_acp(self):
        import scheduler as sm
        self.assertFalse(hasattr(sm, "acp"))           # acp モジュールを取り込んでいない（ポート経由）
        self.assertTrue(hasattr(sm, "AgentTimeoutError"))

    async def test_neutral_timeout_recovers_like_acp(self):
        import agent

        class NeutralTimeoutResident(FakeResident):    # 非ACP の Agent が投げる中立例外
            async def prompt(self, text, on_chunk=None):
                raise agent.AgentTimeoutError("neutral timeout")

        old = NeutralTimeoutResident()
        healthy = FakeResident()

        async def spawn():
            return healthy

        s = sched.Scheduler(old, [], sources.WeatherSource(), views.CaptureView(),
                            spawn_resident=spawn)
        await s.on_user_input("おーい")                # 1
        await s.on_user_input("おーい")                # 2 → 閾値で再起動（ACP 由来でなくても同じ）
        self.assertIs(s.resident, healthy)
        self.assertTrue(old.closed)


if __name__ == "__main__":
    unittest.main()
