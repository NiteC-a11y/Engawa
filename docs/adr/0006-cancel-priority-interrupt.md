# ADR-0006: ユーザー割り込みは cancel 優先

- ステータス: Accepted（2026-06-27 実装 P3）
- 日付: 2026-06-26（決定）/ 2026-06-27（実装）
- 関連: ADR-0005, ADR-0007

## 背景 / 課題
茶々が環境つぶやきの最中に、ユーザーが話しかけたらどう振る舞うか。

## 決定
ユーザー入力が来たら、進行中の ambient ターンを **session/cancel（通知）で畳んでから**、ユーザー発話を同一セッションに投入する。「話しかけたら、つぶやきをやめてこっちを向く」。

## ACP仕様の確認
- session/cancel は **通知**（id無し・返事不要）。
- 受領後 agent はLM要求を停止し、in-flight の session/prompt は **エラーでなく stopReason=cancelled で正常終了**する（クライアントが誤ってエラー表示しないため、agentが中断エラーを握りつぶす）。

## 検討した代替案
- **promptQueueing（キュー積み）**: 環境イベント同士の整列には使う。ユーザー割り込みに使うと「待たせる」ことになり生き物らしさに欠ける。役割を分けた。

## 影響 / 帰結
- P3で実装。
- 安全弁: 環境つぶやきは元々短いので、仮に cancel が効かなくてもターンがすぐ終わり、ハングしない。
- 安全弁の上限化（2026-06-29・codexレビュー S1 **実装済み**）: adapter が cancelled 応答を握り潰す/遅らせる場合に in-flight prompt が `PROMPT_TIMEOUT`(既定240s)まで待つのを避け、`AcpAgent.cancel()` が `CANCEL_GRACE`(既定10s)で in-flight prompt を合成 `stopReason=cancelled` として畳む（`ACPClient.abort_pending`／`_expedite_cancel`）。**timeout でなく cancelled** にするのは、ユーザー起因の意図的中断で住人の段階再起動カウンタ（ADR-0005 系）を進めないため。本当の adapter ハングは続く新ターンが通常 timeout で検出する。

## 備考
- 実機での cancel 挙動は **P3 で検証済み**（cancelled で正常終了・エラーにならない・2026-06-27）。cancel 後の bounded wait も実機確認済み（6/29）。
