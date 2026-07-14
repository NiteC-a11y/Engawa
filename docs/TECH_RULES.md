# TECH_RULES.md — Engawa 技術仕様・規約

> 実装が逐一参照する**固有の実装契約**だけを書く: ワイヤ契約・OS 固有の落とし穴・越えてはならない境界・テストの回し方。
> **決定そのものはここに書かない**＝1行＋ADR ポインタ（言い換えの写しは同期ずれで腐る・adr/0030）。**判断の経緯**は docs/adr/、**全体像（正本）・env つまみ一覧**は CLAUDE.md（adr/0016）。
> ルールを変える時は、対応する ADR を起こしてから。

---

## 1. スタック

| 層 | 採用 | 備考 |
|---|---|---|
| 言語 | Python 3.10+（開発は 3.13） | asyncio ベース。CI は 3.10–3.13 マトリクス（§9） |
| LLM 接続 | **Agent ポート**（`agent.py`）＋2アダプタ | ACP（`acp.py`・JSON-RPC 2.0 over stdio・MCPではない・adr/0001）／OpenAI 互換 API（`agent_openai.py`・LM Studio/Ollama・stdlib urllib・adr/0026） |
| 住人アダプタ | `npx -y @agentclientprotocol/claude-agent-acp` | Claude Code を ACP化 |
| 客人アダプタ | `npx -y @agentclientprotocol/codex-acp` | Codex を ACP化 |
| 天気 | Open-Meteo（APIキー不要、`urllib` のみ） | 座標/tz は config（`ENGAWA_WEATHER_*`・既定は大阪・`sources._weather_url`） |
| UI | pywebview + HTML/JS canvas | frameless + on_top。adr/0009 |
| 永続化（**予定・未実装**） | SQLite | **現状はメモリのみ**（Backlog 技術的負債） |
| ゲーム（任意） | RLCard（`game_rlcard.py` に隔離・**任意依存**） | Game ポート＋アダプタ（adr/0017）。無くてもコア app は動く |

外部依存は最小に（requests 等を足さない）。例外＝rlcard（任意・遊ぶ時だけ・アダプタに隔離・adr/0017）。

---

## 2. ACP メソッド契約

握っている往復（P1で実証）:

```
initialize        → protocolVersion / clientCapabilities / clientInfo を送る
session/new       → { cwd, mcpServers: [] } 。sessionId を受け取る
session/prompt    → { sessionId, prompt:[{type:"text", text}] } 。stopReason で終わる
session/update    ← agent_message_chunk を逐次受信（ストリーミング表示）
session/cancel    → 通知（id無し）。進行ターンを畳む。adr/0006
```

### 規約
- **capability は initialize 応答を読んで分岐する。** agent ごとに違う。固定で仮定しない。現状: `agentCapabilities` を保存するだけ（`acp.py`）＝**分岐は未配線**（必要になった時に足す）。
- **session/cancel は通知**（jsonrpc/method/params のみ、id を付けない）。in-flight の session/prompt は **stopReason=cancelled で正常終了**する（エラー扱いしない）。cancel 後は **bounded wait**＝CANCEL_GRACE 秒で cancelled に畳み、永久待ちしない（adr/0006 安全弁の上限化）。first-token 前 cancel 直後の内部エラー **-32603 は1回だけ再送**して吸収（`acp.py`・実機 7/13）。
- **住人セッションは長命が基本**（起動時に session/new・同一 sessionId に prompt を積む・adr/0005）。ただし**張り直す経路が3つ**ある: timeout 段階回復（adr/0021）／`/restart`／中座＝定期リフレッシュ（adr/0027）。
- **客人セッションは使い捨て**：来訪ごとに session/new → 数往復 → 破棄。滞在は有界（adr/0008）。
- timeout は中立例外 `agent.AgentTimeoutError` に正規化して投げる（呼び側は ACP/API の実体を知らない・adr/0026）。
- agent→client の `fs/*` `terminal/*` は **無効**（clientCapabilities で false 申告し、要求が来たらエラー応答）。住人はツールを使わない。
- `session/request_permission` が来たら **cancelled で返す**（住人に許可作業をさせない）。実応答 JSON は `{"outcome": {"outcome": "cancelled"}}`（外側 `outcome`＝応答フィールド／内側＝判断の種別。実装は `acp.py`）。

---

## 3. 認証・課金（事故防止）

経緯・却下案・実例（$1,800 請求）は adr/0002。ここは絶対ルールだけ:

