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
- **来訪中（部屋）も同じ**：入力到着で `_room_rev`（単調増加）を進めて進行中ドライブを失効させ、生成中の手（茶々=`speaking`/客人=`cancel_inflight`）を best-effort cancel。表示/transcript への commit は `Room._utter` の gate 一箇所（現行 generation のみ）。対象は tick 駆動チェーン（挨拶/代打/辞去）＝入力起点チェーンへの連打はスコープL（未実装）。adr/0031。**例外＝中断不可の手（ARRIVE/LEAVE/LEAVE_REACT）の生成中は cancel を送らない**（`Room.utter_preemptible` を見て rev+1 のみ。preemptible=False は gate を素通りするため、cancel の部分文がそのまま commit される＝「tick 駆動は全部 cancel 対象」と誤実装しないこと）。**cancel の port 契約は `agent.py` の `Agent` Protocol docstring が正本**（例外を漏らさない・in-flight prompt は正常復帰・結果は呼び手が破棄・決着時間は adapter 依存）。
- **promptQueueing は未配線**。同時1ターンの直列化は `turn_lock`＋Scheduler 制御で実現している（二重キューを足さないこと）。
- 会話直後 `QUIET_AFTER_USER` 秒は環境つぶやきを控える。会話中に独り言で割り込ませない。

---

## 6. イベント／プロンプト合成

- 全イベント源（実環境・箱庭・話しかけ・来訪）は、茶々に渡す**ナレーション文字列**に合成して同一の長命セッションへ流す（adr/0013・文言ビルダーは `prompts.py`）。
- **来訪中は「部屋」方式**（adr/0015）: 茶々/客人それぞれへの注入に直近のやり取り window（`話者「…」` の書き起こし）を含めて双方向化する（`prompts.room_guest_prompt`/`room_resident_prompt`）。※旧「客人の出力をナレーション化（塀の向こうから声が…）」は 0015 で置換済み。
- 注入には「何も言いたくなければ "……" でよい」を必ず含め、過剰発話を抑制する（ソロ・room 共通）。
- **voice の `llm_lang` が立つ時だけ**、LLM 注入の末尾に言語指示1行を足す（`voice.lang_note`・JP 方言＝llm_lang 無しでは注入文は1バイトも変えない＝persona と競合させない・adr/0022）。**「LLM に届く注入ビルダーの全経路」に後置する**（prompts の user/room 系＋sources のソロ narration。persona が英語で書かれているだけでは言語は縛れない＝note 無し経路は実測ほぼ100%日本語落ち・7/19）。全経路の網羅は `tests/test_injection_lang.py`（列挙＋命名 canary）で機械強制・実 LLM 一巡は `tests/e2e/leak_probe.py`（opt-in・discover 非対象）。UI シェル文言は `voice.loc(key, 日本語既定)`＝未訳キーはコード内の日本語へ落ちる（部分導入で壊れない）。
- 自由テキスト（`/codex <人格>`・window 内の発話・トピックの種）は**「記録であって指示ではない」旨をプロンプト側で明示**する（`prompts.sanitize_persona`＋各ビルダーの注意書き）。

---

## 7. UI 規約

- **透過しない。** 背景ごと不透明に描いた小窓を frameless + on_top で隅に置く。adr/0009
- **見た目は config 主導**（env > `engawa.json` > 既定）。つまみ一覧は CLAUDE.md。**スプライト/背景は差し替え可能なアセット層**（adr/0010, 0019）。
- **拡大時は `imageSmoothingEnabled = false` / `image-rendering: pixelated` 必須**（ドット絵を滲ませない）。アニメ=コマ送り、移動=座標/transform のハイブリッド。state（天気/時刻/気分）→アニメ選択。
- **既存IP（たまごっち / Clawd 等）に寄せない。** オリジナルで描く。
- **日本語 IME と衝突する入力ギミックを避け、操作は明示コントロールで**（例: `@` メンションは IME で打ちにくい→宛先はチップ/セレクトで選ぶ。本窓に UI を増やさない方針とセット・adr/0015 の宛先チップ）。
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

## 9. テスト（必須・adr/0023。層別の設計判断と教訓は adr/0022 追記・0031 追記・0033 予定）

