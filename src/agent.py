#!/usr/bin/env python3
"""agent.py — LLM に届く経路の中立ポート（ADR-0026）。

ドメイン（`Scheduler` / `conversation` の `Speaker`）が触るのはこの面だけ＝実体（ACP か API か）を
知らない。現行の実装は `acp.AcpAgent`（ACP アダプタ）。将来 `OpenAIAgent`（ローカル OpenAI 互換 API）を
同じ面に並べる（ADR-0026）。生成は composition root（`engawa_main`）が factory を注入し、`Scheduler` は
`acp` を import しない（timeout は下の中立例外だけを捕捉する）。

ここは他のプロジェクトモジュールを import しない葉（typing だけ）＝循環なし。
"""
from typing import Callable, Optional, Protocol


class AgentTimeoutError(TimeoutError):
    """Agent の1ターンが制限時間内に返らなかった（adapter/endpoint 無応答 等）。実装固有の timeout 例外は
    これを継承して正規化する（`acp.ACPTimeoutError` は本例外の subclass）。呼び側（Scheduler）は実体に
    依らずこの中立型だけを `except` する＝住人=段階回復／客人=退場 の分岐が ACP でも API でも共通に効く。"""


class Agent(Protocol):
    """LLM に届く中立ポート（構造的 Protocol＝明示継承は不要）。ドメインが使う面はこれだけ:
    - `prompt(text, on_chunk)` … 1ターン注入し応答本文を返す（streaming は on_chunk へ・**必須**）
    - `cancel()` … 進行中ターンを畳む（cancel 優先・ADR-0006・**必須**）
    - `close()` … 破棄（プロセス/セッションの後始末）
    - `model` / `reported_model` … 要求モデル / 実装が報告した実モデル（/model 表示用・無ければ None）
    - `last_stop_reason` … 直近ターンの stopReason（cancel 時 'cancelled' / timeout 時 'timeout'）
    生成は factory（例 `AcpAgent.spawn_resident`）で行い、composition root が Scheduler に注入する。"""
    model: Optional[str]
    reported_model: Optional[str]
    last_stop_reason: Optional[str]

    async def prompt(self, text: str, on_chunk: Optional[Callable[[str], None]] = None,
                     timeout: Optional[float] = None) -> str: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...
