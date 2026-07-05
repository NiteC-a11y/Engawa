#!/usr/bin/env python3
"""scheduler.py — Scheduler（Mediator・ADR-0013 ①）。

毎ティック {進行中アーク前進 / 新アーク開始 / 天気つぶやき・移ろい / 沈黙} を抽選し、
源の産んだ Narration を resident に注入する。間合い・cooldown・割り込みを一手に持つ。

割り込み（cancel優先・ADR-0006）の対象は **resident の注入ターン（speaking 中）だけ**。
source のカーソル（active）は触らない → QUIET 明けに同じ active から背景継続（Test C で実証）。
close は run() の finally の全 teardown 専用（結了時は reset()+cooldown のみ・ADR-0013 #2）。
"""
import asyncio
import random
import time

from agent import AgentTimeoutError   # 中立 timeout だけ捕捉＝実体(ACP/API)を知らない（ADR-0026・`agent` ローカル変数と衝突しないよう名前で import）
import commands      # スラッシュコマンドの Command パターン（/font /daynight を移譲・ADR-0029 Phase 1）
import config        # 設定解決（env > engawa.json > 既定）
import conversation  # 3人会話の部屋（State パターン・ADR-0015 Inc2）
import debuglog      # デバッグログ（ENGAWA_DEBUG=1 で engawa.log・既定オフ＝no-op）
import game          # ゲームの Port&Adapter 核（ADR-0017。rlcard はアダプタに隔離）
import prompts       # LLM 文言ビルダー（注入プロンプト工場・sources から分離・ADR-0013）
import sources
import views         # GAME_CLOSE_REQUEST（観戦窓×→お開きの制御トークン・入力 wire 形式の共有）

TICK_MIN = config.get_float("ENGAWA_TICK_MIN", "timing", "tick_min", 35, lo=1)
TICK_MAX = config.get_float("ENGAWA_TICK_MAX", "timing", "tick_max", 70, lo=1)
QUIET_AFTER_USER = config.get_float("ENGAWA_QUIET", "timing", "quiet_after_user", 25, lo=0)
ARC_START_PROB = config.get_float("ENGAWA_ARC_PROB", "timing", "arc_prob", 0.30, lo=0, hi=1)
MUTTER_PROB = config.get_float("ENGAWA_MUTTER_PROB", "timing", "mutter_prob", 0.6, lo=0, hi=1)
ACTIVE_BEAT_MIN = config.get_float("ENGAWA_ACTIVE_BEAT_MIN", "timing", "active_beat_min", 5, lo=1)   # アーク/来訪 進行中のビート間隔（短め＝会話が流れる）
ACTIVE_BEAT_MAX = config.get_float("ENGAWA_ACTIVE_BEAT_MAX", "timing", "active_beat_max", 12, lo=1)
TICK_MIN, TICK_MAX = min(TICK_MIN, TICK_MAX), max(TICK_MIN, TICK_MAX)                       # min>max の設定ミスを正す
ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX = min(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX), max(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)
RESIDENT_TIMEOUT_RESTART_AT = config.get_int("ENGAWA_RESIDENT_TIMEOUT_RESTART_AT", "acp", "resident_restart_at", 2, lo=1)  # 住人 prompt がこの回数連続で timeout したら再起動（それ未満はターン破棄のみ＝文脈温存）
RESIDENT_GUARD = config.get_int("ENGAWA_RESIDENT_GUARD", "acp", "resident_guard", 1, lo=0)  # 1=茶々ソロ出力の染み出しガード（注入文の復唱＋地の思考を表示前に除去・ソロは一括描画＝逐次stream無し）/0=従来の逐次stream
ABSENCE_AFTER_TURNS = config.get_int("ENGAWA_ABSENCE_AFTER_TURNS", "absence", "after_turns", 30, lo=0)  # 茶々ソロ発話がこの回数たまったら次のidleで「中座」→裏でセッションを張り直す（ADR-0027 長命セッション劣化の根治。0で無効）
ABSENCE_JITTER = config.get_int("ENGAWA_ABSENCE_JITTER", "absence", "jitter_turns", 10, lo=0)          # 中座タイミングのゆらぎ（after_turns に +0〜これ ターン・自然な「たまに」感）
ABSENCE_GAP = config.get_float("ENGAWA_ABSENCE_GAP", "absence", "gap_sec", 18, lo=1)                   # 不在の長さ（秒）＝この間は黙り、明けにセッションを張り直して戻る
GUEST_IDLE_LEAVE_TICKS = config.get_int("ENGAWA_GUEST_IDLE_LEAVE", "guest", "idle_leave_ticks", 8, lo=1)  # 来訪中、人間沈黙がこのtick数続いたら客人は辞去（来訪中tickは ACTIVE_BEAT=5〜12s。大きいほど長居・有界は維持）
GUEST_FILL_CAP = config.get_int("ENGAWA_GUEST_FILL_CAP", "guest", "fill_cap", 3, lo=0)          # 人間待ちの間、茶々が“人間役の代打”で場をつなぐ回数の上限（=人間不在の連続AIターン上限・ADR-0025。0で無効＝従来の純待ち）
GUEST_FILL_AFTER = config.get_int("ENGAWA_GUEST_FILL_AFTER", "guest", "fill_after_ticks", 2, lo=1)  # 最初の代打までの沈黙tick数（< idle_leave_ticks 前提。予算を使い切ったら idle_leave_ticks で辞去）
GUEST_FILL_SLOWDOWN = config.get_int("ENGAWA_GUEST_FILL_SLOWDOWN", "guest", "fill_slowdown", 1, lo=0)  # 代打の間隔を回ごとに延ばす量（n回目=fill_after+n×これ・大きいほど早く間延び。0で一定・ADR-0025「来た直後は賑やか→ネタ切れで間延び→帰る」）
UI_FONT_MIN, UI_FONT_MAX = commands.FONT_MIN, commands.FONT_MAX   # /font クランプの正本は commands.py（ここは後方互換の再輸出・engawa_main._ui_config の lo/hi と揃える）

