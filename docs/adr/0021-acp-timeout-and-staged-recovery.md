# ADR-0021: ACP は用途別 timeout ＋ 役割別の段階回復で守る（adapter 無応答で永久待ちしない）

- ステータス: Accepted（実装 2026-06-29・codexレビュー S1/S2）
- 日付: 2026-06-29
- 関連: ADR-0005（住人セッションは長命）, ADR-0006（割り込みは cancel 優先）, ADR-0001（ACP）, ADR-0013（Scheduler）

## 背景 / 課題
ACP の1往復（initialize / session/new / session/prompt / cancel）は、adapter が**生きたまま無応答**になると永久待ちになり得る（プロセスは死なずに final response を返さない等）。握手失敗時の teardown 漏れや一時 dir の刈り残しもあった。一過性の遅延と本当の死を区別し、**縁側を落とさず・長命セッション（ADR-0005）を一過性遅延で捨てず**に回復したい。

## 決定
- **用途別 timeout（config 可変）**: `init`120 / `session`60 / `prompt`240 / `send`・`cancel`10 秒（初回 npx ダウンロード＋認証を見込み寛容に）。超過は `ACPTimeoutError`。timeout/例外いずれでも pending を必ず外す（残骸＋遅延応答の取り違え防止）。stdout EOF は `ConnectionError` で pending を解放。
- **役割別の段階回復（2系統ポリシー）**:
  - **住人（茶々）**: ターン破棄 → 連続 `resident_restart_at`(既定2) 回で再起動 → 再起動も失敗なら縁側を閉じる。**1回の timeout で session を捨てない**＝長命セッション（文脈が地続き・ADR-0005）を一過性遅延で吹き飛ばさない。
  - **客人（codex）**: 「急ぎの用で去る」定型退場（ハング client は二度叩かない）。使い捨て前提（ADR-0008）なので畳んで終わり。
  - **ゲーム**: AI が無応答なら席を立った扱いで**お開き**（盤面を勝手に進めない）。
- **握手失敗の teardown 保証（S2）**: `spawn()` の握手失敗（timeout/EOF/error）を `except BaseException` で受け、task cancel＋`shutdown_process` を必ず走らせて再 raise。spawn 失敗時・`close()` 時に temp dir を rmtree。
- **cancel 後の bounded wait（S1 残）**: cancel 通知後、adapter が cancelled 応答を握り潰しても `prompt_timeout`(240) まで待たず、`cancel_grace`(既定10) で in-flight prompt を**合成 `stopReason=cancelled`** として畳む（ADR-0006 の安全弁の上限化）。timeout でなく cancelled にするのは、ユーザー起因の意図的中断で住人の段階再起動カウンタを誤って進めないため。

## 検討した代替案
- **timeout 無し（現状）**: adapter 無応答で永久ハング。却下。
- **無応答で即セッション破棄**: 長命セッションの文脈を一過性遅延で失う（ADR-0005 に反する）。住人は段階回復（破棄→再起動→閉じる）にする。
- **全用途一律 timeout**: 初回 npx は長い／prompt はモデル次第で長い等、用途で必要長が違う。用途別にした。
- **cancel-grace を timeout 扱い**: barge-in が住人の再起動圧を生む。良性の cancelled にして二重計上しない。

## 影響 / 帰結
- adapter が生きたまま無応答でも縁側は落ちず、役割に応じて畳む/再起動する。Job Object 化（孤立 node の最終保険）は別軸で未対応。
- つまみ: `acp` 節（`init_timeout` / `session_timeout` / `prompt_timeout` / `send_timeout` / `cancel_timeout` / `cancel_grace` / `resident_restart_at`・env `ENGAWA_ACP_*`）。
- テスト: `test_acp`（timeout で pending クリア・EOF→ConnectionError・cancel後 bounded wait・close の temp dir 後始末）＋ `test_scheduler`（住人/客人/ゲームの段階回復）。

## 備考
- 「cmd /c の裏の node 取り残し → Job Object 化」は taskkill /T /F の最終保険として残課題（Backlog）。
- 再起動した住人は新セッション＝以前の文脈を持たない（永続化は別途・Open Questions）。
