"""game.py（Game ポート＋セッション・Inc1）: 順番回し／人数非依存／不正手フォールバック／
人間で止まる／レジストリ を FakeGame で検証。RLCard 等は使わない（ライブ未接続）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import game


class FakeGame(game.GameAdapter):
    """おもちゃ: スロット 0,1,… が順に1手（hi/lo）打ち、全員打ったら終局。hi=+1 / lo=-1。"""
    def __init__(self, num_players):
        self.num_players = num_players
        self._moves = []

    def reset(self):
        self._moves = []

    def current_player(self):
        return len(self._moves)

    def is_over(self):
        return len(self._moves) >= self.num_players

    def legal_moves(self, player):
        return ["hi", "lo"]

    def state(self, player):
        return {"player": player, "played": list(self._moves)}

    def play(self, move):
        self._moves.append(move)

    def result(self):
        return [1 if m == "hi" else -1 for m in self._moves]


def _ai(name, move):
    async def decide(state, legal):
        return move
    return game.Player(name, decide)


class TestGameSession(unittest.IsolatedAsyncioTestCase):
    async def test_all_ai_plays_to_end(self):
        a = FakeGame(3)
        moves, over = [], []
        s = game.GameSession(a, [_ai("茶々", "hi"), _ai("客人A", "lo"), _ai("客人B", "hi")],
                             on_move=lambda n, m, _a: moves.append((n, m)),
                             on_over=lambda r: over.append(r))
        await s.begin()
        self.assertTrue(s.over)
        self.assertEqual(moves, [("茶々", "hi"), ("客人A", "lo"), ("客人B", "hi")])  # スロット順に自動進行
        self.assertEqual(over, [[1, -1, 1]])                                        # result が on_over へ

    async def test_player_count_agnostic(self):
        # 人数を増やす＝プレイヤー配列を増やすだけ（客人を足す想定）
        a = FakeGame(4)
        moves = []
        s = game.GameSession(a, [game.Player("私"), _ai("茶々", "hi"),
                                 _ai("客人A", "hi"), _ai("客人B", "lo")],
                             on_move=lambda n, m, _a: moves.append((n, m)))
        await s.begin()                          # スロット0=人間 → 即停止（AI は動かない）
        self.assertFalse(s.over)
        self.assertEqual(moves, [])
        await s.human_move("hi")                 # 私→以降の AI(茶々/客人A/客人B)が自動で続き終局
        self.assertEqual([n for n, _m in moves], ["私", "茶々", "客人A", "客人B"])
        self.assertTrue(s.over)

    async def test_invalid_ai_move_falls_back(self):
        a = FakeGame(1)
        moves = []
        s = game.GameSession(a, [_ai("茶々", "ZZZ")], on_move=lambda n, m, _a: moves.append((n, m)))
        await s.begin()
        self.assertEqual(moves, [("茶々", "hi")])    # 不正手→合法手の先頭(hi)へフォールバック

    async def test_human_illegal_ignored(self):
        a = FakeGame(1)
        s = game.GameSession(a, [game.Player("私")])
        await s.begin()
        await s.human_move("ZZZ")                    # 不正→無視
        self.assertFalse(s.over)
        await s.human_move("hi")
        self.assertTrue(s.over)

    async def test_players_mismatch_raises(self):
        with self.assertRaises(game.GameError):
            game.GameSession(FakeGame(3), [game.Player("私")])   # 1 != 3


class TestRegistry(unittest.TestCase):
    def test_register_make_and_list(self):
        game.register("_faketest", lambda n: FakeGame(n), label="テスト", players=(1, 4))
        a = game.make("_faketest", 3)
        self.assertEqual(a.num_players, 3)
        self.assertIn("_faketest", game.games())
        self.assertEqual(game.games()["_faketest"]["players"], (1, 4))

    def test_unknown_raises(self):
        with self.assertRaises(KeyError):
            game.make("_nope_", 2)


if __name__ == "__main__":
    unittest.main()
