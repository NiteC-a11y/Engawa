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


class _ScriptedClient:
    """prompt 再送テスト用: request が台本どおりの応答を順に返す（実プロセス不使用）。"""
    def __init__(self, resps):
        self.resps = list(resps)
        self.calls = 0
        self.on_chunk = None

    async def request(self, method, params, timeout=None, on_start=None):
        self.calls += 1
        if on_start:
            on_start(self.calls)
        return self.resps.pop(0)


def _ede_error():
    return {"error": {"code": -32603,
                      "message": "Internal error: [ede_diagnostic] result_type=user stop_reason=null"}}


class TestPromptRetryOnInternalError(unittest.IsolatedAsyncioTestCase):
    """first token 前の cancel 直後、次 prompt が adapter 内部エラー(-32603)で弾かれ茶々が無言になる
    （実機 7/13）＝1回だけ再送して吸収する。他コードのエラーと部分発話後は再送しない。"""

    def setUp(self):
        self._wait0 = acp.PROMPT_RETRY_WAIT
        acp.PROMPT_RETRY_WAIT = 0                       # テストは待たない

    def tearDown(self):
        acp.PROMPT_RETRY_WAIT = self._wait0

    async def test_internal_error_retried_once_and_recovers(self):
        client = _ScriptedClient([_ede_error(), {"result": {"stopReason": "end_turn"}}])
        agent = acp.AcpAgent(_DeadProc(), client, "sid", {}, [])
        await agent.prompt("こっち向いた？")
        self.assertEqual(client.calls, 2)               # 再送1回で回復
        self.assertEqual(agent.last_stop_reason, "end_turn")

    async def test_internal_error_retried_only_once(self):
        client = _ScriptedClient([_ede_error(), _ede_error()])
        agent = acp.AcpAgent(_DeadProc(), client, "sid", {}, [])
        await agent.prompt("やあ")
        self.assertEqual(client.calls, 2)               # 2回で打ち止め（無限再送しない・有界）
        self.assertEqual(agent.last_stop_reason, "error")

    async def test_other_error_code_not_retried(self):
        client = _ScriptedClient([{"error": {"code": -32000, "message": "auth failed"}}])
        agent = acp.AcpAgent(_DeadProc(), client, "sid", {}, [])
        await agent.prompt("やあ")
        self.assertEqual(client.calls, 1)               # 認証エラー等は素通し（従来どおり）
        self.assertEqual(agent.last_stop_reason, "error")

    async def test_no_retry_after_partial_output(self):
        class _ChunkThenError(_ScriptedClient):
            async def request(self, method, params, timeout=None, on_start=None):
                self.calls += 1
                self.on_chunk("言いかけ……")             # 途中まで喋ってから死ぬ
                return _ede_error()
        client = _ChunkThenError([])
        agent = acp.AcpAgent(_DeadProc(), client, "sid", {}, [])
        out = await agent.prompt("やあ")
        self.assertEqual(client.calls, 1)               # 二重発話防止＝再送しない
        self.assertEqual(out, "言いかけ……")
        self.assertEqual(agent.last_stop_reason, "error")


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
        env = acp._child_env({"ANTHROPIC_API_KEY": "sk", "PATH": "/x"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)   # 課金事故防止（adr/0002・allowlist の billing deny）
        self.assertEqual(env["PATH"], "/x")          # OS 素性は通る

    def test_injects_extra_env(self):
        env = acp._child_env({"PATH": "/x"}, {"ANTHROPIC_MODEL": "opus"})
        self.assertEqual(env["ANTHROPIC_MODEL"], "opus")   # 我々の制御下の注入は allowlist を経ない

    def test_skips_none_values(self):
        env = acp._child_env({"PATH": "/x"}, None)
        self.assertNotIn("ANTHROPIC_MODEL", env)
        env2 = acp._child_env({"PATH": "/x"}, {"X": None})
        self.assertNotIn("X", env2)

    def test_drops_keys_but_injects_model(self):
        # ベンダーキーは default-deny で落ちつつ、我々の注入(CODEX_CONFIG)は載る
        env = acp._child_env({"ANTHROPIC_API_KEY": "sk", "OPENAI_API_KEY": "sk2", "PATH": "/x"},
                             {"CODEX_CONFIG": '{"model": "gpt-5-codex"}'})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["CODEX_CONFIG"], '{"model": "gpt-5-codex"}')