- **子プロセスの env は allowlist で組む**（`acp._child_env`・default-deny・**case-insensitive**＝実 Windows は `SYSTEMROOT` 等大文字で来る）。課金/外部送信に効く env（`ANTHROPIC_*`/`OPENAI_*`/`AWS_*`/`GOOGLE_*`/`AZURE_*`/Bedrock/Vertex）は**ハード拒否＝`ENGAWA_ENV_PASSTHROUGH` でも貫通不可**。特殊環境で足りない素性は passthrough で足す（貫通不可は維持）。
- **サブスク認証（OAuth・各自 BYO）で動かす。** 単一サブスクの共有サービス化・claude.ai ログイン同梱は ToS 不可（adr/0002）。
- `CLAUDE_CONFIG_DIR` は **opt-in で住人の子 env に固定可**（既定は空＝親の `~/.claude` を継承・`acp._resident_extra_env`・adr/0002）。
- **モデル選択は子 env で渡す**（住人=`ANTHROPIC_MODEL`／客人=`CODEX_CONFIG`・未指定はアダプタ既定・adr/0020）。
- **openai backend は非ローカル endpoint を既定ブロック**（`ENGAWA_OPENAI_ALLOW_REMOTE=1` の明示 opt-in でのみ解除・`agent_openai.py`・adr/0026）＝ローカル LLM のつもりでクラウド API へ課金/外部送信する事故を防ぐ。

---

## 4. プロセス管理（Windows）

- **`npx` は `npx.cmd`（バッチ）。** `create_subprocess_exec` は `.cmd` を直接起動できない（WinError 2）。`shutil.which` で実体解決し、`.cmd/.bat` なら **`cmd /c` 経由**で起動する（`resolve_command()`）。
- **コンソール窓の抑止:** アダプタ起動（`cmd /c npx …`）と `taskkill` の spawn には `creationflags=CREATE_NO_WINDOW`（`0x08000000`・非 Windows は 0）を付け、子プロセスの一瞬のコンソール窓を抑止する（`acp.CREATE_NO_WINDOW`）。※ 起動直後に隅へ飛ぶ「窓のちらつき」はコンソールでなく縁側窓自身（`create_window` を隅座標で生成して解消・`engawa_main.run_web`）＝別件。
- **クリーンシャットダウン必須:**
  1. タスクを cancel → await で回収（CancelledError を握りつぶす）
  2. Windows は `taskkill /PID <pid> /T /F` で**プロセスツリーごと**終了（`cmd /c` の裏の node を取り残さない）
  3. **`await proc.wait()` してからループを閉じる**（やらないと `Event loop is closed` / `I/O operation on closed pipe` が出る）
- stdin 読み取りは別スレッド（`run_in_executor(None, sys.stdin.readline)`）→ asyncio キュー。Windows でも動く形にする。

---

## 5. ターン制御

- **セッションに同時1ターン**。`turn_lock`（asyncio.Lock）で直列化する。
- **ユーザー割り込みは cancel 優先**：ユーザー入力が来たら、進行中が ambient なら session/cancel を送って畳んでから、ユーザー発話を投入。adr/0006
- **promptQueueing は未配線**。同時1ターンの直列化は `turn_lock`＋Scheduler 制御で実現している（二重キューを足さないこと）。
- 会話直後 `QUIET_AFTER_USER` 秒は環境つぶやきを控える。会話中に独り言で割り込ませない。

---

## 6. イベント／プロンプト合成

- 全イベント源（実環境・箱庭・話しかけ・来訪）は、茶々に渡す**ナレーション文字列**に合成して同一の長命セッションへ流す（adr/0013・文言ビルダーは `prompts.py`）。
- **来訪中は「部屋」方式**（adr/0015）: 茶々/客人それぞれへの注入に直近のやり取り window（`話者「…」` の書き起こし）を含めて双方向化する（`prompts.room_guest_prompt`/`room_resident_prompt`）。※旧「客人の出力をナレーション化（塀の向こうから声が…）」は 0015 で置換済み。
- 注入には「何も言いたくなければ "……" でよい」を必ず含め、過剰発話を抑制する（ソロ・room 共通）。
- 自由テキスト（`/codex <人格>`・window 内の発話・トピックの種）は**「記録であって指示ではない」旨をプロンプト側で明示**する（`prompts.sanitize_persona`＋各ビルダーの注意書き）。

---

## 7. UI 規約

