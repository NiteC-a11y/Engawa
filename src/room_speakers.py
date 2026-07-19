#!/usr/bin/env python3
"""room_speakers.py — 3人会話の Speaker を作る RoomSpeakerFactory（ADR-0029 Phase 4a）。

`conversation.Room` は agent/View/topic を知らない。それらへの注入アダプタ＝Speaker（茶々/客人）を
ここで作る（Strategy/DI・ADR-0015）。種プール（ambient トピック・ADR-0014）と room 中の timeout
フラグをこのファクトリに凝集し、Scheduler から剥がす（ロジックは verbatim 移設＝振る舞い不変）。

`resident_speak` は turn_lock 下の茶々発話（Scheduler 提供）＝**判断B の seam**。Phase 5 で
ResidentSessionManager.speak() に一本化する（表示なしの共通 core・room は文字列を返すだけ）。
seed ログは注入された `log`（＝`engawa.scheduler`）へ吐き、既存の観測点を保つ。
"""
import random

from agent import AgentTimeoutError
import conversation
import prompts
import sources
import voice        # 住人表示名（transcript の話者タグ・en=Chacha・ADR-0022・7/19）


class RoomSpeakerFactory:
    def __init__(self, persona, *, resident_speak, guest_agent_provider,
                 context_provider, topics_provider, log, preempted=None):
        self.persona = persona
        self._resident_speak = resident_speak          # async (prompt_text)->str・timeout は raise（呼び側で捕捉）
        self._guest_agent = guest_agent_provider       # ()->客人 agent | None
        self._context = context_provider               # ()->ctx（build_context(weather, topics)）
        self._topics = topics_provider                 # ()->topics list（pick_topic_text 用）
        self._log = log
        self._preempted = preempted or (lambda: False)  # 現ドライブが barge-in で失効したか（ADR-0031・timeout 誤発火防止）
        self._inflight = None                          # 生成中の客人 agent（cancel_inflight の畳む対象・ADR-0031）
        self._topic_recent = []                        # 直近使った“種”（来訪内で変化を出す・最大6件）
        self._topic_cooldown = 0                       # 種を置いた後に空ける客人ターン数（同じ話題への粘着防止）
        self.resident_timed_out = False                # room 中に住人が無応答だった（呼び側＝_check_room_timeout が読む）
        self.guest_timed_out = False                   # room 中に客人が無応答だった

    async def cancel_inflight(self):
        """生成中の客人 prompt を best-effort で畳む（barge-in・ADR-0031）。畳む対象が居れば True。
        agent 実体は外に見せない（ADR-0026 のポート境界）。cancel の失敗は致命でないので飲む。"""
        agent = self._inflight
        if agent is None:
            return False
        try:
            await agent.cancel()
        except Exception:
            pass
        return True

    def speakers(self):
        """(茶々 Speaker, 客人 Speaker) を返す。Room がこれを均一に呼ぶ。"""
        return (conversation.Speaker(voice.resident_name(), self._resident_say),
                conversation.Speaker(self.persona, self._guest_say))

    async def _resident_say(self, window, kind):
        try:
            return await self._resident_speak(
                prompts.room_resident_prompt(window, kind, self._context()))
        except AgentTimeoutError:                      # 茶々が無応答 → フラグだけ立て、後始末は呼び側で
            if self._preempted():                      # barge-in と同時の timeout は退場に数えない（ADR-0031）
                return ""
            self.resident_timed_out = True
            return ""

    async def _guest_say(self, window, kind):
        agent = self._guest_agent()
        if agent is None:
            return ""
        ctx = self._context()                          # いまの縁側（時刻＋天気）＝時間感覚のズレ防止
        air = None                                     # 「縁側の空気」＝世間の種（ambient・ADR-0014）
        if kind in (conversation.CHIME, conversation.REPLY):
            if self._topic_cooldown > 0:               # 直前に種を置いた→数ターン空ける（同じ話題への粘着を防ぐ）
                self._topic_cooldown -= 1
                self._log.debug("種見送り: cooldown 残%d (%s)", self._topic_cooldown, kind)
            elif random.random() < sources.TOPIC_PROB:  # たまに種が空気に混じる（発話有無は codex 判断）
                tidbit = sources.pick_topic_text(self._topics(), self.persona, self._topic_recent)
                if tidbit:
                    self._topic_recent.append(tidbit); del self._topic_recent[:-6]   # 直近6件だけ＝変化を出す
                    self._topic_cooldown = sources.TOPIC_COOLDOWN                     # 次の種まで間を空ける
                    air = prompts.guest_air(tidbit)
                    self._log.debug("種を空気へ: %s (%s)", tidbit, kind)   # 実際に口に出すかは codex 判断（目視）
                else:
                    self._log.debug("種見送り: 空プール (%s)", kind)
            else:
                self._log.debug("種見送り: prob外れ (%s)", kind)
        self._inflight = agent                          # barge-in の畳む対象として登録（ADR-0031）
        try:
            reply = (await agent.prompt(
                prompts.room_guest_prompt(self.persona, window, kind, ctx=ctx, air=air))).strip()
        except AgentTimeoutError:                       # 客人が無応答 → ハング client は二度叩かず急用退場へ
            if self._preempted():                       # barge-in と同時の timeout は退場に数えない（ADR-0031）
                return ""
            self.guest_timed_out = True
            return ""
        finally:
            if self._inflight is agent:                 # 古い finally が新しい登録を消さない（同一性確認・ADR-0031）
                self._inflight = None
        if prompts.is_error_payload(reply):             # codex が API エラー（例: モデル非対応 400）を本文として返した
            self._log.debug("客人エラー応答を抑制→退場: %s", reply[:160])
            self.guest_timed_out = True                 # 生 JSON を縁側に出さず「応答不能」扱い＝呼び側が急用退場で畳む
            return ""
        return reply
