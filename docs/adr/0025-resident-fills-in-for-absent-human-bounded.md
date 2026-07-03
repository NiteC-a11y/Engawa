# ADR-0025: 人間待ちの間、茶々が“人間役の代打”で場をつなぐ（有界・予算付き）

- ステータス: Accepted（実装）
- 日付: 2026-07-02
- 関連: ADR-0015（人間アンカーで有界な3人会話）, ADR-0008（客人=有界の来訪）, ADR-0006（cancel優先）, ADR-0004（自律AI↔AI 会話からの転換）, 原則#3
- 影響: ADR-0015 の歯止め「AwaitingHuman は on_tick で AI を一切動かさない」を **予算付きで緩和**（Supersede ではなく *追補*。有界の原則自体は堅持）。

## 背景 / 課題
ADR-0015 の 3人会話は、人間が黙ると `AwaitingHuman` で **AI を一切動かさず**、沈黙が `idle_leave_ticks`（既定8・約1分）続くと客人が辞去する。これは「人間不在の無際限な自律往復（ADR-0004 の水槽）に戻さない」核だが、実機で使うと **「自分が会話に入らないと、客人が無音のまま待って1分で帰ってしまう」＝寂しい**（ユーザー報告 2026-07-02）。

無音で放置され消える体験の正体は「縁側に二人（茶々・客人）居るのに、人間が駆動しないと場が死ぬ」こと。単に `idle_leave_ticks` を伸ばしても**無音が長引くだけ**で気配は増えない。

## 決定
**人間が席を外している間だけ、茶々が“人間役の代打（driver）”を務める。** 沈黙が続くと茶々が客人に軽く振り、客人が一言返す＝**1往復**で必ず人間待ちへ戻る。これを **予算 `fill_cap` 回**まで許し、使い切ったら従来どおり `idle_leave_ticks` の沈黙で辞去する。

- `AwaitingHuman.on_tick`: 沈黙が「n回目のしきい値 = `fill_after + n×fill_slowdown`」続き、かつ予算が残るなら `ResidentFilling` へ（予算−1）。予算ゼロで沈黙が `idle_leave_ticks` に達したら `Leaving`。
- **テンポは一定でなく減衰**（`fill_slowdown` 既定1）: 来た直後は代打が早く入り、回を追うごとに間隔が延びる＝人間の「来てすぐは賑やか→ネタが尽きて間延び→帰る」を模す。静的に1つの間隔値を選ぶ悩み（速すぎ／遅すぎ）を、時間変化で解消する。`fill_slowdown=0` で一定間隔。
- `ResidentFilling`: 茶々（`MUSE` kind）→ 客人（`REPLY`）の1往復 → `AwaitingHuman`（idle は 0 から数え直し）。
- **人間が一度でも関与したら予算を満タンに戻す**（`Responding` 末尾）＝人間が居る限り場は続き、居なくなればまた数回で店じまい。

## なぜ原則#3 に反しないか（歯止め＝ここが核）
禁じるのは ADR-0004/0015 のとおり **「人間不在・*無際限*・機械的な自律往復」**。本 ADR は:
1. **有界**: 人間不在の連続 AI ターンは `fill_cap`（既定3）で厳密に上限。予算を使い切れば必ず `Leaving`＝終端に着く。人間ゼロでも往復は `Greeting(2) + fill_cap×2 + Leaving(2)` 手で必ず止まる。
2. **人間アンカー維持**: 人間入力はいつでも最優先で割り込み、`Responding` に遷移して主導権を取り戻す（＋予算リセット）。
3. **来訪は有界のまま**: ADR-0008 の核（非常駐・使い捨て・cooldown）は不変。
4. **`fill_cap=0` で従来挙動**: 代打を完全に切れば ADR-0015 の「on_tick で AI を動かさない」に戻る＝退路を残す。

つまり "無際限" だったものを "予算付き有界" にしただけで、地雷（水槽）には近づかない。予算 = ADR-0015 が最難関と呼んだ「連続 AI ターン上限」そのもの。

## 検討した代替案
- **`idle_leave_ticks` を伸ばすだけ**: 無音が長引くだけで気配が増えず、むしろ気まずい。却下（体験が良くならない）。
- **茶々↔客人が対等に勝手に雑談（idle banter）**: 「駆動役が居ない対等往復」は自律往復に見え、止め時が曖昧。**人間役の代打（駆動者を1人に固定＋予算）**の方が有界性が明快。却下。
- **人間が来るまで無制限に代打**: 予算なし＝ADR-0004 の水槽に逆戻り。却下（終端が要る）。

## 影響 / 帰結
- `conversation.py`: `MUSE` kind ＋ `ResidentFilling` state ＋ `fill_cap`/`fill_after`/`_fill_left`。State: `Greeting → AwaitingHuman ⇄ (Responding | ResidentFilling) → Leaving → Closed`。
- `prompts.py`: `_RESIDENT_SCENE[MUSE]`＝「人間は席を外し中。客人に軽く振る／景色に一言。もてなす女将でなく気ままな住人として」。
- `scheduler.py`: config 3本（`ENGAWA_GUEST_FILL_CAP` 既定3 / `ENGAWA_GUEST_FILL_AFTER` 既定2 / `ENGAWA_GUEST_FILL_SLOWDOWN` 既定1）→ `Room` に配線。既定で有効。
- テスト: `conversation` に代打の発火／予算枯渇→辞去／人間で予算リセット／`fill_cap=0` で純待ち／無言なら客人も動かさない。`scheduler` に「沈黙で代打が実プロンプトを流す」「長引いても必ず辞去（有界）」。
- **発話のトーンは実 codex/resident で目視**（代打が“もてなし過ぎ”ないか・自然な間か）。LLM 判断は自動テスト外＝入力（MUSE 注入・予算遷移）を自動化し、発話は目視（ADR-0015 と同じ分担）。

## 備考（Open Questions）
- cancel優先（ADR-0006）の部屋内統合は Inc3 のまま（代打ターンも現状は短く直列化＝人間は待つ）。
- ADR-0024（客人複数化）では「話す順 Strategy」に代打も一枚岩で載る想定（人間不在時の driver 選び）。
- `fill_after`／`fill_slowdown`／`idle_leave_ticks` の体感バランスは engawa.json で調整（既定 fill_after=2・fill_slowdown=1・fill_cap=3）。ビート秒は `timing.active_beat_min/max`（来訪中の tick 間隔）に掛かる。