log = debuglog.get("scheduler")          # デバッグログ（種の注入・来訪/room・cancel/timeout 等の主要ライフサイクル）


def _parse_addr(line):
    """web チップの明示宛先を本文と分離: '\\x00<to>\\x00<text>' → (to, text)。無印(console 等)は (None, line)。"""
    if line.startswith("\x00"):
        parts = line.split("\x00", 2)
        if len(parts) == 3:
            return (parts[1] or None), parts[2]
    return None, line


class Scheduler:
    def __init__(self, resident, source_list, idle, view, spawn_codex=None, spawn_resident=None):
        self.resident = resident
        self.sources = source_list
        self.idle = idle
        self.view = view
        self._spawn_codex = spawn_codex          # 客人(codex)の async factory（adr/0008）
        self._spawn_resident = spawn_resident    # 住人(茶々)再起動用 async factory（timeout 段階回復・None=再起動不可）
        self._resident_timeouts = 0              # 住人 prompt の連続 timeout 回数（成功で 0 復帰）
        self._turns_since_refresh = 0            # 前回のセッション更新からの住人ソロ発話数（中座の圧・ADR-0027）
        self._absent = False                     # 茶々が中座中か（不在の間は黙り、明けにセッション更新）
        self._away_until = 0.0                   # 中座から戻る予定時刻（time.time()）
        self._absence_target = self._roll_absence_target()   # 次に中座する発話数の目標（after_turns＋ゆらぎ）
        self._guest_timed_out = False            # room 中に客人が無応答だった（→急用で退場）
        self._room_resident_timeout = False      # room 中に住人が無応答だった（→退場＋段階回復）
        self.active = None                               # 進行中 source（割り込みで消えない）
        self.room = None                                 # 3人会話の部屋（来訪中だけ・ADR-0015 Inc2）
        self.game = None                                 # ゲームのセッション（対局中だけ・ADR-0017 Inc3）
        self._game_guests = []                           # ゲームのために召喚した客人(codex)＝終局で破棄
        self._game_render = None                         # ゲーム固有の表示器（adapter.render 由来）
        self._game_names = []                            # 各スロットの表示名（結果表示で使う）
        self.cooldowns = {s.key: 0 for s in source_list}
        self.turn_lock = asyncio.Lock()                  # resident 注入(_inject)の直列化＝割り込みの単位
        self.drive_lock = asyncio.Lock()                 # self.active 駆動を tick と召喚で排他（競合防止）
        self.speaking = False                            # resident 注入が in-flight か
        self.last_user_ts = 0.0
        self.weather = None                              # 最新天気を保持（起動時1回＋tick毎更新・捏造防止）
        self.topics = []                                 # 客人の世間話ネタ・プール（ADR-0014）
        self._topic_recent = []                          # 直近使った“種”（来訪またぎで変化を出す・最大6件・ADR-0014 ambient 再設計）
        self._topic_cooldown = 0                          # 種を置いた後に空ける客人ターン数（同じ話題への粘着防止）
        self._topics_at = 0.0                            # 最終更新時刻（TOPIC_REFRESH_MIN で更新）
        self._next_at = 0.0                              # 次ビートの予定時刻（active 中は短間隔・遅延しても保持）
        self.stop = asyncio.Event()
        # スラッシュコマンドの登録制ディスパッチ（ADR-0029 Phase 1）。ctx は薄い adapter（今は View だけ）。
        # 未登録コマンドは _command が従来の if/elif にフォールバックする（/font /daynight だけ移譲済み）。
        self._cmd_ctx = commands.CommandContext(self.view)
        self._commands = commands.default_router()

    # ── 注入（turn_lock で直列化）──────────────────────────
    async def _inject(self, narration):
        async with self.turn_lock:
            log.debug("inject 茶々 (%s%s)", narration.kind,      # 茶々ソロの発話（ambient つぶやき/アーク beat/ソロ応答）＝タイミングの起点
                      f"/{narration.label}" if narration.label else "")
            self.view.turn_start("茶々", narration.kind, narration.label, narration.voice)
            self.speaking = True
            timed_out = False
            stop_reason = None
            try:
                if RESIDENT_GUARD:                       # 染み出しガード: バッファして注入文/思考を除去→一括描画（stream演出は消える・原因を問わず効く）
                    out = await self.resident.prompt(narration.text)
                    clean = prompts.strip_resident_leak(out, narration.text)
                    if clean != out:
                        log.debug("resident guard: leak stripped (%d→%d chars)", len(out), len(clean))
                    self.view.chunk(clean)
                    stop_reason = clean
                else:
                    stop_reason = await self.resident.prompt(narration.text, on_chunk=self.view.chunk)
            except AgentTimeoutError:                  # 茶々が無応答（adapter ハング等）→ 段階回復へ
                timed_out, stop_reason = True, "timeout"
            finally:
                self.speaking = False
                self.view.turn_end()
            if timed_out:
                await self._resident_timed_out()
            else:
                self._resident_timeouts = 0              # 応答が返った＝健康。カウンタ復帰
                self._turns_since_refresh += 1           # 中座の圧を溜める（前回セッション更新からの発話数・ADR-0027）
            return stop_reason

    async def _emit(self, res):
        """next_phase の戻りを処理。None=結了 / SILENT=無言 / Narration=注入。"""
        if res is None:
            self._conclude(self.active)
            return
        if res is sources.SILENT:
            return
        await self._inject(res)

    def _conclude(self, src):
        src.reset()
        self.cooldowns[src.key] = src.cooldown_ticks     # close ではなく reset+cooldown（ADR-0013 #2）
        self.active = None

    async def _restart_resident(self):
        """住人(茶々)のセッションを張り直す（新セッション＝以前の文脈は持たない）。成功 True/失敗 False。
        成功時のみ旧を close（失敗時は現状の茶々を生かす＝現状維持）。timeout 段階回復と /restart（染み出し/不調時）で共用。"""
        if self._spawn_resident is None:                 # 再起動手段が無い
            return False
        old = self.resident
        try:
            new = await self._spawn_resident()
        except Exception:
            return False                                 # 失敗＝旧をそのまま生かす
        self.resident = new
        self._resident_timeouts = 0                      # 健康に復帰
        try:
            await old.close()
        except Exception:
            pass
        return True

    async def _resident_timed_out(self):
        """茶々の prompt が timeout（adapter 無応答）。段階的に回復:
        ターン破棄 → 連続で閾値に達したら再起動 → 再起動も失敗なら縁側を閉じる。
        1回の timeout で session を捨てない＝長命セッション（文脈が地続き・ADR-0005）を一過性の遅延で吹き飛ばさない。"""
        self._resident_timeouts += 1
        if self._resident_timeouts < RESIDENT_TIMEOUT_RESTART_AT:
            self.view.system("  （茶々はふっと黙り込んだ……ちょっと間があいた）")
            return
        if self._spawn_resident is None:                 # 再起動手段が無い → 閉じるしかない
            self.view.system("  （茶々の応答が戻らへん。縁側を閉じるわ）")
            self.stop.set()
            return
        self.view.system("  （茶々がふっと席を外した……呼び直してくる）")
        if await self._restart_resident():
            self.view.system("  （茶々が戻ってきた）")   # ※新セッション＝以前の文脈は持たない（永続化は別途・Backlog）
        else:
            self.view.system("  （茶々を呼び直せなんだ。縁側を閉じるわ）")
            self.stop.set()

    # ── 中座＝世界観に溶かした定期セッション更新（ADR-0027）──────────────
    def _roll_absence_target(self):
        """次に中座する発話数の目標。after_turns にゆらぎ(+0〜jitter)を足す＝自然な「たまに」感。"""
        jitter = random.randint(0, ABSENCE_JITTER) if ABSENCE_JITTER else 0
        return ABSENCE_AFTER_TURNS + jitter

    def _maybe_step_away(self):
        """idle で圧（前回更新からの発話数）が満ちてたら中座に入る。入ったら True。
        leave はローカル定型（LLM 非経由）＝劣化してるかもしれない今のセッションに喋らせない。"""
        if ABSENCE_AFTER_TURNS <= 0:                     # 0＝中座無効（従来どおり若返りなし）
            return False
        if self._turns_since_refresh < self._absence_target:
            return False
        self.view.say("茶々", prompts.absence_leave())   # 「ちょっと外すわ」＝確定発話で直接表示
        self._absent = True
        self.view.set_absent(True)                       # web は茶々スプライトを消す＝空っぽの縁側（console は no-op）
        self._away_until = time.time() + ABSENCE_GAP
        log.debug("茶々 中座へ: %d 発話 >= 目標 %d（gap %.0fs・裏でセッション更新）",
                  self._turns_since_refresh, self._absence_target, ABSENCE_GAP)
        return True

    async def _return_from_away(self):
        """中座から戻る。不在の裏で住人セッションを張り直し（黙って若返り）、戻りの一言を出す。
        再起動手段が無ければ今の茶々のまま戻る（＝ただの休憩に degrade・有害でない）。
        tick と on_user_input の両方から呼ばれ得るので冪等（不在でなければ何もしない）。"""
        if not self._absent:
            return
        self._absent = False                             # 先に降ろす＝二重復帰を防ぐ
        await self._restart_resident()                   # 黙って新セッション（成否問わず戻る）
        self._turns_since_refresh = 0                    # 圧をリセット＝次の中座までまた溜める
        self._absence_target = self._roll_absence_target()
        self.view.set_absent(False)                      # 茶々が戻る＝スプライト復帰（フェードイン）
        self.view.say("茶々", prompts.absence_return())  # 「お待たせ」「どこまで話しとったっけ」＝忘却も自然
        log.debug("茶々 中座から復帰（セッション更新済み・圧リセット→次目標 %d）", self._absence_target)

    # ── ティック ──────────────────────────────────────────
    async def _tick(self, ctx):
        for k in self.cooldowns:
            self.cooldowns[k] = max(0, self.cooldowns[k] - 1)
        if self._absent:                                 # 中座中: 戻り時刻まで黙る。時が来たら裏で更新して戻る（ADR-0027）
            if time.time() >= self._away_until:
                await self._return_from_away()
            return
        if self.game is not None:                        # (0a) 対局中＝AIの番なら1手進める（人間の番/終局は待つ）
            try:
                if self.game.over:
                    await self._end_game()
                elif not self.game.waiting_for_human:
                    await self.game.step()               # AI が1手（ペースは tick 間隔）
                    if self.game is not None and self.game.over:
                        await self._end_game()
                    elif self.game is not None and self.game.waiting_for_human:
                        self._show_human_turn()
            except AgentTimeoutError:                  # AI(客人/茶々)が無応答 → 席を立った扱いでお開き
                await self._abort_game_on_timeout()
            except Exception:                            # adapter 死亡/不正状態 等 → 盤を進めずお開き（tick ループを殺さない）
                await self._abort_game_on_error()
            return
        if self.room is not None:                        # (0) 3人会話の部屋＝人間待ち/沈黙→辞去（State が判断）
            await self.room.on_tick()
            if await self._check_room_timeout():         # 部屋中に無応答→急用退場で畳んだら終わり
                return
            if self.room.closed:
                await self._end_visit()
            return
        if self.active is not None:                      # (1) 進行中アークを前へ
            await self._emit(await self.active.next_phase(ctx))
            return
        guest = next((s for s in self.sources if s.key == "guest"), None)
        if guest is not None and self.cooldowns.get("guest", 0) <= 0 and guest.eligible(ctx):
            guest.reset()                                # (2a) 自発来訪は arc 抽選から独立に判定
            self.active = guest                          #      ＝prob が実効の per-tick 率（arc と競合させない・夕方×prob×cooldown だけ）
            log.debug("tick→自発来訪: %s", guest.persona)
            await self._start_room(guest.persona)        #      3人会話の部屋を開く（ADR-0015）
            return
        if self._maybe_step_away():                      # (2a') idle で圧が満ちたら中座＝裏でセッション更新（ADR-0027・来訪より後・アークより前）
            return
        if random.random() < ARC_START_PROB:             # (2b) 箱庭アーク抽選（guest を除く・gate＋cooldown）
            eligible = [s for s in self.sources
                        if s.key != "guest" and s.eligible(ctx) and self.cooldowns.get(s.key, 0) <= 0]
            if eligible:
                chosen = random.choice(eligible)
                chosen.reset()
                self.active = chosen
                log.debug("tick→アーク: %s", chosen.key)
                await self._emit(await self.active.next_phase(ctx))   # 箱庭アークは起を即出す
                return
        narr = await self.idle.next_phase(ctx)           # (3) 天気つぶやき/移ろい or 沈黙
        if narr is not None and (narr.kind == "transition" or random.random() < MUTTER_PROB):
            await self._inject(narr)

    def _next_interval(self):
        """次ビートまでの間合い。アーク/来訪が進行中(active)や中座中は短く、何もない時は長い ambient。"""
        if self._absent or self.active is not None:      # 中座中は短間隔で「戻り時刻」を拾う（ADR-0027）
            return random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)
        return random.uniform(TICK_MIN, TICK_MAX)

    def _should_fetch_ambient(self):
        """天気/トピックを取得するか。ゲーム中は不要（遅延回避）、中座中は席を外してるので無駄＋
        取得のネットワーク遅延が毎tick乗ると戻り(_return_from_away)が gap より延びるので取得しない（ADR-0027）。"""
        return self.game is None and not self._absent

    async def _tick_loop(self):
        self._next_at = time.time() + random.uniform(TICK_MIN, TICK_MAX)   # 起動直後は即つぶやかない
        while not self.stop.is_set():
            try:                                         # 1秒刻みで起き、active 変化（召喚等）に即追従
                await asyncio.wait_for(self.stop.wait(), timeout=1.0); break
            except asyncio.TimeoutError:
                pass
            now = time.time()
            if now < self._next_at:                      # まだ間合いの途中
                continue
            if self.turn_lock.locked():                  # 注入中は次スライスで再挑戦（next_at 据置＝解け次第すぐ）
                continue
            active_mode = self.game is not None or self.room is not None or \
                (self.active is not None and self.active.key == "guest")
            if not active_mode and now - self.last_user_ts < QUIET_AFTER_USER:
                continue                                 # 会話直後は静か（ただしゲーム/来訪は止めない）
            if self._should_fetch_ambient():             # ゲーム中/中座中は天気・ネタ取得をしない（不要・遅延回避）
                self.weather = await asyncio.to_thread(sources.fetch_weather)
                if now - self._topics_at > sources.TOPIC_REFRESH_MIN * 60:
                    self.topics = await asyncio.to_thread(sources.fetch_topics)
                    self._topics_at = now
            async with self.drive_lock:                  # 召喚と active 駆動を競合させない
                try:
                    await self._tick(sources.build_context(self.weather, self.topics))
                except AgentTimeoutError:              # 各経路で処理済み（保険）。tick ループは止めない
                    pass
            interval = self._next_interval()             # 次の間合い（active 中は短い＝会話が流れる）
            self._next_at = time.time() + interval
            log.debug("next beat +%.1fs (active=%s)", interval, active_mode)   # 予定の間合い＝LLM 遅延と分けてペースを見る（定量分析用）

    # ── ユーザー入力（割り込み・cancel優先）──────────────────
    async def on_user_input(self, line):
        if line == views.GAME_CLOSE_REQUEST:                 # 観戦窓×（ユーザー操作）→ 対局を畳んで縁側へ戻す
            await self._abort_game(); return
        to, line = _parse_addr(line or "")                   # web チップの明示宛先を本文と分離（C方式・console は無印）
        line = line.strip()
        if not line:
            return
        log.debug("user input%s: %s", f" (→{to})" if to else "", line)   # 人間の入力時刻＝会話駆動の起点（定量分析用）
        if line.startswith("/"):
            await self._command(line)
            return
        if self._absent:                                 # 中座中に話しかけられた＝茶々は戻る（新セッションで応じる・ADR-0027）
            await self._return_from_away()
        self.last_user_ts = time.time()
        if self.game is not None and not self.game.over:     # 対局中＝入力は「手」（ADR-0017）
            if not self.game.waiting_for_human:
                self.view.system("  （今は他のプレイヤーの番。待ってな）")
                return
            cur = self.game.adapter.current_player()
            legal = self.game.adapter.legal_moves(cur)
            move = game.parse_move(line, legal)
            if move is None:
                self.view.system(f"  （その手は出せん。打てる手: {' / '.join(map(str, legal))}）")
                return
            await self.game.human_move(move)
            self.view.system(f"  私 → {move}")
            if self.game.over:
                await self._end_game()
            return
        if self.room is not None:                            # 3人会話の可能性（ADR-0015）
            async with self.drive_lock:                      # tick と直列化。ロック内で部屋の有無を確定
                if self.room is not None and not self.room.closed:   # 待機中に tick が辞去した場合に備え再確認
                    await self.room.on_human(line, to)
                    if not await self._check_room_timeout():     # 無応答なら急用退場で畳む（畳んだら下の通常入力へ落ちない）
                        if self.room is not None and self.room.closed:
                            await self._end_visit()
                    self.last_user_ts = time.time()
                    return
            # 部屋が閉じていた（沈黙で辞去等）→ 通常の話しかけに落とす
        if self.speaking:                                # 進行中の注入だけを畳む（ambient・cancel優先）
            log.debug("cancel: user barge-in（speaking 中）")
            await self.resident.cancel()                 # session/cancel → stopReason=cancelled
            self.view.system("[茶々がこちらを向いた]")
        # active(source) は触らない → QUIET 明けに背景継続
        ctx = sources.build_context(self.weather, self.topics)   # 保持した天気を渡す（捏造防止）
        await self._inject(sources.Narration(prompts.user_narration(line, ctx), "user"))
        self.last_user_ts = time.time()

    async def _command(self, line):
        parts = line.split(); cmd = parts[0].lower()
        if self._commands.has(cmd):                      # 登録済みは Router へ（Phase 1: /font /daynight・ADR-0029）
            await self._commands.dispatch(self._cmd_ctx, line, parts)
            return
        if cmd in ("/quit", "/exit", "/bye"):
            self.view.system("[*] 縁側を閉じます。"); self.stop.set()
        elif cmd == "/help":
            self.view.system("  ふつうに打って Enter → 茶々に話しかける")
            self.view.system("  /arc [雀|猫|風]  → 箱庭アークを今すぐ再生")
            self.view.system("  /codex <人格>    → 客人(codex)を呼ぶ（3人会話の部屋を開く・ADR-0015）")
            self.view.system("  /game <id> [見る] → ゲーム（id=blackjack/uno/leduc・「見る」で観戦・要 rlcard。/blackjack は別名）")
            self.view.system("  /model           → 今のモデルを表示（住人=Claude / 客人=codex）")
            self.view.system("  /font [倍率|save] → 文字サイズ（例 /font 1.4・/font で今の値・/font save で保存）")
            self.view.system("  /daynight [on|off|HH:MM|demo|auto] → 背景の昼夜（on/off=有効無効を保存・HH:MM=固定・demo=夕→夜早送り・auto=実時間）")
            self.view.system("  /restart         → 茶々のセッションを張り直す（染み出し/不調の時・文脈はリセット）")
            self.view.system("  /quit            → 縁側を閉じる")
        elif cmd == "/arc":
            await self._play_arc_now(parts[1] if len(parts) > 1 else None)
        elif cmd == "/codex":
            rest = line.split(maxsplit=1)
            persona = prompts.sanitize_persona(rest[1] if len(rest) > 1 else "")  # 自由入力を最小サニタイズ（公開前の最低線）
            await self._summon_guest(persona)
        elif cmd == "/game":                             # 汎用ゲーム起動: /game <id> [見る]
            rest = [p for p in parts[1:] if p not in ("見る", "観戦", "watch")]
            gid = rest[0].lower() if rest else ""
            watch = any(w in line for w in ("見る", "観戦", "watch"))
            await self._start_game(gid, watch)           # 空/不明 id は _start_game が一覧を出す
        elif cmd in ("/blackjack", "/bj"):               # /game blackjack の別名（従来コマンド維持）
            watch = any(w in line for w in ("見る", "観戦", "watch"))   # 「/bj 見る」で観戦(全AI)
            await self._start_game("blackjack", watch)
        elif cmd in ("/restart", "/reset"):              # 茶々のセッションを張り直す（染み出し/不調時・文脈リセット・縁側操作＝ADR-0007）
            if self._spawn_resident is None:
                self.view.system("  （いまは茶々を呼び直せへんのや）")
            else:
                self.view.system("  （茶々にいっぺん席を外してもろて、呼び直すわ……）")
                if await self._restart_resident():
                    self.view.system("  （茶々が戻ってきた。※前の話の続きは覚えてへん）")
                else:
                    self.view.system("  （茶々を呼び直せなんだ。今の茶々のままでいくわ）")
        elif cmd == "/model":                            # 縁側への操作＝茶々には流さない（人格を汚さない・ADR-0007）
            r = self.resident
            if r.reported_model:                         # アダプタが実モデルを報告した＝真実（未指定でも分かる）
                self.view.system(f"  茶々(住人): {r.reported_model}（アダプタ報告）")
            elif r.model:                                # こちらが ENGAWA_MODEL で要求した値
                self.view.system(f"  茶々(住人): {r.model}（指定）")
            else:                                        # 未指定かつアダプタ未報告＝こちらは実物を知らない
                self.view.system("  茶々(住人): 不明（未指定・アダプタ未報告）— 確実に固定するなら ENGAWA_MODEL を設定")
            # 客人は使い捨て＝持続エージェント無し。来訪中なら live な codex の報告を優先、いなければ設定値（来訪時に使う指定）
            g = self.active if (self.active is not None and self.active.key == "guest") else None
            gmodel = getattr(getattr(g, "agent", None), "reported_model", None)
            if gmodel:
                self.view.system(f"  客人(codex): {gmodel}（来訪中・アダプタ報告）")
            else:
                guest = config.get_str("ENGAWA_CODEX_MODEL", "model", "guest", "")
                self.view.system(f"  客人(codex): {guest + '（設定値・来訪時に使用）' if guest else '未指定（来訪時にアダプタ既定）'}")
        else:
            self.view.system(f"  はて、そんな作法（{cmd}）は知らんな。/help どうぞ。")

    async def _summon_guest(self, persona):
        """/codex <人格>：客人を直接召喚（取り次ぎなし・即）。箱庭アーク中なら畳んで通す。
        到着を今すぐ、以降は tick で展開。客人来訪中は重ねない（断る）。"""
        if self._spawn_codex is None:
            self.view.system("  [P4] codex 接続が未設定（spawn_codex 無し）。"); return
        if self.game is not None and not self.game.over:             # 対局中は客人を上げない（room と game の同時成立を防ぐ）
            self.view.system("  今は対局中や。終わってからな。"); return
        if self.active is not None and self.active.key == "guest":   # 既に客人 → 重ねない
            self.view.system("  今は別の客人が来とる。ちょっと待ってな。"); return
        self.last_user_ts = time.time()                  # 召喚も user 活動（直後の独り言を抑制）
        if self.speaking:                                # 喋ってる最中なら畳む（cancel優先・ロック前に解く）
            await self.resident.cancel()
            self.view.system("[茶々がこちらを向いた]")
        async with self.drive_lock:                      # ここから active を触る＝tick と排他
            if self.active is not None and self.active.key == "guest":   # 待機中に自発客人が来た
                self.view.system("  今は別の客人が来とる。ちょっと待ってな。"); return
            if self.active is not None:                  # 箱庭アーク → 畳んで客人を通す
                self._conclude(self.active)
            self.view.system(f"  〔客人〕「{persona}」が訪ねてきた…")
            self.active = sources.GuestSource(persona, self._spawn_codex)
            await self._start_room(persona)              # 3人会話の部屋を開く（到着→人間待ち・ADR-0015）

    # ── 3人会話の部屋（ADR-0015 Inc2）────────────────────────
    async def _start_room(self, persona):
        """codex を先に spawn（失敗なら来訪中止）、Speaker を結線して Room を開き、到着の挨拶を出す。
        以後は tick→on_tick / ユーザー入力→on_human で進む。drive_lock 内から呼ぶ前提。"""
        try:
            await self.active.ensure_agent()             # codex を今 spawn（失敗は例外）
        except Exception as e:
            log.debug("客人 spawn 失敗: %s: %s", type(e).__name__, e)
            self.view.system("  （客人は来られなんだ。codex 接続・ChatGPT 認証を確認してな）")
            self._conclude(self.active)
            return
        log.debug("客人 spawn: %s / room open", persona)
        self._topic_cooldown = 0                          # 来訪ごとに種は早めに出せる（cooldown は来訪内で効かせる）
        resident_spk, guest_spk = self._room_speakers(persona)

        def _on_say(who, text, kind):
            log.debug("say %s (%s)", who, kind)
            self.view.say(who, text)

        self.room = conversation.Room(
            persona, resident_spk, guest_spk, idle_leave_ticks=GUEST_IDLE_LEAVE_TICKS,
            fill_cap=GUEST_FILL_CAP, fill_after=GUEST_FILL_AFTER,
            fill_slowdown=GUEST_FILL_SLOWDOWN, on_say=_on_say)
        await self.room.begin()                          # 到着の挨拶＋茶々の反応 → 人間待ち
        if await self._check_room_timeout():             # 到着の挨拶すら無応答なら即・急用退場で畳む
            return
        if self.room.closed:                             # 念のため（通常は AwaitingHuman）
            await self._end_visit()
        else:                                            # 沈黙検出のため tick を短間隔に
            self._next_at = time.time() + random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)

    def _room_speakers(self, persona):
        """茶々/客人を均一に喋らせる Speaker（Strategy/DI）。Room は agent/View を知らない。"""
        async def resident_say(window, kind):
            async with self.turn_lock:                   # ambient と同じ直列化単位
                self.speaking = True
                ctx = sources.build_context(self.weather, self.topics)   # いまの縁側（時刻＋天気）を渡す
                try:
                    return await self.resident.prompt(prompts.room_resident_prompt(window, kind, ctx))
                except AgentTimeoutError:              # 茶々が無応答 → フラグだけ立て、後始末は呼び側で
                    self._room_resident_timeout = True
                    return ""
                finally:
                    self.speaking = False

        async def guest_say(window, kind):
            agent = self.active.agent if self.active is not None else None
            if agent is None:
                return ""
            ctx = sources.build_context(self.weather, self.topics)   # いまの縁側（時刻＋天気）＝時間感覚のズレ防止
            air = None                                   # 「縁側の空気」＝世間の種（ambient・ADR-0014）
            if kind in (conversation.CHIME, conversation.REPLY):
                if self._topic_cooldown > 0:             # 直前に種を置いた→数ターン空ける（同じ話題への粘着を防ぐ）
                    self._topic_cooldown -= 1
                    log.debug("種見送り: cooldown 残%d (%s)", self._topic_cooldown, kind)
                elif random.random() < sources.TOPIC_PROB:   # たまに種が空気に混じる（発話有無は codex 判断）
                    tidbit = sources.pick_topic_text(self.topics, persona, self._topic_recent)
                    if tidbit:
                        self._topic_recent.append(tidbit); del self._topic_recent[:-6]   # 直近6件だけ＝変化を出す
                        self._topic_cooldown = sources.TOPIC_COOLDOWN                     # 次の種まで間を空ける
                        air = prompts.guest_air(tidbit)
                        log.debug("種を空気へ: %s (%s)", tidbit, kind)   # 実際に口に出すかは codex 判断（目視）
                    else:
                        log.debug("種見送り: 空プール (%s)", kind)
                else:
                    log.debug("種見送り: prob外れ (%s)", kind)
            try:
                return (await agent.prompt(
                    prompts.room_guest_prompt(persona, window, kind, ctx=ctx, air=air))).strip()
            except AgentTimeoutError:                  # 客人が無応答 → ハング client は二度叩かず急用退場へ
                self._guest_timed_out = True
                return ""

        return (conversation.Speaker("茶々", resident_say),
                conversation.Speaker(persona, guest_say))

    async def _end_visit(self):
        """来訪終了: codex を破棄（使い捨て・ADR-0008）し cooldown を置いて部屋を閉じる。"""
        src = self.active
        log.debug("客人辞去 / room close: %s", getattr(src, "persona", None))
        self.room = None
        if src is not None:
            try:
                await src.close()
            except Exception:
                pass
            self.cooldowns[src.key] = src.cooldown_ticks
            src.reset()
            self.active = None

    async def _check_room_timeout(self):
        """room 中の無応答（客人/住人）を畳む。客人は『急ぎの用で去る』定型退場（ハング client は二度叩かない）。
        住人も無応答なら見送りは省いて段階回復へ。いずれも visit 継続不能なので部屋を閉じる。畳んだら True。"""
        if not (self._guest_timed_out or self._room_resident_timeout):
            return False
        resident_dead = self._room_resident_timeout
        log.debug("timeout: %s → 急用退場",
                  "住人+客人" if (resident_dead and self._guest_timed_out) else
                  ("住人" if resident_dead else "客人"))
        self._guest_timed_out = self._room_resident_timeout = False
        self.view.system("  " + prompts.guest_timeout_leave())   # 客人が急用で去る（定型・local。世界観を壊さない）
        await self._end_visit()                                   # 部屋を閉じ codex を破棄（taskkill /T /F で確実に殺す）
        if resident_dead:
            await self._resident_timed_out()                     # 住人も無応答なら段階回復へ
        return True

    async def _teardown_game(self, msg):
        """対局を畳んで縁側へ戻す共通処理（state クリア＋客人破棄＋観戦窓クローズ）。
        これを通さず view.game_close だけだと Scheduler.game が残り『ゲームモードのまま復帰不能』になる。"""
        if msg:
            self.view.system(msg)
        self.game = None
        self._game_render = None
        await self._cleanup_game_guests()
        try:
            self.view.game_close()                       # ×経由なら既に閉じてる＝no-op
        except Exception:
            pass

    async def _abort_game_on_timeout(self):
        """対局中に AI（客人/茶々）が無応答 → 席を立った扱いでお開き（盤面を勝手に進めない）。観戦窓も閉じる。"""
        await self._teardown_game("  （プレイヤーの返事が途切れた……席を立ったようや。お開きにしよ）")

    async def _abort_game_on_error(self):
        """対局中に想定外のエラー（adapter 死亡=ConnectionError・rlcard の不正状態 等）→ 盤を進めずお開き。
        これを通さず例外が `_tick` を抜けると `_tick_loop` が死に、ゲームモードのまま永久停止する。"""
        await self._teardown_game("  （対局でなんぞ起きた……お開きにするわ）")

    async def _abort_game(self):
        """ユーザーが対局を切り上げた（観戦窓×）→ お開きにして縁側へ戻す。対局中でなければ何もしない。"""
        if self.game is None:                            # 終局後の結果表示を×で閉じただけ＝畳むものは無い
            return
        await self._teardown_game("  （お開き。縁側に戻るわ）")

    # ── ゲーム（ADR-0017 Inc3）。GameSession を tick で1手ずつ進める ───────────────
    def _make_game(self, game_id, num_players):
        """ゲームのアダプタを作る。ゲーム登録は composition root（engawa_main._build）で済ませる（任意依存の寄せ・ADR-0017）。テストはこれを差し替える。"""
        return game.make(game_id, num_players)

    def _known_games(self):
        """登録済みゲーム一覧（id→meta）。検証/一覧用。登録は composition root（engawa_main._build）で実施済み（rlcard 未導入なら空）。"""
        return game.games()

    def _list_games(self, known, unknown=None):
        if unknown:
            self.view.system(f"  そんなゲーム（{unknown}）は知らんな。")
        avail = " / ".join(f"{k}（{v['label']}）" for k, v in known.items())
        self.view.system(f"  遊べるの: {avail}")
        self.view.system("  使い方: /game <id> [見る]（例: /game uno、/game blackjack 見る）")

    def _ai_decider(self, agent, name, slot):
        """AIプレイヤーの手番: 自分のスロット＋状態＋合法手を見せて手を選ばせる（不正は先頭へフォールバック）。"""
        async def decide(state, legal_moves):
            reply = await agent.prompt(prompts.game_move_prompt(name, slot, state, legal_moves))
            return game.parse_move(reply, legal_moves)
        return decide

    async def _start_game(self, game_id, watch):
        """対局開始。会話/アークは畳む。基本は 私＋茶々（観戦は茶々のみ）＝客人 codex は呼ばない（A）。
        ゲームが要求する人数に足りない時だけ客人で埋める。"""
        if self.game is not None:
            self.view.system("  もうゲーム中や。"); return
        known = self._known_games()                      # 検証＋対応人数(min,max)の取得
        meta = known.get(game_id)
        if meta is None:                                 # 空/不明 id → 遊べる一覧を出す
            self._list_games(known, unknown=game_id or None); return
        lo, hi = meta["players"]
        want = min(hi, lo if watch else max(2, lo))      # 観戦=最少AIだけ / 参加=私+茶々(最低2)。min を下回らず max も超えない
        try:
            adapter = self._make_game(game_id, want)
        except ImportError:
            self.view.system("  （ゲームには rlcard が要る: pip install rlcard）"); return
        except Exception as e:
            self.view.system(f"  （ゲームを始められなんだ: {e}）"); return
        if self.room is not None:                        # 会話/来訪中なら畳んで通す
            await self._end_visit()
        elif self.active is not None:
            self._conclude(self.active)
        self.last_user_ts = time.time()
        if self.speaking:
            await self.resident.cancel()
        async with self.drive_lock:
            n = adapter.num_players
            players = []
            if not watch:
                players.append(game.Player("私"))        # 人間（観戦時は入れない＝全AI）。slot 0
            players.append(game.Player("茶々", self._ai_decider(self.resident, "茶々", len(players))))
            self._game_guests = []
            while len(players) < n:                      # 人数が足りないゲームの時だけ客人(codex)で埋める（blackjack では起きない）
                persona = random.choice(sources.GUEST_PERSONAS)
                try:
                    agent = await self._spawn_codex()
                except Exception:
                    self.view.system("  （客人が来られず中止）")
                    await self._cleanup_game_guests(); return
                self._game_guests.append(agent)
                slot = len(players)                      # 追加位置＝RLCard の player id（自分の手札参照に使う）
                players.append(game.Player(f"客人〔{persona}〕", self._ai_decider(agent, persona, slot)))
            players = players[:n]                         # 念のためスロット数に合わせる
            names = [p.name for p in players]
            render = adapter.render                        # ゲーム固有の表示器（blackjack は札と勝敗を整形）
            self._game_render = render
            self._game_names = names
            label = game.games().get(game_id, {}).get("label", game_id)
            who = "観戦＝茶々がディーラーと勝負" if watch else "あなたも参加"
            self.view.system(f"  〔{label}〕開始（{who}・{n}人）")

            def on_move(name, move, ad, slot):
                try:                                      # 表示が転けてもゲーム進行は止めない
                    lines = [render.move(name, move, ad, slot)] if render is not None else [f"  {name} → {move}"]
                    cur = ad.current_player() if not ad.is_over() else None
                    self._game_emit(ad, lines, current_slot=cur, over=ad.is_over())
                except Exception:
                    self.view.system(f"  {name} → {move}")
            self.game = game.GameSession(adapter, players, on_move=on_move)
            self.view.game_open(label)                    # web は隣に観戦窓を開く（console は何もしない）
            self.game.begin()
            deal_lines = render.deal(adapter, names) if render is not None else []
            self._game_emit(adapter, deal_lines, current_slot=adapter.current_player(), over=False)
            self._next_at = time.time() + random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)
            if self.game.over:
                await self._end_game()
            elif self.game.waiting_for_human:
                self._show_human_turn()

    def _game_emit(self, adapter, lines, current_slot=None, over=False):
        """局面更新を View へ（snapshot=観戦窓の描画用 / lines=console の文字表現）。"""
        snap = None
        render = self._game_render
        if render is not None and hasattr(render, "snapshot"):
            snap = render.snapshot(adapter, self._game_names, current_slot, over)
        self.view.game_update(snap, lines)

    def _show_human_turn(self):
        cur = self.game.adapter.current_player()
        legal = self.game.adapter.legal_moves(cur)
        render = self._game_render
        if render is not None:
            self.view.system(render.turn(self.game.adapter, cur, "あなた"))
        else:
            self.view.system(f"  あなたの番｜{prompts.describe_state(self.game.adapter.state(cur))}")
        self.view.system(f"  打てる手: {' / '.join(map(str, legal))}  （そのまま打って）")
        self._game_emit(self.game.adapter, [], current_slot=cur, over=False)   # 観戦窓に自分の番を反映

    async def _end_game(self):
        g = self.game
        self.game = None
        if g is not None:
            names = self._game_names or [p.name for p in g.players]
            render = self._game_render
            if render is not None:                        # 結果（ディーラー公開＋各自の勝敗）
                lines = render.result(g.adapter, names)
                snap = render.snapshot(g.adapter, names, None, True) if hasattr(render, "snapshot") else None
            else:
                lines = ["  〔結果〕 " + " / ".join(f"{nm}: {r}" for nm, r in zip(names, g.adapter.result()))]
                snap = None
            self.view.game_update(snap, lines)            # 結果（ディーラー公開＋勝敗）を出す。**窓は閉じない**
        self._game_render = None                          # 閉じるのはユーザーの×か、アプリ終了時の teardown だけ
        await self._cleanup_game_guests()

    async def _cleanup_game_guests(self):
        for agent in self._game_guests:                  # ゲームの客人(codex)を破棄（使い捨て）
            try:
                await agent.close()
            except Exception:
                pass
        self._game_guests = []

    async def _play_arc_now(self, key):
        """/arc：箱庭アークを今すぐ再生（デバッグ）。tick 駆動の active に載せて起→承→転→結を前へ進める。
        以前はここで完走まで while ループでブロックしていて、その間 on_user_input が返らず＝**再生中の
        話しかけ（割り込み）が効かなかった**。active に載せ替えて即 return し、以降は _tick が前進させる
        ＝入力ループが空くので barge-in（cancel優先・ADR-0006）が通る。"""
        weather = await asyncio.to_thread(sources.fetch_weather)
        self.weather = weather                       # 取れた天気は保持（捏造防止・tick と揃える）
        ctx = sources.build_context(weather, self.topics)
        pool = [s for s in self.sources if s.key == key] if key \
            else [s for s in self.sources if s.eligible(ctx)]
        if not pool:
            self.view.system("  （今出せるアークが無い。/arc 雀 等キー指定で強制できる）"); return
        arc = random.choice(pool)
        async with self.drive_lock:                  # tick と排他で active を載せる
            if self.active is not None or self.room is not None or self.game is not None:
                self.view.system("  （今は別のことをしとる。落ち着いてから /arc してな）"); return
            arc.reset()
            self.active = arc                        # 自然アーク同様、起が ~1s 後に出る（デバッグ表記は出さず窓を汚さない）
        self._next_at = time.time()                  # 次スライス(≤1s)で 起 を出す。以降 tick が前進＝割り込み可

    # ── 実行 ──────────────────────────────────────────────
    async def run(self):
        self.weather = await asyncio.to_thread(sources.fetch_weather)   # 起動直後の捏造防止
        self.topics = await asyncio.to_thread(sources.fetch_topics)     # 客人ネタの初回取得
        self._topics_at = time.time()
        tick_task = asyncio.create_task(self._tick_loop())
        try:
            async for line in self.view.inputs():
                if line is None:
                    break
                try:
                    await self.on_user_input(line)
                except AgentTimeoutError:               # どの経路でも timeout でアプリは落とさない（最終保険）
                    self.view.system("  （応答が戻らへん……ちょっと間があいた）")
                if self.stop.is_set():
                    break
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self.stop.set()
            tick_task.cancel()
            try:
                await tick_task
            except (asyncio.CancelledError, Exception):
                pass
            await self._cleanup_game_guests()            # ゲーム中に終了した時の客人(codex)を刈る
            try:
                self.view.game_close()                   # 対局中に終了した時は観戦窓も閉じる
            except Exception:
                pass
            visiting = self.active if self.active not in self.sources else None  # 召喚客人は registry 外
            for s in list(self.sources) + [self.idle] + ([visiting] if visiting else []):
                try:                                     # shutdown teardown（codex leak の最終防波堤）
                    await s.close()
                except Exception:
                    pass
            await self.resident.close()
