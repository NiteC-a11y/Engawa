#!/usr/bin/env python3
"""conversation.py — 3人会話の「部屋」（ADR-0015 / Inc1 設計・Inc2 で Scheduler 結線済み）。

私(人間)・茶々(住人)・客人(codex) が同じ場で聞き合う。最難関の **ターン管理** を
**State パターン**で明示し、「人間が駆動している間だけ AI が応答し、必ず人間待ちに戻る」
＝原則#3（人間不在の無際限な自律往復に戻さない）を *構造で* 保証する。

採用したデザインパターン:
- **value**: `Utterance` / `Transcript` … 共有の発話ログ。codex(使い捨て)には window を毎回渡す土台。
- **Strategy / DI**: `Speaker` … 茶々/客人を均一に「喋らせる」注入カラブル。Room は実体(agent/View)を
  知らない＝差し替え可能・テスト可能（Inc2 で Scheduler が実 agent を結線する）。
- **純関数（Strategy-lite）**: `resolve_addressee` … 宛先解決（名前メンション＋既定は茶々・ADR-0015 決定）。
- **State パターン**: `RoomState` ＝ `Greeting → AwaitingHuman ⇄ (Responding | ResidentFilling) → Leaving → Closed`。
  - `AwaitingHuman` は沈黙が続くと、まず茶々が“人間役の代打”で場をつなぎ（`ResidentFilling`・予算 `fill_cap` 回・ADR-0025）、
    予算を使い切れば Leaving へ。代打の間隔は回ごとに `fill_slowdown` ずつ延びる（来訪が進むほどゆっくり＝ネタ切れの間延び）。
    `fill_cap=0` なら **on_tick で AI を一切動かさない**＝従来どおり自律往復が起き得ない。
  - `Responding` は人間発話あたり **最大 turn_cap(=2) 手**で必ず `AwaitingHuman` へ戻る＝歯止めの本体（人間関与で代打予算も満タンに戻す）。
  - `ResidentFilling` は茶々→客人の **1往復** で必ず `AwaitingHuman` へ戻る＝人間不在でも有界（予算で必ず終端に着く）。
- **Mediator**: 外側は既存 `Scheduler`、その下に部屋の調停役 `Room` を置く。

このモジュールは Scheduler/agent を import しない（Speaker 注入のみで純粋・import は re だけ）。Inc2 で Scheduler が実 agent を Speaker として結線済み。
"""
import re

# ── 宛先解決（ADR-0015 決定: 名前メンション＋既定は茶々）────────────────────
_BOTH_WORDS = ("二人", "両方", "みんな", "双方")
_GUEST_WORDS = ("客人", "お客")
# 呼び名候補＝漢字/カタカナの2字以上の連なり（「近所の物知りなご隠居」→ 近所/物知/隠居。助詞は落ちる）。
_NAME_RUN = re.compile(r"[一-鿿゠-ヿ]{2,}")


def guest_aliases(persona):
    """persona から客人の呼び名候補を粗く拾う。完全一致は要らない（ユーザーは「ご隠居」等と短く呼ぶ）。"""
    return set(_NAME_RUN.findall(persona or "")) | set(_GUEST_WORDS)


_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'), ("'", "'"))


def _unquote(s):
    """発話全体を包む引用符を1組だけ剥がす（codex が台詞を「…」で包みがち→表示を一様に）。
    全体の包みでない（途中に同じ括弧がある／複数組）時はそのまま＝中身の引用は壊さない。"""
    s = s.strip()
    for a, b in _QUOTE_PAIRS:
        if len(s) >= 2 and s[0] == a and s[-1] == b and a not in s[1:-1] and b not in s[1:-1]:
            return s[1:-1].strip()
    return s


def resolve_addressee(text, persona):
    """誰に言ったか → 'both' | 'guest' | 'resident'（既定）。副作用なしの純関数。
    明示語「二人/両方…」=both、「茶々」=resident、persona 語/「客人」=guest、両方該当=both、無印=既定 resident。"""
    t = text or ""
    if any(w in t for w in _BOTH_WORDS):
        return "both"
    to_guest = any(a in t for a in guest_aliases(persona))
    to_resident = "茶々" in t
    if to_guest and to_resident:
        return "both"
    if to_guest:
        return "guest"
    return "resident"


# ── 共有の発話ログ（value）─────────────────────────────────────────────
class Utterance:
    __slots__ = ("speaker", "text")
    def __init__(self, speaker, text):
        self.speaker, self.text = speaker, text


class Transcript:
    """部屋の発話ログ。codex には window(直近N) を毎回渡す（使い捨て＝文脈を持たないため）。"""
    def __init__(self):
        self._items = []

    def append(self, speaker, text):
        self._items.append(Utterance(speaker, text))

    def window(self, n=8):
        return tuple(self._items[-n:])

    def render(self, n=8):
        return "\n".join(f"{u.speaker}「{u.text}」" for u in self.window(n))

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)


