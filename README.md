# Engawa（縁側）

**日本語** | [English](README.en.md)

[![CI](https://github.com/NiteC-a11y/Engawa/actions/workflows/ci.yml/badge.svg)](https://github.com/NiteC-a11y/Engawa/actions/workflows/ci.yml)

> デスクトップの隅に住む AI の住人「茶々（ちゃちゃ）」と過ごす常駐アプリ。育てるでも働かせるでもなく、ただ"居る"。

茶々は**会話アシスタントではありません**。縁側にただ住んでいて、時刻や天気にぽつりと反応し、話しかければ軽く応える。ときどき客人（Codex）が役を着せて訪ねてきて、茶々と世間話をしていく——そんな「**環境に反応する単体の住人＋双方向＋客人来訪**」を目指した個人的な実験プロジェクトです。

自分のマシンの **Claude / ChatGPT のサブスク認証**で動きます（個人利用・API 従量課金はしない設計）。

<p align="center">
  <img src="docs/images/engawa.png" width="300" alt="デスクトップ隅の縁側窓：夕暮れの縁側に座る三毛猫の茶々と、話しかけた会話ログ・入力欄"><br>
  <sub>デスクトップの隅にそっと出る縁側窓（夕暮れ）。背景は実時刻で朝昼夕夜と移ろい、夜は障子から灯りが漏れる。時刻・天気にぽつり反応し、話しかければ応える。客人がくれば、客人と話しをし始める。</sub>
</p>

---

## どう動くか

```
茶々（縁側の住人・一人称・関西寄り・長命セッション）
  ← 実環境イベント（時刻・大阪の天気）        … 自発のつぶやき（実天気が真実）
  ← 箱庭イベント＝アーク（雀 / 猫 / 風・起承転結）… 単調さ破り。実天気に従属
  ← ユーザーの話しかけ（通常テキスト）         … cancel 優先で割り込み
  ← 客人 Codex の来訪（/codex or 夕方の自発来訪）… 世間話に時節トピックを注入
```

- 全イベントは茶々の**同一の長命セッション**に流れ、文脈が地続きになります。
- 茶々の人格はアダプタに渡す `CLAUDE.md` で注入。客人の人格は召喚時にプロンプトへ動的注入。
- 人間が席を外している間は、茶々が“人間役の代打”で場をつなぎます（**予算つきで必ず終端＝有界**／[ADR-0025](docs/adr/0025-resident-fills-in-for-absent-human-bounded.md)）。AI 同士の無際限な自律会話には**戻さない**のが設計上の芯です。

---

## 必要なもの

| 種類 | 内容 |
|---|---|
| Python | 3.10+（開発は 3.13 で検証） |
| Node.js | `npx` 経由で ACP アダプタを起動（`@agentclientprotocol/claude-agent-acp` / `codex-acp`） |
| 認証（住人） | [Claude Code](https://claude.com/claude-code) にサブスク（Pro/Max）でログイン済み |
| 認証（客人） | Codex / ChatGPT にサブスクでログイン済み |
| 任意 | `pywebview`（隅の縁側窓 UI）、`rlcard`（`/game` の対戦AI） |

> API キーは使いません。子プロセスからは `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` を意図的に除去して、サブスク認証だけで動かします。

---

## セットアップ & 起動

```bash
# 1) 認証（先に済ませておく）
claude          # Claude Code にサブスクでログイン
#（Codex/ChatGPT 側も同様にログイン）

# 2) 個人設定を用意（任意・全キー任意＝消せばコード既定）
cp engawa.json.sample engawa.json        # Windows は copy

# 任意依存（必要な機能だけ）
pip install pywebview                     # 隅の縁側窓（ENGAWA_UI=web）に必要
pip install rlcard                        # /game の対戦相手 AI に必要

# 3) 起動
python src/engawa_main.py                # console（端末）
```

**隅の縁側窓（frameless の web UI）** で起動する場合（要 `pywebview`）:

```bat
:: Windows / cmd
set "ENGAWA_UI=web" && python src/engawa_main.py
```

Windows ではランチャの `.bat` も同梱しています:

- `engawa.bat` — 隅の縁側窓で日常起動
- `engawa-debug.bat` — デバッグログ（`engawa.log`）つき＋ログ追尾窓を別で開く

---

## 使い方

通常テキストは**茶々への話しかけ**、`/` から始まる入力は**縁側への操作**です。

| コマンド | 説明 |
|---|---|
| `/codex <人格>` | 客人（Codex）を召喚。**好きな人格を自由に**着せられる（例: `/codex 近所のご隠居`） |
| `/game <id> [見る]` | ミニゲーム（`blackjack` / `uno` / `leduc`）。「見る」で観戦。要 `pip install rlcard` |
| `/arc [雀\|猫\|風]` | 箱庭イベント（アーク）を再生（デバッグ用） |
| `/model` | 今のモデルを表示（住人 / 客人） |
| `/font [倍率\|save]` | web の文字サイズをアプリ内でライブ調整（`/font save` で永続化） |
| `/help` / `/quit` | ヘルプ / 終了 |

---

## 設定

挙動は `engawa.json`（個人設定・**gitignore 済み**）で調整します。優先順位は **環境変数（`ENGAWA_*`）> `engawa.json` > コード既定**。全キー任意で、欠損・破損はすべてコード既定へフォールバックします。

雛形と各項目の意味は [`engawa.json.sample`](engawa.json.sample) を参照（モデル / 来訪頻度 / 代打 / 間合い / トピック / ACP timeout / UI など）。客人の世間話トピックの取得先は [`topic_sources.json`](topic_sources.json) のホワイトリストで管理します。

主な環境変数の例:

```
ENGAWA_UI=web              隅の縁側窓で起動
ENGAWA_MODEL=opus          住人（茶々=Claude）のモデル
ENGAWA_CODEX_MODEL=...     客人（codex）のモデル
ENGAWA_RESIDENT_BACKEND=openai       茶々を Claude でなくローカル LLM(LM Studio/Ollama)で動かす（ADR-0026）
ENGAWA_GUEST_BACKEND=openai          客人もローカル LLM で（省略時は Codex＝要 ChatGPT ログイン）
ENGAWA_OPENAI_BASE_URL=http://localhost:1234/v1   上の OpenAI 互換 endpoint（既定=LM Studio）
ENGAWA_GUEST_PROB=0.1      自発来訪の確率
ENGAWA_DEBUG=1             engawa.log に主要ライフサイクルを記録
```

---

## セキュリティ・共有時の注意
- `ENGAWA_ACP_CMD` / `ENGAWA_CODEX_CMD` は**実行コマンド**、`ENGAWA_SCENE_BG`（`assets.scene_bg`）/ `ENGAWA_SPRITE_CONFIG`（`assets.sprite_config`）は**ローカルファイルのパス**を指定する口です。**第三者から渡された設定・`.bat`・画像/設定ファイルを、中身を確認せずに使わない**でください（信頼できる値だけ指定）。
- `/codex <人格>` の自由入力 persona は**信頼境界ではありません**。客人（Codex）は fs/terminal 無効・API キー除去済みで、ファイル操作や従量課金には直結しませんが、人格崩れ・不快な出力の余地は残ります。配布用途では既定 persona のアローリストを主にするのが安全です。
- `ENGAWA_DEBUG=1` の `engawa.log` には**あなたの入力本文が含まれ得ます**。Issue などに貼る前に中身を確認してください。
- OpenAI backend（`ENGAWA_RESIDENT_BACKEND`/`ENGAWA_GUEST_BACKEND=openai`）は**ローカル/自ホスト専用**です。非ローカル（クラウド等）の `ENGAWA_OPENAI_BASE_URL` は**既定でブロック**します（会話履歴の外部送信・従量課金の事故防止／原則「課金事故を出さない」）。意図的にクラウドの OpenAI 互換 API を使う場合のみ `ENGAWA_OPENAI_ALLOW_REMOTE=1` を明示してください。

## 構成

```
src/           アプリ本体（engawa_main / acp / sources / scheduler / views / prompts / conversation / game …）
assets/        茶々スプライト（sprite.json + chacha.png）
docs/adr/      設計判断と却下理由（ADR 0001〜0028）
docs/          TECH_RULES.md（技術仕様・境界）/ Backlog.md（タスク在庫）/ class-diagram.md
poc/           各フェーズの検証済み基準点（温存）
CLAUDE.md      現行全体像の正本（開発者向けの「住人の心得」）
```

より深い設計背景は次を参照:

- **[CLAUDE.md](CLAUDE.md)** — 全体像・原則・現況の正本
- **[docs/adr/](docs/adr/README.md)** — 設計判断と却下理由（なぜ AI 自律会話から方向転換したか＝ADR-0004、など）
- **[docs/TECH_RULES.md](docs/TECH_RULES.md)** — 技術仕様・規約・境界
- **[docs/Backlog.md](docs/Backlog.md)** — 残タスクの在庫

---

## ステータス

環境反応・双方向・客人来訪・ドット絵 UI の主要経路は実装済みで、召喚 / 自発来訪は実 Codex で E2E 検証済みです。個人的・実験的なプロジェクトのため、仕様は予告なく変わります。

茶々（現行スプライト）:

![茶々のスプライト](assets/chacha.png)

---

## ライセンス

[MIT License](LICENSE)（© 2026 NiteC-a11y）。個人プロジェクトですが、学習・改変・再配布はご自由にどうぞ。
