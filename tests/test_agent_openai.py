"""agent_openai（OpenAI 互換 API アダプタ・ADR-0026 第2アダプタ）のユニット。HTTP はモック＝ネット不要。
検証: 履歴を自前保持し全文脈を毎回送る／本文を返す／on_chunk 一括／timeout・接続失敗は AgentTimeoutError／
cancel 済みは結果破棄／close で履歴リセット／probe のモデル解決と接続失敗の RuntimeError。"""
import os
import sys
import time
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import agent
import agent_openai
import config
from agent_openai import OpenAIAgent


def _mk(model="qwen", timeout=30):
    return OpenAIAgent("http://localhost:1234/v1", model, "k", timeout)


def _reply(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


class TestOpenAIAgentPrompt(unittest.IsolatedAsyncioTestCase):

    async def test_prompt_returns_content_and_grows_history(self):
        a = _mk()
        a._post = lambda path, body: _reply("ほな、ぼちぼちいこか。")
        out = await a.prompt("おーい")
        self.assertEqual(out, "ほな、ぼちぼちいこか。")
        self.assertEqual([m["role"] for m in a._messages], ["system", "user", "assistant"])
        self.assertEqual(a._messages[1]["content"], "おーい")
        self.assertEqual(a.last_stop_reason, "stop")

    async def test_sends_full_history_each_call(self):
        a = _mk()
        seen = []

        def post(path, body):
            seen.append(len(body["messages"]))
            return _reply("うん")

        a._post = post
        await a.prompt("1")            # system,user = 2
        await a.prompt("2")            # system,user,assistant,user = 4
        self.assertEqual(seen, [2, 4])   # ステートレス API に毎回フル文脈を送る＝「長命セッション」を自前で持つ

    async def test_on_chunk_called_once_with_full_content(self):
        a = _mk()
        a._post = lambda path, body: _reply("短い独り言")
        chunks = []
        out = await a.prompt("ねえ", on_chunk=chunks.append)
        self.assertEqual(chunks, ["短い独り言"])   # 非ストリーミング＝全文を1回
        self.assertEqual(out, "短い独り言")

    async def test_timeout_raises_and_unwinds_history(self):
        a = _mk(timeout=0.05)

        def slow(path, body):
            time.sleep(0.2)
            return _reply("late")

        a._post = slow
        with self.assertRaises(agent.AgentTimeoutError):
            await a.prompt("待って")
        self.assertEqual([m["role"] for m in a._messages], ["system"])   # 積んだ user を戻す
        self.assertEqual(a.last_stop_reason, "timeout")

    async def test_connection_error_becomes_agent_timeout(self):
        a = _mk()

        def boom(path, body):
            raise urllib.error.URLError("refused")

        a._post = boom
        with self.assertRaises(agent.AgentTimeoutError):    # endpoint 落ち＝無応答扱いで段階回復へ（app は落とさない）
            await a.prompt("やあ")
        self.assertEqual([m["role"] for m in a._messages], ["system"])

    async def test_cancelled_result_discarded(self):
        a = _mk()

        def post(path, body):
            a._cancelled = True                            # 待機中に barge-in された想定
            return _reply("これは捨てられる")

        a._post = post
        out = await a.prompt("わ")
        self.assertEqual(out, "")                           # 結果は破棄
        self.assertEqual(a.last_stop_reason, "cancelled")
        self.assertEqual([m["role"] for m in a._messages], ["system"])   # user も戻す・assistant 積まない

    async def test_close_resets_history(self):
        a = _mk()
        a._post = lambda path, body: _reply("やあ")
        await a.prompt("1")
        await a.prompt("2")
        self.assertGreater(len(a._messages), 1)
        await a.close()
        self.assertEqual([m["role"] for m in a._messages], ["system"])   # 中座/再spawn で若返る


class TestOpenAIAgentRequestBody(unittest.IsolatedAsyncioTestCase):
    """既定で reasoning_effort=none を送る＝Qwen3.5 等が長考して本文が空になる事故を防ぐ（実機で判明）。"""

    async def test_default_sends_reasoning_none_and_no_max_tokens(self):
        a = _mk()                                    # reasoning 既定 "none"・max_tokens 既定 0
        seen = {}

        def post(path, body):
            seen.update(body)
            return _reply("よ")

        a._post = post
        await a.prompt("やあ")
        self.assertEqual(seen.get("reasoning_effort"), "none")
        self.assertNotIn("max_tokens", seen)         # 0=無指定＝フィールドを送らない

    async def test_empty_reasoning_omits_field_and_max_tokens_sent(self):
        a = OpenAIAgent("http://x/v1", "m", "k", 30, reasoning="", max_tokens=128)
        seen = {}

        def post(path, body):
            seen.update(body)
            return _reply("よ")

        a._post = post
        await a.prompt("やあ")
        self.assertNotIn("reasoning_effort", seen)   # 空=解さない endpoint 向けにフィールドごと省く
        self.assertEqual(seen.get("max_tokens"), 128)


class TestOpenAIAgentProbe(unittest.IsolatedAsyncioTestCase):

    async def test_probe_picks_first_model_when_unset(self):
        a = OpenAIAgent("http://x/v1", "", "k", 30)
        a._get = lambda path: {"data": [{"id": "qwen3.5-9b"}, {"id": "other"}]}
        await a._probe()
        self.assertEqual(a.model, "qwen3.5-9b")            # 未指定＝ロード済み先頭を採用
        self.assertEqual(a.reported_model, "qwen3.5-9b")

    async def test_probe_connection_failure_raises_runtime(self):
        a = OpenAIAgent("http://x/v1", "m", "k", 30)

        def boom(path):
            raise urllib.error.URLError("no server")

        a._get = boom
        with self.assertRaises(RuntimeError):              # composition root が「LM Studio を起動して」と案内
            await a._probe()


class TestOpenAIAgentGuest(unittest.IsolatedAsyncioTestCase):
    """客人（Codex 代替）は system に茶々の人格を載せず汎用の客人枠のみ＝役は prompt 注入（ADR-0008）。"""

    async def test_spawn_guest_uses_guest_system_not_resident_persona(self):
        import persona
        orig = OpenAIAgent._probe

        async def noprobe(self):        # ネットに出ずに spawn を通す
            pass

        OpenAIAgent._probe = noprobe
        try:
            g = await OpenAIAgent.spawn_guest()
        finally:
            OpenAIAgent._probe = orig
        self.assertEqual(g._messages[0]["content"], agent_openai.GUEST_SYSTEM)
        self.assertNotEqual(g._messages[0]["content"], persona.RESIDENT_PERSONA)

    def test_guest_system_is_not_resident_persona(self):
        import persona
        self.assertNotEqual(agent_openai.GUEST_SYSTEM, persona.RESIDENT_PERSONA)
        self.assertNotIn("あなたの人格", agent_openai.GUEST_SYSTEM)   # 茶々の人格ヘッダを含まない


class TestOpenAIEndpointGuard(unittest.TestCase):
    """非ローカル endpoint は既定でブロック（原則#1・課金/会話履歴の外部送信の事故防止・公開レビュー7/4）。
    ENGAWA_OPENAI_ALLOW_REMOTE=1 で明示 opt-in のみ通す。"""

    def setUp(self):
        self._env = os.environ.pop("ENGAWA_OPENAI_ALLOW_REMOTE", None)
        self._cfg = config._CFG
        config._CFG = {}

    def tearDown(self):
        config._CFG = self._cfg
        if self._env is None:
            os.environ.pop("ENGAWA_OPENAI_ALLOW_REMOTE", None)
        else:
            os.environ["ENGAWA_OPENAI_ALLOW_REMOTE"] = self._env

    def test_local_hosts_allowed(self):
        for u in ("http://localhost:1234/v1", "http://127.0.0.1/v1",
                  "http://192.168.1.9:1234/v1", "http://box.local/v1"):
            self.assertTrue(agent_openai._is_local_endpoint(u), u)
            agent_openai._ensure_endpoint_allowed(u)     # 例外を投げない

    def test_remote_blocked_by_default(self):
        self.assertFalse(agent_openai._is_local_endpoint("https://api.openai.com/v1"))
        with self.assertRaises(RuntimeError):
            agent_openai._ensure_endpoint_allowed("https://api.openai.com/v1")

    def test_remote_allowed_with_optin(self):
        os.environ["ENGAWA_OPENAI_ALLOW_REMOTE"] = "1"
        agent_openai._ensure_endpoint_allowed("https://api.openai.com/v1")   # opt-in で通る（例外なし）


class TestOpenAIAgentPortShape(unittest.TestCase):
    """agent.Agent ポートを構造的に満たす（Scheduler が無改造で差せる）。"""

    def test_has_port_surface(self):
        a = _mk()
        for attr in ("model", "reported_model", "last_stop_reason"):
            self.assertTrue(hasattr(a, attr))
        for meth in ("prompt", "cancel", "close"):
            self.assertTrue(callable(getattr(a, meth)))


if __name__ == "__main__":
    unittest.main()
