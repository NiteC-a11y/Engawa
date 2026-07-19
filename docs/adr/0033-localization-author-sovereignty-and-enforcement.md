# ADR-0033: ローカライズは「著者主権＋機械強制」＝現地の人がコード0行で自分の土地の表記と茶々を作りきれる（0022 の refine）

- ステータス: Accepted（設計確定＝設計決定10点＋codex レビュー追補10点・**Inc1〜Inc4 実装済み 2026-07-19＝全増分完了**＝台帳/ローダー/3段解決/全サイト移行/雛形/静的照合＋掃引二系統（residue/sovereignty sentinel）/View canary/DOM sentinel/残ハードコード鍵化＋voice_lint（5状態・初回実行で同梱 en の base 自己参照を発見→修正）/voices/README＋culture（役名 id/display 分離・地名 env>voice>locale・季節/天気語彙は実測非問題で意図的未収載＝痛んだら追加））
- 日付: 2026-07-19
- 関連: ADR-0022（voice バンドル＝親・本 ADR はその完成形）, ADR-0030（TECH_RULES 規約）, ADR-0031/0032（列挙 canary・台帳駆動の流儀）, 原則#4（config 主導）, TECH_RULES §9（テスト方針＝掃引の受け皿）
- 出自: 7/19 ユーザー要件「**現地の人が、自分の土地に合わせた表記と茶々にしたい、で簡単に修正できるように**」＋同日までの修正漏れ続発の実証（7/18 同類穴3件・7/19 lang note ソロ経路・宛先バー・表示名・起動 tag・console ヘッダ・web JS ハードコード3箇所）

## 背景 / 課題

現行のローカライズは **opt-in 鍵化**＝日本語リテラルをコード各所に直書きし、誰かが `voice.loc(key, 既定)` で包んだ所だけ差し替わる。この設計は「漏れ」が既定値で、完全性を機械検査できない——結果、穴の発見手段が**ユーザーのスクショ・実測ハーネス・レビューの目視**に依存し、7/18〜19 の2日間で8件の漏れが後追いで見つかった（うち1件は表示名の一人二役から**茶々の口調が客人化する実機バグ**へ発展）。

著者（現地の人）の視点ではさらに深刻で、4つの穴がある:
1. **キー一覧の正本が無い**: キーは約45箇所のコールサイトに暗黙に散在。strings.json を書くには en バンドルの見様見真似かコード読解しかなく、en に無いキーは**存在すら知りようがない**。
2. **未訳が見えない**: graceful fallback（未訳→日本語）は部分導入には優しいが、完訳したい著者に漏れを教えない。
3. **bundle から直せない壁**: console 固定ヘッダ・自発来訪の役名プール（GUEST_PERSONAS）・季節/天気語彙・地名（PLACE_LABEL の発話ラベル）はハードコード or 未実装（culture）＝著者がどう頑張っても直せない。
4. **手順書が無い**: 「自分の voice を作る」ガイドが存在しない。

根っこは、文字列に2種類（**View に出る表示文字列**と **LLM に届く注入文字列**）があるのに境界が原則化されておらず、実装が**モジュール単位**でスイープされてきたこと（lang note 穴・表示名事故と同型）。

## 決定（方向。設計詳細は次フェーズで固める）

1. **著者主権の原則**: ユーザー可視の文字列は**すべて voice bundle から上書き可能**でなければならない。可視文字列のハードコードは原則違反（＝バグと同格に扱う）。
2. **文字列の2種境界を原則化**: **View 行き**＝`loc()` 経由必須（bundle で差し替え可能）。**LLM 行き**＝日本語のままで良いが `voice.lang_note()` 後置必須（実装・機械強制済み＝`tests/test_injection_lang.py`）。
3. **単一台帳（strings registry）**: キー＋日本語既定を一元化し、コールサイトのインライン既定の散在（同キー別既定のドリフト源）を解消。台帳から **`voices/_template/strings.json`（全キー＋既定入りの雛形）を生成・同梱**＝著者のキー一覧の正本。
4. **同じ台帳から機械強制を2種生やす**:
   - **掃引テスト（開発者向け・CI）**: 対象 voice（en）で全 UI サーフェスを render し、日本語残存を EXEMPT（理由付き台帳）以外で red にする。**DOM レベルを含める**（HTML 文字列検査では JS が組むラベルを見逃す＝7/19 の実証）。
   - **bundle lint（著者向けツール）**: `voices/<id>` の未訳キー一覧をレポート＝graceful fallback の「漏れが見えない」問題を解消。
