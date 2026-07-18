"""conversation.py（3人会話の部屋・ADR-0015/Inc1）: 宛先解決の純関数と、State マシンの
遷移・歯止め（連続AIターン上限／AwaitingHuman は tick で AI を動かさない／沈黙で辞去）を検証。
ライブ未接続なので fake Speaker（カラブル）で完結。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import conversation as conv

PERSONA = "近所の物知りなご隠居"


class TestResolveAddressee(unittest.TestCase):
    def test_guest_by_name_token(self):
        self.assertEqual(conv.resolve_addressee("ご隠居どう思う?", PERSONA), "guest")   # 隠居 を含む

    def test_guest_by_generic_word(self):
        self.assertEqual(conv.resolve_addressee("客人さんは?", PERSONA), "guest")

    def test_resident_default_when_no_mention(self):
        self.assertEqual(conv.resolve_addressee("ええ天気やね", PERSONA), "resident")

    def test_resident_explicit(self):
        self.assertEqual(conv.resolve_addressee("茶々、元気？", PERSONA), "resident")

    def test_both_keyword(self):
        self.assertEqual(conv.resolve_addressee("二人ともどう？", PERSONA), "both")

    def test_both_when_each_named(self):
        self.assertEqual(conv.resolve_addressee("茶々とご隠居、仲ええな", PERSONA), "both")

    def test_other_persona_token(self):
        self.assertEqual(conv.resolve_addressee("風流人さん俳句は?", "句をひねる風流人"), "guest")

    def test_aliases_extracts_name_runs(self):
        al = conv.guest_aliases(PERSONA)
        self.assertIn("隠居", al)
        self.assertIn("客人", al)


class TestUnquote(unittest.TestCase):
    def test_strips_kagi(self):
        self.assertEqual(conv._unquote("「こんばんは」"), "こんばんは")

    def test_strips_other_quotes(self):
        self.assertEqual(conv._unquote("『はい』"), "はい")
        self.assertEqual(conv._unquote('"ok"'), "ok")

    def test_keeps_unwrapped(self):
        self.assertEqual(conv._unquote("こんばんは"), "こんばんは")

    def test_keeps_inner_quote(self):
        self.assertEqual(conv._unquote("彼が「やあ」と言った"), "彼が「やあ」と言った")   # 全体の包みでない

    def test_keeps_multiple_pairs(self):
        self.assertEqual(conv._unquote("「あ」「い」"), "「あ」「い」")                    # 単一の包みでない


class TestTranscript(unittest.TestCase):
    def test_window_and_render(self):
        t = conv.Transcript()
        t.append("私", "やあ"); t.append("茶々", "……"); t.append("ご隠居", "邪魔するで")
        self.assertEqual(len(t), 3)
        self.assertEqual([u.speaker for u in t.window(2)], ["茶々", "ご隠居"])
        self.assertEqual(t.render(2), "茶々「……」\nご隠居「邪魔するで」")


def _spk(name, log, text=None):
    async def fn(window, kind):
        log.append((name, kind, len(window)))
        return text if text is not None else f"{name}-{kind}"
    return conv.Speaker(name, fn)


def _room(log, says=None, **kw):
    on_say = (lambda s, t, k: says.append((s, k))) if says is not None else None
    kw.setdefault("fill_cap", 0)   # 既定は代打オフ＝従来の純待ち挙動を検証（代打は TestResidentFilling で別途）
    return conv.Room(PERSONA, _spk("茶々", log), _spk("ご隠居", log), on_say=on_say, **kw)


def _kinds(log):
    return [(n, k) for (n, k, _w) in log]


class TestRoomFlow(unittest.IsolatedAsyncioTestCase):
    async def test_begin_greets_then_awaits(self):
        log = []
        room = _room(log)
        await room.begin()
        self.assertEqual(room.state_name, "AwaitingHuman")
        self.assertEqual(_kinds(log), [("ご隠居", conv.ARRIVE), ("茶々", conv.REACT)])
        self.assertEqual(len(room.transcript), 2)

    async def test_utter_strips_surrounding_quotes(self):
        # codex が「…」で包んでも、保存/表示は一様に（括弧なし）
        log = []

        def q(name):
            async def fn(window, kind):
                return "「" + name + "の台詞」"
            return conv.Speaker(name, fn)

        room = conv.Room(PERSONA, q("茶々"), q("ご隠居"),
                         on_say=lambda s, t, k: log.append((s, t)))
        await room.begin()
        self.assertTrue(log)
        self.assertTrue(all("「" not in t and "」" not in t for _s, t in log))   # 包む括弧は剥がれる

    async def test_human_to_guest_two_turns_then_await(self):
        log = []
        room = _room(log)
        await room.begin(); log.clear()
        await room.on_human("ご隠居どう思う?")
        self.assertEqual(_kinds(log), [("ご隠居", conv.REPLY), ("茶々", conv.CHIME)])  # 宛先→もう片方
        self.assertEqual(room.state_name, "AwaitingHuman")                              # 必ず人間待ちへ
        self.assertEqual(len(room.transcript), 5)   # 挨拶2 + 私 + 2

    async def test_explicit_to_overrides_and_tags(self):
        # C方式: 本文に客人語が無くても to=guest で客人へ。本文はクリーン・方向は話者タグに残す
        log = []
        room = _room(log)
        await room.begin(); log.clear()
        await room.on_human("こんばんは", to="guest")
        self.assertEqual(_kinds(log), [("ご隠居", conv.REPLY), ("茶々", conv.CHIME)])   # 客人→もう片方
        self.assertIn("私→客人", [u.speaker for u in room.transcript])                  # 方向タグ
        self.assertIn("こんばんは", [u.text for u in room.transcript])                  # 本文はクリーン（呼びかけ無し）

    async def test_human_default_to_resident(self):
        log = []
        room = _room(log)
        await room.begin(); log.clear()
        await room.on_human("ええ天気やね")
        self.assertEqual(_kinds(log), [("茶々", conv.REPLY), ("ご隠居", conv.CHIME)])

    async def test_both_addressing(self):
        log = []
        room = _room(log)
        await room.begin(); log.clear()
        await room.on_human("二人ともどう？")
        self.assertEqual(_kinds(log), [("茶々", conv.REPLY), ("ご隠居", conv.REPLY)])

    async def test_turn_cap_one_stops_after_addressed(self):
        log = []
        room = _room(log, turn_cap=1)
        await room.begin(); log.clear()
        await room.on_human("ご隠居どう?")
        self.assertEqual(_kinds(log), [("ご隠居", conv.REPLY)])   # もう片方は喋らない（上限1）

    async def test_awaiting_tick_runs_no_ai_then_leaves(self):
        log = []
        room = _room(log, idle_leave_ticks=3)
        await room.begin(); log.clear()
        await room.on_tick(); await room.on_tick()
        self.assertEqual(log, [])                                 # 沈黙中は AI を一切動かさない（自律往復が起き得ない）
        self.assertEqual(room.state_name, "AwaitingHuman")
        await room.on_tick()                                      # 3回目で辞去
        self.assertEqual(_kinds(log), [("ご隠居", conv.LEAVE), ("茶々", conv.LEAVE_REACT)])
        self.assertTrue(room.closed)

    async def test_human_resets_idle(self):
        log = []
        room = _room(log, idle_leave_ticks=3)
        await room.begin()
        await room.on_tick(); await room.on_tick()               # idle 2
        await room.on_human("ご隠居どう?")                        # 人間が来た＝idle リセット
        log.clear()
        await room.on_tick(); await room.on_tick()               # また idle 2＝まだ辞去しない
        self.assertEqual(room.state_name, "AwaitingHuman")
        self.assertEqual(log, [])

    async def test_closed_is_terminal_noop(self):
        log = []
        room = _room(log, idle_leave_ticks=1)
        await room.begin(); await room.on_tick()                 # → Leaving → Closed
        self.assertTrue(room.closed)
        log.clear()
        await room.on_human("おーい"); await room.on_tick()
        self.assertEqual(log, [])                                 # 終端後は何も起きない


class TestResidentFilling(unittest.IsolatedAsyncioTestCase):
    """代打（ADR-0025）: 人間待ちの間、茶々が人間役で場をつなぐ。有界（予算 fill_cap）で必ず辞去へ。"""

    async def test_fills_after_silence_then_awaits(self):
        log = []
        room = _room(log, fill_cap=2, fill_after=2, idle_leave_ticks=99)
        await room.begin(); log.clear()
        await room.on_tick()                                     # idle 1＝まだ動かない
        self.assertEqual(log, [])
        await room.on_tick()                                     # idle 2＝代打発火（茶々 MUSE → 客人 REPLY）
        self.assertEqual(_kinds(log), [("茶々", conv.MUSE), ("ご隠居", conv.REPLY)])
        self.assertEqual(room.state_name, "AwaitingHuman")       # 1往復で必ず人間待ちへ戻る
        self.assertEqual(room._fill_left, 1)                     # 予算を1消費

    async def test_budget_exhausts_then_leaves(self):
        log = []
        room = _room(log, fill_cap=1, fill_after=1, idle_leave_ticks=2)
        await room.begin(); log.clear()
        await room.on_tick()                                     # 代打1回（予算→0・idle は 0 に戻る）
        self.assertEqual(_kinds(log), [("茶々", conv.MUSE), ("ご隠居", conv.REPLY)])
        log.clear()
        await room.on_tick()                                     # idle 1＝予算ゼロなのでまだ辞去せず
        self.assertEqual(log, [])
        await room.on_tick()                                     # idle 2＝辞去（必ず終端に着く）
        self.assertEqual(_kinds(log), [("ご隠居", conv.LEAVE), ("茶々", conv.LEAVE_REACT)])
        self.assertTrue(room.closed)

    async def test_human_refills_budget(self):
        log = []
        room = _room(log, fill_cap=1, fill_after=1, idle_leave_ticks=99)
        await room.begin()
        await room.on_tick()                                     # 代打1回で予算を使い切る
        self.assertEqual(room._fill_left, 0)
        await room.on_human("ご隠居どう?")                        # 人間が関与＝予算リセット
        self.assertEqual(room._fill_left, 1)
        log.clear()
        await room.on_tick()                                     # 予算が戻ったのでまた代打が入る
        self.assertEqual(_kinds(log), [("茶々", conv.MUSE), ("ご隠居", conv.REPLY)])

    async def _tick_until_fill(self, room, log, expect_at):
        # 沈黙を刻み、expect_at ティック目でちょうど代打（茶々 MUSE→客人 REPLY）が出ることを確かめる
        for i in range(1, expect_at):
            await room.on_tick()
            self.assertEqual(log, [], f"{i}ティック目で早すぎる代打")
        await room.on_tick()
        self.assertEqual(_kinds(log), [("茶々", conv.MUSE), ("ご隠居", conv.REPLY)])
        log.clear()

    async def test_fills_decelerate_as_budget_depletes(self):
        # 来た直後は早く、回を追うごとに間延び（fill_after=2, slowdown=1 → 2,3,4 ティック間隔）
        log = []
        room = _room(log, fill_cap=3, fill_after=2, fill_slowdown=1, idle_leave_ticks=99)
        await room.begin(); log.clear()
        await self._tick_until_fill(room, log, 2)   # 1回目＝fill_after
        await self._tick_until_fill(room, log, 3)   # 2回目＝fill_after+1
        await self._tick_until_fill(room, log, 4)   # 3回目＝fill_after+2

    async def test_slowdown_zero_keeps_constant_interval(self):
        # slowdown=0 なら間隔は一定（fill_after のまま・減速なし）
        log = []
        room = _room(log, fill_cap=3, fill_after=2, fill_slowdown=0, idle_leave_ticks=99)
        await room.begin(); log.clear()
        await self._tick_until_fill(room, log, 2)
        await self._tick_until_fill(room, log, 2)   # 減速しない＝また2ティック目

    async def test_human_participation_resets_deceleration(self):
        # 人間が関与すると予算が満タンに戻る＝間隔も先頭（fill_after）へ＝賑わい復活
        log = []
        room = _room(log, fill_cap=3, fill_after=2, fill_slowdown=1, idle_leave_ticks=99)
        await room.begin(); log.clear()
        await self._tick_until_fill(room, log, 2)   # 1回目
        await self._tick_until_fill(room, log, 3)   # 2回目＝間延び中
        await room.on_human("ご隠居、さっきの話やけどな"); log.clear()   # 人間が入る＝リセット
        await self._tick_until_fill(room, log, 2)   # 先頭の間隔に戻る（fill_after）

    async def test_fill_cap_zero_is_pure_wait(self):
        log = []
        room = _room(log, fill_cap=0, fill_after=1, idle_leave_ticks=3)
        await room.begin(); log.clear()
        await room.on_tick(); await room.on_tick()               # 代打なし＝AI を一切動かさない（原則#3 の核）
        self.assertEqual(log, [])
        await room.on_tick()                                     # 沈黙のまま辞去
        self.assertEqual(_kinds(log), [("ご隠居", conv.LEAVE), ("茶々", conv.LEAVE_REACT)])

    async def test_silent_resident_skips_guest_reply(self):
        # 茶々が無言（MUSE で空）なら客人も動かさない＝場を無理に回さない
        async def silent(window, kind):
            return "" if kind == conv.MUSE else "x"
        log = []
        room = conv.Room(PERSONA, conv.Speaker("茶々", silent), _spk("ご隠居", log),
                         fill_cap=1, fill_after=1, idle_leave_ticks=99)
        await room.begin(); log.clear()
        await room.on_tick()                                     # 代打枠だが茶々が無言 → 客人 REPLY も無し
        self.assertEqual(log, [])
        self.assertEqual(room.state_name, "AwaitingHuman")


class TestBargeIn(unittest.IsolatedAsyncioTestCase):
    """部屋内 barge-in（ADR-0031）: should_stop（ドライブ失効判定）で残りの手を捨て、
    commit gate（_utter 二段判定）が言いかけを表示/transcript に積まない。有界性は不変。"""

    async def test_responding_stops_after_first_commit(self):
        # 1手目の commit 直後に失効（人間が被せた想定）→ 2手目 CHIME は出ない・必ず人間待ちへ
        log, says, stop = [], [], {"v": False}

        def on_say(s, t, k):
            says.append((s, k)); stop["v"] = True
        room = conv.Room(PERSONA, _spk("茶々", log), _spk("ご隠居", log),
                         fill_cap=0, on_say=on_say)
        await room.begin()
        says.clear(); stop["v"] = False
        await room.on_human("ご隠居どう思う?", should_stop=lambda: stop["v"])
        self.assertEqual(says, [("ご隠居", conv.REPLY)])          # 失効後の CHIME は不発
        self.assertEqual(room.state_name, "AwaitingHuman")        # 歯止めの終着は不変
        self.assertTrue(room.preempted)                           # 失効はプロパティでも観測できる

    async def test_utterance_discarded_when_stopped_mid_say(self):
        # 生成の最中に失効（復帰後・commit 前の gate）→ 言いかけは表示にも transcript にも積まない
        stop, says = {"v": False}, []

        async def fn(window, kind):
            stop["v"] = True                                      # 生成中に barge-in が来た
            return "言いかけの台詞"
        room = conv.Room(PERSONA, conv.Speaker("茶々", fn), _spk("ご隠居", []),
                         fill_cap=0, on_say=lambda s, t, k: says.append((s, t)))
        await room.begin()
        stop["v"] = False; says.clear()
        n = len(room.transcript)
        await room.on_human("ええ天気やね", should_stop=lambda: stop["v"])   # 宛先=茶々(fn)
        self.assertEqual(says, [])                                # 何も表示されない
        self.assertEqual(len(room.transcript), n + 1)             # 積まれたのは人間の行だけ
        self.assertEqual(room.state_name, "AwaitingHuman")

    async def test_greeting_arrive_commits_react_skipped(self):
        # ARRIVE は中断不可（到着という世界状態を確定）・REACT のみ省略可
        log, says = [], []
        room = _room(log, says=says)
        await room.begin(should_stop=lambda: True)                # 最初から失効扱い
        self.assertEqual(_kinds(log), [("ご隠居", conv.ARRIVE)])  # REACT は呼ばれもしない
        self.assertEqual(says, [("ご隠居", conv.ARRIVE)])
        self.assertEqual(room.state_name, "AwaitingHuman")

    async def test_leaving_completes_even_when_stopped(self):
        # 辞去は中断不可＝終端保証（挨拶なく消えない）
        log = []
        room = _room(log, idle_leave_ticks=1)
        await room.begin(); log.clear()
        await room.on_tick(should_stop=lambda: True)              # 失効中でも辞去は完走
        self.assertEqual(_kinds(log), [("ご隠居", conv.LEAVE), ("茶々", conv.LEAVE_REACT)])
        self.assertTrue(room.closed)

    async def test_fill_budget_refunded_on_preempt(self):
        # 代打 MUSE が barge-in で不発（未 commit）→ 予算を返す（客人の退場を早めない）
        stop, log = {"v": False}, []

        async def muse_fn(window, kind):
            if kind == conv.MUSE:
                stop["v"] = True                                  # MUSE 生成中に人間が被せた
            return "むにゃ"
        room = conv.Room(PERSONA, conv.Speaker("茶々", muse_fn), _spk("ご隠居", log),
                         fill_cap=2, fill_after=1, fill_slowdown=0, idle_leave_ticks=99)
        await room.begin()
        stop["v"] = False; log.clear()
        await room.on_tick(should_stop=lambda: stop["v"])         # 代打発火 → MUSE は破棄
        self.assertEqual(room._fill_left, 2)                      # 予算は返る
        self.assertEqual(log, [])                                 # 客人 REPLY も出ない
        self.assertEqual(room.state_name, "AwaitingHuman")

    async def test_fill_budget_consumed_on_silence(self):
        # 無言（LLM 判断）は従来どおり消費＝返すと辞去に着かない（ADR-0031 但し書き）
        log = []
        room = conv.Room(PERSONA, _spk("茶々", log, text=""), _spk("ご隠居", log),
                         fill_cap=2, fill_after=1, fill_slowdown=0, idle_leave_ticks=99)
        await room.begin()
        await room.on_tick(should_stop=lambda: False)             # 失効なし・茶々が無言
        self.assertEqual(room._fill_left, 1)                      # 予算は減ったまま

    async def test_default_no_predicate_unchanged(self):
        # should_stop 省略＝従来挙動（既存スイート全体も回帰網だが、明示の1本を置く）
        log = []
        room = _room(log)
        await room.begin(); log.clear()
        await room.on_human("ご隠居どう思う?")
        self.assertEqual(_kinds(log), [("ご隠居", conv.REPLY), ("茶々", conv.CHIME)])


if __name__ == "__main__":
    unittest.main()
