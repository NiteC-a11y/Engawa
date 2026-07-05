# ADR-0029: Scheduler を薄い Orchestrator に戻す（責務を controller 群へ段階抽出・トップレベル分岐は Chain of Responsibility）

- ステータス: **Accepted（方針確定・2026-07-05）**。Claude の責務分析＋Codex の独立提案＋Codex 第2R レビューで合意。**P1〜4a＋P5(speak 一本化) 実装・マージ済み（Scheduler 839→584 行）／P4b(full Visit)・P5(full RM)・P6(CoR) は費用対効果で意図的に打ち切り**（下記「打ち切りの判断」）。
- 日付: 2026-07-05
- 関連: ADR-0013（イベント源/スケジューラ＝本 ADR が **refine**）, ADR-0015（Room の State パターン・3人会話）, ADR-0017（Game の Port&Adapter）, ADR-0026（Agent 中立ポート）, ADR-0027（茶々の中座＝定期セッション更新）, ADR-0006（cancel 優先の割り込み）, ADR-0007（入力2系統）, ADR-0023（テスト同梱必須）。原則4（設計判断は ADR）・原則6（テスト緑維持）。
- 影響: ADR-0013 を **refine（内部構造の追補）**。「Scheduler が Mediator として各 Port を結線する」方針は保つが、*一枚岩の Mediator* から *薄い Orchestrator ＋ 責務別 controller 群* へ内部を再編する。外部から見た振る舞い（tick 駆動・cancel 優先・有界会話）は不変。
- 出自: Claude の責務分析（2026-07-05）と、独立に依頼した Codex の具体案（`codex/refactor-proposal-2026-07-05-scheduler.md`）が**独立に同じ診断・同じ大枠に収束**。さらに本 ADR ドラフトを Codex に第2ラウンドでレビューさせ（`codex/review-requests-2026-07-05-scheduler.md` の C）、判断B（speak の2層化）と判断C（active 分離の前倒し＋範囲拡大）の補正を反映。本 ADR は両者の突き合わせで確定した計画＋相違点の判断を残すもの。**`codex/` の作業ドキュメントは gitignore＝ローカルのみで、設計判断の正本は本 ADR**（自己完結させてある）。

## 背景 / 課題
`src/scheduler.py` が **839 行・インスタンス属性 ~31 個**（`scheduler.py:60-90`）に肥大化。ADR-0013 では Scheduler は「イベント源を抽選・駆動する Mediator」だが、実際には以下まで直接抱えている＝God Object 化:

- tick のモード優先順位（`_tick` `_tick_loop` `_next_interval` `_should_fetch_ambient`）＝**正当な中核**
- 住人注入と染み出しガード（`_inject` `_emit` `_conclude`）
- 住人セッションの timeout / restart / 中座 refresh（`_restart_resident` `_resident_timed_out` `_roll_absence_target` `_maybe_step_away` `_return_from_away`・ADR-0027）
- スラッシュコマンドの解析・実行（`_command` `_cmd_font` `_cmd_daynight` ＋ /model /restart /game /codex /arc・~140 行）
- 来訪 Room の生成・Speaker 結線・種注入・timeout 処理（`_summon_guest` `_start_room` `_room_speakers` `_end_visit` `_check_room_timeout`・ADR-0015）
- GameSession の生成・AI プレイヤー構築・表示・終了（`_start_game` ほか **13 メソッド**・ADR-0017）
- ユーザー入力の文脈別ルーティング（`on_user_input`）

具体的な痛み:
- **`_tick`（:210）と `on_user_input`（:307）が同じ暗黙モード**（`if game/room/absent/active is not None`）を各自の梯子で分岐＝重複した優先順位ロジック。
- **help テキストが定義から分離**（`:360-368` の手書きリスト）＝既に別名 `/bj` `/tod` `/空` `/reset` 等が未記載でズレている（同期バグ）。
- Game / Visit の状態（`game` `_game_*` / `room` `_topic_*` `_guest_timed_out` 等）が Scheduler 属性に混在＝**単体テストしづらい**（現状は Scheduler 経由の統合テストに寄りがち）。

