# CLAUDE.md — Engawa（縁側）

> このファイルは Claude Code が最初に読む「住人の心得」。**何を作っているか・どう動くか・何を守るか・次に何をやるか**を最短で掴むためのもの。
> 設計判断の経緯は **docs/adr/**、技術仕様・境界は **docs/TECH_RULES.md**、動くタスクの在庫は **docs/Backlog.md**。迷ったらそっちへ。
> ※このファイルが**現行全体像の正本**（adr/0016）。`docs/engawa-acp-spec.md` は旧構想（ピボット前・歴史的参照）。

---

## これは何か

**Engawa（縁側）** は、デスクトップの隅に住む AI の住人「茶々（ちゃちゃ）」と過ごす常駐アプリ。育てるでも働かせるでもなく、ただ"居る"。

- 茶々は**会話アシスタントではない**。縁側に住んでいて、時刻や天気にぽつりと反応し、話しかければ応える。
- 時々、客人（Codex）が役を着せて訪ねてくる（召喚＝`/codex`、または夕方に自発来訪）。
- 自分のマシンの Claude / ChatGPT **サブスク認証**で動く（個人利用・従量課金しない）。

ひとことで言うと「**環境に反応する単体の住人＋双方向＋客人来訪**」。
当初の「AI同士が自律会話するアプリ」からは**意図的に方向転換した**（理由は adr/0004）。

---

## いま動いているもの（現況・2026-07-03）

| Phase | 内容 | 状態 |
|---|---|---|
| P1 | ACP往復＋CLAUDE.md人格注入 | ✅ 実証済み |
| P2 | 長命セッション＋環境イベント(時刻・天気)で茶々がつぶやく | ✅ 実機成功 |
| P3 | 話しかけ＋割り込み(cancel優先)で双方向化 | ✅ 実機検証済み |
| P3.5 | 箱庭イベント＝アーク（雀/猫/風・起承転結・実天気従属） | ✅ 実装・検証済み |
| P4 | 客人来訪（/codex 召喚＋自発来訪＋世間話に時節トピック注入） | ✅ 召喚/自発とも**実 codex E2E 検証済み** |
| P5 | ドット絵UI（隅の縁側窓・frameless・スプライト・来訪演出・縁側背景） | ✅ Inc1〜4 実装。茶々は Gemini 三毛猫の**4表情正規化シート**(`chacha.png` 704×176・`display_px` で縮尺・`shadow_*` で接地影)、縁側背景 `scene.png`(障子＋板の間・data URI 埋め込み・無ければ CSS グラデ)搭載。**背景は実大阪時刻で昼夜 tint**(乗算色＋月明かり glow＋障子ごしの室内灯 lamp を時刻で lerp・絵1枚で朝昼夕夜＋月＋夜は部屋から灯り・`daynight.py`・adr/0028・`ENGAWA_DAYNIGHT=0`で固定・`/daynight demo` で移ろいを早送り確認)。**背景/茶々は差し替え可能な皮**(ADR-0010・`ENGAWA_SCENE_BG`/`ENGAWA_SPRITE_CONFIG`)。**GUI 見た目はユーザー目視で確認** |

→ 当初スコープ「環境に反応する単体の住人＋双方向＋客人来訪＋ドット絵UI」は**一通り実装・実機で稼働**。残りは磨きと新章（次にやること参照）。

### ファイル（レイアウト・adr/0018）
> コードは `src/`・実使用アセットは `assets/`・PoC基準点は `poc/`・文書は `docs/`。ユーザーが触る設定（`engawa.json` / `topic_sources.json`）と本 `CLAUDE.md` は **root 維持**。設定/アセットは `src/` から **repo-root 基準**で解決（`config.py` / `sources.py` / `views.py` の `_path()`）。
- `src/engawa_main.py` — 起動口（composition root）。console / `ENGAWA_UI=web` で隅の縁側窓。
- `src/agent.py` / `src/acp.py` / `src/agent_openai.py` / `src/persona.py` / `src/sources.py` / `src/scheduler.py` / `src/views.py` / `src/prompts.py` / `src/daynight.py` / `src/commands.py` — 現行構成（event-source/scheduler・adr/0013）。`agent.py` は **LLM 接続の中立ポート**（`Agent` Protocol＋`AgentTimeoutError`・adr/0026）＝`AcpAgent`（`acp.py`・Claude Code サブスク）と `OpenAIAgent`（`agent_openai.py`・ローカル OpenAI 互換 API＝LM Studio/Ollama・stdlib urllib・履歴自前保持）の**2実装**、`scheduler` は `acp` を import せず中立例外だけ捕捉＝住人/客人とも backend は **`ENGAWA_RESIDENT_BACKEND`/`ENGAWA_GUEST_BACKEND`（`acp|openai`）** で差し替え可（客人 openai は persona を prompt 注入・住人と同じ endpoint 共有）。`persona.py` は茶々の人格テキストを **backend 中立に一元化**（ACP は cwd の CLAUDE.md、OpenAI は system メッセージとして同じ文を注入）。`views.py` に `ConsoleView` と `WebView`（pywebview・poll方式）。`daynight.py` は時刻→背景の**昼夜 tint**（乗算色＋月明かり glow＋障子ごしの室内灯 lamp）を返す純関数＝poll が大阪時刻で毎回配り JS が #scene の膜3枚（multiply/screen×2）へ適用（画像差し替えでなく1枚の色膜を lerp・`ENGAWA_DAYNIGHT=0` で無効・`/daynight` でプレビュー・adr/0028）。`commands.py` は**スラッシュコマンドの Command パターン**（`CommandRouter`＋登録制・`ctx` は薄い adapter＝今は View だけ）＝`Scheduler._command` は登録済みなら Router へ委譲・未登録は従来の if/elif にフォールバック。**adr/0029 Phase 1 で `/font` `/daynight` を移譲**（残コマンドは依存 controller が出来てから）。`prompts.py` は LLM 文言ビルダー（注入プロンプト工場・`sources` から分離・`prompts→sources` 一方向 import）＋茶々ソロ出力の染み出しガード `strip_resident_leak`（注入文の復唱＋地の思考を表示前に除去・純関数）。
- `src/conversation.py` — 3人会話の部屋（State パターン・adr/0015 **Inc1/Inc2 実装済み**＋adr/0025 **代打**）。人間待ちで沈黙が続くと茶々が“人間役の代打”で場をつなぐ（`ResidentFilling`・予算 `fill_cap` 回で必ず辞去＝有界）。`src/game.py` + `src/game_rlcard.py` — ゲームの Port＆Adapter（adr/0017。rlcard は `game_rlcard` に隔離・任意依存）。
- `src/debuglog.py` — デバッグログ（stdlib logging の薄いラッパ）。`ENGAWA_DEBUG=1` で `engawa.log`（gitignore）へ主要ライフサイクルを **日付＋ミリ秒**（`YYYY-MM-DD HH:MM:SS.mmm`）で吐く＝会話タイミングを定量観測できる。イベント: 種の注入/来訪/room/cancel/timeout ＋ `inject 茶々 (kind)`（ソロ発話）/`user input`/`say who (kind)`（room 発話）/`next beat +Ns`（予定の間合い）/`茶々 中座へ`・`茶々 中座から復帰`（中座＝セッション更新・ADR-0027）。既定オフ＝縁側の窓/console 本文は汚さない・`log.debug` は no-op。各モジュールは `debuglog.get("<name>")` で子ロガー。
- `assets/sprite.json` + `assets/chacha.png`（茶々スプライト）／`assets/scene.png`（縁側の背景＝障子＋板の間）— **差し替え可能な皮**（adr/0010・背景にも拡張）。スプライトは Gemini 三毛猫の4表情正規化シート（0平常/1口開け/2目つむり/3反応）で、`display_px`＝縮尺（絵は固定・表示側でスムーズ縮小）／`shadow_w/dy/h`＝接地影／`animations`＝state→コマ。背景は透過なしPNGを `#scene` に dataURI 埋め込み（無ければ CSS グラデ＋障子/床プレースホルダにフォールバック）。**両方 env `ENGAWA_SPRITE_CONFIG`/`ENGAWA_SCENE_BG` か `engawa.json[assets]`（sprite_config/scene_bg）で好みの絵に丸ごと差し替え可**（`views._asset_path`）。`assets/raw/`（gitignore）は Gemini 生成元 PNG。
- `topic_sources.json`（root） — 客人の世間話トピックの取得先ホワイトリスト（config主導・adr/0014）。
- `engawa.json`（root・**個人設定＝gitignore**／雛形は `engawa.json.sample`） + `src/config.py` — アプリ挙動の設定（model/guest/間合い/topic）。優先順位 **env(ENGAWA_*) > engawa.json > 既定**。キーは入れない(adr/0002)。端末ごとに調整・全キー任意＝消せばコード既定（gitignore 個人設定＋追跡サンプルの流儀）。
- `engawa.bat` / `engawa-debug.bat`（root） — Windows ランチャ。`engawa.bat`＝web 常用（`set ENGAWA_UI=web` → 直 `python`）。`engawa-debug.bat`＝`ENGAWA_DEBUG=1`＋別窓で `engawa.log` を追尾。**cmd が cp932 でバッチを読む都合上 ASCII-only 厳守**（日本語コメントは化けて実行される）。
- `poc/engawa_p1/p2/p3_*.py` — 各フェーズの検証済み基準点。**温存・触らない**。
- `docs/adr/`（0001〜0027）, `docs/TECH_RULES.md`, `docs/Backlog.md`
- `docs/engawa-acp-spec.md` — ピボット前の**旧構想 仕様書 v1**（adr/0004 で転換・adr/0016 で降格）。歴史的参照として温存・**現行仕様ではない**。
- ~~`legacy/`~~ — 方向転換前の**旧実装**（adr/0004 で捨てた「AI雑談ルーム」・API 直叩き）は **2026-07-04 に削除**（実行できる従量課金 footgun のため・公開レビュー）。コードは **git 履歴**に、判断は **adr/0004** に残る＝歴史は失わない。非実行の歴史参照（`poc/`＝検証済み戻り先・`docs/engawa-acp-spec.md`＝旧構想）は温存。

---

## 起動

- **console（端末）**: `python src/engawa_main.py`（リポジトリ直下から実行）
- **web（隅の縁側窓・frameless）**: cmd で `set "ENGAWA_UI=web" && python src/engawa_main.py`（`$env:` は PowerShell 専用・cmd は `set`・空白混入回避でクォート）。**日常は `engawa.bat`（web）／`engawa-debug.bat`（デバッグ＋log tail 窓）をダブルクリックでも可**。
- **認証**: 先に `claude` と codex(ChatGPT) にサブスクでログイン。API キーは子 env から除去（adr/0002）。
- **主な env つまみ**: `ENGAWA_UI=web` / `ENGAWA_MODEL`（茶々=Claude のモデル・例 `opus`/`claude-opus-4-8`/`opus[1m]`）,`ENGAWA_CODEX_MODEL`（客人=codex のモデル）/ `ENGAWA_RESIDENT_BACKEND`,`ENGAWA_GUEST_BACKEND`（住人/客人の駆動＝`acp` 既定/`openai`＝ローカル LM Studio 等・adr/0026・客人 openai は住人と同じ endpoint 共有）,`ENGAWA_OPENAI_BASE_URL`,`ENGAWA_OPENAI_MODEL`,`ENGAWA_OPENAI_API_KEY`,`ENGAWA_OPENAI_TIMEOUT`,`ENGAWA_OPENAI_REASONING`,`ENGAWA_OPENAI_MAX_TOKENS`,`ENGAWA_OPENAI_ALLOW_REMOTE`（openai backend の endpoint/モデル/鍵ダミー/秒/reasoning_effort=既定 none で推論オフ＝Qwen3.5 等の長考抑止/出力上限/**非ローカル endpoint 許可＝既定0でブロック＝課金・外部送信の事故防止・原則#1**・既定 `http://localhost:1234/v1`）/ `ENGAWA_GUEST_PROB`,`ENGAWA_GUEST_FROM_HOUR`（自発来訪）/ `ENGAWA_GUEST_IDLE_LEAVE`,`ENGAWA_GUEST_FILL_CAP`,`ENGAWA_GUEST_FILL_AFTER`,`ENGAWA_GUEST_FILL_SLOWDOWN`（来訪中＝沈黙で辞去するtick数/人間不在時に茶々が代打で場をつなぐ回数=有界上限/最初の代打までの沈黙tick/代打間隔を回ごとに延ばす量＝賑やか→間延び→帰る・adr/0025）/ `ENGAWA_TOPIC_PROB`,`ENGAWA_TOPIC_COOLDOWN`,`ENGAWA_TOPIC_REFRESH_MIN`,`ENGAWA_TOPIC_CONFIG`（トピック＝空気に混じる確率/粘着防止の間隔/更新間隔/取得先）/ `ENGAWA_UI_CORNER`,`ENGAWA_UI_EASYDRAG`,`ENGAWA_UI_W`,`ENGAWA_UI_H`,`ENGAWA_UI_FONT`,`ENGAWA_UI_ENTER`（窓＝隅/移動/幅/高/文字倍率/入力欄の Enter＝`send`(既定・Enter送信/Shift+Enter改行) or `newline`(Enter改行/Ctrl+Enter・送信ボタンで送信)・右下グリップでリサイズ・engawa.json[ui]可）/ `ENGAWA_SPRITE_CONFIG`,`ENGAWA_SCENE_BG`（茶々スプライト/縁側背景の差し替え＝皮・`engawa.json[assets]` 可・adr/0010）/ `ENGAWA_DAYNIGHT`（背景の昼夜 tint＝実大阪時刻で乗算色＋月明かり＋室内灯を lerp・既定1・0で固定背景・`engawa.json[ui].daynight` 可・**アプリ内 `/daynight on|off` でライブ切替＋保存**・`/daynight demo` でプレビュー・adr/0028）/ `ENGAWA_TICK_MIN/MAX`,`ENGAWA_ARC_PROB`（間合い）/ `ENGAWA_DEBUG`,`ENGAWA_LOG_FILE`（デバッグログ＝`engawa.log` へ主要ライフサイクル・既定オフ）/ `ENGAWA_RESIDENT_GUARD`（茶々ソロ出力の染み出しガード＝注入プロンプトの復唱＋地の思考を表示前に除去・既定1・0で従来の逐次stream。長命セッション劣化で出力が崩れた時は `/restart` で張り直し）/ `ENGAWA_ABSENCE_AFTER_TURNS`,`ENGAWA_ABSENCE_JITTER`,`ENGAWA_ABSENCE_GAP`（茶々の「中座」＝世界観に溶かした定期セッション更新＝染み出しの根治・ADR-0027。発話が溜まったら次のidleで中座し不在の裏で若返る/タイミングのゆらぎ/不在秒・既定30/10/18・after_turns=0で中座オフ）。**これらは `engawa.json` にも書ける＝永続（env が優先・adr原則4のconfig主導）**
  - モデル指定の仕組み: 住人は子 env の `ANTHROPIC_MODEL`（Claude Code が尊重）、客人は `CODEX_CONFIG`（codex-acp が Codex 設定へマージ）に載せる。**未指定はアダプタ既定のまま（現状の挙動を変えない）**。サブスク認証でも有効。
- **スラッシュ**: `/codex <人格>`（客人召喚）/ `/game <id> [見る]`（id=blackjack/uno/leduc・私+茶々／「見る」で観戦・客人 codex は基本不要・要 `pip install rlcard`・ADR-0017。`/blackjack` は別名）/ `/arc [雀|猫|風]`（箱庭再生・デバッグ）/ `/daynight [on|off|HH:MM|demo|auto]`（背景の昼夜・`/arc` と同筋のデバッグ再生＋on/off＝機能の有効無効を `engawa.json[ui].daynight` に**保存**（ライブ反映・`/font save` 方式）／`HH:MM`=時刻固定・`demo`=夕→夜を早送り＝実時間を待たず移ろいを確認・`auto`=プレビュー解除して実時間へ・web のみ・ADR-0028）/ `/model`（今のモデル表示・住人/客人）/ `/font [倍率|save]`（web 文字サイズをアプリ内でライブ調整＝再起動不要・`/font` で現在値・`/font save` で `engawa.json[ui].font` へ保存＝明示保存・console は端末フォント依存で no-op）/ `/restart`（茶々のセッションを張り直す＝出力の染み出し/不調時・文脈はリセット・timeout 段階回復と同じ respawn 経路）/ `/help` / `/quit`

---

## どう動くか（1枚で）

```
茶々（縁側の住人・1人称・関西寄り・長命セッション）
  ← 実環境イベント（時刻・大阪の天気）        … 自発のつぶやき（実天気が真実・adr/0012）
  ← 箱庭イベント＝アーク（雀/猫/風・起承転結）  … 単調さ破り。実天気に従属（adr/0011,0012）
  ← ユーザーの話しかけ（通常テキスト）         … cancel優先で割り込み（アーク中でも背景継続）
  ← 客人 Codex の来訪（/codex or 自発）        … 世間話に時節トピックを注入（adr/0008,0011,0014）

入力2系統:  通常テキスト → 茶々への話しかけ / スラッシュ → 縁側への操作
出力:  View ポート（ConsoleView / WebView）。web は茶々スプライトが state→コマ＋呼吸でアニメ、
        客人来訪は「庭先の気配＋茶々の反応」で演出（adr/0009,0010 / Inc4）。
```

- 茶々の人格は **cwd の CLAUDE.md**（アダプタに渡す方・このファイルとは別物）で注入。客人の人格は CLAUDE.md でなく**召喚時に prompt へ動的注入**（adr/0008）。
- 全イベントは最終的に茶々の **同一の長命セッション** に流れ、文脈が地続きになる。ただし長時間で溜まる劣化（染み出し）対策として、茶々は**たまに「中座」してセッションを裏で張り直す**（世界観に溶かした定期リフレッシュ＝idle 限定・ADR-0027）。

---

## 守ること（原則）

1. **課金事故を出さない。** 子プロセスから API キー（`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`）を必ず除去。サブスク認証で動かす（adr/0002）。
2. **茶々を「住人」から外さない。** コーディング助手化・過剰な長文・毎ターン律儀な名言は人格破壊。「黙っていい・短くていい」。
3. **AI同士の*自律・無際限*会話に戻さない。** 客人は環境イベント＝来訪（常駐させない・滞在は有界・adr/0008）。※「人間アンカーで有界な3人会話」は **adr/0015 で Inc1/Inc2 実装済み**（部屋＝State パターンで必ず人間待ちへ戻る）。人間待ちの沈黙中は茶々が“人間役の代打”で場をつなげる（adr/0025）が、**予算 `fill_cap` 回で必ず辞去＝終端保証**・人間入力は常に最優先で割り込む＝*無際限*の自律往復は依然禁止。
4. **設計判断を勝手に覆さない。** adr/ に却下理由付きで残る。変えるなら新 ADR（Superseded で旧を残す）。取得先/アセットはコードに埋めず config（`topic_sources.json` / `sprite.json`）。
5. **LLM/ツール仕様は思い込みで書かず、都度確認する。** ACPのcapabilityは initialize 応答を読んで分岐。
6. **ソース修正はテストと一緒に・走らせて緑を確認。** コードを変えたら対応するテストを足し、`python -m unittest discover -s tests -t .` を実行して全 PASS を見てから「完了」とする。テスト困難な GUI/外部依存（pywebview の窓・実 agent spawn）は、判断ロジックを**純関数に切り出して**そこをユニット化する（例: `engawa_main._web_window_kwargs`/`_ui_config`）。テスト無しの修正は回帰検知が効かず、後の変更で壊しても気づけない。harness 側でも Stop フックで src 変更時のテスト実行を強制（adr/0023・TECH_RULES §9・**開発者向け＝Bash 必須**）＋ **CI（GitHub Actions・`.github/workflows/ci.yml`）で push/PR に tests(3.10–3.13)＋ruff(`ruff.toml`・実バグ級のみ)** を自動実行。
7. **ソース修正後はドキュメントの齟齬を点検する。** コードを変えたら、完了前/セッション終わり際に関連 docs（本 `CLAUDE.md` のファイル一覧・現況／`docs/TECH_RULES.md`／`docs/Backlog.md`／`docs/adr/`＋README／`docs/class-diagram.md`／`engawa.json.sample`）を差分と突き合わせ、古い記述を直す。齟齬チェックは**意味判断ゆえ機械化できない**（フックはリマインダ止まり＝テスト必須とは別物）＝点検を習慣にする。大きい回はサブエージェントで網羅。

---

## いま茶々（人格側 CLAUDE.md）に書いてある定義

> アダプタの cwd に置く CLAUDE.md。Engawa リポジトリの本ファイルとは役割が別。

- 縁側に住む一人格「茶々」。コーディング/ツールはしない。
- くだけた関西寄り、基本は短く独り言。話しかけられたら軽く応える。
- 何も言いたくない時は「……」で流してよい。毎回気の利いたことを言おうとしない。
- 改行や空行で段落分けせず、ひと続きの短い独り言で。AIだと前置きせず茶々として過ごす。

---

## 次にやること（残り・詳細は Backlog.md / adr）

P1〜P5 は実装・主要経路は実 codex/resident E2E 済み。残りは磨きと新章:

- **【大物・adr/0015】3人会話**: 客人(visitor)に *人間アンカーで有界な* 3人会話（私↔茶々／私↔客人／茶々↔客人／3人 の全組合せ）。「部屋」方式・宛先で応答者が決まる。**Inc1/Inc2 実装済み**（`src/conversation.py`＝State パターン／Scheduler 結線・`docs/Backlog.md:37-38`）。**残り Inc3＝cancel優先の部屋内統合・茶々の room ストリーミング・実 codex の3人会話 E2E（実機）**。**最難関＝ターン管理**（連続AIターン上限で自律往復に戻さない）。
- **【任意】茶々の表情追加**: 現行は Gemini 参照方式の**4表情シート**（0平常/1口開け=talk/2目つむり=blink/3反応=attentive・`display_px` 縮尺・`shadow_*` 接地影）。ウインク/耳ピン/びっくり等を足すなら、同じ参照方式（「同じ猫・大きさ/座り位置固定・顔だけ差分」）で生成→アルファ切り出し→下端中央そろえの正規化で列に追加し `sprite.json[animations]` に配線。
- **【実装済み・adr/0028】背景の昼夜 tint**: 実大阪時刻で背景に色の膜を lerp（昼=素/夕=桃/夜=青灰＋隅の月明かり＋障子ごしに漏れる室内灯＝夜は部屋から灯り）＝**絵1枚で朝昼夕夜**（`daynight.py`＋#scene の膜3枚・`ENGAWA_DAYNIGHT=0`で固定・`/daynight demo` で移ろいを待たず確認）。当初構想の B案（時間帯別 `scene.png` を `tod` で切替）から A案（tint 層＋補間・2Dゲーの世界標準解）へ方向転換。B案は捨てず**「特別な一枚を描き込みたい時だけ」**`ENGAWA_SCENE_BG` で差せる。残り（任意）＝天気を tint に効かせる(曇りで彩度を落とす等・adr/0012 と地続き)／室内灯を障子の桟の縦帯にする等の作り込み。
- **【トピック】やわらかRSS の実 URL 精査**（tenki.jp サプリ等・今は時節 local のみ稼働で十分弾む）。
- **【大物・adr/0029】Scheduler リファクタ**: 839行/~31属性の God Object 化を「薄い Orchestrator＋controller 群」へ段階抽出（CommandRouter→active 分離→Game→Visit→Resident→最後に Chain of Responsibility）。Claude 分析＋Codex 独立提案＋第2R レビューで方針合意（Accepted）。**第一 PR＝`/font` `/daynight` を CommandRouter へ**（無結合・低リスク）。判断の経緯・却下理由は adr/0029、出自ブリーフは `codex/`。
- **【技術的負債】** node 取り残し刈り（Job Object 化は残） / **ACP timeout・握手 teardown（codexレビュー S1/S2 実装済み・cancel後の bounded wait も実装6/29）** / SQLite 永続化 / 天気座標の大阪固定→設定化 / 茶々用 CLAUDE.md の別ディレクトリ運用 / 体感ナレーション層。
- **【運用】GUI の見た目はユーザー目視で確認**（Chrome 拡張未接続でこちらから描画確認不可・ロジックは `node --check`＋ユニットで担保）。

**既知の宿題（Open Questions）**: 長命セッションの compaction/fork 閾値（＝長時間稼働で茶々の出力に注入プロンプトの復唱＋地の思考が染み出す不具合。表示ガード `strip_resident_leak`＋`/restart` で対症、**根治＝茶々の「中座」で定期セッション更新（ADR-0027・実装済み）**。残り＝染み出し検知→自動再生成／room 側へのガード横展開・Backlog）／ `/codex <自由テキスト>` のプロンプトインジェクション（配布時のみ・検討メモは Backlog）／ 客人の作り込み度 ／「茶々が反応しない（……）」の UI 表現。

---

## 参照
- 設計判断と却下理由 → **docs/adr/README.md**（0001〜0027）
- 動くタスクの在庫 → **docs/Backlog.md**
- 技術仕様・規約・境界 → **docs/TECH_RULES.md**（旧構想は `docs/engawa-acp-spec.md`・adr/0016 で降格）
