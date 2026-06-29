# ADR-0003: 住人の人格は cwd の CLAUDE.md で注入する

- ステータス: Accepted（P1で実証）
- 日付: 2026-06-26
- 関連: ADR-0001, ADR-0008

## 背景 / 課題
茶々を「コーディング助手」でなく縁側の住人として喋らせたい。ACP越しにどう人格を入れるか。

## 決定
アダプタの cwd に **CLAUDE.md** を置き、そこに人格定義を書く。

## 実証
P1で、cwdにCLAUDE.mdを置いた状態で claude-code-acp を起動 → 茶々が関西弁・住人として応答（A判定）。**ACP越しでも CLAUDE.md 経由の人格注入が効く**ことを確認。

## 検討した代替案
- **Agent SDK 直叩きで systemPrompt 完全置換**: 不要になった（CLAUDE.mdで足りた）。退避先として温存。
- **`.claude/output-styles/`**: 試さず。CLAUDE.mdで足りた。

## 影響 / 帰結
- L3が薄いまま人格を持てる。

## 備考
- Codex（客人）は CLAUDE.md を読まない。客人の人格は別経路（ADR-0008: 召喚時に動的注入）。