一方 Game（ADR-0017）と Room（ADR-0015・`conversation.py`）は**既に Port 化・State 化されている**。残っているのは *それらの生成・結線・進行制御* が Scheduler に漏れている部分。

## 決定
**Scheduler を「消さず、薄い Orchestrator に戻す」。** 責務別 controller を段階抽出し、Scheduler は最終的に `run()` / tick loop・入力 loop の起動停止 / 各 controller への委譲 / controller 間の排他・優先の最小管理 だけを持つ。

目標形:
```
engawa_main
  └─ Scheduler（薄い Orchestrator）
       ├─ CommandRouter          … slash command（Command パターン・登録制）
       ├─ ResidentSessionManager … 住人 agent の turn/restart/中座（＋AbsencePolicy）
       ├─ VisitController        … 来訪 Room lifecycle（＋RoomSpeakerFactory・種注入）
       ├─ GameController         … 対局の生成・進行・終了
       └─ View
```

### 抽出順（費用対効果・安全順・Codex 第2R で order 補正）
1. **CommandRouter**（`commands.py`）＝ if/elif 増殖を止める。help を各 Command が自分で持つ＝同期バグ解消。低リスク・無結合。第一 PR は `/font` `/daynight` だけ（view＋config 依存のみ・ctx を先取りで作り込まない）。
2. **`active` の意味分離**（`active_source`＝arc 進行 ／ `active_guest`＝来訪の persona/agent/cooldown）＝**Game 抽出の前**に（判断C）。リネームだけでなく tick 条件も分ける。
3. **GameController**（`game_controller.py`）＝最大の異物（13 メソッド）を Scheduler から委譲へ。
4. **VisitController ＋ RoomSpeakerFactory**（`visit_controller.py`）＝Room lifecycle・Speaker 結線・種注入・timeout。`active_guest` が既に分離済み＝二重責務を持ち込まない。
5. **ResidentSessionManager ＋ AbsencePolicy**（`resident_session.py`）＝住人 turn の中核。**最も繊細**（`speaking`/`turn_lock`）ゆえテストを厚く。**`speak()` の2層設計（判断B）は Visit 抽出の前に固定**（実装順は後でも設計は先に）。
6. **Tick / Input を Chain of Responsibility に**（handler 列）＝**最後**。1〜5 で `ctx` を薄くしてから。

### 設計上の判断（突き合わせで確定した勘所）
- **【判断A】トップレベル分岐は State でなく Chain of Responsibility**。`_tick`/`on_user_input` の実体は「優先順に試して、処理したら打ち切る」fall-through 梯子（absent→game→room→active→guest→step-away→arc→ambient）。これは CoR に素直に対応する。State パターンにすると idle 内の guest/step-away/arc/ambient 優先処理が結局 `IdleState` に残って肥える。**Room（ADR-0015）で State を採ったのは、部屋のターン管理が*本物の排他 FSM* だから**で、トップレベルの*優先ラダー*とは形が違う＝別パターンで良い。
  - ただし CoR は **1〜4 の後（最後）**にやる。先に controller を抜いて handler が依存する `ctx` を薄くしないと、巨大 ctx への依存で失敗する。
  - 将来オプション：厳密排他の大モード（game/visit/away）だけ軽い `mode` を持ち、idle 内だけ CoR にするハイブリッドも可（Open Question）。
