# CLAUDE.md — Engawa（縁側）

> このファイルは Claude Code が最初に読む「住人の心得」。**何を作っているか・どう動くか・何を守るか・次に何をやるか**を最短で掴むためのもの。
> 設計判断の経緯は **docs/adr/**、技術仕様・境界は **docs/TECH_RULES.md**、動くタスクの在庫は **docs/Backlog.md**。迷ったらそっちへ。
> ※このファイルが**現行全体像の正本**（adr/0016）。`docs/engawa-acp-spec.md` は旧構想（ピボット前・歴史的参照）。

---

## これは何か

**Engawa（縁側）** は、デスクトップの隅に住む AI の住人「茶々（ちゃちゃ）」と過ごす常駐アプリ。育てるでも働かせるでもなく、ただ"居る"。

- 茶々は**会話アシスタントではない**。縁側に住んでいて、時刻や天気にぽつりと反応し、話しかければ応える。
- 時々、客人（Codex）が役を着せて訪ねてくる（召喚＝`/codex`、または夕方に自発来訪）。
- 自分のマシンの Claude / ChatGPT **サブスク認証**で動く（各自 BYO サブスク＝自分のログイン・従量課金しない。配布は BYO 前提で可・単一サブスクの共有サービス化と claude.ai ログイン同梱は ToS 不可・adr/0002）。

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
| P5 | ドット絵UI（隅の縁側窓・frameless・スプライト・来訪演出・縁側背景） | ✅ Inc1〜4 実装。茶々は Gemini 三毛猫の**4表情正規化シート**(`chacha.png` 704×176・`display_px` で縮尺・`shadow_*` で接地影)、縁側背景 `scene.png`(障子＋板の間・外部PNGを起動時読込・無ければ CSS グラデ／層の別は下記ファイル一覧)搭載。**背景は実大阪時刻で昼夜 tint**(乗算色＋月明かり glow＋障子ごしの室内灯 lamp を時刻で lerp・絵1枚で朝昼夕夜＋月＋夜は部屋から灯り・`daynight.py`・adr/0028・`ENGAWA_DAYNIGHT=0`で固定・`/daynight demo` で移ろいを早送り確認)。**背景/茶々は差し替え可能な皮**(ADR-0010・`ENGAWA_SCENE_BG`/`ENGAWA_SPRITE_CONFIG`)。**GUI 見た目はユーザー目視で確認** |

→ 当初スコープ「環境に反応する単体の住人＋双方向＋客人来訪＋ドット絵UI」は**一通り実装・実機で稼働**。残りは磨きと新章（次にやること参照）。

### ファイル（レイアウト・adr/0018）
> コードは `src/`・実使用アセットは `assets/`・PoC基準点は `poc/`・文書は `docs/`。ユーザーが触る設定（`engawa.json` / `topic_sources.json`）と本 `CLAUDE.md` は **root 維持**。設定/アセットは `src/` から **repo-root 基準**で解決（`config.py` / `sources.py` / `views.py` の `_path()`）。
- `src/engawa_main.py` — 起動口（composition root）。console / `ENGAWA_UI=web` で隅の縁側窓。
- `src/agent.py` / `src/acp.py` / `src/agent_openai.py` / `src/persona.py` / `src/voice.py` / `src/sources.py` / `src/scheduler.py` / `src/views.py` / `src/prompts.py` / `src/daynight.py` / `src/commands.py` / `src/props.py` — 現行構成（event-source/scheduler・adr/0013）。`agent.py` は **LLM 接続の中立ポート**（`Agent` Protocol＋`AgentTimeoutError`・adr/0026）＝`AcpAgent`（`acp.py`・Claude Code サブスク）と `OpenAIAgent`（`agent_openai.py`・ローカル OpenAI 互換 API＝LM Studio/Ollama・stdlib urllib・履歴自前保持）の**2実装**、`scheduler` は `acp` を import せず中立例外だけ捕捉＝住人/客人とも backend は **`ENGAWA_RESIDENT_BACKEND`/`ENGAWA_GUEST_BACKEND`（`acp|openai`）** で差し替え可（客人 openai は persona を prompt 注入・住人と同じ endpoint 共有）。`persona.py` は茶々の人格テキストを **backend 中立に一元化**（ACP は cwd の CLAUDE.md、OpenAI は system メッセージとして同じ文を注入）。**どの人格文を使うかは `voice.py`**（adr/0022・7/18 実装）＝「声」バンドル `voices/<id>/`（meta/persona/strings・base 継承）を `ENGAWA_VOICE`/`engawa.json[voice].id` で選択（既定 `ja-osaka`=組み込み persona.py＝ゼロ設定で現状維持）。UI シェル文言は `voice.loc(key)` で鍵化＝**日本語既定はコードに書かず `locales/strings.json`（台帳＝単一正本・adr/0033）**から3段解決（voice strings → 呼び側 default〔動的既定＝absence 系 random プール専用〕→ 台帳 → キー名表示＝欠損が一目でバレる。整合は `tests/test_strings_registry.py` が AST で機械強制＝キー∈台帳・インライン既定禁止・placeholder 一致）＋言語指示は `voice.lang_note()`（llm_lang 時だけ注入末尾に1行・JP 方言では注入不変。**prompts の user/room 系と `sources.py` のソロ narration＝ambient/arc/transition の全経路に後置**＝persona が英語で書かれているだけでは言語は縛れず、note 無しソロ経路は実測ほぼ100%日本語落ちだった 7/19 の穴の修正。全経路網羅は合成テスト `tests/test_injection_lang.py`＝命名 canary 付き＋実 LLM の opt-in ハーネス `tests/e2e/leak_probe.py` で担保・adr/0022 追記）。**同梱は `voices/en`＝英語の茶々（transcreation）＋英語UI**。`views.py` に `ConsoleView` と `WebView`（pywebview・poll方式）。`daynight.py` は時刻→背景の**昼夜 tint**（乗算色＋月明かり glow＋障子ごしの室内灯 lamp）を返す純関数＝poll が大阪時刻で毎回配り JS が #scene の膜3枚（multiply/screen×2）へ適用（画像差し替えでなく1枚の色膜を lerp・`ENGAWA_DAYNIGHT=0` で無効・`/daynight` でプレビュー・adr/0028）。`commands.py` は**スラッシュコマンドの Command パターン**（`CommandRouter`＋登録制・`ctx` は薄い adapter＝今は View だけ）＝`Scheduler._command` は登録済みなら Router へ委譲・未登録は従来の if/elif にフォールバック。**adr/0029 Phase 1 で `/font` `/daynight` を移譲**（残コマンドは依存 controller が出来てから）。`prompts.py` は LLM 文言ビルダー（注入プロンプト工場・`sources` から分離・`prompts→sources` 一方向 import）＋出力ガード族の純関数＝茶々ソロ出力の染み出しガード `strip_resident_leak`（注入文の復唱＋地の思考を表示前に除去）と `is_error_payload`（backend が API エラー＝例:codex モデル非対応400 を本文として流した時に生 JSON を検出＝room では guest_timed_out へ回して急用退場で畳む・縁側に生エラーを出さない）。
- `src/conversation.py` — 3人会話の部屋（State パターン・adr/0015 **Inc1/Inc2 実装済み**＋adr/0025 **代打**）。人間待ちで沈黙が続くと茶々が“人間役の代打”で場をつなぐ（`ResidentFilling`・予算 `fill_cap` 回で必ず辞去＝有界）。**部屋内 barge-in**（adr/0031 スコープM・7/18）＝tick 駆動チェーン（挨拶/代打/辞去）中の入力が進行中ドライブを失効させ（`should_stop` 注入＝generation 判定）、言いかけは `_utter` の commit gate で表示/transcript に積まない（ARRIVE/Leaving は中断不可・fill 予算は不発なら返す）。`src/room_speakers.py` — `RoomSpeakerFactory`（adr/0029 Phase 4a）＝room の茶々/客人 Speaker を作る注入アダプタ＋種プール（ambient トピック）＋timeout フラグを凝集（客人が API エラーを本文として返した時も `is_error_payload` で検出→`guest_timed_out`＝生 JSON を出さず急用退場で畳む）。barge-in 用に生成中の客人 agent を追跡し `cancel_inflight()` で畳む（agent 実体は外に見せない・barge と同時の timeout は退場に数えない・adr/0031）。茶々発話は Scheduler 注入の `resident_speak`（turn_lock 下）＝**判断B の seam**（judgment B の speak 一本化＝P5 で共有コア `_speak_locked` に集約済み・full RM は費用対効果で見送り）。`src/game.py` + `src/game_rlcard.py` — ゲームの Port＆Adapter（adr/0017。rlcard は `game_rlcard` に隔離・任意依存）。`src/game_controller.py` — 対局の**運用**（生成/AI手番/表示/終了/お開き）を Scheduler から切り出した `GameController`（adr/0029 Phase 3）＝`game`/`_game_*` state も所有。Scheduler は `self.games` へ委譲（tick→`on_tick`・入力→`on_user_input`・`/game`→`start`・shutdown→`close`）、Scheduler 状態への結び目（場払い/次ビート/住人現物）は callback/provider で注入（`drive_lock` は Scheduler 所有のまま）。後方互換で `Scheduler.game`(read)/`_make_game`(get/set) プロパティを残す。
- `src/debuglog.py` — デバッグログ（stdlib logging の薄いラッパ）。`ENGAWA_DEBUG=1` で `engawa.log`（gitignore）へ主要ライフサイクルを **日付＋ミリ秒**（`YYYY-MM-DD HH:MM:SS.mmm`）で吐く＝会話タイミングを定量観測できる。イベント: 種の注入/来訪/room/cancel/timeout ＋ `inject 茶々 (kind)`（ソロ発話）/`user input`/`say who (kind)`（room 発話）/`next beat +Ns`（予定の間合い）/`茶々 中座へ`・`茶々 中座から復帰`（中座＝セッション更新・ADR-0027）。既定オフ＝縁側の窓/console 本文は汚さない・`log.debug` は no-op。各モジュールは `debuglog.get("<name>")` で子ロガー。
- `assets/sprite.json` + `assets/chacha.png`（茶々スプライト）／`assets/scene.png`（縁側の背景＝障子＋板の間）— **差し替え可能な皮**（adr/0010・背景にも拡張）。スプライトは Gemini 三毛猫の4表情正規化シート（0平常/1口開け/2目つむり/3反応）で、`display_px`＝縮尺（絵は固定・表示側でスムーズ縮小）／`shadow_w/dy/h`＝接地影／`animations`＝state→コマ。**スプライトをダブルクリックすると「ニャー」吹き出し＋頭上にハート♥がふわっと**（LLM/アセット非経由のクライアント完結＝トークン0・`views.py` の `meow`/`hearts`）。背景は透過なしPNGを `#scene` へ流し込む（読み方は直下の**アセットの層**注記／無ければ CSS グラデ＋障子/床プレースホルダにフォールバック）。**両方 env `ENGAWA_SPRITE_CONFIG`/`ENGAWA_SCENE_BG` か `engawa.json[assets]`（sprite_config/scene_bg）で好みの絵に丸ごと差し替え可**（`views._asset_path`）。`assets/raw/`（gitignore）は Gemini 生成元 PNG。
  - **アセットの層（"dataURI 埋め込み＝外部ファイル不要" と読み違えるとビルドで転ぶ・PyInstaller 回の教訓＝2層を1文に混ぜない）**:
    - **ブラウザ層**: data URI で注入＝WebView に届く HTML に外部参照は残さない（`<img src>`/http fetch ゼロ）。
    - **ファイル層**: `assets/` の外部PNGを**起動時にディスク読み**（`_load_sprite`/`_load_scene_bg` の `open→b64encode`）→ 配布時（PyInstaller）は `datas` で**同梱必須**（`views._base_dir` が frozen 時 `sys._MEIPASS/assets` を解決・spec の datas 参照）。
    - **フォールバック**: 欠損時は procedural cat＋CSS グラデ＋障子/床プレースホルダ（起動は妨げない）。
- `voices/en/`（root） — 英語 voice バンドル（adr/0022・meta.json+persona.md+strings.json＝英語の茶々と英語UI。`ENGAWA_VOICE=en` で有効・自作 voice はフォルダを足すだけ・配布時は spec datas 同梱）。`voices/_template/strings.json` は**著者向け雛形**（全キー＋日本語既定・`tools/gen_voice_template.py` が台帳から生成＝チェックイン・一致はテストで強制・adr/0033）。**自作手順は `voices/README.md`**（コピー→meta/persona→訳→`python tools/voice_lint.py <id>`＝未訳/未知キー/placeholder の見える化・exit 0=完訳→起動。コード0行・PR 不要）。
- `locales/strings.json` + `locales/culture.json`（root） — **UI 文言の台帳と、土地・役のデータ既定＝単一正本**（adr/0033。「定義は locales・訳と声は voices」・strings は専用ローダーが ok/missing/malformed/wrong-shape を識別・欠損はキー名表示で起動継続。culture＝`place`（茶々が天気で言う地名・env `ENGAWA_PLACE_LABEL` > voice culture > locales 既定）＋`guest_personas`（自発来訪の役プール＝**安定 id と display を分離**・topic 照合は id でも一致＝display の翻訳でマッチが壊れない）。spec datas 同梱・`ENGAWA_LOCALES_DIR` で差し替え=テスト用）。
- `assets/props.json` + `assets/props/` — **縁側の小物の台帳と絵**（adr/0032 v2・7/18）。台帳は **entity+component**（`place`=置き場/`when`=条件・全AND・述語 Registry＝新条件は述語1個/`effect`=汎用演出 `rise`＝煙/湯気の粒・**小物専用コード禁止＝新小物はコード0行で台帳に1行**/`narrate`=茶々の環境行に載る一文＝**茶々は自分の縁側の物を知っている**・「あなたが焚いた蚊取り線香がある」）。`src/props.py`（カタログ=単一正本・純関数）を views と prompts が共有。**資産は起動時注入・「いま出ている集合」は poll が毎回配る**（daynight と同経路＝月替わり再起動不要・将来「茶々が点け消し」も同経路）。昼夜膜の下＝夜は小物も暮れる・欠損スキップ。台帳は **renderer 非依存の契約**＝canvas シーン化（庭側ビュー最終形・Backlog）に台帳ごと引っ越す。差し替えは `ENGAWA_PROPS_CONFIG`/`engawa.json[assets].props_config`。位置調整はユーザー目視。蚊取り線香=6〜9月・rise の煙付き。
- `topic_sources.json`（root） — 客人の世間話トピックの取得先ホワイトリスト（config主導・adr/0014）。
- `engawa.json`（root・**個人設定＝gitignore**／雛形は `engawa.json.sample`） + `src/config.py` — アプリ挙動の設定（model/guest/間合い/topic）。優先順位 **env(ENGAWA_*) > engawa.json > 既定**。キーは入れない(adr/0002)。端末ごとに調整・全キー任意＝消せばコード既定（gitignore 個人設定＋追跡サンプルの流儀）。
- `engawa.bat` / `engawa-debug.bat`（root） — Windows ランチャ。`engawa.bat`＝web 常用（`set ENGAWA_UI=web` → `start pythonw` で**切り離して即閉じ**＝黒い cmd 窓が残らない。web 経路は stdout 出力ゼロ＝--noconsole exe と同じ理屈で安全・7/18）。`engawa-debug.bat`＝`ENGAWA_DEBUG=1`＋ブロッキング console＋別窓で `engawa.log` を追尾（出力を見たい時はこちら）。`engawa-en.bat`＝英語 voice で起動する薄い call ラッパ（`ENGAWA_VOICE=en`→engawa.bat・常用を英語に決めたら `engawa.json[voice].id` に落として本体 bat を使うのが本筋・adr/0022）。**cmd が cp932 でバッチを読む都合上 ASCII-only 厳守**（日本語コメントは化けて実行される）。
- `poc/engawa_p1/p2/p3_*.py` — 各フェーズの検証済み基準点。**温存・触らない**。
- `docs/adr/`（0001〜0033）, `docs/TECH_RULES.md`（**固有の実装契約のみ**＝ワイヤ契約/OS地雷/境界/テスト運用・決定は1行＋ADRポインタ・adr/0030）, `docs/Backlog.md`
- `docs/engawa-acp-spec.md` — ピボット前の**旧構想 仕様書 v1**（adr/0004 で転換・adr/0016 で降格）。歴史的参照として温存・**現行仕様ではない**。
- ~~`legacy/`~~ — 方向転換前の**旧実装**（adr/0004 で捨てた「AI雑談ルーム」・API 直叩き）は **2026-07-04 に削除**（実行できる従量課金 footgun のため・公開レビュー）。コードは **git 履歴**に、判断は **adr/0004** に残る＝歴史は失わない。非実行の歴史参照（`poc/`＝検証済み戻り先・`docs/engawa-acp-spec.md`＝旧構想）は温存。