5. **culture.json（ADR-0022 Inc3）は同原則のデータ側**として接続: 客人役名プール・地名・季節/天気語彙を bundle 側データへ（着手条件は 7/19 に実需露見済み＝en 画面の和字役名）。
6. **graceful fallback は維持**（ADR-0022 の決定を変えない）: 未訳キーは日本語へ落ちる＝部分導入で壊れない。「完全性」は fallback の廃止でなく lint/掃引の**可視化**で担保する。
7. **手順書**: `voices/README`（フォルダを作る→meta/persona を書く→雛形 strings を埋める→lint で漏れ確認→起動。コード0行・PR 不要）。

## 検討した代替案

- **現状維持（opt-in 鍵化＋docs の後送りリスト）** → 却下。7/18〜19 の漏れ8件で破綻を実証済み。散文リストは点検を人に依存し、機械強制がない。
- **gettext / babel（stdlib gettext 含む）** → 却下。抽出ツール（キー台帳の自動化）は魅力だが、voice は「言語」でなく**「声」が主役**（方言・persona・base 継承を1バンドルで束ねる）で .po の言語モデルと不整合。`loc()` が既に gettext 相当の API 形をしており、載せ替えの益が薄い（車輪の再発明回避の検討として実施）。
- **全文言の即時完全鍵化（big bang）** → 却下。漸進導入の利点（ADR-0022・部分導入で壊れない）を捨てる。意図的残置は EXEMPT 台帳（理由必須）で管理し段階的に狭める。

## 影響 / 帰結

- 新しい UI 文言は台帳に1行足せば、雛形・lint・掃引テストが**自動追従**＝「新 voice はフォルダを足すだけ」（ADR-0022 の約束）が文字通りになる。
- 実装スコープ: 既存 `loc()` 約45コールサイトの台帳移行／残ハードコードの鍵化（console ヘッダ=codex 7/19[中]2・起動系）／雛形生成／lint／掃引テスト。**she/her 中立化検討（Backlog）と PLACE_LABEL・GUEST_PERSONAS の英語化はこの章に合流**。
- テスト側の前倒し3本（注入 snapshot・leak_probe 本番配線・DOM ラベル掃引）は実装済み・TECH_RULES §9 に正本化済み＝本 ADR の掃引はその**全サーフェス拡張**。
- LLM 注入側（プロンプト工場）は本 ADR の対象外＝日本語のまま＋lang note 方式を維持（変えるなら別 ADR）。

## 設計決定（追記 2026-07-19・ユーザー確定）

1. **台帳は JSON**（既存 strings.json と同じ流儀＝非開発者に優しい・コメントは `_comment` キー）。
2. **置き場は新設 `locales/`（repo root）＝世界慣習の名前**（Rails/i18next 等の最多数派。「local」は `/usr/local` 系の誤読があり不採用）。**voices/ は動かさない**＝locale は言語×地域・voice はその上位（`ja-osaka`/`ja-kyoto` は同 locale 別声）で、voices を locales の下に畳むと ADR-0022 の「声が主役」の序列が逆転するため却下。
3. **分担ルール（一行）**: **「キーの定義は locales・訳と声は voices」**。
   - `locales/strings.json` … 台帳＝全キー＋日本語既定の単一正本（開発者が定義・著者は読むだけ）。
   - `locales/culture.json` … (Inc3) 季節/天気語彙・役名プール・地名の日本既定（同じ対称性）。
   - `voices/_template/strings.json` … 台帳から生成した雛形（著者の作業場に近接させる）。
   - `voices/<id>/strings.json`（+ culture.json）… 上書き。
4. **`loc()` の解決順は3段**: `voices/<id>` → base → **`locales/strings.json`（既定）**＝コード内インライン既定を廃止（同キー別既定のドリフト源を根治）。
5. 移行コスト: 既存 `voices/en`・PyInstaller datas・`ENGAWA_VOICES_DIR`・bat は無変更（新設ファイルのみ locales/ へ。locales/ の datas 同梱は実装時に spec へ追加）。
6. **雛形はチェックイン＋一致テスト**: `voices/_template/strings.json` は生成物だが repo にコミット（clone 後すぐコピー可・diff で見える）。「雛形＝台帳から再生成した結果と一致」をユニットテストで張る＝台帳だけ更新して雛形を忘れると赤。生成は `tools/` の小スクリプト。
7. **掃引テストは SURFACES registry＋DOM 二段**: render callable を明示列挙（injection canary と同型・EXEMPT は理由付き）。web は文字列検査に加えブラウザテストで DOM レベルも掃く（JS が実行時に組むラベルの見逃し防止＝7/19 の実証）。console は文字列レベル。
8. **lint は CLI・CI 外**: `python tools/voice_lint.py <id>`＝未訳キー・未知キー（typo 検出）・meta 欠落を人間可読の表で報告。開発側の番人は掃引テスト（CI）が担い、lint は著者の道具＝「部分訳でも良い」（graceful fallback）と緊張させない。
9. **culture.json の移行順は実需順**: 役名プール（実害露見済み）→ 地名（PLACE_LABEL 素通し 2/40）→ 季節/天気語彙（痛み未観測）。スキーマはフラット JSON から始め、必要になったら構造化。
10. **台帳欠損時はキー名表示で起動継続**: loc はキー文字列をそのまま返す＝壊れが画面で一目でバレる（静かに誤魔化さない）。起動は止めない（props/voice の欠損スキップと同じ流儀）。