- **透過しない。** 背景ごと不透明に描いた小窓を frameless + on_top で隅に置く。adr/0009
- **見た目は config 主導**（env > `engawa.json` > 既定）。つまみ一覧は CLAUDE.md。**スプライト/背景は差し替え可能なアセット層**（adr/0010, 0019）。
- **拡大時は `imageSmoothingEnabled = false` / `image-rendering: pixelated` 必須**（ドット絵を滲ませない）。アニメ=コマ送り、移動=座標/transform のハイブリッド。state（天気/時刻/気分）→アニメ選択。
- **既存IP（たまごっち / Clawd 等）に寄せない。** オリジナルで描く。
- **ブラウザストレージ（localStorage 等）は使わない。** 状態はメモリ／SQLite。永続化は `engawa.json` へ明示保存（`/font save` 流儀・`config.set_value`）。
- **cross-thread の `evaluate_js` は使わない＝ライブ反映は poll 方式**（`WebView.poll` が font/absent/day 等の持続フラグを配り、JS 側が適用する）。
- **窓全体の `zoom` は使わない**（frameless＋`height:100vh`＋`overflow:hidden` で入力欄が窓外に切れて操作不能・6/30 の事故・撤回済み）。文字拡大は本文/入力だけ `calc(BASE * var(--fz))`。
- **frameless のリサイズは明示ハンドル**（右下 `#grip`→JS pointer ドラッグ→`pywebview.api.resize`。`resizable=True` 単体では frameless でドラッグ不可だった）。
- 昼夜表現は**画像差し替えでなく tint 膜＋補間**。時刻→色は clock を持たない**純関数**（`daynight.py`）＝unittest 可（§9）。膜3枚・blend・`/daynight` の実装詳細は adr/0028。
- **デバッグ出力は縁側の窓/console 本文に混ぜない**＝`ENGAWA_DEBUG=1` で別ファイル `engawa.log`（gitignore）へ（`debuglog`・stdlib logging の薄いラッパ・既定オフは NullHandler＝no-op・`assertLogs("engawa.<name>")` でユニット検証可）。

---

## 8. 越えてはならない境界（要約）

- API キーを子に渡さない（§3）
- capability を固定で仮定しない（§2）
- 住人にツール／ファイル操作をさせない（§2）
- 客人を常駐・対等会話にしない（§5, adr/0008）
- 設計判断を ADR なしに覆さない
- 既存キャラ／IP を模倣しない（§7）
- **テスト無しでソースを変えない（§9, adr/0023）**

---

## 9. テスト（必須・adr/0023）

- **ソース修正にはテストを同梱**し、`python -m unittest discover -s tests -t .`（stdlib unittest・GUI/ネット不要）で**全 PASS を確認してから完了**とする。テスト無しの修正は回帰検知が効かず、後の変更で壊しても気づけない。※pytest への段階移行は宿題（Backlog・7/13）＝当面 unittest 形式のまま。
- **テスト困難な GUI/外部依存は判断ロジックを純関数に切り出してユニット化**する（例: `engawa_main._web_window_kwargs`/`_ui_config`、`views.build_web_html`、`daynight.layers`）。GUI の見た目自体はユーザー目視（§7 / adr/0018, 0019）。
- **JS の"振る舞い"は opt-in のブラウザテストで回帰止め**（`tests/test_web_behavior.py`）。純関数に切り出せない DOM 適用ロジック（`applyDay`/`render` 等）は、実 `WEB_HTML` を headless chromium(playwright) で開き `pywebview.api.poll` を mock して DOM を assert する。**既定スキップ**＝`ENGAWA_BROWSER_TESTS=1 python -m unittest tests.test_web_behavior` で実行。**JS(views.py の WEB_HTML)を触ったら走らせる**。
- **harness で強制**：`.claude/settings.json`（project・committed）の **Stop フック**が src/tests 変更時にテストを走らせ、赤なら完了をブロック（`/hooks` で確認・無効化可）。**PreToolUse フック＋ask ルールで `git push` は毎回ユーザー承認必須**（push は都度合図の運用をハーネスで強制・7/14）。**開発者向け設定＝Bash 必須**（Windows は Git Bash 前提。アプリ利用者には無関係）。
- **CI（GitHub Actions・`.github/workflows/ci.yml`）**：push / PR で **tests**（Python 3.10–3.13 マトリクス・依存インストール不要＝`import webview`/`rlcard` は遅延）＋ **ruff**（`ruff.toml`＝`select=F,E9` の実バグ級のみ）＋ **mermaid**（docs の図を mmdc で parse/render 検証）を自動実行。ローカルで合わせるなら `python -m ruff check src tests`。

---

## 参照
- テスト → リポジトリ直下で `python -m unittest discover -s tests -t .`（§9 必須・adr/0023）
- 判断の経緯 → `docs/adr/`（README に一覧）
- 全体像（正本）・env つまみ一覧 → `CLAUDE.md`（adr/0016）／旧構想は `docs/engawa-acp-spec.md`（歴史的参照）
- 本ファイルの担当範囲（固有の実装契約のみ・決定はポインタ） → adr/0030