- **【判断B】「茶々が turn_lock 下で喋る」を2層で共通化**（Codex 第2R で精緻化）。ソロ発話 `_inject`（:93）と room の茶々発話 `_room_speakers.resident_say`（:548）は `speaking`/`turn_lock` を**両方が触る共有可変状態**＝割り込み（ADR-0006）の中核。**ただし表示契約が別**：ソロは `turn_start/chunk/turn_end` のストリーミング表示、room は文字列を返して Room の `on_say`→`view.say` で1行。素朴に1メソッドへ一本化すると room が**二重表示**になる。よって2層に分ける:
  - `ResidentSessionManager.speak(prompt_text, *, on_timeout) -> str` … `turn_lock`/`speaking`/`resident.prompt`/timeout カウントの**共通 core**（表示を持たない）。
  - `ResidentSessionManager.inject_narration(narration) -> stop_reason` … `speak()` を使い、**加えて** `view.turn_start/chunk/turn_end` ＋ leak guard（表示付き）。ambient/user/arc はこれを呼ぶ。
  - room の茶々 `Speaker` は `speak(room_resident_prompt(...), on_timeout=mark_room_timeout)` を呼び**文字列を返すだけ**（表示は Room 側）。

  timeout 挙動は経路差を残す（ソロ→`_resident_timed_out`／room→部屋を畳んでから住人回復）＝`on_timeout` で注入。**ResidentSessionManager は Agent の所有者だが View の所有者にしない**（room/game 発話は文字列返却＝Room の State 表示境界を壊さない）。leak guard を room prompt にも効かせるかは別判断（初手は適用可・表示経路は分ける）。
- **【判断C】`active` の二重責務（arc と guest 兼用）を Game 抽出の前に分離**（Codex 第2R で前倒し＋範囲拡大）。現状 `self.active` が「進行中の箱庭アーク」（`:239` の `next_phase`）と「来訪中の客人 source／agent holder」（`:245` `:561`）の両方を指す＝後続抽出が全部この二重性を回さされる混乱源。`active_source`（arc）と `active_guest`（来訪の persona/agent/cooldown/close）に分ける。**リネーム限定では不足**＝tick 条件にも踏み込む：短 interval 判定 / `active_mode` / `/arc` の busy 判定 / `/codex` の arc 畳み / shutdown の `visiting = active not in sources` 算出。**Game 抽出の前**に置く（`_start_game` が開始時に active/room を畳む＝状態名が明確な方が安全・Visit は二重責務の影響を最も強く受ける）。
- **【判断D】ADR とテストの規約を守る**（原則4/6）。本 ADR で方向を残し、**既存の統合テストは characterization test として緑を保ったまま** controller ユニットを*足す*。**抽出とテスト書き換えを同じ PR に混ぜない**（混ぜると回帰が隠れる）。lock を急に別所有へ移さない（初回は Scheduler から注入）。

## 検討した代替案
- **一括書き換え（big-bang）**: 動いて実機 E2E 済み・並行制御（`drive_lock`/`turn_lock`）と cancel 優先が繊細＝壊すと痛い。却下＝段階抽出。
- **現状維持**: God Object・help 同期バグ・単体テスト困難が残る。却下。
- **トップレベルを State パターン（Claude 初案）**: 優先ラダーと形が合わず `IdleState` が肥える。却下＝CoR（判断A）。State は Room（排他 FSM）に限定して継続。
- **`_inject` と住人 lifecycle を別コンポーネントに分割（Claude 初案）**: `speaking`/`turn_lock` の共有で二重管理を生む。却下＝ResidentSessionManager に凝集（判断B）。
- **`speak()` を `_inject` と完全同一に一本化（Claude 初案の判断B）**: 表示契約が別（ソロ=turn ストリーミング／room=文字列→view.say）で room が二重表示になる。却下＝共通 core `speak()`＋表示付き `inject_narration()` の2層（判断B・Codex 第2R）。
- **第一 PR で Command 一括移行（Claude 初案）**: `/game` `/codex` `/restart` は controller/resident 依存で先走ると壊れやすい。却下＝**まず `/font` `/daynight` だけ**（Codex 案・無結合・テスト明確・中核に触れない）。
- **`active` 二重責務を後回し（Codex 初案）／Visit 直前にリネームだけ（Claude 初案）**: 前者は後続抽出が全部混乱を回す、後者は tick 条件を見落とす。却下＝**Game 前・リネーム＋tick 条件分割**（判断C・Codex 第2R）。

