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
import game_controller  # 対局の運用を委譲するコントローラ（ADR-0029 Phase 3。game モジュールはこちらが参照）
import prompts       # LLM 文言ビルダー（注入プロンプト工場・sources から分離・ADR-0013）
import room_speakers  # 3人会話の Speaker を作る RoomSpeakerFactory（ADR-0029 Phase 4a）
import sources
import views         # GAME_CLOSE_REQUEST（観戦窓×→お開きの制御トークン・入力 wire 形式の共有）
from voice import loc  # UI シェル文言の voice 上書き（未訳キーは第2引数の日本語へ・ADR-0022 Inc2）

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

ARC_KEY_ALIASES = {"sparrow": "雀", "cat": "猫", "wind": "風"}   # /arc の英別名（source key は和字のまま・ADR-0022）


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
        self._speakers = None                    # 来訪中の RoomSpeakerFactory（種プール＋timeout フラグを凝集・ADR-0029 P4a）
        # 進行中 source を意味で2つに分離（ADR-0029 P2）。tick フロー上この2つは相互排他（同時に立たない）。
        self.active_source = None                        # 進行中の箱庭アーク source（next_phase で進む・割り込みで消えない）
        self.active_guest = None                         # 来訪中の客人 source（GuestSource＝persona/agent/cooldown/close の holder）
        self.room = None                                 # 3人会話の部屋（来訪中だけ・ADR-0015 Inc2）
        self._room_rev = 0                               # 部屋入力の generation（単調増加・最新入力が古いドライブを失効させる・ADR-0031）
        # 対局の state/運用は GameController が所有（ADR-0029 Phase 3）。生成は drive_lock の後（下）。
        self.cooldowns = {s.key: 0 for s in source_list}
        self.turn_lock = asyncio.Lock()                  # resident 注入(_inject)の直列化＝割り込みの単位
        self.drive_lock = asyncio.Lock()                 # active_source/active_guest 駆動を tick と召喚で排他（競合防止）
        self.speaking = False                            # resident 注入が in-flight か
        self.last_user_ts = 0.0
        self.weather = None                              # 最新天気を保持（起動時1回＋tick毎更新・捏造防止）
        self.topics = []                                 # 客人の世間話ネタ・プール（ADR-0014）
        self._topics_at = 0.0                            # 最終更新時刻（TOPIC_REFRESH_MIN で更新）
        self._next_at = 0.0                              # 次ビートの予定時刻（active 中は短間隔・遅延しても保持）
        self.stop = asyncio.Event()
        # スラッシュコマンドの登録制ディスパッチ（ADR-0029 Phase 1）。ctx は薄い adapter（今は View だけ）。
        # 未登録コマンドは _command が従来の if/elif にフォールバックする（/font /daynight だけ移譲済み）。
        self._cmd_ctx = commands.CommandContext(self.view)
        self._commands = commands.default_router()
        # 対局の運用を委譲（ADR-0029 Phase 3）。drive_lock は Scheduler 所有のまま注入、Scheduler 状態への
        # 結び目（場払い・次ビート・住人現物）は callback/provider で渡す（controller に散らさない・Codex 第2R）。
        self.games = game_controller.GameController(
            view=self.view, spawn_codex=self._spawn_codex,
            resident_provider=lambda: self.resident,
            drive_lock=self.drive_lock, preempt=self._preempt_for_game,
            bump_beat=self._bump_active_beat)

    # 後方互換のプロパティ（既存テスト/コードが Scheduler 越しに対局状態を見る口・ADR-0029 Phase 3）
    @property
    def game(self):
        return self.games.game                           # 進行中の GameSession（無ければ None）

    @property
    def _make_game(self):
        return self.games.make_game                      # ゲームアダプタ生成の差し替え口（テストが FakeGame を挿す）

    @_make_game.setter
    def _make_game(self, fn):
        self.games.make_game = fn

    async def _preempt_for_game(self):
        """対局開始時の場払い（GameController.start から呼ばれる）＝会話/来訪/アークを畳む＋user 活動記録＋
        喋り中なら cancel（cancel 優先・ADR-0006）。Scheduler 状態（room/active_source/last_user_ts/speaking）を触る。"""
        if self.room is not None:                        # 会話/来訪中なら畳んで通す
            await self._end_visit()
        elif self.active_source is not None:             # 箱庭アーク → 畳んで対局を通す（来訪は上の room 分岐で処理）
            self._conclude(self.active_source)
        self.last_user_ts = time.time()
        if self.speaking:
            await self.resident.cancel()

    def _bump_active_beat(self):
        """次ビートを active ペース（短間隔）へ（_next_at は Scheduler 所有）。対局開始直後に AI の手を早く回す。"""
        self._next_at = time.time() + random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)

    # ── 注入（turn_lock で直列化）──────────────────────────
    async def _speak_locked(self, prompt_text, on_chunk=None):
        """turn_lock を握った前提で resident.prompt（speaking フラグ管理・timeout は raise）。
        茶々ソロ(_inject)と room 発話(_room_resident_speak)の**共通コア**（判断B の speak 一本化・ADR-0029 P5）。
        turn_lock 自体は呼び側が持つ＝turn_start/turn_end を lock 内に保てる（barge-in の交錯防止）。"""
        self.speaking = True
        try:
            return await self.resident.prompt(prompt_text, on_chunk=on_chunk)
        finally:
            self.speaking = False

    async def _inject(self, narration):
        async with self.turn_lock:
            log.debug("inject 茶々 (%s%s)", narration.kind,      # 茶々ソロの発話（ambient つぶやき/アーク beat/ソロ応答）＝タイミングの起点
                      f"/{narration.label}" if narration.label else "")
            self.view.turn_start("茶々", narration.kind, narration.label, narration.voice)
            timed_out = False
            stop_reason = None
            try:
                if RESIDENT_GUARD:                       # 染み出しガード: バッファして注入文/思考を除去→一括描画（stream演出は消える・原因を問わず効く）
                    out = await self._speak_locked(narration.text)
                    clean = prompts.strip_resident_leak(out, narration.text)
                    if clean != out:
                        log.debug("resident guard: leak stripped (%d→%d chars)", len(out), len(clean))
                    self.view.chunk(clean)
                    stop_reason = clean
                else:
                    stop_reason = await self._speak_locked(narration.text, on_chunk=self.view.chunk)
            except AgentTimeoutError:                  # 茶々が無応答（adapter ハング等）→ 段階回復へ
                timed_out, stop_reason = True, "timeout"
            finally:
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
            self._conclude(self.active_source)           # _emit は箱庭アーク進行専用（来訪は room 経由）
            return
        if res is sources.SILENT:
            return
        await self._inject(res)

    def _conclude(self, src):
        src.reset()
        self.cooldowns[src.key] = src.cooldown_ticks     # close ではなく reset+cooldown（ADR-0013 #2）
        if src is self.active_source:                    # src が入ってる方を降ろす（arc/guest 両対応・ADR-0029 P2）
            self.active_source = None
        if src is self.active_guest:
            self.active_guest = None

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
            self.view.system(loc("resident_hiccup", "  （茶々はふっと黙り込んだ……ちょっと間があいた）"))
            return
        if self._spawn_resident is None:                 # 再起動手段が無い → 閉じるしかない
            self.view.system(loc("resident_dead", "  （茶々の応答が戻らへん。縁側を閉じるわ）"))
            self.stop.set()
            return
        self.view.system(loc("absence_away", "  （茶々がふっと席を外した……呼び直してくる）"))
        if await self._restart_resident():
            self.view.system(loc("absence_back", "  （茶々が戻ってきた）"))   # ※新セッション＝以前の文脈は持たない（永続化は別途・Backlog）
        else:
            self.view.system(loc("absence_fail", "  （茶々を呼び直せなんだ。縁側を閉じるわ）"))
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
        self.view.say("茶々", loc("absence_leave", prompts.absence_leave()))   # 「ちょっと外すわ」＝確定発話で直接表示（voice 上書き可）
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
        self.view.say("茶々", loc("absence_return", prompts.absence_return()))  # 「お待たせ」「どこまで話しとったっけ」＝忘却も自然
        log.debug("茶々 中座から復帰（セッション更新済み・圧リセット→次目標 %d）", self._absence_target)

    # ── ティック ──────────────────────────────────────────
    async def _tick(self, ctx):
        for k in self.cooldowns:
            self.cooldowns[k] = max(0, self.cooldowns[k] - 1)
        if self._absent:                                 # 中座中: 戻り時刻まで黙る。時が来たら裏で更新して戻る（ADR-0027）
            if time.time() >= self._away_until:
                await self._return_from_away()
            return
        if self.games.active:                            # (0a) 対局中＝GameController が1手進める（無応答/エラーはお開き）
            await self.games.on_tick()
            return
        if self.room is not None:                        # (0) 3人会話の部屋＝人間待ち/沈黙→辞去（State が判断）
            await self.room.on_tick(should_stop=self._room_stop_token())
            if await self._check_room_timeout():         # 部屋中に無応答→急用退場で畳んだら終わり
                return
            if self.room.closed:
                await self._end_visit()
            return
        if self.active_source is not None:               # (1) 進行中アークを前へ
            await self._emit(await self.active_source.next_phase(ctx))
            return
        guest = next((s for s in self.sources if s.key == "guest"), None)
        if guest is not None and self.cooldowns.get("guest", 0) <= 0 and guest.eligible(ctx):
            guest.reset()                                # (2a) 自発来訪は arc 抽選から独立に判定
            self.active_guest = guest                    #      ＝prob が実効の per-tick 率（arc と競合させない・夕方×prob×cooldown だけ）
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
                self.active_source = chosen
                log.debug("tick→アーク: %s", chosen.key)
                await self._emit(await self.active_source.next_phase(ctx))   # 箱庭アークは起を即出す
                return
        narr = await self.idle.next_phase(ctx)           # (3) 天気つぶやき/移ろい or 沈黙
        if narr is not None and (narr.kind == "transition" or random.random() < MUTTER_PROB):
            await self._inject(narr)

    def _next_interval(self):
        """次ビートまでの間合い。アーク/来訪が進行中や中座中は短く、何もない時は長い ambient。"""
        if self._absent or self.active_source is not None or self.active_guest is not None:  # 進行中(アーク/来訪)・中座中は短間隔（ADR-0027）
            return random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)
        return random.uniform(TICK_MIN, TICK_MAX)

    def _should_fetch_ambient(self):
        """天気/トピックを取得するか。ゲーム中は不要（遅延回避）、中座中は席を外してるので無駄＋
        取得のネットワーク遅延が毎tick乗ると戻り(_return_from_away)が gap より延びるので取得しない（ADR-0027）。"""
        return not self.games.active and not self._absent

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
            active_mode = self.games.active or self.room is not None or \
                self.active_guest is not None
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
            await self.games.abort_by_user(); return
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
        if await self.games.on_user_input(line):         # 対局中＝入力は「手」（GameController が処理して True・ADR-0017）
            return
        if self.room is not None:                            # 3人会話の可能性（ADR-0015）
            await self._room_barge_in()                      # 生成中の手を畳み、進行中ドライブを失効させる（ADR-0031）
            async with self.drive_lock:                      # tick と直列化。ロック内で部屋の有無を確定
                if self.room is not None and not self.room.closed:   # 待機中に tick が辞去した場合に備え再確認
                    await self.room.on_human(line, to, should_stop=self._room_stop_token())
                    if not await self._check_room_timeout():     # 無応答なら急用退場で畳む（畳んだら下の通常入力へ落ちない）
                        if self.room is not None and self.room.closed:
                            await self._end_visit()
                    self.last_user_ts = time.time()
                    return
            # 部屋が閉じていた（沈黙で辞去等）→ 通常の話しかけに落とす
        interrupted = self.speaking                      # 振り向いた事実は注入にも語る（UI 演出と茶々の文脈を一致）
        if interrupted:                                  # 進行中の注入だけを畳む（ambient・cancel優先）
            log.debug("cancel: user barge-in（speaking 中）")
            await self.resident.cancel()                 # session/cancel → stopReason=cancelled
            self.view.system(loc("turned_to_you", "[茶々がこちらを向いた]"))
        # active(source) は触らない → QUIET 明けに背景継続
        ctx = sources.build_context(self.weather, self.topics)   # 保持した天気を渡す（捏造防止）
        await self._inject(sources.Narration(prompts.user_narration(line, ctx, interrupted=interrupted), "user"))
        self.last_user_ts = time.time()

    async def _command(self, line):
        parts = line.split(); cmd = parts[0].lower()
        if self._commands.has(cmd):                      # 登録済みは Router へ（Phase 1: /font /daynight・ADR-0029）
            await self._commands.dispatch(self._cmd_ctx, line, parts)
            return
        if cmd in ("/quit", "/exit", "/bye"):
            self.view.system(loc("closing", "[*] 縁側を閉じます。")); self.stop.set()
        elif cmd == "/help":
            self.view.system(loc("help_talk", "  ふつうに打って Enter → 茶々に話しかける"))
            self.view.system(loc("help_arc", "  /arc [雀|猫|風]  → 箱庭アークを今すぐ再生"))
            self.view.system(loc("help_codex", "  /codex <人格>    → 客人(codex)を呼ぶ（3人会話の部屋を開く・ADR-0015）"))
            self.view.system(loc("help_game", "  /game <id> [見る] → ゲーム（id=blackjack/uno/leduc・「見る」で観戦・要 rlcard。/blackjack は別名）"))
            self.view.system(loc("help_model", "  /model           → 今のモデルを表示（住人=Claude / 客人=codex）"))
            self.view.system(loc("help_font", "  /font [倍率|save] → 文字サイズ（例 /font 1.4・/font で今の値・/font save で保存）"))
            self.view.system(loc("help_daynight", "  /daynight [on|off|HH:MM|demo|auto] → 背景の昼夜（on/off=有効無効を保存・HH:MM=固定・demo=夕→夜早送り・auto=実時間）"))
            self.view.system(loc("help_restart", "  /restart         → 茶々のセッションを張り直す（染み出し/不調の時・文脈はリセット）"))
            self.view.system(loc("help_quit", "  /quit            → 縁側を閉じる"))
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
            await self.games.start(gid, watch)           # 空/不明 id は GameController が一覧を出す
        elif cmd in ("/blackjack", "/bj"):               # /game blackjack の別名（従来コマンド維持）
            watch = any(w in line for w in ("見る", "観戦", "watch"))   # 「/bj 見る」で観戦(全AI)
            await self.games.start("blackjack", watch)
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
            g = self.active_guest
            gmodel = getattr(getattr(g, "agent", None), "reported_model", None)
            if gmodel:
                self.view.system(f"  客人(codex): {gmodel}（来訪中・アダプタ報告）")
            else:
                guest = config.get_str("ENGAWA_CODEX_MODEL", "model", "guest", "")
                self.view.system(f"  客人(codex): {guest + '（設定値・来訪時に使用）' if guest else '未指定（来訪時にアダプタ既定）'}")
        else:
            self.view.system(loc("unknown_cmd", "  はて、そんな作法（{cmd}）は知らんな。/help どうぞ。").format(cmd=cmd))

    async def _summon_guest(self, persona):
        """/codex <人格>：客人を直接召喚（取り次ぎなし・即）。箱庭アーク中なら畳んで通す。
        到着を今すぐ、以降は tick で展開。客人来訪中は重ねない（断る）。"""
        if self._spawn_codex is None:
            self.view.system("  [P4] codex 接続が未設定（spawn_codex 無し）。"); return
        if self.games.active and not self.games.over:                # 対局中は客人を上げない（room と game の同時成立を防ぐ）
            self.view.system(loc("busy_game", "  今は対局中や。終わってからな。")); return
        if self.active_guest is not None:                            # 既に客人 → 重ねない
            self.view.system(loc("busy_guest", "  今は別の客人が来とる。ちょっと待ってな。")); return
        self.last_user_ts = time.time()                  # 召喚も user 活動（直後の独り言を抑制）
        if self.speaking:                                # 喋ってる最中なら畳む（cancel優先・ロック前に解く）
            await self.resident.cancel()
            self.view.system(loc("turned_to_you", "[茶々がこちらを向いた]"))
        async with self.drive_lock:                      # ここから active_source/active_guest を触る＝tick と排他
            if self.active_guest is not None:            # 待機中に自発客人が来た
                self.view.system(loc("busy_guest", "  今は別の客人が来とる。ちょっと待ってな。")); return
            if self.active_source is not None:           # 箱庭アーク → 畳んで客人を通す
                self._conclude(self.active_source)
            self.view.system(loc("visit_arrive", "  〔客人〕「{persona}」が訪ねてきた…").format(persona=persona))
            self.active_guest = sources.GuestSource(persona, self._spawn_codex)
            await self._start_room(persona)              # 3人会話の部屋を開く（到着→人間待ち・ADR-0015）

    # ── 3人会話の部屋（ADR-0015 Inc2）────────────────────────
    def _room_stop_token(self):
        """現ドライブ用の失効判定を作る（開始時 rev を閉じ込める＝「自分はもう最新でない」・ADR-0031）。
        bool フラグはクリア競合・カウンタは減算リークがあるため単調増加 rev で判定（codex レビュー採用）。"""
        rev = self._room_rev
        return lambda: self._room_rev != rev

    async def _room_barge_in(self):
        """部屋への入力到着＝進行中ドライブを失効させ（rev+1）、生成中の手を best-effort で畳む（ADR-0031）。
        tick 駆動チェーン（挨拶/代打/辞去）中は入力ループが空いているのでここが即時に走る。
        入力起点チェーン中の連打は run() の逐次入力ゆえ届かない＝スコープL（ADR-0031 備考）。
        **中断不可の手（ARRIVE/辞去）の生成中は rev だけ進めて cancel しない**＝preemptible=False は
        gate を素通りするので、cancel の部分文がそのまま commit される穴があった（codex diff レビュー 7/18）。
        rev は進めてよい: 非中断手は gate を無視して完走し、後続の中断可の手（REACT 等）だけが省略される。"""
        self._room_rev += 1
        if self.room is not None and not self.room.utter_preemptible:
            log.debug("barge-in 保留: 中断不可の手（ARRIVE/辞去）を生成中＝完走を待つ")
            return
        preempted = False
        if self.speaking:                                # 茶々が room 発話の生成中（ソロ barge-in と同型）
            log.debug("cancel: user barge-in（room・茶々生成中）")
            await self.resident.cancel()
            preempted = True
        if self._speakers is not None and await self._speakers.cancel_inflight():
            log.debug("cancel: user barge-in（room・客人生成中）")
            preempted = True
        if preempted:                                    # 実際に畳んだ時だけ演出（自然完了と紛れない）
            self.view.system(loc("room_turned", "[話の途中でこちらを向いた]"))

    async def _start_room(self, persona):
        """codex を先に spawn（失敗なら来訪中止）、Speaker を結線して Room を開き、到着の挨拶を出す。
        以後は tick→on_tick / ユーザー入力→on_human で進む。drive_lock 内から呼ぶ前提。"""
        try:
            await self.active_guest.ensure_agent()       # codex を今 spawn（失敗は例外）
        except Exception as e:
            log.debug("客人 spawn 失敗: %s: %s", type(e).__name__, e)
            self.view.system(loc("visit_fail", "  （客人は来られなんだ。codex 接続・ChatGPT 認証を確認してな）"))
            self._conclude(self.active_guest)
            return
        log.debug("客人 spawn: %s / room open", persona)
        self._speakers = room_speakers.RoomSpeakerFactory(   # 種プール＋timeout フラグを凝集・per-room で新規（cooldown=0 始まり）
            persona, resident_speak=self._room_resident_speak,
            guest_agent_provider=lambda: self.active_guest.agent if self.active_guest is not None else None,
            context_provider=lambda: sources.build_context(self.weather, self.topics),
            topics_provider=lambda: self.topics, log=log,
            preempted=lambda: self.room.preempted if self.room is not None else False)   # barge-in 時の timeout 誤発火防止（ADR-0031）
        resident_spk, guest_spk = self._speakers.speakers()

        def _on_say(who, text, kind):
            log.debug("say %s (%s)", who, kind)
            self.view.say(who, text)

        self.room = conversation.Room(
            persona, resident_spk, guest_spk, idle_leave_ticks=GUEST_IDLE_LEAVE_TICKS,
            fill_cap=GUEST_FILL_CAP, fill_after=GUEST_FILL_AFTER,
            fill_slowdown=GUEST_FILL_SLOWDOWN, on_say=_on_say)
        await self.room.begin(should_stop=self._room_stop_token())   # 到着の挨拶＋茶々の反応 → 人間待ち
        if await self._check_room_timeout():             # 到着の挨拶すら無応答なら即・急用退場で畳む
            return
        if self.room.closed:                             # 念のため（通常は AwaitingHuman）
            await self._end_visit()
        else:                                            # 沈黙検出のため tick を短間隔に
            self._next_at = time.time() + random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)

    async def _room_resident_speak(self, prompt_text):
        """room の茶々発話: turn_lock 下で1発話（表示なし・文字列返却・timeout は呼び側=RoomSpeakerFactory が捕捉）。
        judgment B の **speak 一本化**（ADR-0029 P5）＝ソロ注入(_inject)と同じ共通コア `_speak_locked` を通す
        （turn_lock/speaking/resident.prompt を1箇所に集約）。表示契約だけ別（room は say・ソロは turn ストリーム）。"""
        async with self.turn_lock:                   # ambient（_inject）と同じ直列化単位＝割り込みの単位
            return await self._speak_locked(prompt_text)

    async def _end_visit(self):
        """来訪終了: codex を破棄（使い捨て・ADR-0008）し cooldown を置いて部屋を閉じる。"""
        src = self.active_guest
        log.debug("客人辞去 / room close: %s", getattr(src, "persona", None))
        self.room = None
        if src is not None:
            try:
                await src.close()
            except Exception:
                pass
            self.cooldowns[src.key] = src.cooldown_ticks
            src.reset()
            self.active_guest = None
        self._speakers = None                            # 来訪終了＝ファクトリ破棄（種プール/timeout フラグごと・P4a）

    async def _check_room_timeout(self):
        """room 中の無応答（客人/住人）を畳む。客人は『急ぎの用で去る』定型退場（ハング client は二度叩かない）。
        住人も無応答なら見送りは省いて段階回復へ。いずれも visit 継続不能なので部屋を閉じる。畳んだら True。
        timeout フラグは RoomSpeakerFactory が持つ（_end_visit でファクトリごと破棄＝リセット不要・P4a）。"""
        sp = self._speakers
        if sp is None or not (sp.guest_timed_out or sp.resident_timed_out):
            return False
        resident_dead = sp.resident_timed_out
        log.debug("timeout: %s → 急用退場",
                  "住人+客人" if (resident_dead and sp.guest_timed_out) else
                  ("住人" if resident_dead else "客人"))
        self.view.system("  " + loc("guest_timeout_leave", prompts.guest_timeout_leave()))   # 客人が急用で去る（定型・local。世界観を壊さない）
        await self._end_visit()                                   # 部屋を閉じ codex を破棄（taskkill /T /F で確実に殺す・_speakers も None に）
        if resident_dead:
            await self._resident_timed_out()                     # 住人も無応答なら段階回復へ
        return True

    async def _play_arc_now(self, key):
        """/arc：箱庭アークを今すぐ再生（デバッグ）。tick 駆動の active に載せて起→承→転→結を前へ進める。
        以前はここで完走まで while ループでブロックしていて、その間 on_user_input が返らず＝**再生中の
        話しかけ（割り込み）が効かなかった**。active に載せ替えて即 return し、以降は _tick が前進させる
        ＝入力ループが空くので barge-in（cancel優先・ADR-0006）が通る。"""
        key = ARC_KEY_ALIASES.get((key or "").lower(), key)   # 英別名（sparrow/cat/wind・英語 voice の /help と対応・ADR-0022）
        weather = await asyncio.to_thread(sources.fetch_weather)
        self.weather = weather                       # 取れた天気は保持（捏造防止・tick と揃える）
        ctx = sources.build_context(weather, self.topics)
        pool = [s for s in self.sources if s.key == key] if key \
            else [s for s in self.sources if s.eligible(ctx)]
        if not pool:
            self.view.system("  （今出せるアークが無い。/arc 雀 等キー指定で強制できる）"); return
        arc = random.choice(pool)
        async with self.drive_lock:                  # tick と排他で active_source を載せる
            if self.active_source is not None or self.active_guest is not None \
                    or self.room is not None or self.games.active:
                self.view.system("  （今は別のことをしとる。落ち着いてから /arc してな）"); return
            arc.reset()
            self.active_source = arc                 # 自然アーク同様、起が ~1s 後に出る（デバッグ表記は出さず窓を汚さない）
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
                    self.view.system(loc("no_response", "  （応答が戻らへん……ちょっと間があいた）"))
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
            await self.games.close()                     # 対局中に終了した時の客人(codex)を刈る＋観戦窓を閉じる
            visiting = self.active_guest if (self.active_guest is not None and self.active_guest not in self.sources) else None  # 召喚客人は registry 外
            for s in list(self.sources) + [self.idle] + ([visiting] if visiting else []):
                try:                                     # shutdown teardown（codex leak の最終防波堤）
                    await s.close()
                except Exception:
                    pass
            await self.resident.close()
