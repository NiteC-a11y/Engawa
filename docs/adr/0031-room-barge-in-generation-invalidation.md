# ADR-0031: 部屋内 barge-in ＝ 最新入力が古い generation を無効化し、commit は現行 generation のみ（スコープM）

- ステータス: Accepted（スコープM 実装・スコープL は設計記録のみ）
- 日付: 2026-07-18
- 関連: ADR-0006（cancel優先）, ADR-0015（3人会話・Inc3）, ADR-0025（代打）, ADR-0026（Agent ポート）, 原則#3
- 出自: 設計→codex 外部レビューの二段（`codex/review-requests-2026-07-18-room-cancel.md` → `codex/review-2026-07-18-room-cancel.md`）

## 背景 / 課題

部屋（3人会話）の AI 発話チェーン（Greeting の挨拶2手・Responding の REPLY→CHIME・ResidentFilling の代打1往復）は
drive_lock を握ったまま完走し、barge-in（ADR-0006）はソロ経路（`room is None`）にしか無い。実害として
「自分の発話の後に、前のビートへの返事が追い越して表示され、とんちんかんに見える」（ユーザー報告 2026-07-18）。

codex レビューで判明した構造上の要点: **`Scheduler.run()` の入力ループは1件ずつ `await on_user_input(line)` する逐次処理**
（`scheduler.py:560-564`）。ゆえに
- **tick 駆動のチェーン**（自発来訪の挨拶・代打・辞去）中は入力ループが空いている＝割り込みを検知**できる**。
- **入力起点のチェーン**（自分の発話への Responding）中は次の入力が読まれもしない＝割り込みを検知**できない**。

ソロ barge-in が効いていたのは、つぶやきが tick タスク駆動で入力ループが空いていたから。

## 決定（スコープM）

**tick 駆動チェーンへの barge-in を「generation 無効化＋commit gate＋in-flight cancel」で実装する。`run()` の入力ループには触れない。**

核の不変条件（レビュー採用）:
1. **最新の人間入力が古い room generation を無効化する** — Scheduler が単調増加の `_room_rev` を持ち、部屋への
   会話入力の到着で +1。各ドライブ（begin/on_tick/on_human）は開始時 rev を閉じ込めた `should_stop()`
   （＝「自分はもう最新でない」）を受け取る。bool フラグ（クリア競合）・待機カウンタ（減算リーク/ABA）は却下。
2. **表示/transcript への commit は現行 generation のみ** — 停止判定は「各手の前」＋「prompt 復帰後・commit 前」の
   二段で、どちらも `Room._utter()` の一箇所（commit gate）。言いかけの部分テキストは画面にも transcript にも積まない。
   破棄の状態源は Room に一元化（Speaker/factory 側での二重破棄はしない）。
3. **in-flight は best-effort cancel** — 入力到着時、茶々生成中（`speaking`）なら `resident.cancel()`、客人生成中なら
   `RoomSpeakerFactory.cancel_inflight()`（factory が in-flight agent を同一性確認付きで追跡・Scheduler に agent 実体を
   見せない＝ADR-0026 のポート境界維持）。実際に畳んだ時だけ演出1行「[話の途中でこちらを向いた]」。

仕様の明確化（レビュー採用）:
- **Greeting**: ARRIVE は中断不可（到着という世界状態を確定させる）・REACT のみ省略可。**Leaving**: 中断不可（終端保証・短い）。
- **代打予算**: MUSE が barge-in で不発（未 commit）なら予算を返す。無言（LLM 判断の沈黙）は従来どおり消費
  （返すと「沈黙で予算が減らない×idle リセット」で辞去に着かない無際限ループが生じるため）。
- **timeout 誤発火防止**: barge-in と同時の `AgentTimeoutError` は、そのドライブが失効済みなら急用退場に数えない。

## なぜ有界性（原則#3）に触れないか

無効化は手を**減らす**方向にしか働かない。turn_cap・fill_cap・idle_leave・Leaving の終端保証は不変。
「人間入力は常に最優先」が、手と手の間だけでなく**生成の最中にも**効くようになる＝原則#3 の強化。

## 検討した代替案

- **bool フラグ**: 連打時「入力1が lock 取得でクリア→入力1のチェーン完走→入力2の畳む意図が消える」競合。却下。
- **待機入力カウンタ**: 例外・task cancel・room close・ソロへのフォールスルーの全経路で厳密な減算が要る＋ABA。却下（revision が上位互換）。
- **factory 側でも部分テキストを破棄（二重化）**: 状態源が二つになる。却下（commit gate は Room 一箇所）。
- **スコープL＝room 入力の worker 化**: `run()` の逐次入力を room だけ background task 化し、入力起点チェーンへの連打
  barge-in も可能にする完全版。**設計はレビュー済み・実装は保留**（`run()` という最も繊細な並行制御の変更で、
  代打/挨拶の追い越し（体感の主因と推定）はスコープM で消えるため）。M で実機に暮らし、
  「自分の発話への応答中の連打が待たされる」がまだ痛ければ着手（レビューの Inc3b/3c 設計＝worker 所有と teardown・
  per-prompt turn token・forced-cancel 後の客人 respawn を土台にする）。
