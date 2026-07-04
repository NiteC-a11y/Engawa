# TECH_RULES.md — Engawa 技術仕様・規約

> 実装が逐一参照する据え置きルール。**判断の経緯**は docs/adr/、**全体像（正本）**は CLAUDE.md（adr/0016）。ここには「どう作るか」の確定事項だけ書く。
> ルールを変える時は、対応する ADR を起こしてから。

---

## 1. スタック

| 層 | 採用 | 備考 |
|---|---|---|
| 言語 | Python 3.13 | asyncio ベース |
| Agent駆動 | **ACP**（Agent Client Protocol, JSON-RPC 2.0 over stdio） | MCPではない。adr/0001 |
| 住人アダプタ | `npx -y @agentclientprotocol/claude-agent-acp` | Claude Code を ACP化 |
| 客人アダプタ | `npx -y @agentclientprotocol/codex-acp` | Codex を ACP化（P4） |
| 天気 | Open-Meteo（APIキー不要、`urllib` のみ） | 大阪 lat 34.6937 / lon 135.5023 |
| UI（P5） | pywebview + HTML/JS canvas | frameless + on_top。adr/0009 |
| 永続化（**予定・未実装**） | SQLite | spec §11。residents/guests/events/messages/sessions。**現状はメモリのみ**（Backlog 技術的負債） |
| ゲーム（任意） | RLCard（`game_rlcard.py` に隔離・**任意依存**） | AI が既存ゲームに参加。Game ポート＋アダプタ（ADR-0017）。無くてもコア app は動く（遊ぶ時だけ `pip install rlcard`） |

外部依存は最小に。天気は標準ライブラリのみで取る（requests等を足さない）。**例外＝ゲームの rlcard（任意・遊ぶ時だけ・アダプタに隔離・ADR-0017）。**

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
- **capability は initialize 応答を読んで分岐する。** agentごとに違う（fork/resume/list/promptQueueing 等）。固定で仮定しない。
  - 現状: `agentCapabilities` を保存するだけ（`acp.py`）。実際の **capability 分岐は未配線**＝必要になった時に足す。
- **session/cancel は通知**（jsonrpc/method/params のみ、id を付けない）。受領後 in-flight の session/prompt は **stopReason=cancelled で正常終了**する（エラーではない）。エラー扱いしないこと。
- **住人セッションは長命**：session/new は起動時1回。以後 prompt を同一 sessionId に積む。adr/0005
- **客人セッションは使い捨て**：来訪ごとに session/new → 数往復 → 破棄。滞在は往復数で上限を切る。adr/0008
- agent→client の `fs/*` `terminal/*` は **無効**（clientCapabilities で false 申告し、要求が来たらエラー応答）。住人はツールを使わない。
- `session/request_permission` が来たら **cancelled で返す**（住人に許可作業をさせない）。実応答 JSON は `{"outcome": {"outcome": "cancelled"}}`（ACP `RequestPermissionResponse`：外側 `outcome`＝応答フィールド／内側＝判断の種別。実装は `acp.py`）。

---

## 3. 認証・課金（事故防止）

**絶対ルール:**
- 子プロセスの env から **`ANTHROPIC_API_KEY` を必ず除去**してから spawn する。
  - 理由: 認証情報の優先順位で API キーが OAuth より優先される。残ると意図せず API 従量課金（`claude -p` がキーを継いで $1,800 請求の実例）。adr/0002
- サブスク認証（OAuth）で動かす。**個人利用限定。** 配布する場合は各ユーザ BYO か API キー（claude.ai ログイン同梱は ToS 違反）。
- アカウント取り違え防止に、必要なら子 env に `CLAUDE_CONFIG_DIR` を明示で渡す（例 `~/.claude-main`）。会社org に吸われるのを防ぐ。
- **モデル選択は子 env で渡す**（config 主導・`ENGAWA_MODEL`/`ENGAWA_CODEX_MODEL` → 既定はアダプタ任せで現状維持）。住人(Claude)は `ANTHROPIC_MODEL`（Claude Code が尊重・`opus`/`claude-opus-4-8`/`opus[1m]` 等）、客人(codex)は `CODEX_CONFIG` の `{"model": …}`（codex-acp が Codex 設定へマージ）。サブスク認証でも有効。API キーと違い課金事故とは無関係だが、注入経路は同じ子 env（`acp.py` `_child_env`）。

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
- **promptQueueing** は環境イベント同士の整列に使う想定。ユーザー割り込みには使わない（待たせない）。
  - 現状: **promptQueueing は未配線**。同時1ターンの直列化は §5 の `turn_lock`＋Scheduler 制御で実現している（promptQueueing 経路は存在しない＝二重キューを足さないこと）。
- 会話直後 `QUIET_AFTER_USER` 秒は環境つぶやきを控える。会話中に独り言で割り込ませない。

---

## 6. イベント／プロンプト合成

- 全イベント源（実環境・箱庭・話しかけ・来訪）は、茶々に渡す**ナレーション文字列**に合成して session/prompt へ流す（spec §6）。
- 客人（Codex）の出力は**そのまま茶々に渡さず、ナレーション化**して渡す（「塀の向こうから声が…」）。adr/0008
- ナレーションには「何も言いたくなければ "……" でよい」を必ず含め、過剰発話を抑制する。

---

## 7. UI 規約（P5）

