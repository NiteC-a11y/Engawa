#!/usr/bin/env python3
"""game_rlcard.py — RLCard を Game ポート(game.GameAdapter)に合わせるアダプタ（ADR-0017）。

**rlcard 依存はこのファイルだけに閉じ込める**（コア game.py / app 本体は rlcard を知らない）。
ゲームを使う時だけ import される＝rlcard は「遊ぶ時だけ必要な任意依存」。spike で確認した
`raw_obs`(読める状態) / `raw_legal_actions`(手の文字列) / `env.step(move, raw_action=True)` を
そのままポートに写す。差し替え（PettingZoo/自作）はこのファイルと同じ形の別アダプタを足すだけ。
"""
import game

try:
    import rlcard
except ImportError:                      # 未インストールでもコア app は動く（遊ぶ時だけ要求）
    rlcard = None


class RLCardAdapter(game.GameAdapter):
    """1つの RLCard env を包む。num_players は env の実値（uno 等は config を無視し2固定なので実値を採る）。"""
    def __init__(self, game_id, num_players, seed=None):
        if rlcard is None:
            raise game.GameError("rlcard が必要です（pip install rlcard）。遊ぶ時だけ要ります。")
        cfg = {"allow_step_back": False, "game_num_players": int(num_players)}
        if seed is not None:
            cfg["seed"] = seed
        self._env = rlcard.make(game_id, config=cfg)
        self.num_players = self._env.num_players       # 実際の人数（config が効かないゲームの保険）

    def reset(self):
        self._env.reset()

    def current_player(self):
        return self._env.get_player_id()

    def is_over(self):
        return self._env.is_over()

    def state(self, player):
        return self._env.get_state(player)["raw_obs"]          # 読める状態（手札・場…）

    def legal_moves(self, player):
        return list(self._env.get_state(player)["raw_legal_actions"])

    def play(self, move):
        self._env.step(move, raw_action=True)                 # 文字列の手を直接適用

    def result(self):
        return list(self._env.get_payoffs())


# ── レジストリ登録（ゲーム種類を増やす口＝ここに1行足すだけ）──────────────────
def register_rlcard_games():
    game.register("blackjack", lambda n: RLCardAdapter("blackjack", n),
                  label="ブラックジャック", players=(1, 7))
    game.register("uno", lambda n: RLCardAdapter("uno", n),
                  label="UNO", players=(2, 2))           # RLCard の UNO は2人固定
    game.register("leduc", lambda n: RLCardAdapter("leduc-holdem", n),
                  label="レダックポーカー", players=(2, 4))
