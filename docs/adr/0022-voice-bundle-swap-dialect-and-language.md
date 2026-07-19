# ADR-0022: 茶々の「声」は voice バンドルで差し替える（方言/言語を base⟂voice 分離・config 主導）

- ステータス: Accepted（**Inc1/Inc2 実装済み 2026-07-18**・`voices/en` 同梱＝最初の外国語ロケール到来。culture.json=Inc3 は未着手のまま）
- 日付: 2026-06-30（実装 2026-07-18）
- 関連: ADR-0003（人格は cwd の CLAUDE.md で注入）, ADR-0004（環境反応型の単体住人）, ADR-0005（長命セッション）, ADR-0010（差し替え可能なアセット層）, ADR-0013（イベント源/Scheduler の Port&Adapter・YAGNI）, ADR-0019（presentation は意味 state ＋ config 駆動の差し替え）, ADR-0020（config 主導の選択・env > json > 既定）

## 背景 / 課題

茶々は今、関西（大阪）弁の Japanese な住人（ADR-0003/0004）。これを多言語・多方言に開きたい。ただし方針として **「茶々をどう親しみやすく語らせるか」は各地域に委ねる**（その言語圏の声は現地が**書き起こす**＝transcreation・機械翻訳で薄めない・原則#2「住人から外さない」）。設計側の責務は中身の翻訳ではなく、**差し替えだけを簡単にしておくこと**。

要件は2軸が混在する:
- **国内・方言**: 大阪弁を**温存**しつつ、京都弁・鹿児島弁へ置き換えられる。
- **言語**: 英語へ置き換えられる。

ここで素朴な i18n（`ja`/`en` の言語コードで切る）は**粒度が粗すぎる**。大阪/京都/鹿児島はどれも言語コード上は `ja` で、区別できない。つまり差し替えの単位は「言語」ではなく **「声(voice)」** であるべき、という気づきが出発点。

## 決定

1. **差し替え単位は voice バンドル**。ID は `ja-osaka` / `ja-kyoto` / `ja-kagoshima` / `en` … のような **voice ID**。方言も言語も**同一機構**で挿す（言語は voice の一属性に過ぎない）。

2. **base 言語 ⟂ voice を分離**し、欠落は継承する。フォールバック連鎖 `<voice> → <base> → 組み込み既定`。バンドルは pure content（コードに種類を登録しない・原則#4 / ADR-0010）:
   ```
   voices/<id>/
     meta.json    … { base:"ja", label:"京都弁", llm_lang:null }  ← base 言語・表示名・出力言語
     persona.md   … 茶々の声（必須・現地が書き起こす）             ← 方言で要るのは実質これだけ
     strings.json … (任意) UIシェル上書き。無ければ base から継承
     culture.json … (任意) 季節/天気/客人。無ければ base から継承
   ```
   - **方言差し替え**（大阪→京都/鹿児島）＝ `meta.json`(base:ja) ＋ `persona.md` の **1〜2ファイルだけ**。JP 文化（二十四節気/旬/天気/客人ペルソナ）は base=ja から**丸ごと継承**。
   - **言語差し替え**（ja→en）＝同じ枠 ＋ `strings.json`（UI を訳す）＋ 必要なら `culture.json` ＋ `llm_lang:"en"`。

3. **声 = persona.md は ADR-0003 の人格注入機構に直結**。`acp.AcpAgent.spawn_resident` が住人に渡す **cwd の CLAUDE.md** に、選ばれた `voices/<id>/persona.md` を load するだけ。方言は LLM が得意＝**persona がそのまま指示**になる。したがって **JP 方言では `prompts.py` を触らない**（「日本語で答えて」を足さない＝persona と競合させない）。`llm_lang` は base が日本語以外（英語等）の時だけ効かせる**任意ノブ**。

4. **選択は config 主導**（ADR-0020 と同流）: 優先順位 `ENGAWA_VOICE`(env) > `engawa.json` の `voice` > 既定 **`ja-osaka`**。**消せば大阪弁**（全キー任意＝既定に戻る）。

5. **voice は spawn 時に確定**＝長命セッションに焼き込む（ADR-0005）。リアルタイム `/voice` 切替はしない（住人 re-spawn＝文脈喪失になるため）。位置づけは `/model`（ADR-0020）と同格＝**縁側への操作なので茶々には流さない**（ADR-0007）。

6. **スプライト（三毛猫）は言語中立＝不変**。本 ADR は声/文字列のみを扱い、P5・ADR-0010/0019 の見た目層とは独立。

## 検討した代替案

