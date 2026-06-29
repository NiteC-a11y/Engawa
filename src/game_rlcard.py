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
    def __init__(self, game_id, num_players, seed=None, render=None):
        if rlcard is None:
            raise game.GameError("rlcard が必要です（pip install rlcard）。遊ぶ時だけ要ります。")
        cfg = {"allow_step_back": False, "game_num_players": int(num_players)}
        if seed is not None:
            cfg["seed"] = seed
        self._env = rlcard.make(game_id, config=cfg)
        self.num_players = self._env.num_players       # 実際の人数（config が効かないゲームの保険）
        self.render = render                           # ゲーム固有の表示器（任意）

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


# ── ブラックジャックの表示（観戦/対局で「札と勝敗が見える」ように整形）───────────
_SUIT = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}


def _card(c):
    return _SUIT.get(c[0], c[0]) + ("10" if c[1] == "T" else c[1])   # 'DT'→♦10 / 'HA'→♥A


def _hand(cards):
    return " ".join(_card(c) for c in cards)


def _value(cards):
    """ブラックジャックの手の点数（A は 11→bust なら 1）。"""
    total = aces = 0
    for c in cards:
        r = c[1]
        if r in ("T", "J", "Q", "K"):
            total += 10
        elif r == "A":
            total += 11
            aces += 1
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


class BlackjackRender:
    """配り→各手→結果（ディーラー公開）を console 行に整形。adapter.state(=raw_obs) を読むだけ。"""
    def deal(self, adapter, names):
        ro = adapter.state(adapter.current_player())
        parts = [f"{nm}: {_hand(ro[f'player{i} hand'])}（{_value(ro[f'player{i} hand'])}）"
                 for i, nm in enumerate(names)]
        up = ro["dealer hand"]
        return [f"  配り｜{' ／ '.join(parts)}",
                f"  ディーラー: {_hand(up)} ?（表 {_value(up)}）"]

    def turn(self, adapter, slot, name):
        ro = adapter.state(slot)
        hand = ro[f"player{slot} hand"]
        up = ro["dealer hand"]
        return f"  {name}の番｜{_hand(hand)}（{_value(hand)}）  ／ ディーラー {_hand(up)} ?"

    def move(self, name, move, adapter, slot):
        hand = adapter.state(slot)[f"player{slot} hand"]
        v = _value(hand)
        verb = "ヒット" if move == "hit" else "ステイ"
        bust = " バースト!" if v > 21 else ""
        return f"  {name} → {verb}（{_hand(hand)} = {v}{bust}）"

    def result(self, adapter, names):
        ro = adapter.state(0)
        dealer = ro["dealer hand"]
        dv = _value(dealer)
        lines = [f"  ── 結果 ── ディーラー: {_hand(dealer)} = {dv}{' バースト!' if dv > 21 else ''}"]
        payoffs = adapter.result()
        for i, nm in enumerate(names):
            hand = ro[f"player{i} hand"]
            r = int(payoffs[i])
            outcome = "勝ち" if r > 0 else ("負け" if r < 0 else "引き分け")
            lines.append(f"  {nm}: {_hand(hand)} = {_value(hand)} → {outcome}")
        return lines

    def snapshot(self, adapter, names, current_slot=None, over=False):
        """観戦窓（カード描画）用の構造化状態。プレイ中はディーラー伏せ札、終局で全公開＋勝敗。"""
        ro = adapter.state(0)
        dealer = ro["dealer hand"]
        payoffs = adapter.result() if over else [None] * len(names)
        players = []
        for i, nm in enumerate(names):
            hand = ro[f"player{i} hand"]
            r = payoffs[i]
            outcome = None
            if r is not None:
                r = int(r)
                outcome = "勝ち" if r > 0 else ("負け" if r < 0 else "引き分け")
            players.append({"name": nm, "cards": [_card(c) for c in hand], "value": _value(hand),
                            "current": (i == current_slot) and not over, "outcome": outcome})
        shown = dealer if over else dealer[:1]              # プレイ中は表向き1枚だけ
        return {"label": "ブラックジャック", "over": over,
                "dealer": {"cards": [_card(c) for c in shown], "value": _value(shown),
                           "hidden": (not over)},
                "players": players}


# ── レジストリ登録（ゲーム種類を増やす口＝ここに1行足すだけ）──────────────────
def register_rlcard_games():
    game.register("blackjack", lambda n: RLCardAdapter("blackjack", n, render=BlackjackRender()),
                  label="ブラックジャック", players=(1, 7))
    game.register("uno", lambda n: RLCardAdapter("uno", n),
                  label="UNO", players=(2, 2))           # RLCard の UNO は2人固定
    game.register("leduc", lambda n: RLCardAdapter("leduc-holdem", n),
                  label="レダックポーカー", players=(2, 4))
