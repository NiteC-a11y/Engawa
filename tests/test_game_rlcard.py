"""game_rlcard.py（RLCardAdapter・ADR-0017）: 実 rlcard でアダプタとフルゲーム＋GameSession を検証。
rlcard 未インストール時は丸ごと skip（コア suite は rlcard 無しで緑のまま＝任意依存）。"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import rlcard  # noqa: F401
    HAVE_RLCARD = True
except ImportError:
    HAVE_RLCARD = False
import game
import game_rlcard


@unittest.skipUnless(HAVE_RLCARD, "rlcard 未インストール（pip install rlcard）")
class TestRLCardAdapter(unittest.TestCase):
    def test_blackjack_3p_full_random_game(self):
        a = game_rlcard.RLCardAdapter("blackjack", 3, seed=1)
        self.assertEqual(a.num_players, 3)
        a.reset()
        turns = 0
        while not a.is_over() and turns < 300:
            cur = a.current_player()
            legal = a.legal_moves(cur)
            self.assertTrue(legal)                      # 合法手が必ずある
            self.assertIsInstance(a.state(cur), dict)   # 読める状態（dict）
            a.play(random.choice(legal))
            turns += 1
        self.assertTrue(a.is_over())
        self.assertEqual(len(a.result()), 3)            # 3人ぶんの結果

    def test_register_adds_games(self):
        game_rlcard.register_rlcard_games()
        self.assertIn("blackjack", game.games())
        self.assertIn("uno", game.games())

    def test_uno_is_2p(self):
        a = game_rlcard.RLCardAdapter("uno", 3)          # 3 を要求しても…
        self.assertEqual(a.num_players, 2)               # …UNO は2固定（実値を採る）


@unittest.skipUnless(HAVE_RLCARD, "rlcard 未インストール")
class TestSessionWithRLCard(unittest.IsolatedAsyncioTestCase):
    async def test_ai_only_blackjack_completes(self):
        # 全スタックE2E（LLM無し）: 実アダプタ＋ランダムAI×3を GameSession で完走（観戦モード相当）
        async def rand_decide(state, legal):
            return random.choice(legal)
        a = game_rlcard.RLCardAdapter("blackjack", 3, seed=7)
        players = [game.Player(f"P{i}", rand_decide) for i in range(a.num_players)]
        over = []
        s = game.GameSession(a, players, on_over=lambda r: over.append(r))
        s.begin()
        steps = 0
        while await s.step() and steps < 300:
            steps += 1
        self.assertTrue(s.over)
        self.assertEqual(len(over[0]), 3)


class TestBlackjackRenderPure(unittest.TestCase):
    """表示ヘルパ（純粋関数・rlcard 不要）。"""
    def test_value(self):
        self.assertEqual(game_rlcard._value(["DT", "H7"]), 17)       # 10+7
        self.assertEqual(game_rlcard._value(["DT", "HA"]), 21)       # 10+A(11)
        self.assertEqual(game_rlcard._value(["HA", "DA", "H9"]), 21)  # 11+1+9（A 片方は1）
        self.assertEqual(game_rlcard._value(["DK", "HQ", "S5"]), 25)  # bust

    def test_card_and_hand(self):
        self.assertEqual(game_rlcard._card("DT"), "♦10")
        self.assertEqual(game_rlcard._card("HA"), "♥A")
        self.assertEqual(game_rlcard._hand(["S2", "C9"]), "♠2 ♣9")


@unittest.skipUnless(HAVE_RLCARD, "rlcard 未インストール")
class TestBlackjackSnapshot(unittest.TestCase):
    """観戦窓（カード描画）用の構造化スナップショット。"""
    def test_play_then_over(self):
        a = game_rlcard.RLCardAdapter("blackjack", 2, seed=11, render=game_rlcard.BlackjackRender())
        a.reset()
        snap = a.render.snapshot(a, ["私", "茶々"], current_slot=0, over=False)
        self.assertEqual(snap["label"], "ブラックジャック")
        self.assertFalse(snap["over"])
        self.assertTrue(snap["dealer"]["hidden"])            # プレイ中は伏せ札あり
        self.assertEqual(len(snap["dealer"]["cards"]), 1)     # 表向き1枚だけ
        self.assertEqual(len(snap["players"]), 2)
        self.assertTrue(snap["players"][0]["current"])        # current_slot=0 を反映
        self.assertIsNone(snap["players"][0]["outcome"])
        while not a.is_over():
            a.play(random.choice(a.legal_moves(a.current_player())))
        over = a.render.snapshot(a, ["私", "茶々"], over=True)
        self.assertTrue(over["over"])
        self.assertFalse(over["dealer"]["hidden"])            # 終局で公開
        self.assertIn(over["players"][0]["outcome"], ("勝ち", "負け", "引き分け"))


@unittest.skipUnless(HAVE_RLCARD, "rlcard 未インストール")
class TestBlackjackRenderLive(unittest.TestCase):
    def test_deal_and_result_lines(self):
        a = game_rlcard.RLCardAdapter("blackjack", 2, seed=11, render=game_rlcard.BlackjackRender())
        a.reset()
        deal = a.render.deal(a, ["私", "茶々"])
        self.assertTrue(any("ディーラー" in ln for ln in deal))     # 配りでディーラーの表が出る
        while not a.is_over():
            a.play(random.choice(a.legal_moves(a.current_player())))
        res = a.render.result(a, ["私", "茶々"])
        self.assertEqual(len(res), 3)                                # ディーラー行＋2人
        self.assertTrue(any("結果" in ln for ln in res))


if __name__ == "__main__":
    unittest.main()
