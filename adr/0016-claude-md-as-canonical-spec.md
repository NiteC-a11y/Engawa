# ADR-0016: ドキュメントの正本を CLAUDE.md に定め、`engawa-acp-spec.md`(v1) を旧構想として降格

- ステータス: Accepted
- 日付: 2026-06-28
- 関連: ADR-0004（マルチエージェント会話からの転換）, ADR-0005, ADR-0008, TECH_RULES, CLAUDE.md

## 背景 / 課題
レビューで、ドキュメント層に「正本（現行全体像の据え置き文書）が存在しない」構造的な穴が見つかった。

- `engawa-acp-spec.md` は **ピボット前の旧構想**（「複数の AI 人格が自律的に会話する」＝ADR-0004 で捨てた方向／`V1 = Claude Code 同士のみ・Codex は V2 以降`／`V1 推奨：1ターン1セッション＋transcript 再投入`）。現行実装（resident=Claude **長命**／guest=Codex **使い捨て**）と真っ向から食い違う。
- `TECH_RULES.md`（§冒頭・§参照）と `adr/README.md` 冒頭は、正本として **`engawa-spec-v2.md` を参照しているが、そのファイルは存在しない**（＝書く予定だった v2 が未作成のまま参照だけ残った）。
- 実態として現行全体像は **CLAUDE.md＋ADR 群** が担っている。

「仕様書」を名乗る `engawa-acp-spec.md` が現行として宙吊りで、入口（README/TECH_RULES）が不在ファイルを指す状態は、読み手を誤らせる。

## 決定
1. **現行全体像の正本は `CLAUDE.md` とする。** 既に de-facto 正本で常時メンテされている。「どう動くか（1枚で）」が全体像を担う。
2. **`engawa-acp-spec.md` は旧構想（Superseded）として降格・温存。** 削除しない（`legacy/app.py` の温存と同じ思想）。冒頭に「旧構想／ADR-0004 でピボット済み」の banner を付す。なお ACP の機構面で今も有効な契約は **TECH_RULES §2 が正本**。
3. **新規 `engawa-spec-v2.md` は作らない。** v2 を別途書くと「正本がまた同期ずれする」本問題を再生産するため。`TECH_RULES.md` / `README.md` の `engawa-spec-v2.md`（「spec v2」）参照は **`CLAUDE.md`（正本）へ向け直す**。

## 検討した代替案
- **新規 `engawa-spec-v2.md` を起こして正本にする**: 参照名どおりにはなるが、維持すべき文書が1枚増え、CLAUDE.md と二重管理＝同期ずれの再発。却下。
- **`engawa-acp-spec.md` の中身を現行へ全面改訂して正本にする**: ピボットの歴史的記録を失い、改訂量も最大。却下。
- **`engawa-acp-spec.md` を削除する**: 履歴温存の文化に反する。却下。

## 影響 / 帰結
- 役割が一意になる：**全体像＝CLAUDE.md／決定の経緯＝adr/／据え置きルール＝TECH_RULES／旧構想＝engawa-acp-spec.md（歴史的参照）**。
- 不在ファイル `engawa-spec-v2.md` への壊れ参照が解消。
- 本 ADR と地続きの**機械的追従**（決定済み事実への索引・図合わせ。ADR 不要）：
  - `adr/README.md` のステータス（0011=実装済み・P3.5／0013=実装済み）。
  - `adr/0013` クラス図のフィールドを実装に一致（Narration に `voice`／Context は `prev_desc`→`topics`、prev_desc は WeatherSource 側／Scheduler `speaking_task:Task`→`speaking:bool`）。
  - `Backlog.md` の node 取り残し項目を「Job Object 化」に限定（`taskkill /T /F` は acp.py で実装済み）。

## 備考
- `engawa-acp-spec.md` に残る「今も有効な ACP 機構の記述」を TECH_RULES §2 へ完全に畳み込む作業は、当面そのまま（§2 でおおむねカバー済み）。欠落が顕在化したら被せる。
