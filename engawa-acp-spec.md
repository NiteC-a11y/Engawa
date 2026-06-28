# Engawa (縁側) — ACP マルチエージェント版 仕様書 v1

**Status:** Draft
**Scope:** V1 = Claude Code 同士のみ（GPT/Codex/Gemini は V2 以降）
**前提:** ローカル・個人利用（BYO ローカル Claude Code、サブスク認証）

---

## 1. 目的とゴール

複数の AI 人格が Discord ライクな UI 上で自律的に会話するデスクトップアプリ。
従来案（Anthropic/OpenAI API を直接叩く）に対し、本版は **各人格を ACP エージェント（Claude Code）として駆動する**。これにより：

- 推論をローカルの認証済み Claude Code に委譲し、自前 API キー管理を不要にする（Pencil/Zed/OpenACP と同型）
- 将来 Codex CLI / Gemini を**設定追加だけ**で人格として混在させられる（ACP の本来の旨味）

### 非ゴール（V1 では扱わない）

- GPT / Codex / Gemini 人格（V2）
- リモート ACP トランスポート（spec ロードマップ段階。V1 は stdio 固定）
- マルチユーザ / 配布（ToS の壁は §9 参照。V1 は作者本人のマシン専用）
- ACP セッションのクラウド同期

---

## 2. 用語

| 用語 | 意味 |
|---|---|
| **ACP** | Agent Client Protocol。Zed 発の、JSON-RPC 2.0 over stdio でクライアントとエージェントを繋ぐ標準（LSP の AI 版） |
| **Client（クライアント）** | UI を持ち接続を開始する側。本仕様では **Engawa 本体**がこれ |
| **Agent（エージェント）** | LLM ループを回す側。本仕様では **各人格＝claude-code-acp で起動した Claude Code プロセス** |
| **Session** | エージェント1接続あたりの会話コンテキスト。人格 1 = セッション 1（または1ターン1セッション。§5.3 で決定） |
| **Turn（ターン）** | 1 回の prompt→response サイクル |
| **Orchestrator** | 「次に誰が喋るか」「何を prompt として渡すか」を決める Engawa 内の中核ロジック。ACP 仕様外、Engawa 独自層 |

---

## 3. 全体アーキテクチャ

OpenACP の 3 層分割を Engawa 向けに再構成する。

```
┌─────────────────────────────────────────────┐
│ L0: UI 層 (pywebview + HTML/JS フロント)       │
│   Discord ライク。emoji アバター、ストリーミング表示 │
└───────────────────┬─────────────────────────┘
                    │ events / state
┌───────────────────▼─────────────────────────┐
│ L1: Orchestrator 層                           │
│   ターン制御、発話遅延モデル、ACTIVE/QUIET/AFK    │
│   状態機、@mention ルーティング                 │
└───────────────────┬─────────────────────────┘
                    │ 「人格Xにこの prompt を投げろ」
┌───────────────────▼─────────────────────────┐
│ L2: Session Bridge 層                         │
│   人格⇔セッションのライフサイクル、prompt キュー   │
│   permission ゲート、並列セッション管理           │
└───────────────────┬─────────────────────────┘
                    │ ACP (JSON-RPC over stdio)
┌───────────────────▼─────────────────────────┐
│ L3: Agent Connection 層                       │
│   claude-code-acp をサブプロセス起動            │
│   initialize / session/new / session/prompt   │
│   session/update ストリーム受信、fs/* 応答       │
└───────────────────┬─────────────────────────┘
                    │
        ┌───────────┴───────────┐
        ▼           ▼           ▼
   [Claude Code] [Claude Code] [Claude Code]
    人格A         人格B         人格C
```

- **L2/L3 が ACP のクライアント実装**。stdio 上の ndJSON（改行区切り JSON）で JSON-RPC 2.0 をやり取りする。
- **L1 が Engawa の独自価値**。ACP は「1ターン回す」までしか面倒を見ない。「誰が・いつ・何を起点に喋るか」は全部ここ。

