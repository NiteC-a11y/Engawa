# ADR-0017: ゲームは「Game ポート＋RLCard アダプタ」で受け、AI が既存ゲームに参加する（初の任意外部依存 rlcard）

- ステータス: Accepted（方向確定・Inc 実装中）
- 日付: 2026-06-28
- 関連: ADR-0008（客人=有界の来訪）, ADR-0009/0010（pywebview/ドット絵UI）, ADR-0013（ポート構成）, ADR-0015（部屋・Speaker・State）, TECH_RULES §1（外部依存は最小に）

## 背景 / 課題
「縁側で AI（茶々/客人）と一緒にゲームしたい。ただし**ゲーム自体は自作せず、既にあるゲームに AI が"プレイヤー"として参加**する」想定。
- **Pyxel / pygame** はゲームエンジンだが、Engawa は**チャット常駐＋asyncio＋pywebview**。これらはメインループ/独自ウィンドウを握り、**今のチャットUI（特に日本語IMEのテキスト入力・折返しログ）を捨てて作り直す**ことになる＝車輪の再開発の逆。しかもカードの絵は今の HTML/canvas で十分描ける。
- 一方 **RLCard**（カードゲームの toolkit）は**ゲームが実装済み**で、`state['raw_obs']`（実カード）/`state['raw_legal_actions']`（手の文字列）が**人間=LLM が読める形**で取れ、`env.step(move, raw_action=True)` で手を直接適用できる（spike で確認）。＝「エンジンが真実・AIは合法手を選ぶ」を**ゲーム自作ゼロ**で実現できる。

## 決定
1. **ゲームは framework 非依存の Game ポート（`game.GameAdapter`）で受ける。** RLCard はその1アダプタ（`game_rlcard.RLCardAdapter`）。**rlcard 依存は `game_rlcard.py` だけに閉じ込める**（コア `game.py` / app 本体は rlcard を知らない）。種類は**レジストリ**（`register`）で後から増やす。差し替え（PettingZoo/自作/オンライン）は同形の別アダプタを足すだけ。
2. **プレイヤーは `Player`（人間 or AI）。** AI は「状態＋合法手→手」を選ぶ注入カラブル（Speaker と同じ Strategy/DI）。`GameSession` は**人数非依存**（プレイヤー配列長で決まる）＝**私＋茶々＋客人(可変)**で埋める。**AI-only（人間は観戦）も同じ仕組み**。進行は **step 方式**（1手ずつ）で tick 駆動＝ペースを置いて観られる。
3. **rlcard は「遊ぶ時だけ要る任意依存」。** 未インストールでもコア app は動く（import はゲーム使用時のみ・テストは skip）。「外部依存は最小に」（TECH_RULES §1）の**意識的な例外**を、ゲーム自作回避の対価として採用。
4. **UI は今の pywebview/HTML のまま**（Pyxel/pygame には替えない）。札の描画は既存 canvas に足す。

## 検討した代替案
- **Pyxel / pygame でUIごと作り直し**: チャット常駐・async・日本語入力と衝突。却下（ゲーム主体への大転換なら別途）。
- **PettingZoo**: 活発・盤ゲームも多いが、観測が**RL用にエンコード**＝LLM 向けに一枚剥がす手間。カードは中で RLCard を使う。「カード×LLMプレイヤー」なら RLCard が素直。将来盤ゲームに広げる時に再検討（同じ RLCard ベースで地続き）。
- **ゲームを自作**: ルール・かな処理等を全部書く＝車輪の再開発。却下。

## 影響 / 帰結
- **初の重め外部依存**（rlcard＋numpy・ただし任意）。Engawa の「依存最小」方針に小さな穴を開ける。
- ゲームごとに**人数制約**（RLCard の UNO は2人固定／blackjack は1〜N／doudizhu は3固定 等）。アダプタは env の**実人数**を採る。
- **LLM が合法手を外す可能性** → `GameSession` は不正手を**合法手の先頭へフォールバック**（将来 prompt 改善＋振り直しも可）。
- 客人を複数同時に呼べる（ADR-0008 の有界・使い捨ては維持）＝人数ぶんトークン/プロセス増（有界なゲームなので許容）。

## 備考（実装の刻み）
- **Inc1**: Game ポート核（`game.py`・依存ゼロ・FakeGame でテスト）。
- **Inc2（本ADR）**: `RLCardAdapter`（`game_rlcard.py`・rlcard 隔離）＋ step 方式＋AI-only。実 rlcard で3人ブラックジャック完走を検証。
- **Inc3**: AI の decide を実 LLM（state＋legal_moves を見せて手を選ばせる）＋ Scheduler の `/blackjack [見る]` ＋ console 表示。実 claude/codex の E2E はユーザー実機。
- **Inc4**: web の札UI（既存 canvas に pixel-art カード）。
