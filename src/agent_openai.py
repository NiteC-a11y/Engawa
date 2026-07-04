#!/usr/bin/env python3
"""agent_openai.py — OpenAI 互換 API アダプタ（ADR-0026 第2アダプタ・任意経路）。

LM Studio / Ollama 等の OpenAI 互換 chat completions endpoint を `agent.Agent` ポートとして包む
（Scheduler は無改造で差さる）。ACP と違い API は**ステートレス**＝会話履歴を自前で保持する。これが
local 版の「長命セッション」（ADR-0005）で、中座 / `/restart` は `close()`→新インスタンスで履歴リセット
（ADR-0027 と整合）。人格は `persona.RESIDENT_PERSONA` を system メッセージに載せる（backend 中立・ADR-0003）。

HTTP は stdlib `urllib`（依存ゼロ・`asyncio.to_thread` で非同期化）。非ストリーミング＝住人ガードが一括
描画するので十分（茶々の発話は短い）。cancel は別タスクから flag を立て、進行中 await は呼び側が畳む＝
HTTP 完了後に結果を破棄して抑制する（local は速く従量課金も無いので実害小・ADR-0006 の趣旨は満たす）。
この葉は他アダプタ（acp）に依存しない＝2アダプタは相互非依存。
"""
import asyncio
import json
import urllib.error
import urllib.request

import config
import persona
from agent import AgentTimeoutError

DEFAULT_BASE_URL = "http://localhost:1234/v1"    # LM Studio の既定


def _base_url():
    return config.get_str("ENGAWA_OPENAI_BASE_URL", "openai", "base_url", DEFAULT_BASE_URL).rstrip("/")


def _model():
    return config.get_str("ENGAWA_OPENAI_MODEL", "openai", "model", "")           # 空=endpoint がロード済みの先頭を採用


def _api_key():
    return config.get_str("ENGAWA_OPENAI_API_KEY", "openai", "api_key", "lm-studio")  # local は不問（ダミーで可）


def _timeout():
    return config.get_float("ENGAWA_OPENAI_TIMEOUT", "openai", "timeout", 120.0, lo=5.0)  # 秒（初回はモデルロードで遅い）


class OpenAIAgent:
    """`agent.Agent` の OpenAI 互換 API 実装（構造的に prompt/cancel/close/model/reported_model/last_stop_reason
    を満たす）。会話履歴を self._messages に自前保持（先頭が system=人格）。"""

    def __init__(self, base_url, model, api_key, timeout, system=None):
        self.base_url = base_url
        self.model = model or None                    # 我々が要求したモデル（未指定 None）
        self.reported_model = None                    # endpoint が /models で報告した実モデル
        self.last_stop_reason = None
        self._api_key = api_key
        self._timeout = timeout
        self._system = persona.RESIDENT_PERSONA if system is None else system
        self._messages = [{"role": "system", "content": self._system}]
        self._cancelled = False
        self._lock = asyncio.Lock()                   # 同一 agent への同時 prompt を直列化（履歴の競合防止）

    @classmethod
    async def spawn_resident(cls, model=None):
        """住人（茶々）を OpenAI 互換 endpoint で。疎通確認して実モデルを掴む。
        繋がらなければ RuntimeError（composition root が『LM Studio を起動して』と案内）。"""
        self = cls(_base_url(), model or _model(), _api_key(), _timeout())
        await self._probe()
        return self

    async def _probe(self):
        """GET /models で疎通確認＋実モデル解決。未指定ならロード済みの先頭を採用。繋がらなければ RuntimeError。"""
        try:
            data = await asyncio.to_thread(self._get, "/models")
        except Exception as e:
            raise RuntimeError(
                f"OpenAI 互換 endpoint に繋がりません（{self.base_url}）。LM Studio 等でモデルをロードし、"
                f"ローカルサーバを起動してから再実行してください。（{type(e).__name__}）") from e
        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if ids:
            self.reported_model = self.model if self.model in ids else ids[0]
            if not self.model:
                self.model = ids[0]                   # 未指定＝ロード済みの先頭を使う

    async def prompt(self, text, on_chunk=None, timeout=None):
        """1ターン注入。応答本文を返し履歴に積む。timeout/接続失敗は AgentTimeoutError（呼び側が段階回復）。
        cancel 済みなら本文を破棄して "" を返す（積んだ user も戻す＝履歴を汚さない）。"""
        async with self._lock:
            self._cancelled = False
            self._messages.append({"role": "user", "content": text})
            body = {"model": self.model or "", "messages": self._messages, "stream": False}
            try:
                resp = await asyncio.wait_for(
                    asyncio.to_thread(self._post, "/chat/completions", body),
                    timeout=timeout or self._timeout)
            except asyncio.TimeoutError:
                self.last_stop_reason = "timeout"
                self._messages.pop()
                raise AgentTimeoutError(f"OpenAI API 応答 timeout（{timeout or self._timeout}s・{self.base_url}）")
            except asyncio.CancelledError:
                self.last_stop_reason = "cancelled"
                self._messages.pop()
                raise
            except (urllib.error.URLError, OSError) as e:            # endpoint 落ち等＝無応答扱いで段階回復へ（app は落とさない）
                self.last_stop_reason = "timeout"
                self._messages.pop()
                raise AgentTimeoutError(f"OpenAI API 接続失敗（{self.base_url}・{type(e).__name__}）")
            if self._cancelled:                                      # 待機中に barge-in された＝結果は捨てる（ADR-0006）
                self.last_stop_reason = "cancelled"
                self._messages.pop()
                return ""
            content = (((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            self._messages.append({"role": "assistant", "content": content})
            self.last_stop_reason = (resp.get("choices") or [{}])[0].get("finish_reason") or "stop"
            if on_chunk and content:
                on_chunk(content)                                   # 非ストリーミング＝全文を1回（guard 経路は on_chunk 無しで戻り値のみ）
            return content

    async def cancel(self):
        """進行中ターンを畳む（cancel 優先・ADR-0006）。flag を立て、await の結果を prompt 側で破棄させる。
        HTTP 自体は止められないが local は速く従量課金も無い＝完了後に捨てるだけ。"""
        self._cancelled = True
        self.last_stop_reason = "cancelled"

    async def close(self):
        """破棄＝会話履歴をリセット（中座 / 再spawn で若返る・ADR-0027）。プロセスは持たないので他に後始末なし。"""
        self._messages = [{"role": "system", "content": self._system}]

    # --- HTTP（stdlib・to_thread から呼ぶ同期メソッド）---
    def _post(self, path, body):
        req = urllib.request.Request(
            self.base_url + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get(self, path):
        req = urllib.request.Request(
            self.base_url + path, headers={"Authorization": f"Bearer {self._api_key}"}, method="GET")
        with urllib.request.urlopen(req, timeout=min(self._timeout, 10)) as r:
            return json.loads(r.read().decode("utf-8"))