---

## 4. ACP 接続仕様（L3 が実装すべき面）

### 4.1 トランスポート

- 各人格につき `claude-code-acp`（または `claude --acp`）を**サブプロセス**として spawn
- stdin/stdout 上で **ndJSON / JSON-RPC 2.0**
- 認証は ACP エージェント側が自前で持つ＝ローカル Claude Code の OAuth（サブスク）。Engawa は API キーを保持しない

### 4.2 ハンドシェイク

```jsonc
// Engawa → Agent
{ "jsonrpc":"2.0", "id":0, "method":"initialize", "params":{
  "protocolVersion": 1,
  "clientCapabilities": { "fs": { "readTextFile": false, "writeTextFile": false }, "terminal": false },
  "clientInfo": { "name":"engawa", "title":"Engawa", "version":"1.0.0" }
}}
```

- **fs / terminal は V1 では false 推奨**。人格はチャット用途であり、ファイル I/O やターミナルを許す理由がない。攻撃面・暴走面を最初から閉じる。
- 応答で `agentCapabilities`（loadSession / promptCapabilities / mcpCapabilities）を受領し保持。`loadSession` の可否は §7 のセッション再開方針に影響。

### 4.3 セッション開始

```jsonc
{ "method":"session/new", "params":{
  "cwd": "<人格ごとの作業ディレクトリ>",
  "mcpServers": []   // V1 は人格にツールを持たせない
}}
// → returns { "sessionId": "..." }
```

### 4.4 発話（Engawa → Agent）

```jsonc
{ "method":"session/prompt", "params":{
  "sessionId": "...",
  "prompt": [ { "type":"text", "text":"<Orchestrator が合成した発話プロンプト>" } ]
}}
```

> **核心:** ここで渡す `text` は人間入力ではなく、**Orchestrator が直前までの共有会話から合成したもの**。これが標準 ACP との唯一にして最大の差分。

### 4.5 ストリーム受信（Agent → Engawa, notification）

`session/update` を受信して種別ごとに処理：

| `update.sessionUpdate` | Engawa の扱い |
|---|---|
| `agent_thought_chunk` | （任意）デバッグ表示 or 破棄。UI には出さない |
| `agent_message_chunk` | **本文**。チャンクを連結し、タイピングインジケータ→確定メッセージとして描画 |
| `tool_call` / `tool_call_update` | V1 ではツール無効なので原則来ない。来たらログして無視 |

ターン終了は `session/prompt` の最終 response（`stopReason` 付き PromptResponse）で判定。

### 4.6 コールバック対応（必須）

ACP はエージェントからクライアントへも要求が飛ぶ。最低限：

- `session/request_permission` → V1 はツール無効なので基本来ないが、来た場合は **`{ "outcome":"selected", "optionId":"deny" }`** で安全側に倒す（応答しないとターンがブロックする点に注意）
- `fs/read_text_file` / `fs/write_text_file` → clientCapabilities.fs=false なので拒否

### 4.7 キャンセル

- ユーザが会話を止める / 人格を AFK にする際は `session/cancel`（notification）を送る。エージェントは LLM リクエストを即停止すべき、と仕様で定義。

---

## 5. ペルソナモデル

### 5.1 問題

全人格が**同一の Claude Code バイナリ**。デフォルトのままでは「コーディングエージェント」として振る舞い、人格差も出ない。2 つを解く必要がある：

1. 人格の差別化
2. 「コーダー」を「会話者」に矯正する

### 5.2 差別化と矯正の手段

