"""game_controller: GameController を Scheduler 抜きで isolation 検証（ADR-0029 Phase 3）。

ゲーム挙動そのものの characterization は test_scheduler.TestGameMode（実 Scheduler 経由・FakeGame）が担う。
ここは**注入境界**＝preempt/bump_beat/resident_provider を controller が正しく使うか、validate→preempt の
順序（生成前に弾いたら場を払わない）を、Scheduler 依存なしで確認する。
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import game_controller
import game_rlcard
import views
from tests.test_game import FakeGame


class FakeResident:
    async def prompt(self, *a, **k):
        return ""

    async def cancel(self):
        pass


def _systems(v):
    return [m for (t, m, _l) in v.events if t == "system"]


class TestGameControllerInjection(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        game_rlcard.register_rlcard_games()          # registry を live と同じに（rlcard 不要・lambda 登録）

    def _make(self):
        v = views.CaptureView()
        calls = {"preempt": 0, "bump": 0, "resident": 0}

        async def preempt():
            calls["preempt"] += 1

        def bump():
            calls["bump"] += 1

        def resident_provider():
            calls["resident"] += 1
            return FakeResident()

        gc = game_controller.GameController(
            view=v, spawn_codex=None, resident_provider=resident_provider,
            drive_lock=asyncio.Lock(), preempt=preempt, bump_beat=bump,
            make_game=lambda gid, n: FakeGame(n))
        return gc, v, calls

    async def test_start_uses_injected_hooks(self):
        gc, v, calls = self._make()
        await gc.start("blackjack", watch=True)      # 全AI（茶々のみ・codex 不要）
        self.assertTrue(gc.active)                   # 対局が立った
        self.assertFalse(gc.over)
        self.assertEqual(calls["preempt"], 1)        # 場払いを呼んだ
        self.assertEqual(calls["bump"], 1)           # 次ビートを active ペースへ
        self.assertGreaterEqual(calls["resident"], 1)  # 住人の現物を provider から取った

    async def test_unknown_game_does_not_preempt(self):
        gc, v, calls = self._make()
        await gc.start("nope", watch=False)
        self.assertFalse(gc.active)
        self.assertEqual(calls["preempt"], 0)        # 生成前に弾く＝場を払わない（validate→preempt の順序）
        self.assertTrue(any("知らんな" in (m or "") for m in _systems(v)))

    async def test_double_start_refused(self):
        gc, v, _ = self._make()
        await gc.start("blackjack", watch=True)
        await gc.start("blackjack", watch=True)      # 二重開始は拒否
        self.assertTrue(any("もうゲーム中" in (m or "") for m in _systems(v)))

    async def test_abort_clears_and_input_gate(self):
        gc, v, _ = self._make()
        self.assertFalse(await gc.on_user_input("hi"))   # 非対局時は消費しない（Scheduler が通常入力へ）
        await gc.start("blackjack", watch=True)
        await gc.abort_by_user()                     # ×でお開き
        self.assertFalse(gc.active)                  # 縁側へ復帰
        self.assertFalse(await gc.on_user_input("hi"))   # 再び非対局＝消費しない


if __name__ == "__main__":
    unittest.main()