# ── 発話者（Strategy/DI）。Room は agent/View を知らず、注入された fn を呼ぶだけ ──────
# kind: 場面の種別。実プロンプト文言は Inc2 で Scheduler 側の fn が解釈する（Room は配役と順序だけ持つ）。
# MUSE: 人間が席を外している間、茶々が“人間役の代打”として場を回す振り（ADR-0025）。
ARRIVE, REACT, REPLY, CHIME, LEAVE, LEAVE_REACT, MUSE = (
    "arrive", "react", "reply", "chime", "leave", "leave_react", "muse")


class Speaker:
    """茶々/客人を均一に喋らせる注入アダプタ。name は transcript の話者タグ。
    fn: async (window: tuple[Utterance], kind: str) -> str | None（None/空＝無言で積まない）。"""
    def __init__(self, name, fn):
        self.name = name
        self._fn = fn

    async def say(self, window, kind):
        return await self._fn(window, kind)


# ── 部屋（Mediator）＋ 状態（State パターン）────────────────────────────────
class Room:
    def __init__(self, persona, resident, guest, *, turn_cap=2, idle_leave_ticks=4,
                 fill_cap=3, fill_after=2, fill_slowdown=1, on_say=None):
        self.persona = persona
        self.resident = resident          # Speaker（茶々）
        self.guest = guest                # Speaker（客人）
        self.turn_cap = max(1, int(turn_cap))           # 人間発話あたりの連続AIターン上限（歯止め）
        self.idle_leave_ticks = max(1, int(idle_leave_ticks))   # 人間沈黙が続いたら客人は辞去
        # 代打（ADR-0025）: 人間が来るまで茶々が人間役を代行する回数の上限（=人間不在の連続AIターン上限）。
        # 0 で無効＝従来の純待ち挙動。fill_after 沈黙で1回発火し、予算を使い切ったら idle_leave_ticks で辞去。
        self.fill_cap = max(0, int(fill_cap))
        self.fill_after = max(1, int(fill_after))       # 最初の代打までの沈黙ティック数（< idle_leave_ticks 前提）
        # 回を追うごとに代打の間隔を延ばす（来訪が進むほどゆっくり＝人間の「来た直後は賑やか→ネタ切れで間延び→帰る」を模す）。
        # n回目の代打しきい値 = fill_after + n*fill_slowdown。人間が関与すると予算リセット＝この間隔も先頭に戻る（賑わい復活）。
        self.fill_slowdown = max(0, int(fill_slowdown))
        self._fill_left = self.fill_cap                 # 残り代打回数（人間の発話でリセット＝また代打できる）
        self.transcript = Transcript()
        self._on_say = on_say or (lambda speaker, text, kind: None)   # 表示/記録フック（任意）
        self._stop = lambda: False        # 現ドライブの失効判定（barge-in・ADR-0031）。各ドライブ入口で差し替え
        self._state = Greeting(self)

    # 観測用
    @property
    def closed(self):
        return isinstance(self._state, Closed)

    @property
    def state_name(self):
        return type(self._state).__name__

    @property
    def preempted(self):
        """現在のドライブが barge-in で失効しているか（commit gate と同じ判定・ADR-0031）。"""
        return self._stop()

    # 外部イベント（Scheduler が駆動）。状態へ委譲＝State パターン。
    # should_stop: 「このドライブはもう最新でない」判定（barge-in・ADR-0031）。省略時は従来挙動（止まらない）。
    async def begin(self, should_stop=None):
        self._stop = should_stop or (lambda: False)
        await self._state.enter()

    async def on_human(self, text, to=None, should_stop=None):
        self._stop = should_stop or (lambda: False)
        await self._state.on_human((text or "").strip(), to)

    async def on_tick(self, should_stop=None):
        self._stop = should_stop or (lambda: False)
        await self._state.on_tick()

    # 1人に喋らせて transcript/表示へ（無言は積まない）。commit gate はここ一箇所（ADR-0031）＝
    # 停止判定は「手の前」＋「復帰後・commit 前」の二段。失効ドライブの言いかけは表示にも transcript にも積まない。
    async def _utter(self, speaker, kind, preemptible=True):
        if preemptible and self._stop():
            return ""
        text = _unquote((await speaker.say(self.transcript.window(), kind) or "").strip())
        if preemptible and self._stop():
            return ""
        if text:
            self.transcript.append(speaker.name, text)
            self._on_say(speaker.name, text, kind)
        return text

    def _goto(self, state):
        self._state = state
        return state


class RoomState:
    def __init__(self, room):
        self.room = room

    async def enter(self):
        pass

    async def on_human(self, text, to=None):   # 既定: 無視（Greeting/Leaving/Closed 中の人間入力）
        pass

    async def on_tick(self):
        pass


