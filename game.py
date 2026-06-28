#!/usr/bin/env python3
"""game.py — ゲームの「ポート＋セッション」（Port & Adapter / Inc1・ライブ未接続）。

「既にあるゲームに AI が参加する」を、特定 framework（RLCard 等）に密結合せずに実現するための核。
- **GameAdapter（ポート）**: 1ゲームの実体を包む抽象。RLCard/PettingZoo/自作 をこの形に合わせる。
  実装（例 RLCardAdapter）は別モジュールに置き、ここ(core)は framework を一切 import しない＝差し替え/テスト可能。
- **Player**: スロットの担い手。人間 or AI（AI は「状態＋合法手を見て手を選ぶ」注入カラブル＝Strategy/DI）。
- **GameSession**: 順番回し。**プレイヤー人数に非依存**（players のリスト長で決まる）＝私＋茶々＋客人(可変)で埋められる。
  AI のターンは自動進行、人間のターンで止まって入力待ち（cancel優先・有界の思想は会話の Room と同じ）。
- **レジストリ**: `register(id, factory, …)` でゲーム種類を後から増やせる（アダプタを足すだけ）。

RLCard は本ファイルに出てこない（アダプタ側に閉じ込める）。
"""


class GameError(Exception):
    pass


# ── ポート（抽象）。framework 非依存。実装が RLCard 等をこの形に合わせる ──────────
class GameAdapter:
    """1ゲームの最小インターフェース。state/legal_moves は「人間(=LLM)が読める」形で返すこと。"""
    num_players = 0

    def reset(self):
        raise NotImplementedError

    def current_player(self):
        """今が誰のターンか（スロット index）。"""
        raise NotImplementedError

    def is_over(self):
        raise NotImplementedError

    def legal_moves(self, player):
        """その時点の合法手（選べる手の文字列リスト）。"""
        raise NotImplementedError

    def state(self, player):
        """player から見た読める状態（手札・場 等の dict）。プロンプト/表示にそのまま使える形。"""
        raise NotImplementedError

    def play(self, move):
        """現プレイヤーの手 move を適用して局面を進める。"""
        raise NotImplementedError

    def result(self):
        """終局時の各スロットの結果（payoffs 等のリスト）。"""
        raise NotImplementedError


# ── プレイヤー（人間 or AI）。AI は決定を注入（Strategy/DI）──────────────────────
class Player:
    """name=表示/話者タグ。decide=async (state, legal_moves)->move（AI用）。None なら人間（UI から入力）。"""
    def __init__(self, name, decide=None):
        self.name = name
        self._decide = decide

    @property
    def is_human(self):
        return self._decide is None

    async def choose(self, state, legal_moves):
        return await self._decide(state, legal_moves)


# ── セッション（順番回し）。GameAdapter と Player と表示フックにだけ依存 ──────────
class GameSession:
    def __init__(self, adapter, players, on_move=None, on_over=None):
        if len(players) != adapter.num_players:
            raise GameError(f"players({len(players)}) != num_players({adapter.num_players})")
        self.adapter = adapter
        self.players = players                       # index = スロット
        self._on_move = on_move or (lambda name, move, state: None)
        self._on_over = on_over or (lambda result: None)

    @property
    def over(self):
        return self.adapter.is_over()

    @property
    def current(self):
        return self.players[self.adapter.current_player()]

    async def begin(self):
        """開始。最初の人間ターンまで（or 終局まで）AI を自動で進める。"""
        self.adapter.reset()
        await self._run_ai()

    async def human_move(self, move):
        """人間の手。現プレイヤーが人間で合法な時だけ適用し、続けて AI を進める。"""
        if self.over:
            return
        cur = self.adapter.current_player()
        if not self.players[cur].is_human:
            return
        if move not in self.adapter.legal_moves(cur):   # UI は合法手だけ出す前提・保険
            return
        self._apply(cur, move)
        await self._run_ai()

    async def _run_ai(self):
        """現プレイヤーが AI の間、自動で打つ。人間のターン or 終局で止まる。"""
        while not self.over and not self.current.is_human:
            cur = self.adapter.current_player()
            legal = self.adapter.legal_moves(cur)
            move = await self.players[cur].choose(self.adapter.state(cur), legal)
            if move not in legal:                       # 不正手は合法手の先頭へフォールバック（堅牢化）
                move = legal[0]
            self._apply(cur, move)
        if self.over:
            self._on_over(self.adapter.result())

    def _apply(self, slot, move):
        name = self.players[slot].name
        self.adapter.play(move)
        self._on_move(name, move, self.adapter)


# ── レジストリ（ゲーム種類を後から増やす口）──────────────────────────────────
_REGISTRY = {}


def register(game_id, factory, *, label="", players=(2, 2)):
    """ゲームを登録。factory(num_players)->GameAdapter。players=(min,max) は対応人数。"""
    _REGISTRY[game_id] = {"factory": factory, "label": label or game_id, "players": tuple(players)}


def make(game_id, num_players):
    if game_id not in _REGISTRY:
        raise KeyError(f"未登録のゲーム: {game_id}（登録済み: {list(_REGISTRY)}）")
    return _REGISTRY[game_id]["factory"](num_players)


def games():
    return dict(_REGISTRY)