- **言語コード（ja/en）で切る古典 i18n**: 大阪/京都/鹿児島を表現できない（全部 ja）。→ voice ID を一級の差し替え単位にする。
- **茶々を機械翻訳／自動方言変換で量産**: 人格がぺたんと潰れる（原則#2）。→ 各地域が persona を**書き起こす**（transcreation）。
- **voice を「全部入り」にして base 継承を持たない**: 方言ごとに JP 文化（二十四節気/旬/天気/客人）を複製＝重複地獄・更新漏れ。→ **base⟂voice 分離＋継承**で方言は persona 一枚に。
- **gettext / `.po`・`.mo` 一式**: 標準だがツールチェーンが重く、`engawa.json`/`topic_sources.json`/`sprite.json` の JSON-config 文化に合わない。→ UI は**軽量 JSON カタログ**（`strings.json`）。日付/時刻帯の地域化が要れば `Babel` を**部分採用**（温存・全否定しない）。
- **リアルタイム `/voice` 切替**: 長命セッション（ADR-0005）に焼くため re-spawn 必須＝文脈喪失。→ config→再起動が本筋。

## 影響 / 帰結

- **方言追加はファイルを足すだけ**（コード登録なし）。京都弁は persona 一枚、英語はその枠＋訳。「差し替えだけ簡単に」を構造で担保。
- **既存の技術的負債と合流**:
  - CLAUDE.md 記載「茶々用 CLAUDE.md の別ディレクトリ運用」＝この `voices/<id>/persona.md` に化ける。
  - 「天気座標の大阪固定→設定化」＝`culture.json`（言語版・地域版）に自然吸収。
- **実装時に触る継ぎ目（見取り図）**:
  - `acp.spawn_resident`: persona を `voices/<id>/persona.md` から選んで cwd CLAUDE.md へ。
  - UI シェル: scheduler(`/help`・system)・views・engawa_main の文言を小さな `loc("key")` 越しに（active voice の `strings.json` → base → 組み込み既定）。
  - `prompts.py`（ADR の A1 で分離済みの注入プロンプト工場）: `llm_lang` を**任意で**参照するのみ。JP 方言では不変。
  - `config.py`: `voice` 解決（env > json > 既定 `ja-osaka`）。起動行に `茶々=<voice.label>` 表示。
- **YAGNI 線引き（ADR-0013）**: いま作るのは **voice 選択 ＋ persona オーバーレイ ＋ UI フォールバック** まで。`culture.json`（季節/天気の差し替え機構）は**最初の外国語ロケールが実際に来るまで作らない**。方言ユースケースで継ぎ目を**安く検証**してから言語へ投資する。

## 実装メモ（追記 2026-07-18・Inc1/Inc2）

- `src/voice.py` … バンドル解決（env `ENGAWA_VOICE` > `engawa.json[voice].id` > 既定 `ja-osaka`＝組み込み）。
  `persona_text()`（底=persona.RESIDENT_PERSONA）／`llm_lang()`／`label()`／`loc(key, default)`（strings 継承
  `<voice>→<base>→コード内日本語`）。置き場 `voices/`（frozen 時 `sys._MEIPASS/voices`・`ENGAWA_VOICES_DIR` で差し替え）。
- 注入: `acp.setup_persona_dir`／`agent_openai` の system が `voice.persona_text()` を使う（両 backend 同文・ADR-0026）。
- `prompts._lang_note()` … `llm_lang` が立つ時だけ住人注入の末尾に言語指示1行（JP 方言では 1 バイトも不変＝決定3どおり）。※7/19 に本体を `voice.lang_note()` へ移設し sources のソロ narration にも後置（下記追記）。
- UI 鍵化は**漸進**（高頻度シェルのみ）: /help・barge-in 演出・来訪・中座・timeout 系・起動行・web 固定ラベル
  （`views._localize_html`）。未鍵化（日本語フォールバック）＝ /model 詳細・/restart 経路・/arc /game の対話文言・
  commands.py（/font /daynight）・game_controller。~~住人表示名「茶々」は固有名として維持~~＝7/19 に `voice.resident_name()` で voice 追従へ切替（下記追記）。
- **英語運用の追補（7/18 同日）**: `conversation.resolve_addressee` に英字対応（persona の英単語 alias＝機能語ストップ
  ＋単語境界照合・「Guest/Chacha/both/you two」）／`prompts.strip_resident_leak` の思考除去ヒューリスティック
  （先頭の非日本語塊＝染み出し）は **llm_lang 非日本語では跳ばす**（英文中の和語引用で正当な英文を壊さない）／
  `/arc` に英別名 sparrow/cat/wind（`scheduler.ARC_KEY_ALIASES`）。
- 同梱バンドルは `voices/en`（persona=英語の茶々の書き起こし・strings=UI 訳・llm_lang=en）。PyInstaller spec の
  datas に `voices/en` を追加。設定雛形は `engawa.json.sample[voice]`。
- 方言ユースケース（persona 一枚差し）は `test_voice.test_persona_only_bundle` で継ぎ目を検証。~~京都弁の実バンドルは未同梱~~＝**`voices/ja-kyoto` を 7/19 に同梱**（ADR-0033 の著者手順ドッグフーディング産・部分訳スタイル＝strings13キー＋culture place=京都・llm_lang 無しで注入不変を実 bundle で実証）。

## 追記（2026-07-19・lang note のソロ経路穴＝実測→修正→検証3層）

