"""room_speakers: RoomSpeakerFactory を Scheduler 抜きで isolation 検証（ADR-0029 Phase 4a）。

種/timeout の characterization は test_scheduler（実 Scheduler 経由・TestThreeWayRoom /
TestTimeoutRecovery / test_topic_cooldown_spaces_out_seeds）が担う。ここは Speaker 生成・
resident_speak seam・timeout フラグ・種 cooldown を Scheduler 依存なしで確認する。
"""
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from agent import AgentTimeoutError
import conversation
import room_speakers
import sources


class FakeAgent:
    def __init__(self, reply="ほう", raise_timeout=False):
        self.reply = reply
        self._raise = raise_timeout
        self.prompts = []

    async def prompt(self, text, on_chunk=None):
        self.prompts.append(text)
        if self._raise:
            raise AgentTimeoutError("hang")
        return self.reply


def _factory(persona="ご隠居", *, resident_speak=None, agent=None, topics=None):
    async def default_resident_speak(text):
        return "ふむ"
    return room_speakers.RoomSpeakerFactory(
        persona,
        resident_speak=resident_speak or default_resident_speak,
        guest_agent_provider=lambda: agent,
        context_provider=lambda: {"tod": "昼", "now": None},
        topics_provider=lambda: (topics or []),
        log=logging.getLogger("test.room_speakers"))


class TestRoomSpeakerFactory(unittest.IsolatedAsyncioTestCase):
    def test_speakers_named_correctly(self):
        cha, guest = _factory("旅人").speakers()
        self.assertEqual(cha.name, "茶々")
        self.assertEqual(guest.name, "旅人")

    async def test_resident_say_goes_through_seam(self):
        seen = []

        async def rs(text):
            seen.append(text)
            return "おお"
        f = _factory(resident_speak=rs)
        out = await f._resident_say([], conversation.REPLY)
        self.assertEqual(out, "おお")
        self.assertEqual(len(seen), 1)               # resident_speak(seam) を1回通った
        self.assertFalse(f.resident_timed_out)

    async def test_resident_timeout_sets_flag(self):
        async def rs(text):
            raise AgentTimeoutError("hang")
        f = _factory(resident_speak=rs)
        out = await f._resident_say([], conversation.REPLY)
        self.assertEqual(out, "")                    # 空を返して Room は継続
        self.assertTrue(f.resident_timed_out)        # フラグ＝呼び側(_check_room_timeout)が畳む

    async def test_guest_timeout_sets_flag(self):
        f = _factory(agent=FakeAgent(raise_timeout=True))
        out = await f._guest_say([], conversation.REPLY)
        self.assertEqual(out, "")
        self.assertTrue(f.guest_timed_out)

    async def test_guest_none_agent_is_silent(self):
        f = _factory(agent=None)                     # まだ spawn 前など
        self.assertEqual(await f._guest_say([], conversation.REPLY), "")

    async def test_guest_error_payload_suppressed_and_leaves(self):
        # codex が API エラー（モデル非対応 400）を本文として返した → 生 JSON はセリフにせず退場フラグ
        err = ('{ "type": "error", "error": { "type": "invalid_request_error", "code": '
               '"unsupported_value", "message": "This model is not supported when using '
               'X-OpenAI-Internal-Codex-Responses-Lite.", "param": "model" }, "status": 400 }')
        f = _factory(agent=FakeAgent(reply=err))
        out = await f._guest_say([], conversation.REPLY)
        self.assertEqual(out, "")                     # 生 JSON は縁側に出さない（transcript にも積まれない）
        self.assertTrue(f.guest_timed_out)            # 応答不能扱い＝呼び側(_check_room_timeout)が急用退場で畳む

    async def test_seed_cooldown_spaces_out(self):
        topics = [{"text": "夏至の話", "tone": "季節", "source": "時節"}]
        saved = (sources.TOPIC_PROB, sources.TOPIC_COOLDOWN)
        try:
            sources.TOPIC_PROB, sources.TOPIC_COOLDOWN = 1.0, 2   # 必ず種→以後2ターン空ける
            f = _factory(agent=FakeAgent(), topics=topics)
            await f._guest_say([], conversation.REPLY)            # 1回目＝種を置く
            self.assertEqual(f._topic_cooldown, 2)               # cooldown 立つ
            self.assertEqual(len(f._topic_recent), 1)
            await f._guest_say([], conversation.REPLY)            # 2回目＝cooldown で見送り
            self.assertEqual(f._topic_cooldown, 1)               # 減っただけ・新規の種なし
            self.assertEqual(len(f._topic_recent), 1)
        finally:
            sources.TOPIC_PROB, sources.TOPIC_COOLDOWN = saved


if __name__ == "__main__":
    unittest.main()
