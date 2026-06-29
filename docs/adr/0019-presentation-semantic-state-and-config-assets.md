# ADR-0019: presentation は「意味 state ＋ config 駆動の差し替えアセット」で統一する（sprite/sound/scene 共通）

- ステータス: Accepted（方針確定・実装はこれから／sprite は先行事例として整合）
- 日付: 2026-06-29
- 関連: ADR-0010（スプライト=差し替え皮）, ADR-0013（イベント源/スケジューラ＝View ポート）, ADR-0014（取得先は config・コードに埋めない）, ADR-0009（不透明な隅窓）, ADR-0017（本物の seam はポートで受ける）

## 背景 / 課題
これから出力（presentation）を厚くしたい希望が複数ある：**環境音＋茶々の鳴き声 / 背景の景色 / 時間帯（朝・昼・夕・夜）**。
これらは別物に見えて、実は**同じ核の信号（tod / 天気 / 進行中イベント / 茶々の state）に反応する別チャネルの出力**でしかない。
各機能を場当たりに作ると、(a) 核（Scheduler・人格ロジック）に presentation の具体（音ファイル名・画像パス・CSS）が滲む、(b) sprite / sound / scene が三者三様の流儀になる、(c) headless テストが崩れる、という負債になる。新ポートを機能ごとに乱立させるのも過剰になりやすい。

## 決定
presentation を **2つの規律**で統一する。新パラダイムではなく、既に効いている **View ポート（ADR-0013）** と **差し替えアセット（ADR-0010）** の延長として固める。

1. **核は「意味（semantic state）」だけ出す。presentation がそれを具体化する。**
   - 核が出すのは「夕方になった」「客人が来た」「茶々が聞いている」等の*意味*。音・絵・背景の*具体*は知らない。
   - `tod` / mood（天気由来）/ 茶々 state / 進行中イベント を、View が受け取れる**明示的な presentation state** に育てる（今は JS が poll から推測している部分を、核が明示的に持って渡す方向へ）。sprite も sound も scene も**同じ1つの state を読むだけ**になる。

2. **アセットは config 駆動の差し替え資源にする（共通 resolver）。**
   - `sprite.json` の流儀をそのまま `sound.json`（環境音・鳴き声）/ `scene.json`（背景・時間帯）へ展開。**ファイル・有効・音量・コマ等は config で宣言し、コードに具体を埋めない**（ADR-0010 / 0014 と同じ思想）。
   - 解決は `config.py` の env > json > 既定＋ root 基準 `_path()` を共通利用（`views.py` / `sources.py` の既存 `_path` パターン）。欠損/壊れは graceful フォールバック（sprite 欠落→procedural、音無し→無音、背景無し→既定グラデ）。

**重いポート（抽象IF＋複数アダプタ）は"本物の seam"だけに温存**する＝複数実装 or 外部依存 or 境界テストが要る所（ACP・Game/rlcard・View の console/web）。単一実装の出力（pywebview/HTML の音・背景）には config 駆動アセットで足り、抽象IF は ceremony になるだけなので作らない。

## 検討した代替案
- **機能ごとに独立実装（その場主義）**: 早いが上記の負債 (a)(b)(c)。却下。
- **音/背景それぞれに抽象 Port＋複数アダプタ**: P&A の過剰適用（YAGNI）。当面1実装（HTML）なのに indirection だけ増える。本物の seam に温存し、presentation アセットは config で受ける。却下。
- **rlcard-showdown 等の外部 viz を抱える**: 別 web アプリで思想・重さが不一致（UNO カード描画の調査でも確認＝UNO 非対応かつ React/Django 一式）。自前の薄い presentation の方が軽い。却下。

## 影響 / 帰結
- 音・背景・夜は**全部同じ型**に乗る＝実装が揃い、差し替え可能で、headless テスト（「実際に鳴らさず scene=night を assert」）が効く。**GUI 目視はユーザー / ロジックは自動**の分担を維持できる。
- `tod` は既に `build_context` にある核の信号なので「夜っぽく」は**既存信号の presentation マッピング**を足すだけ＝新しい状態を作らない。音・背景は同じ信号からの新チャネル。
- 既存 `sprite.json` / `ENGAWA_SPRITE_CONFIG` は本 ADR の先行事例として整合（遡及で違反なし）。

## 備考
- 「意味 state を核が明示的に持つ」への移行は段階的でよい（今の poll 推測を壊さず、必要な分だけ state を足す）。
- アセットの著作権/商標は各自オリジナル or ライセンス確認（ADR-0010 と同じ）。音素材も同様。
