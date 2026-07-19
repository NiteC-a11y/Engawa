#!/usr/bin/env python3
"""game_controller.py — 対局の運用を Scheduler から切り出したコントローラ（ADR-0029 Phase 3）。

`GameSession`（ADR-0017 の Port&Adapter 核）を tick で1手ずつ進める運用ロジック（生成／AI
プレイヤー構築／表示／終了／お開き）を、Scheduler の God 化から剥がして凝集させる。
ロジックは Scheduler から **verbatim 移設**＝振る舞い不変。

Scheduler 状態への結び目は**注入で最小化**（Codex 第2R の釘刺し）:
- `preempt`（async）… 対局開始時の場払い＝room/アークを畳む＋user 活動記録＋喋り中なら cancel。
  Scheduler 状態（room/active_source/last_user_ts/speaking/resident）を触るので callback で渡す。
- `bump_beat`（sync）… 次 tick を active ペースへ（`_next_at` は Scheduler 所有＝散らさない）。
- `drive_lock` … Scheduler 所有のまま注入（所有権を急に移さない）。
- `resident_provider` … 住人 agent の現物を返す getter（Phase 4 で ResidentSessionManager 由来に）。
- `make_game` … アダプタ生成の差し替え口（テストが FakeGame を挿す・Scheduler._make_game プロパティ経由）。
"""
import random

from agent import AgentTimeoutError
import game
import prompts
import sources
import voice        # 住人表示名（対局プレイヤー名・en=Chacha・ADR-0022・7/19）