## 影響 / 帰結
- **新モジュール**: `commands.py`（Command 群＋Router）／`game_controller.py`／`visit_controller.py`（＋RoomSpeakerFactory）／`resident_session.py`（＋AbsencePolicy）／後で tick・input handler 群。
- **Scheduler**: 839 行 → **~250-350 行**。残るのは `__init__` `_tick_loop` `_tick`（handler 委譲だけ）`on_user_input`（同）`_next_interval` `_should_fetch_ambient` `_play_arc_now` `run`。ゲーム/来訪/コマンドの private が消える。
- **テスト**: `TestFontCommand`/`TestDayNightCommand` は commands 単位へ。`TestGameSession`/`TestGameMode` は GameController へ。`TestThreeWayRoom`/`TestAutonomousGuestVisit`/`TestTimeoutRecovery` は Visit へ。`TestRestartAndGuard`/`TestAbsenceRefresh` は Resident へ。**Scheduler 統合テストは "controller に委譲されるか" 程度に薄くする**。移行中は既存統合テストを緑のまま維持。
- **完了判定**: `scheduler.py` が 250-350 行 ／ game・room 関連 private が Scheduler から消滅 ／ command の if/elif が消滅 ／ `_tick`・`on_user_input` が handler 委譲のみ。

### リスク（実装時に効く・Codex 第2R の罠を含む）
- `drive_lock`/`turn_lock` の**所有権は最後まで Scheduler 起点**＝controller には依存注入し「誰が持つか」より「同じ lock を使い続ける」を優先。急に controller 所有へ移さない。
- `active` の arc/guest 兼用＝Visit/Game 分離時の二重管理（判断C で Game 前に先回り）。
- **`last_user_ts` を controller に散らさない**＝Scheduler が「ユーザー活動があった」と判断して更新、controller は戻り値で `handled` bool を返すだけ（quiet period・ambient 抑制の理由を1箇所に保つ）。
- **CommandRouter の `ctx` を先取りで作り込まない**＝まだ無い controller を先取りすると歪む。第一 PR は `view`＋`config` だけ依存の `/font` `/daynight` に絞る。
- `resident` は通常会話・来訪・ゲームで共有＝ResidentSessionManager 分離後の参照経路を統一（`resident_manager.resident`）。
- 既存テストが統合寄り＝controller 単位と統合の2層に分ける（判断D）。
- `/restart`・`/model` を CommandRouter へ移す時、依存先（`_restart_resident`＝Resident フェーズ）が未抽出＝ctx コールバック（`_play_arc_now` と同じ手）で Scheduler に戻すか、そのコマンドは依存 controller が出来るまで据え置く。

## 推奨する最初の PR
- **`src/commands.py` 追加＋ `/font` `/daynight` だけ Command 化＋ `Scheduler._command()` を Router へ委譲**。既存 `TestFontCommand`/`TestDayNightCommand` を緑のまま通す。GameController/VisitController/ResidentSessionManager/handler 化は含めない。
- 振る舞い差分が最小で、設計改善の足場（Command パターン＋Router＋薄い ctx）を作れる。

## 備考（Open Questions）
- **CoR の handler 粒度**（Phase 6）：細かくしすぎると `ctx` が肥大化し、handler が `next_at`/`last_user_ts` を直接触り始めると責務が拡散。初手は `_tick` の見た目を保ったまま private へ委譲する程度から。大モード（game/visit/away）だけ軽い `mode`＋idle 内 CoR のハイブリッドも選択肢（ctx が薄くなってから判断）。
- **`active` 分離の踏み込み量**（判断C）：tick 条件（短 interval / active_mode / /arc busy / /codex 畳み / shutdown visiting）まで手を入れる＝リネーム限定でない、は確定。個々の条件をどう分けるかは実装時に確定。
- **leak guard を room prompt に効かせるか**（判断B）：初手は適用可だが表示経路は分ける。room 発話での染み出し実測が出たら判断（ADR-0027 の Open Question とも地続き）。
- 各フェーズは独立 PR ＋ 完了時に本 ADR へ実装メモを追補。

