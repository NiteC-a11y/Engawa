#!/usr/bin/env python3
"""scheduler.py — Scheduler（Mediator・ADR-0013 ①）。

毎ティック {進行中アーク前進 / 新アーク開始 / 天気つぶやき・移ろい / 沈黙} を抽選し、
源の産んだ Narration を resident に注入する。間合い・cooldown・割り込みを一手に持つ。

割り込み（cancel優先・ADR-0006）の対象は **resident の注入ターン（speaking_task）だけ**。
source のカーソル（active）は触らない → QUIET 明けに同じ active から背景継続（Test C で実証）。
close は run() の finally の全 teardown 専用（結了時は reset()+cooldown のみ・ADR-0013 #2）。
"""
import asyncio
import os
import random
import time

import sources

TICK_MIN = float(os.environ.get("ENGAWA_TICK_MIN", "35"))
TICK_MAX = float(os.environ.get("ENGAWA_TICK_MAX", "70"))
QUIET_AFTER_USER = float(os.environ.get("ENGAWA_QUIET", "25"))
ARC_START_PROB = float(os.environ.get("ENGAWA_ARC_PROB", "0.30"))
MUTTER_PROB = float(os.environ.get("ENGAWA_MUTTER_PROB", "0.6"))


class Scheduler:
    def __init__(self, resident, source_list, idle, view, spawn_codex=None):
        self.resident = resident
        self.sources = source_list
        self.idle = idle
        self.view = view
        self._spawn_codex = spawn_codex          # 客人(codex)の async factory（adr/0008）
        self.active = None                               # 進行中 source（割り込みで消えない）
        self.cooldowns = {s.key: 0 for s in source_list}
        self.turn_lock = asyncio.Lock()                  # resident 注入(_inject)の直列化＝割り込みの単位
        self.drive_lock = asyncio.Lock()                 # self.active 駆動を tick と召喚で排他（競合防止）
        self.speaking = False                            # resident 注入が in-flight か
        self.last_user_ts = 0.0
        self.weather = None                              # 最新天気を保持（起動時1回＋tick毎更新・捏造防止）
        self.topics = []                                 # 客人の世間話ネタ・プール（ADR-0014）
        self._topics_at = 0.0                            # 最終更新時刻（TOPIC_REFRESH_MIN で更新）
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
        if self.active is not None:                      # (1) 進行中アークを前へ
            await self._emit(await self.active.next_phase(ctx))
            return
        if random.random() < ARC_START_PROB:             # (2) 新アーク抽選（gate＋cooldown）
            eligible = [s for s in self.sources
                        if s.eligible(ctx) and self.cooldowns.get(s.key, 0) <= 0]
            if eligible:
                self.active = random.choice(eligible)
                self.active.reset()
                await self._emit(await self.active.next_phase(ctx))   # 起を即出す
                return
        narr = await self.idle.next_phase(ctx)           # (3) 天気つぶやき/移ろい or 沈黙
        if narr is not None and (narr.kind == "transition" or random.random() < MUTTER_PROB):
            await self._inject(narr)

    async def _tick_loop(self):
        while not self.stop.is_set():
            wait = random.uniform(TICK_MIN, TICK_MAX)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=wait); break
            except asyncio.TimeoutError:
                pass
            if self.turn_lock.locked():
                continue
            if time.time() - self.last_user_ts < QUIET_AFTER_USER:   # 会話直後は静か
                continue
            self.weather = await asyncio.to_thread(sources.fetch_weather)   # 保持を更新
            if time.time() - self._topics_at > sources.TOPIC_REFRESH_MIN * 60:
                self.topics = await asyncio.to_thread(sources.fetch_topics)  # ネタを定期更新
                self._topics_at = time.time()
            async with self.drive_lock:                  # 召喚と active 駆動を競合させない
                await self._tick(sources.build_context(self.weather, self.topics))

    # ── ユーザー入力（割り込み・cancel優先）──────────────────
    async def on_user_input(self, line):
        line = (line or "").strip()
        if not line:
            return
        if line.startswith("/"):
            await self._command(line)
            return
        self.last_user_ts = time.time()
        if self.speaking:                                # 進行中の注入だけを畳む
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
            self.view.system("  /quit            → 縁側を閉じる")
        elif cmd == "/arc":
            await self._play_arc_now(parts[1] if len(parts) > 1 else None)
        elif cmd == "/codex":
            rest = line.split(maxsplit=1)
            persona = rest[1].strip() if len(rest) > 1 else "気まぐれな旅の客"
            await self._summon_guest(persona)
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
            weather = await asyncio.to_thread(sources.fetch_weather)
            await self._emit(await self.active.next_phase(sources.build_context(weather, self.topics)))
            if self.active is None:      # 第一声すら出ず結了＝codex spawn 失敗（召喚は明示動作なので可視化）
                self.view.system("  （客人は来られなんだ。codex 接続・ChatGPT 認証を確認してな）")

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
            for s in list(self.sources) + [self.idle]:   # shutdown teardown（leak 最終防波堤）
                try:
                    await s.close()
                except Exception:
                    pass
            await self.resident.close()