class TestChildEnvAllowlist(unittest.TestCase):
    """子 env の allowlist（default-deny・adr/0002 の 🔴 課金安全ギャップを塞ぐ）。"""
    def test_allows_os_and_runtime_essentials(self):
        base = {"PATH": "/x", "HOME": "/home/me", "SystemRoot": r"C:\Windows",
                "APPDATA": r"C:\a", "USERPROFILE": r"C:\u", "LC_CTYPE": "UTF-8",
                "NODE_OPTIONS": "--x", "NPM_CONFIG_REGISTRY": "https://r", "HTTPS_PROXY": "http://p"}
        env = acp._child_env(base)
        for k in base:
            self.assertIn(k, env, f"{k} は adapter 起動に要る＝通すべき")

    def test_drops_unlisted_app_vars(self):
        env = acp._child_env({"PATH": "/x", "SOME_RANDOM_APP_TOKEN": "secret"})
        self.assertIn("PATH", env)
        self.assertNotIn("SOME_RANDOM_APP_TOKEN", env)   # 未知は default-deny

    def test_hard_denies_billing_and_redirect_routes(self):
        # 🔴 旧 denylist で素通りだった従量/外部送信の別ルートを構造的に遮断
        base = {"PATH": "/x",
                "ANTHROPIC_API_KEY": "sk", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "http://x",
                "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
                "AWS_ACCESS_KEY_ID": "a", "AWS_BEARER_TOKEN_BEDROCK": "b",
                "GOOGLE_APPLICATION_CREDENTIALS": "/g", "OPENAI_API_KEY": "o", "OPENAI_BASE_URL": "http://o",
                "AZURE_OPENAI_API_KEY": "z"}
        env = acp._child_env(base)
        self.assertEqual(env, {"PATH": "/x"})   # PATH だけ残り、課金/外部送信 env は全滅

    def test_passthrough_admits_extra_but_not_billing(self):
        base = {"PATH": "/x", "MY_TOOL_HOME": "/t", "ANTHROPIC_API_KEY": "sk"}
        # 逃げ道で MY_TOOL_HOME は通せるが、billing 系は passthrough でも貫通不可（ハード下限）
        env = acp._child_env(base, passthrough=("MY_TOOL_HOME", "ANTHROPIC_API_KEY"))
        self.assertIn("MY_TOOL_HOME", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_passthrough_default_empty_drops_extra(self):
        env = acp._child_env({"PATH": "/x", "MY_TOOL_HOME": "/t"})   # passthrough 未指定＝落ちる
        self.assertNotIn("MY_TOOL_HOME", env)

    def test_case_insensitive_keeps_uppercase_windows_vars(self):
        # 実 Windows の os.environ は大文字キー(SYSTEMROOT 等)で来る＝case-insensitive で通さないと
        # node 必須のシステム変数を取りこぼし adapter が起動しない（実機で判明・回帰防止）。
        base = {"SYSTEMROOT": r"C:\Windows", "COMSPEC": r"C:\Windows\cmd.exe", "WINDIR": r"C:\Windows",
                "PROGRAMFILES": r"C:\Program Files", "COMMONPROGRAMFILES": r"C:\PF\Common",
                "APPDATA": r"C:\a", "PATH": "/x"}
        env = acp._child_env(base)
        for k in base:
            self.assertIn(k, env, f"{k}（大文字 Windows 名）は case-insensitive で通すべき")
        # 大文字の billing 変数はやはり落ちる
        self.assertNotIn("ANTHROPIC_API_KEY",
                         acp._child_env({"ANTHROPIC_API_KEY": "sk", "PATH": "/x"}))

    def test_is_billing_env(self):
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_ACCESS_KEY_ID",
                     "GOOGLE_X", "GCLOUD_Y", "GCP_Z", "OPENAI_API_KEY", "AZURE_X",
                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
            self.assertTrue(acp._is_billing_env(name), name)
        for name in ("PATH", "HOME", "CLAUDE_CONFIG_DIR", "ANTHROPICS", "NODE_OPTIONS"):
            self.assertFalse(acp._is_billing_env(name), name)


class TestConfigDirEnv(unittest.TestCase):
    def test_set_injects_claude_config_dir(self):
        self.assertEqual(acp._config_dir_env("/home/me/.claude-main"),
                         {"CLAUDE_CONFIG_DIR": "/home/me/.claude-main"})

    def test_empty_or_blank_is_none(self):
        # 未設定（空/空白のみ）は注入なし＝親の ~/.claude を継承（現状維持・adr/0002）
        self.assertIsNone(acp._config_dir_env(""))
        self.assertIsNone(acp._config_dir_env("   "))
        self.assertIsNone(acp._config_dir_env(None))

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(acp._config_dir_env("  /x/.claude  "),
                         {"CLAUDE_CONFIG_DIR": "/x/.claude"})


class TestMergeEnv(unittest.TestCase):
    def test_merges_non_none(self):
        self.assertEqual(acp._merge_env({"A": "1"}, {"B": "2"}), {"A": "1", "B": "2"})

    def test_skips_none_parts(self):
        self.assertEqual(acp._merge_env(None, {"A": "1"}, None), {"A": "1"})

    def test_all_empty_is_none(self):
        # 全部 None なら注入なし（現状維持）
        self.assertIsNone(acp._merge_env(None, None))
        self.assertIsNone(acp._merge_env())


class TestResidentExtraEnv(unittest.TestCase):
    def test_none_when_both_empty(self):
        # モデルも認証プロファイルも未指定＝注入なし（既定挙動を変えない）
        self.assertIsNone(acp._resident_extra_env("", ""))

    def test_model_only(self):
        self.assertEqual(acp._resident_extra_env("opus", ""),
                         {"ANTHROPIC_MODEL": "opus"})

    def test_config_dir_only(self):
        self.assertEqual(acp._resident_extra_env("", "/home/me/.claude-main"),
                         {"CLAUDE_CONFIG_DIR": "/home/me/.claude-main"})

    def test_both(self):
        self.assertEqual(acp._resident_extra_env("opus", "/home/me/.claude-main"),
                         {"ANTHROPIC_MODEL": "opus", "CLAUDE_CONFIG_DIR": "/home/me/.claude-main"})

    def test_resident_child_env_keeps_key_drop(self):
        # 認証プロファイル注入と課金対策(allowlist の billing deny)は独立＝退行しない（adr/0002）
        env = acp._child_env({"ANTHROPIC_API_KEY": "sk", "PATH": "/x"},
                             acp._resident_extra_env("opus", "/home/me/.claude-main"))
        self.assertNotIn("ANTHROPIC_API_KEY", env)                       # billing deny で落ちる
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/home/me/.claude-main")   # 我々の注入は載る
        self.assertEqual(env["ANTHROPIC_MODEL"], "opus")

    def test_guest_path_does_not_inject_config_dir(self):
        # 客人(codex)は別 CLI＝CLAUDE_CONFIG_DIR 無関係。guest の extra_env は _model_env のみで注入しない。
        env = acp._child_env({"OPENAI_API_KEY": "sk", "PATH": "/x"},
                             acp._model_env("CODEX_CONFIG", "gpt-5-codex", json_key="model"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn("OPENAI_API_KEY", env)


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