## 実装メモ
- **Phase 1（CommandRouter・2026-07-05・PR #1 merge `3c3267a`）**: `src/commands.py` 新設＝`Command`／`CommandRouter`（登録制・別名対応・大小無視）／`CommandContext`（薄い adapter＝今は View だけ）＋`FontCommand`・`DayNightCommand`（ロジックは Scheduler から **verbatim 移設**＝振る舞い不変）。`Scheduler._command` は `self._commands.has(cmd)` なら Router へ委譲、未登録は従来 if/elif にフォールバック。`_cmd_font`/`_cmd_daynight` を削除、`import os`/`import daynight` も不要になり除去。`/font` クランプ定数は commands.py が正本（`FONT_MIN/MAX`）＝scheduler は後方互換で再輸出（`UI_FONT_MIN/MAX`・既存テストの `sched.UI_FONT_MAX` 参照を保つ＝テスト再編と混ぜない・判断D）。**scheduler.py 839→763 行**（−76）。テスト: 既存 `TestFontCommand`/`TestDayNightCommand`（統合・characterization）緑のまま＋新 `tests/test_commands.py`（Router 機構＋command 単体・9件）＝全 347 PASS・ruff clean。help テキストの各コマンドへの co-location は次段以降（今回は dispatch のみ移譲）。
- **Phase 2（`active` 意味分離・2026-07-05・branch `refactor/adr-0029-active-split`）**: `self.active`（arc と guest 兼用）を **`self.active_source`（箱庭アーク・`next_phase` 進行）** と **`self.active_guest`（来訪＝`GuestSource`・codex holder）** に分離（判断C）。監査で全 site（~20 in scheduler・8 in tests）を arc/guest/both に分類。**リネームだけでなく tick 条件も分けた**: `active_mode`＝`active_guest is not None`／`_next_interval`＝`active_source or active_guest`／`/arc` busy 判定・`/codex` の arc 畳み・shutdown の `visiting` 算出。`_conclude(src)` は「src が入ってる方のフィールドを null」に一般化（arc/guest 両対応・相互排他が崩れても安全）。arc と guest は tick フロー上**相互排他**（同時に立たない）＝分離しても状態増えず意味が明確化。テストの `s.active` 8箇所は意味どおり `active_source`/`active_guest` へ機械的リネーム（characterization 維持・振る舞い不変）。**全 347 PASS・ruff clean・残存 `self.active` ゼロ**。これで **Phase 3（GameController）** が `active` の二重責務を持ち込まずに進められる。
- **Phase 3（GameController・2026-07-05・branch `refactor/adr-0029-game-controller`）**: 対局の運用（`game`/`_game_*` state ＋ 13メソッド＝生成/AI手番/表示/終了/お開き）を `src/game_controller.py` の `GameController` へ **verbatim 移設**。Scheduler は `self.games` へ委譲: tick→`on_tick`、入力→`on_user_input(line)->bool`（True で消費）、`/game`→`start`、観戦窓×→`abort_by_user`、shutdown→`close`。`_should_fetch_ambient`/`active_mode`/`_summon_guest`/`_play_arc_now` の `self.game` 判定は `self.games.active`/`over` に。**Scheduler 状態への結び目は注入で最小化**（判断D・Codex 罠）: `preempt`（async・場払い＝room/arc 畳み＋last_user_ts＋cancel）／`bump_beat`（次ビート＝`_next_at` は Scheduler 所有）／`resident_provider`（住人現物・Phase4 で置換）／`drive_lock` は **Scheduler 所有のまま注入**（所有権を急に移さない）。`make_game` は差し替え口（テストの FakeGame）。**後方互換プロパティ**: `Scheduler.game`(read→`games.game`)／`Scheduler._make_game`(get/set→`games.make_game`)＝既存テストの `s.game` 読み・`s._make_game` 差し替えを保つ（判断D）。テストは Codex 案どおり `s._start_game(...)`→`s.games.start(...)` に向け直し（同じアサーション＝characterization 維持）＋新 `tests/test_game_controller.py`（注入境界の isolation・preempt/bump/resident 配線・validate→preempt 順序・4件）。**scheduler.py 763→610 行**（−153・累計 839→610）。**全 351 PASS・ruff clean**。`import game` は未使用になり除去（game_controller が参照）。
- **Phase 4a（RoomSpeakerFactory・2026-07-05・branch `refactor/adr-0029-room-speakers`）**: 最タングルの visit を安全に刻むため、まず `_room_speakers` の **Speaker クロージャ＋種プール＋timeout フラグ** を `src/room_speakers.py` の `RoomSpeakerFactory` へ verbatim 移設。room lifecycle（summon/start/end/timeout）は Scheduler に残す（Phase 4b で VisitController へ）。**判断B の seam を確定**: 茶々発話は Scheduler の `_room_resident_speak(prompt_text)`（turn_lock 下・timeout は raise）を注入＝Phase 5 で `ResidentSessionManager.speak()` に一本化。factory 注入: `resident_speak`/`context_provider`/`topics_provider`/`guest_agent_provider`/`log`（seed ログを `engawa.scheduler` に保ち `test_topic_cooldown` を無改変で通す）。timeout フラグは factory が持ち `_start_room` が per-room 生成・`_check_room_timeout` が読む・`_end_visit` が破棄。**テスト無改変で緑**（種/timeout 内部はテスト非参照＝characterization 維持）＋新 `tests/test_room_speakers.py`（isolation・6件）＝**全 357 PASS・ruff clean**。**scheduler.py 610→580 行**。
- **Phase 5（speak 一本化・2026-07-05・branch `refactor/adr-0029-resident-session`）＝縮小版**: 深い監査で **full ResidentSessionManager は test ripple 大**と判明（テストが `sched.RESIDENT_GUARD`/`sched.ABSENCE_AFTER_TURNS` の module global を patch・`s.speaking`/`s._absent` に代入・注入 ~7・割り込み中核）＝P4b と同じ「糊を注入で戻すだけ」の匂い。よって **full RM は見送り**、判断B の本当の payoff＝**speak の二重実装解消だけ回収**。共有コア `_speak_locked(prompt_text, on_chunk)`（turn_lock を握った前提で speaking 管理＋resident.prompt・timeout は raise）を新設し、`_inject`（ソロ・turn_start/chunk/turn_end 表示付き）と `_room_resident_speak`（room・文字列返却）が両方それを通す。**turn_lock 自体は呼び側が握る**＝turn_start/turn_end を lock 内に保ち barge-in の交錯を防ぐ（naive な `speak()` 一本化＝turn を lock 外に出すと交錯するのを回避）。新モジュール無し・定数移動無し・**テスト無改変で全 357 緑・ruff clean**。barge-in/timeout/room/absence の delicate 系も明示再確認で緑。

