# ADR-0024: 客人を複数化する会話＝Mediator＋話す順 Strategy＋AIターン予算（N人へ一般化）

- ステータス: Proposed（設計の形を記録・着手は要望確定＋ADR-0015 Inc3 の後）
- 日付: 2026-07-01
- 関連: ADR-0015（3人会話・State/Speaker/Mediator）, ADR-0008（客人は有界・常駐させない）, ADR-0006（cancel 優先の割り込み）, ADR-0013（YAGNI）, 原則#3（AI同士の自律・無際限会話に戻さない）

## 背景 / 課題

「客人を2名にできる？」という要望。現状 ADR-0015 は **私＋茶々＋客人1名の3人**にスコープし、`Room` は guest を**単数**で持つ（`conversation.py:109-112`）、`resolve_addressee` は `guest`/`resident`/`both` の**二値AI**（47-58）、`RoomState.Responding` の応答コンビは2AIを**べた書き**（`"guest":[(guest,REPLY),(resident,CHIME)]` 等・205-207）。**そのままでは複数客人は不可**。

一方で**継ぎ目は良い**：`Speaker`（Strategy/DI）・`Transcript`/`Utterance`（名前ベース＝人数非依存）は無改修で N 対応の土台、`GameSession`（人数非依存）という可変人数の前例もある＝**作り直しは要らない**。

**真の難所はターン管理**：茶々＋客人2＝**AIが3体**になると、AI同士のピンポン暴走リスクが跳ね上がる。これは ADR-0015 の State パターンが最も守りたかった所（原則#3）。

## 決定（設計の形・パターン）

N人会話を **Mediator ＋ 話す順 Strategy ＋ AIターン予算** で表す。GoF の Mediator＋Strategy／業界のマルチエージェント GroupChat（Manager＝Mediator・speaker-selection＝Strategy・max-round＝予算）と**同型**。今の作りの自然な一般化で、作り直しを避ける。

1. **Mediator＝Room（既存）**：参加者は直接喋らず Room が「フロア（発言権）」を仕切る。`guest`（単数）→ **`guests: list[Speaker]`**（resident＋guests）に一般化。
2. **State＝RoomState（既存・維持）**：`Greeting→AwaitingHuman⇄Responding→Leaving→Closed` の生命周期は不変（人間アンカーの骨は変えない）。
3. **話す順＝`TurnPolicy`（Strategy・新設）**：今べた書きの「宛先→(REPLY/CHIME)コンビ」を、**(宛先, 参加者, 残予算) → 次の話者列** を返す差し替え可能な戦略へ昇格。既定＝「宛先がまず応じ、他は最大1回だけ相槌、予算内で」。round-robin／司会(茶々)主導 等に差し替え可能。
4. **歯止め＝AIターン予算（Talking-stick / Round）**：人間発話ごとに **全AI共有の予算 B**（既存 `turn_cap=2` の一般化）を発行。AIが喋るたび消費、0 で必ず `AwaitingHuman` へ戻る。**個別capでなく「全体cap」が肝**＝3体でも合計 B 手で人間に戻る（暴走しない）。

付随:
- **宛先解決**：`resolve_addressee` を **複数客人名＋「みんな」** に拡張（名前メンションで特定客人へ／無印は既定 茶々）。
- **生命周期**：Scheduler/`GuestSource` が **codex を複数 spawn/close**（使い捨て・滞在有界は維持・ADR-0008）。
- **表示**：View は客人ごとに別の voice 行で区別（誰の発言か分かる）。

## 検討した代替案

- **Observer / リアクティブ**（各AIが Transcript を監視して自発反応）→ まさに原則#3 違反（人間不在の自律往復）。却下。
- **AI個別のターンcap**（各AIに2手ずつ）→ 3体で最大6手のピンポン＝緩すぎ。**全体予算**にする。
- **部屋を2つに分ける（1客人×2）**→ 互いの発言が聞こえず「同席」にならない。却下。
- **司会者なしの free-for-all**→ 混沌＆暴走。Mediator は必須。
- **guest を単に2フィールドへ（guest1/guest2）**→ スケールせずコンビが重複。`list` ＋ `TurnPolicy` にする。

## 影響 / 帰結

- 既存 `Speaker`/`Transcript` は**無改修で N 対応**（人数非依存が活きる）。
- 変える所：`Room`（`guests: list`）・`RoomState.Responding`（`TurnPolicy` へ委譲）・`resolve_addressee`（複数名＋みんな）・`TurnPolicy`（新規）・Scheduler/`GuestSource`（複数 codex の spawn/close）・View（客人別表示）。
- `turn_cap` の意味を **全AI共有の予算**へ一般化（**後方互換**：客人1名なら現挙動と一致するよう既定を選ぶ＝1客人は本 ADR の特殊形）。
- **前提**：ADR-0015 **Inc3（実 codex の3人 E2E）を先に**仕上げる（追記 2026-07-18: E2E は実機済み・Inc3 残は部屋内cancel統合/roomストリーミング）。その上の拡張。
- 重さ：中〜大。本体は turn 管理の再設計。テストは「3AIが予算 B 手で必ず人間に戻る」「特定客人宛の応答順」を**純ロジックで担保しやすい**（`conversation.py` は `re` のみの純モジュール）。

## 備考

- 業界類例：マルチエージェントの GroupChat は「Manager(Mediator) ＋ speaker-selection(Strategy) ＋ max-round(予算)」＝本 ADR と同型。「いい形がありそう」の直感は正しく、**Mediator＋Strategy＋予算**が定石。
- 原則#3 の担保は **「全体予算が必ず尽きて人間へ戻る」という構造**で保つ（State＋予算）。客人の有界性（ADR-0008）も維持（使い捨て・滞在有界）。
- 着手は要望が固まってから（YAGNI・ADR-0013）。本 ADR は**設計の置き場**として記録（Proposed）。実装時に Accepted へ。