- **不具合**: lang note が `prompts.py` のビルダー（user_narration/room_*）にしか無く、`sources.py` のソロ narration（ambient/event/transition）は言語指示ゼロ。persona は英語で**書かれている**だけで言語を**縛らない**ため、en モードの「起動→話しかけ前」のソロ独り言が日本語になる（7/18 実機 E2E は先に話しかけ→英語慣性で以降マスク＝見えなかった）。
- **実測（10回×4カテゴリ×2エンジン=80発話・実 Claude opus＋qwen3.5-9b/LM Studio）**: 完全に二値。note 有り経路=40/40 きれいな英語（日本語片・訳語ゆれゼロ・蚊取り線香 narrate も自然に消化）／無し経路=非「……」発話がほぼ100%日本語（qwen は日本語落ち時に人格も崩壊＝丁寧語女言葉）。「LLM 翻訳任せのガチャ」ではなく決定論的な構造穴。懸念だった天気語彙の訳ゆれは**非問題**と確定（茶々は天気を復唱しない設計＝WEATHER_CODE 英語列は不要と判断）。
- **修正**: `lang_note()` を `voice.py` へ移設（prompts は委譲・sources→voice は葉 import で一方向維持）し、sources の3ビルダー末尾へ後置。JP 既定では空文字＝1バイト不変。修正後の再測（ambient/arc×3×両エンジン）で **12/12 英語・人格維持**（qwen も "...mornin', the clouds over Osaka are feelin' pretty light today."）。
- **見逃しの構造（3枚重ね）**: ①実装をモジュール境界（prompts のみ）でスイープし概念単位（LLM に届く全注入）でしなかった ②層またぎ不変条件の合成テスト不在（ADR-0031 ARRIVE 穴と同型） ③E2E が一つの操作順序しか踏まず露出窓（起動→idle→初手つぶやき）を通らなかった。
- **同日追補（表示名と宛先バー・ユーザー実機スクショ起点）**: en 画面で「チップ=Chacha／話者タグ=茶々」の不一致と宛先バー見出し「宛先」の鍵化漏れが露見→ ①`voice.resident_name()` 新設（strings `resident_name`・既定「茶々」＝JP 不変）＝scheduler/room_speakers/game_controller/ConsoleView の表示名を voice 追従に（実装メモの「固有名として維持」を更新・宛先解決は文面ベースで無影響） ②`ui_addr`（=To）を `views._localize_html` に追加。自発来訪の役名プール（GUEST_PERSONAS）の英語化は culture.json Inc3 へ送り（ユーザー判断）。**→当日実機で2バグ露見・即日補修**: (a) `Speaker.name` が「LLM が読む会話窓のタグ」と「画面表示」の一人二役だったため、表示名切替が**注入文の窓ラベルまで `Chacha「…」` に変え**、場面指示「茶々として」と分裂→**茶々の口調が客人化**（実機で饒舌な説話調に）。`Speaker(name, fn, display=)` で分離＝窓は「茶々」（v0.1.8 とバイト同一）・画面だけ display。 (b) web JS に「茶々」ハードコード3箇所（ソロ転写ラベル固定・`speaker==='茶々'` の色分け＝Chacha 行が客人スタイル・`!=='茶々'` の客人在室判定＝Chacha 発話で誤って耳ピン/客人名登録）→ `const RESIDENT`（`_localize_html` が resident_name を注入）経由に統一。教訓: 「web は who を表示している」を実表示で検証せず仮定した＝掃引テスト（ADR-0033 予定）の必要性の追加実証。
- **再発防止（検証3層・ユーザー方針）**: **層A**=`tests/test_injection_lang.py`（ビルダー明示列挙×「en で note・JP で不変」＋命名 canary＝`*_narration`/`*_prompt` の公開関数は「検証済み∪除外明記」に必ず分類。`game_move_prompt` は手番トークン契約＝言語中立のため除外明記）／**層B**=`tests/e2e/leak_probe.py`（実 LLM で全カテゴリ一巡する opt-in ハーネス・**既定は trial ごと新品セッション＝全発話が文脈慣性なしの初手**＝露出の急所。セッション使い回しは #1 以降に言語慣性が乗り再発を過小検出するため慣性観測用 `--sticky` に分離＝codex 7/19 [中] 反映・unittest discover 非対象）／**層C**=人手 E2E は初手順序を変えた窓（起動→idle 放置→初手 vs 即話しかけ）を踏む。

## 備考

- 本 ADR の肝は **「声」を主役・「言語」をその属性に置く**こと。`i18n=言語` より正確で、方言を一級市民にできる。
- 既定 `ja-osaka` がフォールバックの底＝**ゼロ設定で現状維持**。未訳キーは下位へ落ちるので、英語版を**部分的に**始めても壊れない（漸進導入）。
- 茶々が地域色（例: 鹿児島なら桜島・芋焼酎）をどこまで自分の声に混ぜるかは **persona 著者の裁量**。MVP では voice（声）と local culture を強制分離しない。
