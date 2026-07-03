"""ACPClient: transport(stdout)が EOF で閉じた時、応答待ち request が
ハングせず ConnectionError で畳まれる（S1 回帰）。実 adapter/プロセスは使わない。"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
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


class _HangStdin:
    def write(self, b):
        pass

    async def drain(self):
        pass


class _HangProc:
    """応答を返さない adapter（request が timeout する側）。reader は走らせない。"""
    def __init__(self):
        self.stdin = _HangStdin()


class _DeadProc:
    returncode = 0          # shutdown_process は returncode!=None で即 return


class TestRequestTimeout(unittest.IsolatedAsyncioTestCase):
    async def test_request_times_out_and_clears_pending(self):
        client = acp.ACPClient(_HangProc())
        with self.assertRaises(acp.ACPTimeoutError):          # 無応答 → 永久待ちでなく timeout（S1）
            await client.request("session/prompt", {}, timeout=0.05)
        self.assertFalse(client._pending)                     # 残骸を残さない（pop 済み）

    async def test_acp_timeout_is_timeouterror(self):
        self.assertTrue(issubclass(acp.ACPTimeoutError, TimeoutError))


class TestAbortPending(unittest.IsolatedAsyncioTestCase):
    async def test_result_resolves_future(self):
        client = acp.ACPClient(_HangProc())
        fut = asyncio.get_running_loop().create_future()
        client._pending[7] = fut
        client.abort_pending(7, result={"result": {"stopReason": "cancelled"}})
        self.assertEqual((await fut)["result"]["stopReason"], "cancelled")
        self.assertFalse(client._pending)                 # pop 済み（残骸なし）

    async def test_noop_when_absent_or_done(self):
        client = acp.ACPClient(_HangProc())
        client.abort_pending(99, result={"x": 1})         # 不在 → 例外なく no-op
        done = asyncio.get_event_loop().create_future()
        done.set_result("keep")
        client._pending[1] = done
        client.abort_pending(1, result={"x": 1})          # 決着済み → 触らない
        self.assertEqual(done.result(), "keep")


class TestCancelBoundedWait(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_expedites_inflight_prompt(self):
        """adapter が cancelled 応答を返さなくても、cancel 後 CANCEL_GRACE 秒で
        in-flight prompt が stopReason=cancelled で畳まれる（PROMPT_TIMEOUT=240 を待たない・S1 残）。"""
        grace0 = acp.CANCEL_GRACE
        acp.CANCEL_GRACE = 0.05
        try:
            client = acp.ACPClient(_HangProc())
            agent = acp.AcpAgent(client.proc, client, "sid", {}, [])
            task = asyncio.create_task(agent.prompt("やあ"))
            await asyncio.sleep(0.02)                       # prompt が _pending に載る
            self.assertIsNotNone(agent._prompt_rid)
            self.assertTrue(client._pending)
            await agent.cancel()                           # notify＋grace タスク仕込み
            text = await asyncio.wait_for(task, timeout=2)  # 240 でなく grace で返る
            self.assertEqual(text, "")                      # チャンク無し（adapter は何も返してない）
            self.assertEqual(agent.last_stop_reason, "cancelled")
            self.assertFalse(client._pending)               # pop 済み
        finally:
            acp.CANCEL_GRACE = grace0

    async def test_cancel_noop_when_idle(self):
        """喋っていない（in-flight prompt 無し）時の cancel は grace タスクを仕込まない。"""
        client = acp.ACPClient(_HangProc())
        agent = acp.AcpAgent(client.proc, client, "sid", {}, [])
        await agent.cancel()
        self.assertIsNone(agent._expedite_task)


class TestCloseRemovesPersonaDir(unittest.IsolatedAsyncioTestCase):
    async def test_close_rmtrees_persona_dir(self):
        import pathlib
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp(prefix="engawa_test_"))
        agent = acp.AcpAgent(_DeadProc(), None, "sid", {}, [], persona_dir=d)
        self.assertTrue(d.exists())
        await agent.close()
        self.assertFalse(d.exists())                          # temp dir を後始末（S2）


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


class TestSessionModel(unittest.TestCase):
    def test_picks_current_with_name(self):
        result = {"sessionId": "s1", "models": {"currentModelId": "opus", "availableModels": [
            {"modelId": "opus", "name": "Claude Opus", "description": "x"},
            {"modelId": "haiku", "name": "Claude Haiku"}]}}
        self.assertEqual(acp._session_model(result), "Claude Opus（opus）")

    def test_id_only_when_no_name(self):
        result = {"models": {"currentModelId": "x", "availableModels": []}}
        self.assertEqual(acp._session_model(result), "x")

    def test_none_when_absent(self):
        self.assertIsNone(acp._session_model({"sessionId": "s1"}))   # アダプタが models を返さない版
        self.assertIsNone(acp._session_model({"models": {}}))
        self.assertIsNone(acp._session_model({"models": {"currentModelId": ""}}))


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


class TestNoConsoleWindow(unittest.IsolatedAsyncioTestCase):
    """子プロセス（アダプタ起動の cmd/npx／後始末の taskkill）が窓を出さないよう
    creationflags=CREATE_NO_WINDOW を渡すことの回帰テスト。外すと『ぱっと開いて閉じる窓』が
    復活する（Windows 固有・過去に一悶着）。非Windows では CREATE_NO_WINDOW==0（無影響）。"""

    async def test_spawn_passes_no_window_flag(self):
        seen = {}

        async def fake_exec(*args, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop after recording")   # ハンドシェイク前で止める

        with mock.patch("acp.asyncio.create_subprocess_exec", fake_exec):
            with self.assertRaises(RuntimeError):
                await acp.AcpAgent.spawn(["dummy-adapter"], cwd=".")
        self.assertIn("creationflags", seen)                  # フラグ自体を渡している
        self.assertEqual(seen["creationflags"], acp.CREATE_NO_WINDOW)

    @unittest.skipUnless(os.name == "nt", "taskkill は Windows のみ")
    async def test_shutdown_taskkill_passes_no_window_flag(self):
        seen = {}

        async def fake_exec(*args, **kwargs):
            seen["args"], seen["kwargs"] = args, kwargs

            class _K:
                async def wait(self_):
                    return 0
            return _K()

        class _Stdin:
            def is_closing(self_):
                return False

            def close(self_):
                pass

        class _Proc:
            returncode = None
            pid = 4321
            stdin = _Stdin()
            _transport = type("T", (), {"close": lambda self_: None})()

            async def wait(self_):
                return 0

            def kill(self_):
                pass

        with mock.patch("acp.asyncio.create_subprocess_exec", fake_exec):
            await acp.shutdown_process(_Proc())
        self.assertEqual(seen["args"][0], "taskkill")
        self.assertEqual(seen["kwargs"].get("creationflags"), acp.CREATE_NO_WINDOW)


if __name__ == "__main__":
    unittest.main()