### 打ち切りの判断（2026-07-05）
P1〜P4a＋P5(speak 一本化) で **Scheduler 839→584 行**。**自己完結サブシステムの抽出（CommandRouter/active 分離/GameController/RoomSpeakerFactory）＋speak 共通化 は完了**。残る **P4b(full VisitController)・P5(full RM)・P6(Tick/Input の CoR)** は監査の結果、いずれも *コーディネーション糊を Scheduler 状態への注入 callback で戻すだけ*（Codex 自身が釘刺した「Scheduler 全体を渡すと責務が移動するだけ」）で、モード遷移の調停は **そもそも Orchestrator の本業**＝抽出の旨味が薄く ripple/リスクが上回る。よって **ここで意図的に打ち切る**（YAGNI・費用対効果）。将来 visit/resident の lifecycle が独立して育つ（例: 永続化・AbsencePolicy 単体・N人会話で turn 管理が複雑化）時に、その必要に駆動されて再開すればよい＝ADR は Open のまま残す。ステータスは **Accepted（P1〜4a＋speak 実装済み・P4b/P5full/P6 は必要が出るまで保留）** に更新。

### ADR に明示した合意（Codex 第2R「ADR に残すべき判断」）
- top-level は State でなく **CoR**（実施は controller 抽出後・判断A）。
- `active` は **`active_source` / `active_guest`** に分ける（arc progression と guest lifecycle の混在解消・判断C）。
- 茶々発話は **`speak()` で排他・timeout を共通化しつつ、表示付き `inject_narration()` と文字列返却の room/game 発話を分ける**（判断B）。
- **characterization test を維持し、抽出とテスト再編を同じ PR に混ぜない**（判断D）。