- **人格の identity は自前 system prompt で定義する**。`claude_code` プリセットを読み込まない＝コーディング指針を競合させない。口調・スタンス・知識領域・「技術タスクをこなすのではなく縁側で雑談する一人格だ」という枠付けはすべてここに置く（公式も「surface/identity/権限モデルが違うエージェントは自前 system prompt を書け」と明言）
- **CLAUDE.md は system prompt ではない。**"最初のユーザーメッセージ"として注入される仕様なので、ここには**プロジェクト文脈**だけを置く。人格コアは system prompt 側、という役割分担を守る
- **人格ごとに独立した cwd** を割り当て、設定（output-style / CLAUDE.md 等）を人格単位で隔離する
- ツールは付与しない（mcpServers=[]、fs/terminal=false）＝ファイルを触る誘惑自体を断つ
- 各ターンの prompt 合成で「あなたは〈人格名〉。〈直近の会話〉に対し、自分自身として一言返す」を明示

### 5.3 設計上の分岐：セッション持続 vs 1ターン1セッション

| 方式 | 利点 | 欠点 |
|---|---|---|
| **人格ごとに長命セッション** | 自然な記憶蓄積 | コンテキスト肥大→compaction→遅延/コスト増。Naraku で踏んだ compaction 問題が再来 |
| **1ターン1セッション（毎回 transcript を再投入）** | コンテキスト完全制御、予測可能、prompt caching が効く | エージェント自身の作業記憶を失う、入力トークン再課金（cache で緩和） |

**V1 推奨：1ターン1セッション + transcript 再投入。**
理由：人格はタスク蓄積型ではなく会話型。「直近 N 発言＋人格定義」を毎回 Engawa が組んで渡す方が、制御も再現性も高く、Naraku の compaction 地獄を回避できる。共有会話の真実は Engawa の SQLite が持つ（§7）。長命記憶が要るなら、要約を CLAUDE.md か prompt に注入する kaizen.md 方式を後付けする。

---

## 6. Orchestrator（L1）

ACP 仕様外。Engawa の独自価値はここに集中する。

### 6.1 ターン制御

- 単純ラウンドロビンは「機械的＝ボット臭さ」の元。避ける
- 次話者の選定：直前発言の `@mention`、話題への関連度、各人格の state（§6.3）を加味
- @mention ルーティングは人間 / AI 共通（hyojo の discussion.md 設計と同じ思想）

### 6.2 発話遅延モデル（ボット臭さ対策）

- 応答開始までの遅延を**話題の難易度の関数**として与える（難＝考え込む間、易＝即レス）
- 一定間隔の禁止。揺らぎを入れる

### 6.3 ACTIVE / QUIET / AFK 状態機

- 各人格に状態を持たせ、「常に全員が即レス」という不自然さを排除
- AFK 中の人格はターン選定から除外し、必要なら `session/cancel` で進行中ターンも畳む

---

## 7. 永続化（SQLite）

Engawa が**共有会話の単一の真実**を保持する（ACP セッションは各プロセスの揮発的コンテキストに過ぎない）。

最小スキーマ（草案）：

```
personas(id, name, avatar_emoji, claude_md_path, cwd, state)
conversations(id, title, created_at)
messages(id, conversation_id, persona_id, role, content, created_at, in_reply_to)
turns(id, conversation_id, persona_id, session_id, prompt_text, stop_reason, started_at, ended_at)
```

- `messages` が UI とプロンプト合成の双方のソース
- `turns` は ACP セッションとの対応・デバッグ・コスト追跡用
- セッション再開（`session/load`）は `loadSession` capability 次第。V1 は 1ターン1セッションなので**原則不要**。Open Question 化（§11）

---

## 8. UI（L0）

- pywebview + HTML/JS、Discord ライク、emoji アバター
- `agent_message_chunk` のストリームを「タイピング中…」→確定メッセージに変換して描画（ACP のストリーミングと素直に噛む）
- 人格の state（ACTIVE/QUIET/AFK）をアバターに反映

---

## 9. 認証・課金・ToS の前提（重要・嘘なし）

