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
    生成は factory（例 `AcpAgent.spawn_resident`）で行い、composition root が Scheduler に注入する。

    cancel の契約（ソロ barge-in と room barge-in の2呼び手が依存・ADR-0006/0031）:
    1. best-effort＝呼び手に例外を漏らさない。
    2. cancel 時点で in-flight だった prompt は **例外でなく正常復帰**する（AgentTimeoutError を投げない・
       戻り値は部分テキストでよい）。その結果を採用するかは呼び手の commit gate が決める＝採用してはならない。
    3. 決着までの時間は adapter 依存（ACP=CANCEL_GRACE 上限の bounded wait／OpenAI=自前 timeout 上限で
       HTTP 完走後に破棄）。呼び手はこの差に依存しない（cancel 後はロック解放を await するだけ）。
    4. cancel の完了は、サーバ側生成の停止や次 prompt の受付可能性まで**保証しない**（ACP の synthetic
       cancel 後の次 prompt は adapter 層の再送等で回復する・acp.py 参照）。"""
    model: Optional[str]
    reported_model: Optional[str]
    last_stop_reason: Optional[str]

    async def prompt(self, text: str, on_chunk: Optional[Callable[[str], None]] = None,
                     timeout: Optional[float] = None) -> str: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...
