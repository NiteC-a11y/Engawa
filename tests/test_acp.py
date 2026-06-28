"""ACPClient: transport(stdout)が EOF で閉じた時、応答待ち request が
ハングせず ConnectionError で畳まれる（S1 回帰）。実 adapter/プロセスは使わない。"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acp


class _FakeStdout:
    def __init__(self, gate):
        self._gate = gate

    async def readline(self):
        await self._gate.wait()   # request が _pending に載るまで待ってから
        return b""                # EOF（adapter 死亡相当）


class _FakeStdin:
    def write(self, b):
        pass

    async def drain(self):
        pass


class _FakeProc:
    def __init__(self, gate):
        self.stdout = _FakeStdout(gate)
        self.stdin = _FakeStdin()


class TestTransportClose(unittest.IsolatedAsyncioTestCase):
    async def test_pending_request_fails_on_eof(self):
        gate = asyncio.Event()
        client = acp.ACPClient(_FakeProc(gate))
        reader = asyncio.create_task(client.reader())
        req = asyncio.create_task(client.request("initialize", {}))
        await asyncio.sleep(0.02)
        self.assertTrue(client._pending)            # 応答待ちに載っている
        gate.set()                                  # reader が EOF を見る → pending 解放
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(req, timeout=2)  # ハングせず例外
        await reader
        self.assertFalse(client._pending)           # 後始末（残骸なし）

    async def test_fail_pending_skips_completed_futures(self):
        client = acp.ACPClient(_FakeProc(asyncio.Event()))
        done = asyncio.get_event_loop().create_future()
        done.set_result(123)
        client._pending[1] = done
        client._fail_pending(ConnectionError("closed"))
        self.assertEqual(done.result(), 123)        # 完了済みは触らない
        self.assertFalse(client._pending)


class TestModelEnv(unittest.TestCase):
    def test_resident_model_plain_id(self):
        self.assertEqual(acp._model_env("ANTHROPIC_MODEL", "claude-opus-4-8"),
                         {"ANTHROPIC_MODEL": "claude-opus-4-8"})

    def test_guest_model_json_wrapped(self):
        self.assertEqual(acp._model_env("CODEX_CONFIG", "gpt-5-codex", json_key="model"),
                         {"CODEX_CONFIG": '{"model": "gpt-5-codex"}'})

    def test_empty_model_is_none(self):
        # 未指定（空）はアダプタ既定のまま＝注入なし（現状維持）
        self.assertIsNone(acp._model_env("ANTHROPIC_MODEL", ""))
        self.assertIsNone(acp._model_env("CODEX_CONFIG", "", json_key="model"))


class TestChildEnv(unittest.TestCase):
    def test_drops_api_key(self):
        env = acp._child_env({"ANTHROPIC_API_KEY": "sk", "PATH": "/x"},
                             ("ANTHROPIC_API_KEY",))
        self.assertNotIn("ANTHROPIC_API_KEY", env)   # 課金事故防止（adr/0002）
        self.assertEqual(env["PATH"], "/x")

    def test_injects_extra_env(self):
        env = acp._child_env({"PATH": "/x"}, (), {"ANTHROPIC_MODEL": "opus"})
        self.assertEqual(env["ANTHROPIC_MODEL"], "opus")

    def test_skips_none_values(self):
        env = acp._child_env({"PATH": "/x"}, (), None)
        self.assertNotIn("ANTHROPIC_MODEL", env)
        env2 = acp._child_env({"PATH": "/x"}, (), {"X": None})
        self.assertNotIn("X", env2)

    def test_drop_then_inject_independent(self):
        # API キーは除去しつつモデルは載る
        env = acp._child_env({"ANTHROPIC_API_KEY": "sk", "OPENAI_API_KEY": "sk2"},
                             ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
                             {"CODEX_CONFIG": '{"model": "gpt-5-codex"}'})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["CODEX_CONFIG"], '{"model": "gpt-5-codex"}')


if __name__ == "__main__":
    unittest.main()
