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

import config        # 設定解決（env > engawa.json > 既定）
import conversation  # 3人会話の部屋（State パターン・ADR-0015 Inc2）
import sources

TICK_MIN = config.get_float("ENGAWA_TICK_MIN", "timing", "tick_min", 35, lo=1)
TICK_MAX = config.get_float("ENGAWA_TICK_MAX", "timing", "tick_max", 70, lo=1)
QUIET_AFTER_USER = config.get_float("ENGAWA_QUIET", "timing", "quiet_after_user", 25, lo=0)
ARC_START_PROB = config.get_float("ENGAWA_ARC_PROB", "timing", "arc_prob", 0.30, lo=0, hi=1)
MUTTER_PROB = config.get_float("ENGAWA_MUTTER_PROB", "timing", "mutter_prob", 0.6, lo=0, hi=1)
ACTIVE_BEAT_MIN = config.get_float("ENGAWA_ACTIVE_BEAT_MIN", "timing", "active_beat_min", 5, lo=1)   # アーク/来訪 進行中のビート間隔（短め＝会話が流れる）
ACTIVE_BEAT_MAX = config.get_float("ENGAWA_ACTIVE_BEAT_MAX", "timing", "active_beat_max", 12, lo=1)
TICK_MIN, TICK_MAX = min(TICK_MIN, TICK_MAX), max(TICK_MIN, TICK_MAX)                       # min>max の設定ミスを正す
ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX = min(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX), max(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)