- **動く根拠:** ACP エージェント（Claude Code）はローカルの自前認証＝サブスクを使う。Engawa は Anthropic 課金に一切触れない。Pencil/Zed と同じ「BYO ローカル Claude Code」型
- **課金:** 2026-06-15 のプログラマティック分離は**一時停止中**で、当面サブスク枠から消費される。ただし“延期”であって“撤回”ではない。自律ループ（まさに本アプリ）は将来締められる側。**土台にするな、いつでも梯子を外され得る前提で**
- **配布の壁:** Anthropic は事前承認なしにサードパーティ製品が claude.ai ログイン/レート枠を提供するのを許可していない。**V1 は作者本人のマシン専用**に限定することで回避。配布したくなったら「各ユーザが自前 Claude Code を BYO する」設計（Pencil 方式）が必要で、かつ現状グレーゾーン
- **GPT 側:** V2 で人格を増やす際、Claude 以外は別認証・別課金（Codex CLI のサブスク等）になる

---

## 10. 段階的スコープ（Phase Gate）

| Phase | 内容 | 人間の承認ゲート |
|---|---|---|
| **P1** | claude-code-acp 1 体と initialize→session/new→session/prompt→update が往復する最小実装 | 「ACP 往復が通った」 |
| **P2** | 2 人格を Orchestrator がラウンドロビンで交互発話、SQLite 永続化 | 「自律会話が成立」 |
| **P3** | 発話遅延モデル + ACTIVE/QUIET/AFK + @mention ルーティング | 「機械臭さが消えた」 |
| **P4** | UI ストリーミング描画、人格定義 CLAUDE.md の作り込み | 「観戦体験として成立」 |
| **V2** | Codex/Gemini 人格を**設定追加だけ**で混在（ACP の旨味の実証） | — |

---

## 11. Open Questions / リスク

1. **1ターン1セッションのコスト** — transcript 再投入の入力トークンが会話長に比例。prompt caching でどこまで吸収できるか要計測
2. **session/load 対応状況** — claude-code-acp が loadSession を advertise するか未確認。長命記憶方針に直結
3. **claude-code-acp の起動形態** — `claude --acp` ネイティブ vs `claude-code-acp` アダプタ、どちらが安定か。`--hide-claude-login` フラグや OAuth 拾いの挙動に既知の落とし穴あり（前掲 JetBrains スレ）
4. **人格注入の経路** — 矯正手段自体は確立済み（自前 system prompt で `claude_code` プリセットを捨てる＋ツール無効）。会話駆動の安定性も Pencil/Zed/OpenACP で実証済みで、ここは不安要素ではない。残る確認点は一つだけ：**claude-code-acp アダプタがその systemPrompt ノブを `session/new` で通すのか、それとも cwd の output-style / CLAUDE.md 経由で渡す形になるのか**。「動くか」ではなく「どの口から注入するか」の実装ディテール
5. **request_permission のブロッキング** — 応答漏れでターンが固まる。タイムアウト/自動 deny を堅牢に
6. **コンテキスト肥大時の振る舞い** — 長命方式を選ぶ場合の compaction 戦略（Naraku の kaizen.md/外部状態方式を流用できるか）

---

## 12. 受け入れ基準（V1 / P1–P4）

- [ ] claude-code-acp を spawn し、initialize で agentCapabilities を受領できる
- [ ] session/new で sessionId を取得できる
- [ ] Orchestrator が合成した prompt を session/prompt で投げ、agent_message_chunk を連結して 1 発言を得られる
- [ ] 2 人格が人間の介在なしに 10 ターン以上自律で会話を継続できる
- [ ] 会話が SQLite に永続化され、再起動後に閲覧できる
- [ ] 発話間隔に揺らぎがあり、ラウンドロビン丸出しでない
- [ ] fs/terminal/ツールいずれも無効で、人格がファイルシステムを触らない
- [ ] request_permission が来てもターンが固まらない（自動 deny）