class Greeting(RoomState):
    """来訪の入り＝客人が到着の挨拶、茶々が一言（有界・人間不要のアーク的瞬間）→ 人間待ちへ。
    ARRIVE は中断不可＝「到着した」という世界状態を確定させてから人間に譲る（REACT のみ省略可・ADR-0031）。"""
    async def enter(self):
        r = self.room
        await r._utter(r.guest, ARRIVE, preemptible=False)
        await r._utter(r.resident, REACT)
        await r._goto(AwaitingHuman(r)).enter()


class AwaitingHuman(RoomState):
    """部屋オープン＝人間待ち。沈黙が続いたら、まず茶々が“人間役の代打”で場をつなぎ（予算 fill_cap 回・
    ADR-0025）、予算を使い切ったら客人が辞去する（有界維持）。fill_cap=0 なら代打なし＝従来の純待ち＝
    **on_tick で AI を一切動かさない**（原則#3 の核はこの経路で保たれる）。"""
    def __init__(self, room):
        super().__init__(room)
        self.idle = 0

    async def on_human(self, text, to=None):
        if text:
            await self.room._goto(Responding(self.room, text, to)).enter()

    async def on_tick(self):
        self.idle += 1
        r = self.room
        if r._fill_left > 0:                                  # 予算が残る間は代打で場をつなぐ（人間はいつでも割り込める）
            used = r.fill_cap - r._fill_left                  # これまで代打した回数＝進むほど間隔が延びる（ネタ切れの間延び）
            if self.idle >= r.fill_after + used * r.fill_slowdown:
                r._fill_left -= 1
                await r._goto(ResidentFilling(r)).enter()
                return
        if self.idle >= r.idle_leave_ticks:                  # 予算ゼロ＋沈黙継続 → 客人は辞去（必ず終端に着く）
            await r._goto(Leaving(r)).enter()


class Responding(RoomState):
    """人間発話への応答。宛先AI→もう片方が一言、ただし **最大 turn_cap 手** で必ず人間待ちへ戻る（歯止め）。"""
    def __init__(self, room, human_text, to=None):
        super().__init__(room)
        self.human_text = human_text
        self.to = to                  # 明示宛先（web チップ由来。無ければ本文から名前メンション解決・C方式）

    async def enter(self):
        r = self.room
        addr = self.to or resolve_addressee(self.human_text, r.persona)
        # 本文はクリーンのまま。誰宛かは話者タグに残す＝場の全員(茶々/客人)が方向を知れる（部屋の原則）。表示は View が別途エコー済み
        tag = {"guest": "私→客人", "both": "私→二人とも"}.get(addr, "私")
        r.transcript.append(tag, self.human_text)
        order = {
            "guest":    [(r.guest, REPLY), (r.resident, CHIME)],
            "resident": [(r.resident, REPLY), (r.guest, CHIME)],
            "both":     [(r.resident, REPLY), (r.guest, REPLY)],
        }[addr]
        turns = 0
        for speaker, kind in order:
            if turns >= r.turn_cap:
                break
            if await r._utter(speaker, kind):
                turns += 1
        r._fill_left = r.fill_cap                          # 人間が関与した＝代打予算を満タンに戻す（次の沈黙でまた代打・有界は不変）
        await r._goto(AwaitingHuman(r)).enter()           # 必ず人間待ちへ（ピンポンに入らない）


class ResidentFilling(RoomState):
    """人間が席を外している間、茶々が“人間役の代打”で場を回す（ADR-0025）。茶々が客人に振り、客人が一言返す
    ＝有界（1往復）で必ず人間待ちへ戻る。予算 `_fill_left` は AwaitingHuman が管理し、使い切れば辞去に向かう。"""
    async def enter(self):
        r = self.room
        if await r._utter(r.resident, MUSE):    # 茶々が代打で場に振る（無言なら客人も動かさない）
            await r._utter(r.guest, REPLY)       # 客人が茶々に短く応じる
        elif r._stop():                          # barge-in で代打が不発（未 commit）＝予算を返す。
            r._fill_left = min(r.fill_cap, r._fill_left + 1)   # 無言（LLM 判断）は従来どおり消費＝返すと辞去に着かない（ADR-0031）
        await r._goto(AwaitingHuman(r)).enter()  # 必ず人間待ちへ戻る（idle は 0 から数え直し）


class Leaving(RoomState):
    """辞去＝客人が暇を告げ、茶々が見送る（有界）→ 終端。codex の破棄は Scheduler 側（closed を見て）。
    中断不可＝終端保証に触らない・挨拶なく消える不自然を避ける（ADR-0031）。"""
    async def enter(self):
        r = self.room
        await r._utter(r.guest, LEAVE, preemptible=False)
        await r._utter(r.resident, LEAVE_REACT, preemptible=False)
        r._goto(Closed(r))


class Closed(RoomState):
    """終端。以後の on_human/on_tick は no-op（来訪終了）。"""
    pass