class Scheduler:
    def __init__(self, resident, source_list, idle, view, spawn_codex=None):
        self.resident = resident
        self.sources = source_list
        self.idle = idle
        self.view = view
        self._spawn_codex = spawn_codex          # 客人(codex)の async factory（adr/0008）
        self.active = None                               # 進行中 source（割り込みで消えない）
        self.room = None                                 # 3人会話の部屋（来訪中だけ・ADR-0015 Inc2）
        self.cooldowns = {s.key: 0 for s in source_list}
        self.turn_lock = asyncio.Lock()                  # resident 注入(_inject)の直列化＝割り込みの単位
        self.drive_lock = asyncio.Lock()                 # self.active 駆動を tick と召喚で排他（競合防止）
        self.speaking = False                            # resident 注入が in-flight か
        self.last_user_ts = 0.0
        self.weather = None                              # 最新天気を保持（起動時1回＋tick毎更新・捏造防止）
        self.topics = []                                 # 客人の世間話ネタ・プール（ADR-0014）
        self._topics_at = 0.0                            # 最終更新時刻（TOPIC_REFRESH_MIN で更新）
        self._next_at = 0.0                              # 次ビートの予定時刻（active 中は短間隔・遅延しても保持）
        self.stop = asyncio.Event()

    # ── 注入（turn_lock で直列化）──────────────────────────
    async def _inject(self, narration):
        async with self.turn_lock:
            self.view.turn_start("茶々", narration.kind, narration.label, narration.voice)
            self.speaking = True
            try:
                stop_reason = await self.resident.prompt(narration.text, on_chunk=self.view.chunk)
            finally:
                self.speaking = False
                self.view.turn_end()
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

    # ── ティック ──────────────────────────────────────────
    async def _tick(self, ctx):
        for k in self.cooldowns:
            self.cooldowns[k] = max(0, self.cooldowns[k] - 1)
        if self.room is not None:                        # (0) 3人会話の部屋＝人間待ち/沈黙→辞去（State が判断）
            await self.room.on_tick()
            if self.room.closed:
                await self._end_visit()
            return
        if self.active is not None:                      # (1) 進行中アークを前へ
            await self._emit(await self.active.next_phase(ctx))
            return
        if random.random() < ARC_START_PROB:             # (2) 新アーク抽選（gate＋cooldown）
            eligible = [s for s in self.sources
                        if s.eligible(ctx) and self.cooldowns.get(s.key, 0) <= 0]
            if eligible:
                chosen = random.choice(eligible)
                chosen.reset()
                self.active = chosen
                if chosen.key == "guest":                # 自発来訪 → 3人会話の部屋を開く（ADR-0015）
                    await self._start_room(chosen.persona)
                else:
                    await self._emit(await self.active.next_phase(ctx))   # 箱庭アークは起を即出す
                return
        narr = await self.idle.next_phase(ctx)           # (3) 天気つぶやき/移ろい or 沈黙
        if narr is not None and (narr.kind == "transition" or random.random() < MUTTER_PROB):
            await self._inject(narr)

    def _next_interval(self):
        """次ビートまでの間合い。アーク/来訪が進行中(active)は短く、何もない時は長い ambient。"""
        if self.active is not None:
            return random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)
        return random.uniform(TICK_MIN, TICK_MAX)

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
            guest_active = self.active is not None and self.active.key == "guest"
            if not guest_active and now - self.last_user_ts < QUIET_AFTER_USER:
                continue                                 # 会話直後は静か（ただし来訪は止めない）
            self.weather = await asyncio.to_thread(sources.fetch_weather)   # 保持を更新
            if now - self._topics_at > sources.TOPIC_REFRESH_MIN * 60:
                self.topics = await asyncio.to_thread(sources.fetch_topics)  # ネタを定期更新
                self._topics_at = now
            async with self.drive_lock:                  # 召喚と active 駆動を競合させない
                await self._tick(sources.build_context(self.weather, self.topics))
            self._next_at = time.time() + self._next_interval()   # 次の間合い（active 中は短い＝会話が流れる）

    # ── ユーザー入力（割り込み・cancel優先）──────────────────
    async def on_user_input(self, line):
        line = (line or "").strip()
        if not line:
            return
        if line.startswith("/"):
            await self._command(line)
            return
        self.last_user_ts = time.time()
        if self.room is not None and not self.room.closed:   # 3人会話＝人間が宛先を決めて駆動（ADR-0015）
            async with self.drive_lock:                      # tick と直列化（部屋の状態機械を保護）
                await self.room.on_human(line)
                if self.room.closed:
                    await self._end_visit()
            self.last_user_ts = time.time()
            return
        if self.speaking:                                # 進行中の注入だけを畳む（ambient・cancel優先）
            await self.resident.cancel()                 # session/cancel → stopReason=cancelled
            self.view.system("[茶々がこちらを向いた]")
        # active(source) は触らない → QUIET 明けに背景継続
        ctx = sources.build_context(self.weather, self.topics)   # 保持した天気を渡す（捏造防止）
        await self._inject(sources.Narration(sources.user_narration(line, ctx), "user"))
        self.last_user_ts = time.time()

    async def _command(self, line):
        parts = line.split(); cmd = parts[0].lower()
        if cmd in ("/quit", "/exit", "/bye"):
            self.view.system("[*] 縁側を閉じます。"); self.stop.set()
        elif cmd == "/help":
            self.view.system("  ふつうに打って Enter → 茶々に話しかける")
            self.view.system("  /arc [雀|猫|風]  → 箱庭アークを今すぐ再生（デバッグ）")
            self.view.system("  /codex <人格>    → 客人(codex)を呼ぶ（到着→世間→辞去の短い来訪）")
            self.view.system("  /model           → 今のモデルを表示（住人=Claude / 客人=codex）")
            self.view.system("  /quit            → 縁側を閉じる")
        elif cmd == "/arc":
            await self._play_arc_now(parts[1] if len(parts) > 1 else None)
        elif cmd == "/codex":
            rest = line.split(maxsplit=1)
            persona = rest[1].strip() if len(rest) > 1 else "気まぐれな旅の客"
            await self._summon_guest(persona)
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
        except Exception:
            self.view.system("  （客人は来られなんだ。codex 接続・ChatGPT 認証を確認してな）")
            self._conclude(self.active)
            return
        resident_spk, guest_spk = self._room_speakers(persona)
        self.room = conversation.Room(
            persona, resident_spk, guest_spk,
            on_say=lambda who, text, kind: self.view.say(who, text))
        await self.room.begin()                          # 到着の挨拶＋茶々の反応 → 人間待ち
        if self.room.closed:                             # 念のため（通常は AwaitingHuman）
            await self._end_visit()
        else:                                            # 沈黙検出のため tick を短間隔に
            self._next_at = time.time() + random.uniform(ACTIVE_BEAT_MIN, ACTIVE_BEAT_MAX)

    def _room_speakers(self, persona):
        """茶々/客人を均一に喋らせる Speaker（Strategy/DI）。Room は agent/View を知らない。"""
        async def resident_say(window, kind):
            async with self.turn_lock:                   # ambient と同じ直列化単位
                self.speaking = True
                try:
                    return await self.resident.prompt(sources.room_resident_prompt(window, kind))
                finally:
                    self.speaking = False

        async def guest_say(window, kind):
            agent = self.active.agent if self.active is not None else None
            if agent is None:
                return ""
            return (await agent.prompt(sources.room_guest_prompt(persona, window, kind))).strip()

        return (conversation.Speaker("茶々", resident_say),
                conversation.Speaker(persona, guest_say))

    async def _end_visit(self):
        """来訪終了: codex を破棄（使い捨て・ADR-0008）し cooldown を置いて部屋を閉じる。"""
        src = self.active
        self.room = None
        if src is not None:
            try:
                await src.close()
            except Exception:
                pass
            self.cooldowns[src.key] = src.cooldown_ticks
            src.reset()
            self.active = None

    async def _play_arc_now(self, key):
        weather = await asyncio.to_thread(sources.fetch_weather)
        ctx = sources.build_context(weather, self.topics)
        pool = [s for s in self.sources if s.key == key] if key \
            else [s for s in self.sources if s.eligible(ctx)]
        if not pool:
            self.view.system("  （今出せるアークが無い。/arc 雀 等キー指定で強制できる）"); return
        arc = random.choice(pool); arc.reset()
        self.view.system(f"  〔debug〕{arc.key} を再生（実天気={ctx['desc'] or '不明'}）")
        while True:
            res = await arc.next_phase(ctx)
            if res is None:
                break
            if res is sources.SILENT:
                continue
            await self._inject(res)
            await asyncio.sleep(1.0)
        arc.reset()

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
                await self.on_user_input(line)
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
            visiting = self.active if self.active not in self.sources else None  # 召喚客人は registry 外
            for s in list(self.sources) + [self.idle] + ([visiting] if visiting else []):
                try:                                     # shutdown teardown（codex leak の最終防波堤）
                    await s.close()
                except Exception:
                    pass
            await self.resident.close()