- **透過しない。** 背景（空・障子・板の間）ごと不透明に描いた小窓を frameless + on_top で隅に置く。adr/0009
- **窓サイズは config 主導**：`ENGAWA_UI_W`/`ENGAWA_UI_H`（既定 400×520）を `engawa.json[ui]` / env で（env>json>既定・ADR-0020 流）。**リサイズは右下グリップ**（frameless は掴む縁が無いので明示ハンドル `#grip`→JS pointer ドラッグ→`pywebview.api.resize`→`window.resize`・min 240 クランプ。`resizable=True` 単体では frameless でドラッグ不可だった）。配置は `ENGAWA_UI_CORNER`（br/bl/tr/tl）/ `ENGAWA_UI_EASYDRAG`。
- **文字サイズは config 主導**（`ENGAWA_UI_FONT`・既定 1.0・目が悪い人向け）。本文/入力のフォントだけを `calc(BASE * var(--fz))` で拡大＝`#log`(overflow:auto) はスクロール・`#bar` は flex で残る＝**入力欄を押し出さない**。**窓全体の `zoom` は使わない**（frameless＋`height:100vh`＋`overflow:hidden` で拡大すると入力欄が窓外に切れて操作不能・6/30 の事故・撤回済み）。
- **文字サイズはアプリ内でライブ調整可**（`/font <倍率>`・ADR-0007 の縁側操作＝茶々に流さない）。`Scheduler._cmd_font`→`View.set_font(scale)`（web だけ実装・console は no-op）。ライブ適用は**cross-thread な `evaluate_js` を使わず poll 方式**：`WebView.poll`/`game_poll` が `font` を返し、JS が `--fz` を差し替える（本窓・観戦窓とも）。**永続化は明示保存**（`/font save`→`config.set_value("ui","font",…)` が `engawa.json[ui].font` へ書き戻す＝localStorage 禁止と両立・config 主導）。`ENGAWA_UI_FONT`(env) は json より優先なので save 後も env が立てば env が効く（保存時に告知）。クランプ 0.8〜2.2 は `engawa_main._ui_config` と `scheduler.UI_FONT_MIN/MAX` を揃える。
- **観戦窓(GAME_HTML)も同じ `ENGAWA_UI_FONT` で拡大**（本窓だけ拡大の不整合を避ける）。盤は文字＋カード箱(`.card`)＋行を `calc(BASE * var(--fz))` で揃え、**窓サイズも font 倍**に（`build_game_html(font)`／`set_layout(...,font)`／`game_open`）＝盤がはみ出さない。
- **ドット絵は差し替え可能なアセット層**。state機構・ループはコード、スプライトは別（Aseprite製シートに後で差し替え）。adr/0010
- 拡大時は **`imageSmoothingEnabled = false` / `image-rendering: pixelated`** 必須（ドット絵を滲ませない）。
- アニメ=コマ送り（パラパラ）、移動=座標/transform、のハイブリッド。state（天気/時刻/気分）→アニメ選択。
- 既存IP（たまごっち / Clawd 等）に寄せない。オリジナルで描く。
- ブラウザストレージ（localStorage 等）は使わない。状態はメモリ／SQLite。
- **デバッグ出力は縁側の窓/console 本文に混ぜない**＝別ファイル `engawa.log`（gitignore）へ。`ENGAWA_DEBUG=1`（config・既定オフ）で `debuglog`（stdlib logging の薄いラッパ）が主要ライフサイクル（種の注入/来訪/room/cancel/timeout ＋ inject/user input/say/next beat）を **日付＋ミリ秒**（`YYYY-MM-DD HH:MM:SS.mmm`）で吐く＝会話タイミングを定量観測できる。off は NullHandler＝`log.debug` は no-op（本番の負荷ゼロ）。各モジュールは `debuglog.get("<name>")` の子ロガー、`setup` は composition root が1度だけ。個々の debug 出力は `assertLogs("engawa.<name>")` でユニット検証できる。

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

- **ソース修正にはテストを同梱**し、`python -m unittest discover -s tests -t .`（stdlib unittest・GUI/ネット不要）で**全 PASS を確認してから完了**とする。テスト無しの修正は回帰検知が効かず、後の変更で壊しても気づけない。
- **テスト困難な GUI/外部依存は判断ロジックを純関数に切り出してユニット化**する（例: `engawa_main._web_window_kwargs`/`_ui_config`、`views.build_web_html`）。GUI の見た目自体はユーザー目視（§7 / adr/0018,0019）。
- **harness で強制**：`.claude/settings.json`（project・committed）の **Stop フック**が src/tests 変更時にテストを走らせ、赤なら完了をブロック（`/hooks` で確認・無効化可）。**開発者向け設定＝Bash 必須**（`grep`/`tail` を使う。Windows は Git Bash 前提）。アプリの利用者には無関係だが、この repo を Claude Code で開く開発者が Windows で Bash 不在だとフックが動かない点に注意。

---

## 参照
- テスト → リポジトリ直下で `python -m unittest discover -s tests -t .`（stdlib unittest のみ・GUI/ネット不要・`tests/`・§9 必須・adr/0023）
- 判断の経緯 → `docs/adr/`（README に一覧）
- 全体像（正本） → `CLAUDE.md`（adr/0016）／旧構想は `docs/engawa-acp-spec.md`（歴史的参照）
- 住人の心得 → `CLAUDE.md`