---

## 起動

- **console（端末）**: `python src/engawa_main.py`（リポジトリ直下から実行）
- **web（隅の縁側窓・frameless）**: cmd で `set "ENGAWA_UI=web" && python src/engawa_main.py`（`$env:` は PowerShell 専用・cmd は `set`・空白混入回避でクォート）。**日常は `engawa.bat`（web）／`engawa-debug.bat`（デバッグ＋log tail 窓）をダブルクリックでも可**。
- **認証**: 先に `claude` と codex(ChatGPT) にサブスクでログイン。API キーは子 env から除去（adr/0002）。
- **主な env つまみ**: `ENGAWA_UI=web` / `ENGAWA_MODEL`（茶々=Claude のモデル・例 `opus`/`claude-opus-4-8`/`opus[1m]`）,`ENGAWA_CODEX_MODEL`（客人=codex のモデル）/ `ENGAWA_CLAUDE_CONFIG_DIR`（住人=Claude の認証プロファイル固定＝子 env に `CLAUDE_CONFIG_DIR` を渡し組織アカウント誤選択を回避・**既定は空＝親の `~/.claude` を継承＝opt-in**（ハードコード固定は逆に壊す）・`engawa.json[auth].claude_config_dir` 可・客人は別 CLI で対象外・`acp._resident_extra_env`・adr/0002）,`ENGAWA_ENV_PASSTHROUGH`（子 env の allowlist＝`acp._child_env` の default-deny で課金/外部送信 env〔`ANTHROPIC_*`/`AWS_*`/`CLAUDE_CODE_USE_BEDROCK` 等〕を構造的に遮断する・その allowlist に特殊環境で足りない素性を足す逃げ道＝カンマ区切り・既定空・**課金系はここでも貫通不可**・アダプタが起動しない時に足す・`engawa.json[auth].env_passthrough` 可・adr/0002 追加点検 🔴）/ `ENGAWA_RESIDENT_BACKEND`,`ENGAWA_GUEST_BACKEND`（住人/客人の駆動＝`acp` 既定/`openai`＝ローカル LM Studio 等・adr/0026・客人 openai は住人と同じ endpoint 共有）,`ENGAWA_OPENAI_BASE_URL`,`ENGAWA_OPENAI_MODEL`,`ENGAWA_OPENAI_API_KEY`,`ENGAWA_OPENAI_TIMEOUT`,`ENGAWA_OPENAI_REASONING`,`ENGAWA_OPENAI_MAX_TOKENS`,`ENGAWA_OPENAI_ALLOW_REMOTE`（openai backend の endpoint/モデル/鍵ダミー/秒/reasoning_effort=既定 none で推論オフ＝Qwen3.5 等の長考抑止/出力上限/**非ローカル endpoint 許可＝既定0でブロック＝課金・外部送信の事故防止・原則#1**・既定 `http://localhost:1234/v1`）/ `ENGAWA_GUEST_PROB`,`ENGAWA_GUEST_FROM_HOUR`（自発来訪）/ `ENGAWA_GUEST_IDLE_LEAVE`,`ENGAWA_GUEST_FILL_CAP`,`ENGAWA_GUEST_FILL_AFTER`,`ENGAWA_GUEST_FILL_SLOWDOWN`（来訪中＝沈黙で辞去するtick数/人間不在時に茶々が代打で場をつなぐ回数=有界上限/最初の代打までの沈黙tick/代打間隔を回ごとに延ばす量＝賑やか→間延び→帰る・adr/0025）/ `ENGAWA_TOPIC_PROB`,`ENGAWA_TOPIC_COOLDOWN`,`ENGAWA_TOPIC_REFRESH_MIN`,`ENGAWA_TOPIC_CONFIG`（トピック＝空気に混じる確率/粘着防止の間隔/更新間隔/取得先）/ `ENGAWA_WEATHER_LAT`,`ENGAWA_WEATHER_LON`,`ENGAWA_WEATHER_TZ`,`ENGAWA_PLACE_LABEL`（茶々が眺める実天気の観測地点＝緯度/経度/Open-Meteo の timezone/地名ラベル・**既定は大阪＝未指定なら現行のまま**・3つ連動で住む土地を替える・`engawa.json[weather]` 可・`sources._weather_url`。地名ラベルの優先は **env/json > voice culture > locales 既定**＝en voice は未設定でも「Osaka」・adr/0033 Inc4。注: 昼夜tintと会話の時刻はローカル時刻依存でこの tz とは別）/ `ENGAWA_UI_CORNER`,`ENGAWA_UI_EASYDRAG`,`ENGAWA_UI_W`,`ENGAWA_UI_H`,`ENGAWA_UI_FONT`,`ENGAWA_UI_ENTER`（窓＝隅/移動/幅/高/文字倍率/入力欄の Enter＝`send`(既定・Enter送信/Shift+Enter改行) or `newline`(Enter改行/Ctrl+Enter・送信ボタンで送信)・右下グリップでリサイズ・engawa.json[ui]可）/ `ENGAWA_VOICE`,`ENGAWA_VOICES_DIR`,`ENGAWA_LOCALES_DIR`（茶々の「声」＝voice バンドル選択・例 `en`=英語の茶々＋英語UI・既定 `ja-osaka`=組み込み大阪弁・`engawa.json[voice].id` 可・置き場差し替えは VOICES_DIR／LOCALES_DIR=UI文言台帳の置き場＝テスト用・起動時確定＝ライブ切替なし・adr/0022/0033）/ `ENGAWA_SPRITE_CONFIG`,`ENGAWA_SCENE_BG`,`ENGAWA_PROPS_CONFIG`（茶々スプライト/縁側背景/小物台帳の差し替え＝皮・`engawa.json[assets]` 可・adr/0010/0032）/ `ENGAWA_DAYNIGHT`（背景の昼夜 tint＝実大阪時刻で乗算色＋月明かり＋室内灯を lerp・既定1・0で固定背景・`engawa.json[ui].daynight` 可・**アプリ内 `/daynight on|off` でライブ切替＋保存**・`/daynight demo` でプレビュー・adr/0028）/ `ENGAWA_TICK_MIN/MAX`,`ENGAWA_ARC_PROB`（間合い）/ `ENGAWA_DEBUG`,`ENGAWA_LOG_FILE`（デバッグログ＝`engawa.log` へ主要ライフサイクル・既定オフ）/ `ENGAWA_RESIDENT_GUARD`（茶々ソロ出力の染み出しガード＝注入プロンプトの復唱＋地の思考を表示前に除去・既定1・0で従来の逐次stream。長命セッション劣化で出力が崩れた時は `/restart` で張り直し）/ `ENGAWA_ABSENCE_AFTER_TURNS`,`ENGAWA_ABSENCE_JITTER`,`ENGAWA_ABSENCE_GAP`（茶々の「中座」＝世界観に溶かした定期セッション更新＝染み出しの根治・ADR-0027。発話が溜まったら次のidleで中座し不在の裏で若返る/タイミングのゆらぎ/不在秒・既定30/10/18・after_turns=0で中座オフ）。**これらは `engawa.json` にも書ける＝永続（env が優先・adr原則4のconfig主導）**
  - モデル指定の仕組み: 住人は子 env の `ANTHROPIC_MODEL`（Claude Code が尊重）、客人は `CODEX_CONFIG`（codex-acp が Codex 設定へマージ）に載せる。**未指定はアダプタ既定のまま（現状の挙動を変えない）**。サブスク認証でも有効。
- **スラッシュ**: `/codex <人格>`（客人召喚）/ `/game <id> [見る]`（id=blackjack/uno/leduc・私+茶々／「見る」で観戦・客人 codex は基本不要・要 `pip install rlcard`・ADR-0017。`/blackjack` は別名）/ `/arc [雀|猫|風]`（箱庭再生・デバッグ・英別名 sparrow/cat/wind 可）/ `/daynight [on|off|HH:MM|demo|auto]`（背景の昼夜・`/arc` と同筋のデバッグ再生＋on/off＝機能の有効無効を `engawa.json[ui].daynight` に**保存**（ライブ反映・`/font save` 方式）／`HH:MM`=時刻固定・`demo`=夕→夜を早送り＝実時間を待たず移ろいを確認・`auto`=プレビュー解除して実時間へ・web のみ・ADR-0028）/ `/model`（今のモデル表示・住人/客人）/ `/font [倍率|save]`（web 文字サイズをアプリ内でライブ調整＝再起動不要・`/font` で現在値・`/font save` で `engawa.json[ui].font` へ保存＝明示保存・console は端末フォント依存で no-op）/ `/restart`（茶々のセッションを張り直す＝出力の染み出し/不調時・文脈はリセット・timeout 段階回復と同じ respawn 経路）/ `/help` / `/quit`

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
3. **AI同士の*自律・無際限*会話に戻さない。** 客人は環境イベント＝来訪（常駐させない・滞在は有界・adr/0008）。※「人間アンカーで有界な3人会話」は **adr/0015 で Inc1/Inc2 実装済み**（部屋＝State パターンで必ず人間待ちへ戻る）。人間待ちの沈黙中は茶々が“人間役の代打”で場をつなげる（adr/0025）が、**予算 `fill_cap` 回で必ず辞去＝終端保証**・人間入力は常に最優先で割り込む＝*無際限*の自律往復は依然禁止。（割り込みの実装範囲は adr/0031 スコープM＝tick 駆動チェーン。ARRIVE/辞去は世界状態と終端保証のため完走・応答チェーン中の連打対応＝スコープL は保留）
4. **設計判断を勝手に覆さない。** adr/ に却下理由付きで残る。変えるなら新 ADR（Superseded で旧を残す）。取得先/アセットはコードに埋めず config（`topic_sources.json` / `sprite.json`）。
5. **LLM/ツール仕様は思い込みで書かず、都度確認する。** ACPのcapabilityは initialize 応答を読んで分岐。
6. **ソース修正はテストと一緒に・走らせて緑を確認。** コードを変えたら対応するテストを足し、`python -m unittest discover -s tests -t .` を実行して全 PASS を見てから「完了」とする。テスト困難な GUI/外部依存（pywebview の窓・実 agent spawn）は、判断ロジックを**純関数に切り出して**そこをユニット化する（例: `engawa_main._web_window_kwargs`/`_ui_config`）。テスト無しの修正は回帰検知が効かず、後の変更で壊しても気づけない。harness 側でも Stop フックで src 変更時のテスト実行を強制（adr/0023・TECH_RULES §9・**開発者向け＝Bash 必須**）＋ **CI（GitHub Actions・`.github/workflows/ci.yml`）で push/PR に tests(3.10–3.13)＋ruff(`ruff.toml`・実バグ級のみ)** を自動実行。
7. **ソース修正後はドキュメントの齟齬を点検する。** コードを変えたら、完了前/セッション終わり際に関連 docs（本 `CLAUDE.md` のファイル一覧・現況／`docs/TECH_RULES.md`／`docs/Backlog.md`／`docs/adr/`＋README／`docs/class-diagram.md`／`engawa.json.sample`）を差分と突き合わせ、古い記述を直す。齟齬チェックは**意味判断ゆえ機械化できない**（フックはリマインダ止まり＝テスト必須とは別物）＝点検を習慣にする。大きい回はサブエージェントで網羅。

---

## いま茶々（人格側 CLAUDE.md）に書いてある定義

> アダプタの cwd に置く CLAUDE.md。Engawa リポジトリの本ファイルとは役割が別。**正本は `src/persona.py`（＝既定 voice `ja-osaka` の内容）。voice を替えると `voices/<id>/persona.md` がこれに代わる**（例 en＝英語の茶々・adr/0022）。

- 縁側に住む一人格「茶々」。コーディング/ツールはしない。
- くだけた関西寄り、基本は短く独り言。話しかけられたら軽く応える。
- 何も言いたくない時は「……」で流してよい。毎回気の利いたことを言おうとしない。
- 改行や空行で段落分けせず、ひと続きの短い独り言で。AIだと前置きせず茶々として過ごす。

---

## 次にやること（残り・詳細は Backlog.md / adr）

P1〜P5 は実装・主要経路は実 codex/resident E2E 済み。残りは磨きと新章:

- **【大物・adr/0015】3人会話**: 客人(visitor)に *人間アンカーで有界な* 3人会話（私↔茶々／私↔客人／茶々↔客人／3人 の全組合せ）。「部屋」方式・宛先で応答者が決まる。**Inc1/Inc2 実装済み**（`src/conversation.py`＝State パターン／Scheduler 結線・`docs/Backlog.md:37-38`）。**実 codex の3人会話 E2E は実機済み**（7/18 確認）。**部屋内 cancel 統合＝スコープM 実装済み（adr/0031・7/18・codex 設計レビュー経由）**＝tick 駆動チェーン（挨拶/代打/辞去）への被せが即効く。**barge-in 実機E2E 済み（7/18・代打中の被せで即畳めるのを確認）**。残り＝**スコープL**（自分の発話への応答チェーン中の連打＝`run()` 入力の worker 化・設計済み保留・実機で痛ければ着手）／room ストリーミング（`RESIDENT_GUARD` でソロも非ストリームの間は見送り）。**最難関＝ターン管理**（連続AIターン上限で自律往復に戻さない）は不変。
- **【任意】茶々の表情追加**: 現行は Gemini 参照方式の**4表情シート**（0平常/1口開け=talk/2目つむり=blink/3反応=attentive・`display_px` 縮尺・`shadow_*` 接地影）。ウインク/耳ピン/びっくり等を足すなら、同じ参照方式（「同じ猫・大きさ/座り位置固定・顔だけ差分」）で生成→アルファ切り出し→下端中央そろえの正規化で列に追加し `sprite.json[animations]` に配線。
- **【実装済み・adr/0028】背景の昼夜 tint**: 実大阪時刻で背景に色の膜を lerp（昼=素/夕=桃/夜=青灰＋隅の月明かり＋障子ごしに漏れる室内灯＝夜は部屋から灯り）＝**絵1枚で朝昼夕夜**（`daynight.py`＋#scene の膜3枚・`ENGAWA_DAYNIGHT=0`で固定・`/daynight demo` で移ろいを待たず確認）。当初構想の B案（時間帯別 `scene.png` を `tod` で切替）から A案（tint 層＋補間・2Dゲーの世界標準解）へ方向転換。B案は捨てず**「特別な一枚を描き込みたい時だけ」**`ENGAWA_SCENE_BG` で差せる。残り（任意）＝天気を tint に効かせる(曇りで彩度を落とす等・adr/0012 と地続き)／室内灯を障子の桟の縦帯にする等の作り込み。
- **【トピック】やわらかRSS の実 URL 精査**（tenki.jp サプリ等・今は時節 local のみ稼働で十分弾む）。
- **【実装済み・adr/0029】Scheduler リファクタ**: 839行/~31属性の God Object 化を「薄い Orchestrator＋controller 群」へ段階抽出。**P1（CommandRouter＝`/font` `/daynight`）→P2（`active` 意味分離）→P3（GameController）→P4a（RoomSpeakerFactory）→P5（speak 一本化・縮小版）まで実装・マージ済み（Scheduler 839→584 行＝**P5 完了時点の実測**・以後の機能追加で増減）**。残る **P4b（full Visit）・P5full（RM）・P6（Chain of Responsibility）は費用対効果で意図的に打ち切り**（YAGNI＝糊の再配置は Orchestrator の本業・必要が育ったら再開）。判断の経緯・却下理由・実装メモは adr/0029、出自ブリーフは `codex/`。
- **【技術的負債】** node 取り残し刈り（Job Object 化は残） / **ACP timeout・握手 teardown（codexレビュー S1/S2 実装済み・cancel後の bounded wait も実装6/29・first-token前cancel直後の内部エラー-32603は1回再送で吸収 7/13）** / SQLite 永続化 / ~~天気座標の大阪固定~~→**設定化済み**（`ENGAWA_WEATHER_LAT/_LON/_WEATHER_TZ`＋`ENGAWA_PLACE_LABEL`・`engawa.json[weather]`・既定は大阪で現行維持・`sources._weather_url`／会話・昼夜tintの時刻はローカル依存で別問題として残る） / ~~茶々用 CLAUDE.md の別ディレクトリ運用~~→**voice バンドルで解消**（`voices/<id>/persona.md`＝`voice.persona_text()` が両 backend へ注入・adr/0022・7/18） / 体感ナレーション層。
- **【運用】GUI の見た目はユーザー目視で確認**（Chrome 拡張未接続でこちらから描画確認不可・ロジックは `node --check`＋ユニットで担保）。

**既知の宿題（Open Questions）**: 長命セッションの compaction/fork 閾値（＝長時間稼働で茶々の出力に注入プロンプトの復唱＋地の思考が染み出す不具合。表示ガード `strip_resident_leak`＋`/restart` で対症、**根治＝茶々の「中座」で定期セッション更新（ADR-0027・実装済み）**。残り＝染み出し検知→自動再生成／room 側へのガード横展開・Backlog）／ `/codex <自由テキスト>` のプロンプトインジェクション（配布時のみ・検討メモは Backlog）／ 客人の作り込み度 ／「茶々が反応しない（……）」の UI 表現。

---

## 参照
- 設計判断と却下理由 → **docs/adr/README.md**（0001〜0033）
- 動くタスクの在庫 → **docs/Backlog.md**
- 技術仕様・規約・境界 → **docs/TECH_RULES.md**（固有の実装契約のみ・adr/0030。旧構想は `docs/engawa-acp-spec.md`・adr/0016 で降格）
