# ADR-0001: ローカルagent駆動は MCP でなく ACP を使う

- ステータス: Accepted
- 日付: 2026-06-26
- 関連: ADR-0002, ADR-0008

## 背景 / 課題
API課金を避け、手元のローカル Claude Code / Codex をアプリ(Engawa)から駆動してLLMを動かしたい。当初は「アプリをMCP経由でClaude Codeに繋ぐ」構想だった。

## 決定
MCPではなく **ACP（Agent Client Protocol, JSON-RPC 2.0 over stdio）** を採用する。`claude-code-acp` / `codex-acp` アダプタを spawn してローカルagentを駆動する。

## 検討した代替案
- **MCP sampling（`sampling/createMessage`）**: サーバがクライアントのLLMに補完を要求できる機構で、理屈上はこの用途に合致する。却下理由は二重:
  1. Claude Code は sampling をクライアントとして実装していない（推論を頼む口が無い）。
  2. sampling 自体が MCP仕様 2026-07-28 (SEP-2577) で deprecated。新規実装は採用すべきでない、と明記。
- **アプリをMCPツールサーバ化**: Claude Codeが「頭」になりアプリは受動的ツールに留まる。自前UIで人格を動かす構図にならない。却下。
- **Agent SDK / `claude -p` 直叩き**: 可能だが人格チャットより重いコーディング向けループ。ACPの方が標準化された薄い口。

## 影響 / 帰結
- L3（Agent Connection層）が標準ACPで実装でき、Claude も Codex も同型に扱える（ADR-0008の前提）。
- P1で initialize → session/new → session/prompt → session/update 往復を実証済み。

## 備考
- ACPの一部（fork/resume/cancel 等）はまだ unstable 扱いの機能が混じる。capability は agent ごとに違うので initialize 応答を読んで分岐する。