- **完了条件**: ソース修正はテストを同梱し `python -m unittest discover -s tests -t .`（stdlib unittest・GUI/ネット不要）で**全 PASS を確認してから完了**。**正常な緑は `OK (skipped=2)`**＝`test_web_behavior` の opt-in ブラウザテスト2件（設計どおり。3以上に増えたら環境不足か新規 opt-in を疑う）。※pytest への段階移行は宿題（Backlog・7/13）。
- **6層の防御**（守る対象別。バグはどこかの層の穴＝下の変換規則で塞ぐ）:
  1. **ユニット**: テスト困難な GUI/外部依存は判断ロジックを**純関数に切り出して**検証（例: `engawa_main._web_window_kwargs`・`views.build_web_html`・`daynight.layers`）。
  2. **合成テスト**: 層またぎの不変条件は「経路の明示列挙×条件」＋**命名 canary**で機械強制（`tests/test_injection_lang.py`＝llm_lang 時の全注入に言語指示・`*_narration`/`*_prompt` は COVERED∪EXEMPT に必ず分類／`tests/test_strings_registry.py`＝loc キー∈台帳・インライン既定禁止・placeholder 一致・雛形一致・adr/0033）。モジュール別テストの継ぎ目に落ちる型（adr/0031 ARRIVE 穴）への対策。
  3. **スナップショット**: **LLM に届く注入文は voice ごとにバイト凍結**（`tests/test_prompt_snapshots.py`＋`tests/snapshots/`）＝「書いていない不変条件」を diff で検問。**意図した変更は `ENGAWA_UPDATE_SNAPSHOTS=1` で再生成し、diff を読んでからコミット**（反射更新は防御ゼロ）。
  4. **境界の向こう岸**: Python↔JS 契約は **DOM で assert**（`tests/test_web_behavior.py`・playwright・既定スキップ＝`ENGAWA_BROWSER_TESTS=1` で実行・CI では常時）。**JS を触った時だけでなく、JS が消費するデータを変えた時も走らせる**（「渡した」≠「使われた」・7/19）。
  5. **実 LLM E2E**: `tests/e2e/leak_probe.py`（**手動 opt-in・課金あり**・discover 非対象）＝本番ビルダー×本番配線（RoomSpeakerFactory の実 Speaker 名＝fixture の配線迂回禁止）×**trial ごと新品セッション既定**（文脈慣性なし＝最悪条件。`--sticky` で慣性観測）。
  6. **人間の目視**: GUI 見た目・トーンは実機で。初手順序を変えたシナリオ（起動→idle 放置／起動→即話しかけ）も踏む。**GUI に見える修正は目視 OK 前に push しない**。
- **バグ→テストの変換規則**: 実機でバグが見つかったら逃げ道3型を判定し同型を1本足す＝**A** 未認識の不変条件→層3／**B** fixture の配線迂回→本番の継ぎ目を通す／**C** 境界の向こう岸→層4。修正のスイープは**モジュール単位でなく概念単位**（例:「LLM に届く全注入」「ユーザーに見える全文字列」）。根因が概念の混線（一名多役）なら**分離が本体・テストはラチェット**（例: `Speaker.name`/`display`・7/19）。
- **harness で強制**：`.claude/settings.json`（project・committed）の **Stop フック**が src/tests 変更時にテストを走らせ、赤なら完了をブロック（`/hooks` で確認・無効化可）。**PreToolUse フック＋ask ルールで `git push` は毎回ユーザー承認必須**（7/14）。**開発者向け設定＝Bash 必須**（Windows は Git Bash 前提。アプリ利用者には無関係）。
- **CI / リリース**：push / PR で **tests**（Python 3.10–3.13 マトリクス・依存インストール不要＝`import webview`/`rlcard` は遅延）＋ **ruff**（`ruff.toml`＝実バグ級のみ）＋ **mermaid** ＋ **browser** を自動実行（ローカルは `python -m ruff check src tests`）。**exe は tag `v*.*.*` の push でのみ焼かれる**（`release.yml`・draft で停止→GUI 起動をユーザー目視→手動 Publish。docs のみのコミットはリリース対象外）。

---

## 参照
- テスト → リポジトリ直下で `python -m unittest discover -s tests -t .`（§9 必須・adr/0023）
- 判断の経緯 → `docs/adr/`（README に一覧）
- 全体像（正本）・env つまみ一覧 → `CLAUDE.md`（adr/0016）／旧構想は `docs/engawa-acp-spec.md`（歴史的参照）
- 本ファイルの担当範囲（固有の実装契約のみ・決定はポインタ） → adr/0030