- **寝かせる（設計だけ残す）**: とんちんかんの実害報告があり、M は低リスクで根に効く。却下。

## 影響 / 帰結

- `conversation.py`: `begin/on_human/on_tick` に `should_stop=None`（既定＝従来挙動不変・純粋性維持＝注入カラブルのみ）。
  `_utter(preemptible=True)` に二段 gate。`Room.preempted` プロパティ。ResidentFilling の予算返却。
- `room_speakers.py`: `_inflight` 追跡（finally は同一性確認）＋ `cancel_inflight()` ＋ `preempted` 注入で timeout 抑制。
- `scheduler.py`: `_room_rev`／ドライブ入口で `should_stop` を配線／room 入力路で rev+1→cancel→演出→on_human。
- テスト: conversation（二段 gate・Greeting/Leaving の可否・予算返却 vs 沈黙消費）・room_speakers（in-flight/cancel/
  timeout 抑制）・scheduler（tick 駆動チェーン中の入力で cancel が飛ぶ・古い行が表示されない・誤退場しない）。
- 実機 E2E（ユーザー）: 代打・挨拶中の被せ→即畳めるか、直後の次手が正常か（codex-acp の cancel 挙動は未検証領域・
  `engawa-debug.bat` で観察）。

## 追記（2026-07-18・codex diff レビューの指摘反映）

**穴**: `_room_barge_in` が部屋の状態を見ずに無条件で in-flight を cancel していた。`preemptible=False`
（ARRIVE/辞去）は「破棄 gate の無効化」でしかないため、**cancel 由来の部分文/空文字が逆に gate を素通りして
commit され得た**（`AcpAgent.prompt` は cancel 時に途中 buf を返す）＝「到着を確定」「挨拶なく消えない」の宣言と乖離。
**修正**: `Room._utter` が生成中の手の中断可否を `utter_preemptible` として公開し、`_room_barge_in` は
rev+1 は常に行い（非中断手は gate を無視して完走・後続の REACT だけ省略＝仕様どおり）、**cancel と演出は
中断可の手の生成中だけ**。統合テスト3本（ARRIVE 完走・辞去完走・REACT は畳める）＋Room 単体1本。

**なぜ既存テストで見つからなかったか（原因分析）**:
1. **層またぎの不変条件を層ごとに検証していた** — 「ARRIVE は中断不可」は本来 (a) scheduler が cancel を
   送らない ＋ (b) Room が破棄しない の合成仕様。実装は (b) だけを作り、テストも conversation 単体
   （偽 Speaker＝常に完全文・cancel が存在しない層）と scheduler 統合（cancel は検証するが塞き止める手が
   MUSE/REPLY だけ）に分かれ、**合成（cancel × 非中断 commit）がちょうど継ぎ目に落ちた**。
2. **fake のブロック位置が実装者の想定をなぞった**（確認バイアス）—「中断不可＝scheduler は触らない」という
   誤読が、テストの「どの手で被せるか」の選定にそのまま伝播した。
3. **状態×手種の窓を列挙しなかった** — barge-in は任意の生成中に来る＝窓は7つ（ARRIVE/REACT/REPLY/CHIME/
   MUSE/LEAVE/LEAVE_REACT）あるのに2窓しか張っていなかった。
**教訓**: 層をまたぐ否定形の不変条件（「Xは〜されない」）は実経路の統合テストで**窓を列挙して**張る。

## 備考（Open Questions）

- スコープL（入力 worker・revision の本領＝Responding 連打）: 上記のとおり設計済み・保留。再開時は新 ADR でなく
  本 ADR の追記＋Backlog 起票で足りる（不変条件は同じ・実装範囲の拡張のみ）。
- 中断された側の AI へ「遮られた」事実を次 prompt にローカル注として渡す案（レビュー提案）: M では未実装
  （transcript に言いかけが無い＝文脈は自然に保たれる読み）。違和感が観測されたら足す。
- codex-acp の session/cancel 実挙動と forced-cancel 後の同一 session 汚染: E2E で観察。問題が出たら
  レビュー勧告どおり「forced cancel＝汚染疑い→close→respawn」を factory/GuestSource 層に足す（スコープL 前倒し）。
