"""conversation.py（3人会話の部屋・ADR-0015/Inc1）: 宛先解決の純関数と、State マシンの
遷移・歯止め（連続AIターン上限／AwaitingHuman は tick で AI を動かさない／沈黙で辞去）を検証。
ライブ未接続なので fake Speaker（カラブル）で完結。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


if __name__ == "__main__":
    unittest.main()