## 設計追補（2026-07-19・codex 設計レビュー10件を全採用＝`codex/review-2026-07-19-adr-0033.md`・EXEMPT の owner/期限のみソロ運用に過剰として不採用）

11. **掃引は二系統に分離**: residue sweep（en の全サーフェスに日本語既定が漏れない）＋ **sovereignty canary**（台帳の全既定を sentinel `__L10N_<key>__` に差し替えた test voice で全サーフェスを駆動し、sentinel の出現で「可視文言が台帳経由」を証明）＝決定1「上書き可能」の直接の機械強制（日本語残存検査だけでは英語直書きを見逃す・[高]①）。
12. **lint の状態は5値**: `missing / inherited-from-base / same-as-default / translated(overridden) / unknown`。「完訳」= missing=0 かつ unknown=0（same-as-default は要確認表示＝意図した同値を偽陽性にしない・[高]②。既定入り雛形のコピー直後に存在判定で全キー緑になる自己矛盾の解消）。
13. **locales 正本は専用ローダー**: 読込結果を `ok / missing / malformed / wrong-shape` で保持し debuglog に一度だけ原因を出す（`_read_json` の「全部 {} に畳む」流儀は任意 bundle 用で、正本には使わない・[高]③）。起動継続は決定10のまま。frozen smoke で `locales/strings.json` の存在＋代表キー解決を検証。空文字・非文字列の値は欠損扱い。
14. **base 継承は一段限定と明文化**（再帰なし・cycle detection を作らない＝YAGNI）。base 不在・自己参照・base がさらに base を持つ場合は lint が警告。strings/persona/llm_lang/culture の優先順位は同一の表（`voice → base → locales/組み込み`）で固定。
15. **SURFACES にもカテゴリ別の追加検知 canary**（console 出力経路／web シェル／poll item type／ゲーム窓／system・コマンド応答）＝列挙でなく canary が強い、という injection テストの強みを移植。**EXEMPT は surface＋要素＋理由の3項目・テスト内の型付き定数から**（JSON 化は実需が出たら・[低]①）。
16. **DOM 掃引は状態駆動**: 各ケースに poll payload／操作→期待 selector・属性を持たせ、状態遷移後に掃く（開いて body を一度走査では動的 UI を漏らす）。canvas は描画へ渡すモデル値を純関数で検証。
17. **culture の役名は安定 ID と display を分離**し、topic/persona 照合は ID で行う（Speaker.name 一人二役事故の culture 先回り）。地名の優先順位は `ENGAWA_PLACE_LABEL`(env) > voice culture > locale 既定。定型ナレはユーザー可視分のみ strings（guest_timeout_leave 等は鍵化済み）。
18. **loc() の静的照合テスト**: 全 `loc()` キー ∈ 台帳・台帳の未使用キー検査・**placeholder 集合一致**（書式は `str.format` と定める）・動的キーは原則禁止（理由付き EXEMPT）。移行中の `loc(key, default)` 併存は実装増分で廃止期限を切る。
19. **雛形一致テストは二段**: 意味比較（キー・値集合）＋バイト一致（生成器は UTF-8/改行/indent/キー順を決定的に固定）。`_comment` は翻訳キー集合から除外。
20. **voices/README は codex 提案の10項目目次を仕様採用**（voice と locale の違い→最短手順→meta→strings 書式→culture→lint 状態と直し方→fallback 説明→実機チェックリスト→よくある失敗→配布と `ENGAWA_VOICES_DIR`。lint 実行例と起動コマンド込み・exit code 定義・`--json` は実需まで不要）。

## 備考（実装フェーズへ送る細目）

- 実装の増分割り（台帳移行 ~45 コールサイト／console ヘッダ等の残ハードコード鍵化／雛形＋一致テスト／掃引／lint／culture の順や束ね方）。
- EXEMPT 台帳の置き場と粒度（掃引テスト内のコード定数か JSON か）。
- culture.json の具体スキーマ（役名プールのキー名・地名の扱いと `ENGAWA_PLACE_LABEL` との優先関係）。