class GameController:
    def __init__(self, *, view, spawn_codex, resident_provider, drive_lock, preempt, bump_beat, make_game=None):
        self.view = view
        self._spawn_codex = spawn_codex
        self._resident_provider = resident_provider
        self._drive_lock = drive_lock
        self._preempt = preempt                      # async: 場を払う（room/arc 畳み＋user 活動＋cancel）
        self._bump_beat = bump_beat                  # sync: 次 tick を active ペースに（Scheduler の _next_at）
        self.make_game = make_game or game.make      # テストが差し替える口（Scheduler._make_game 経由）
        self._game = None                            # GameSession（対局中だけ・ADR-0017 Inc3）
        self._game_guests = []                       # ゲームのために召喚した客人(codex)＝終局で破棄
        self._game_render = None                     # ゲーム固有の表示器（adapter.render 由来）
        self._game_names = []                        # 各スロットの表示名（結果表示で使う）

    # ── 状態の問い合わせ（Scheduler が委譲判定に使う）──────────────────────
    @property
    def game(self):
        return self._game                            # GameSession（後方互換: Scheduler.game プロパティが返す）

    @property
    def active(self):
        return self._game is not None

    @property
    def over(self):
        return self._game is None or self._game.over

    # ── お開き（畳んで縁側へ戻す）─────────────────────────────
    async def _teardown(self, msg):
        """対局を畳んで縁側へ戻す共通処理（state クリア＋客人破棄＋観戦窓クローズ）。
        これを通さず view.game_close だけだと game が残り『ゲームモードのまま復帰不能』になる。"""
        if msg:
            self.view.system(msg)
        self._game = None
        self._game_render = None
        await self._cleanup_guests()
        try:
            self.view.game_close()                   # ×経由なら既に閉じてる＝no-op
        except Exception:
            pass

    async def _abort_on_timeout(self):
        """対局中に AI（客人/茶々）が無応答 → 席を立った扱いでお開き（盤面を勝手に進めない）。観戦窓も閉じる。"""
        await self._teardown("  （プレイヤーの返事が途切れた……席を立ったようや。お開きにしよ）")

    async def _abort_on_error(self):
        """対局中に想定外のエラー（adapter 死亡=ConnectionError・rlcard の不正状態 等）→ 盤を進めずお開き。
        これを通さず例外が tick を抜けると tick ループが死に、ゲームモードのまま永久停止する。"""
        await self._teardown("  （対局でなんぞ起きた……お開きにするわ）")

    async def abort_by_user(self):
        """ユーザーが対局を切り上げた（観戦窓×）→ お開きにして縁側へ戻す。対局中でなければ何もしない。"""
        if self._game is None:                       # 終局後の結果表示を×で閉じただけ＝畳むものは無い
            return
        await self._teardown("  （お開き。縁側に戻るわ）")

    # ── 生成・進行 ─────────────────────────────────────────
    def _known(self):
        """登録済みゲーム一覧（id→meta）。検証/一覧用。登録は composition root（engawa_main._build）で実施済み。"""
        return game.games()

    def _list(self, known, unknown=None):
        if unknown:
            self.view.system(f"  そんなゲーム（{unknown}）は知らんな。")
        avail = " / ".join(f"{k}（{v['label']}）" for k, v in known.items())
        self.view.system(f"  遊べるの: {avail}")
        self.view.system("  使い方: /game <id> [見る]（例: /game uno、/game blackjack 見る）")

    def _ai_decider(self, agent, name, slot):
        """AIプレイヤーの手番: 自分のスロット＋状態＋合法手を見せて手を選ばせる（不正は先頭へフォールバック）。"""
        async def decide(state, legal_moves):
            reply = await agent.prompt(prompts.game_move_prompt(name, slot, state, legal_moves))
            return game.parse_move(reply, legal_moves)
        return decide

    async def start(self, game_id, watch):
        """対局開始。会話/アークは畳む（preempt）。基本は 私＋茶々（観戦は茶々のみ）＝客人 codex は呼ばない（A）。
        ゲームが要求する人数に足りない時だけ客人で埋める。"""
        if self._game is not None:
            self.view.system("  もうゲーム中や。"); return
        known = self._known()                        # 検証＋対応人数(min,max)の取得
        meta = known.get(game_id)
        if meta is None:                             # 空/不明 id → 遊べる一覧を出す
            self._list(known, unknown=game_id or None); return
        lo, hi = meta["players"]
        want = min(hi, lo if watch else max(2, lo))  # 観戦=最少AIだけ / 参加=私+茶々(最低2)。min を下回らず max も超えない
        try:
            adapter = self.make_game(game_id, want)
        except ImportError:
            self.view.system("  （ゲームには rlcard が要る: pip install rlcard）"); return
        except Exception as e:
            self.view.system(f"  （ゲームを始められなんだ: {e}）"); return
        await self._preempt()                        # 会話/来訪/アークを畳む＋user 活動記録＋喋り中なら cancel（Scheduler 状態）
        async with self._drive_lock:
            n = adapter.num_players
            players = []
            if not watch:
                players.append(game.Player("私"))    # 人間（観戦時は入れない＝全AI）。slot 0
            name = voice.resident_name()
            players.append(game.Player(name, self._ai_decider(self._resident_provider(), name, len(players))))
            self._game_guests = []
            while len(players) < n:                  # 人数が足りないゲームの時だけ客人(codex)で埋める（blackjack では起きない）
                persona = random.choice(sources.GUEST_PERSONAS)
                try:
                    agent = await self._spawn_codex()
                except Exception:
                    self.view.system("  （客人が来られず中止）")
                    await self._cleanup_guests(); return
                self._game_guests.append(agent)
                slot = len(players)                  # 追加位置＝RLCard の player id（自分の手札参照に使う）
                players.append(game.Player(f"客人〔{persona}〕", self._ai_decider(agent, persona, slot)))
            players = players[:n]                     # 念のためスロット数に合わせる
            names = [p.name for p in players]
            render = adapter.render                    # ゲーム固有の表示器（blackjack は札と勝敗を整形）
            self._game_render = render
            self._game_names = names
            label = game.games().get(game_id, {}).get("label", game_id)
            who = "観戦＝茶々がディーラーと勝負" if watch else "あなたも参加"
            self.view.system(f"  〔{label}〕開始（{who}・{n}人）")

            def on_move(name, move, ad, slot):
                try:                                  # 表示が転けてもゲーム進行は止めない
                    lines = [render.move(name, move, ad, slot)] if render is not None else [f"  {name} → {move}"]
                    cur = ad.current_player() if not ad.is_over() else None
                    self._emit(ad, lines, current_slot=cur, over=ad.is_over())
                except Exception:
                    self.view.system(f"  {name} → {move}")
            self._game = game.GameSession(adapter, players, on_move=on_move)
            self.view.game_open(label)                # web は隣に観戦窓を開く（console は何もしない）
            self._game.begin()
            deal_lines = render.deal(adapter, names) if render is not None else []
            self._emit(adapter, deal_lines, current_slot=adapter.current_player(), over=False)
            self._bump_beat()                         # 次 tick を active ペースへ（Scheduler の _next_at）
            if self._game.over:
                await self._end()
            elif self._game.waiting_for_human:
                self._show_human_turn()

    async def on_tick(self):
        """対局中の tick: AIの番なら1手進める（人間の番/終局は待つ）。無応答/エラーはお開き。"""
        try:
            if self._game.over:
                await self._end()
            elif not self._game.waiting_for_human:
                await self._game.step()               # AI が1手（ペースは tick 間隔）
                if self._game is not None and self._game.over:
                    await self._end()
                elif self._game is not None and self._game.waiting_for_human:
                    self._show_human_turn()
        except AgentTimeoutError:                    # AI(客人/茶々)が無応答 → 席を立った扱いでお開き
            await self._abort_on_timeout()
        except Exception:                            # adapter 死亡/不正状態 等 → 盤を進めずお開き（tick ループを殺さない）
            await self._abort_on_error()

    async def on_user_input(self, line):
        """対局中の入力＝手。処理したら True（Scheduler は以降の通常入力へ落とさない）。非対局/終局は False。"""
        if self._game is None or self._game.over:
            return False
        if not self._game.waiting_for_human:
            self.view.system("  （今は他のプレイヤーの番。待ってな）")
            return True
        cur = self._game.adapter.current_player()
        legal = self._game.adapter.legal_moves(cur)
        move = game.parse_move(line, legal)
        if move is None:
            self.view.system(f"  （その手は出せん。打てる手: {' / '.join(map(str, legal))}）")
            return True
        await self._game.human_move(move)
        self.view.system(f"  私 → {move}")
        if self._game.over:
            await self._end()
        return True

    def _emit(self, adapter, lines, current_slot=None, over=False):
        """局面更新を View へ（snapshot=観戦窓の描画用 / lines=console の文字表現）。"""
        snap = None
        render = self._game_render
        if render is not None and hasattr(render, "snapshot"):
            snap = render.snapshot(adapter, self._game_names, current_slot, over)
        self.view.game_update(snap, lines)

    def _show_human_turn(self):
        cur = self._game.adapter.current_player()
        legal = self._game.adapter.legal_moves(cur)
        render = self._game_render
        if render is not None:
            self.view.system(render.turn(self._game.adapter, cur, "あなた"))
        else:
            self.view.system(f"  あなたの番｜{prompts.describe_state(self._game.adapter.state(cur))}")
        self.view.system(f"  打てる手: {' / '.join(map(str, legal))}  （そのまま打って）")
        self._emit(self._game.adapter, [], current_slot=cur, over=False)   # 観戦窓に自分の番を反映

    async def _end(self):
        g = self._game
        self._game = None
        if g is not None:
            names = self._game_names or [p.name for p in g.players]
            render = self._game_render
            if render is not None:                    # 結果（ディーラー公開＋各自の勝敗）
                lines = render.result(g.adapter, names)
                snap = render.snapshot(g.adapter, names, None, True) if hasattr(render, "snapshot") else None
            else:
                lines = ["  〔結果〕 " + " / ".join(f"{nm}: {r}" for nm, r in zip(names, g.adapter.result()))]
                snap = None
            self.view.game_update(snap, lines)        # 結果（ディーラー公開＋勝敗）を出す。**窓は閉じない**
        self._game_render = None                      # 閉じるのはユーザーの×か、アプリ終了時の close だけ
        await self._cleanup_guests()

    async def _cleanup_guests(self):
        for agent in self._game_guests:              # ゲームの客人(codex)を破棄（使い捨て）
            try:
                await agent.close()
            except Exception:
                pass
        self._game_guests = []

    async def close(self):
        """アプリ終了時: 対局中に終了した時の客人(codex)を刈り、観戦窓も閉じる（shutdown teardown）。"""
        await self._cleanup_guests()
        try:
            self.view.game_close()
        except Exception:
            pass
