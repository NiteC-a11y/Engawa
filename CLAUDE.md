# CLAUDE.md — Engawa（縁側）

> このファイルは Claude Code が最初に読む「住人の心得」。**何を作っているか・どう動くか・何を守るか・次に何をやるか**を最短で掴むためのもの。
> 設計判断の経緯は **docs/adr/**、技術仕様・境界は **docs/TECH_RULES.md**、動くタスクの在庫は **docs/Backlog.md**。迷ったらそっちへ。
> ※このファイルが**現行全体像の正本**（adr/0016）。`docs/engawa-acp-spec.md` は旧構想（ピボット前・歴史的参照）。

---

## これは何か

**Engawa（縁側）** は、デスクトップの隅に住む AI の住人「茶々（ちゃちゃ）」と過ごす、たまごっち的な常駐アプリ。

- 茶々は**会話アシスタントではない**。縁側に住んでいて、時刻や天気にぽつりと反応し、話しかければ応える。
- 時々、客人（Codex）が役を着せて訪ねてくる（召喚＝`/codex`、または夕方に自発来訪）。
- 自分のマシンの Claude / ChatGPT **サブスク認証**で動く（個人利用・従量課金しない）。

ひとことで言うと「**環境に反応する単体の住人＋双方向＋客人来訪**」。
当初の「AI同士が自律会話するアプリ」からは**意図的に方向転換した**（理由は adr/0004）。

---

## いま動いているもの（現況・2026-06-28）

| Phase | 内容 | 状態 |
|---|---|---|
| P1 | ACP往復＋CLAUDE.md人格注入 | ✅ 実証済み |
| P2 | 長命セッション＋環境イベント(時刻・天気)で茶々がつぶやく | ✅ 実機成功 |
| P3 | 話しかけ＋割り込み(cancel優先)で双方向化 | ✅ 実機検証済み |
| P3.5 | 箱庭イベント＝アーク（雀/猫/風・起承転結・実天気従属） | ✅ 実装・検証済み |
| P4 | 客人来訪（/codex 召喚＋自発来訪＋世間話に時節トピック注入） | ✅ 召喚/自発とも**実 codex E2E 検証済み** |
| P5 | ドット絵UI（隅の縁側窓・frameless・スプライト・来訪演出） | ✅ Inc1〜4 実装。茶々は Gemini 生成の三毛猫(8コマ)＋自作差分2＝**現行10コマ**(`chacha.png` 800×64)搭載。**GUI 見た目はユーザー目視で確認** |

→ 当初スコープ「環境に反応する単体の住人＋双方向＋客人来訪＋ドット絵UI」は**一通り実装・実機で稼働**。残りは磨きと新章（次にやること参照）。

### ファイル（レイアウト・adr/0018）
> コードは `src/`・実使用アセットは `assets/`・PoC基準点は `poc/`・文書は `docs/`。ユーザーが触る設定（`engawa.json` / `topic_sources.json`）と本 `CLAUDE.md` は **root 維持**。設定/アセットは `src/` から **repo-root 基準**で解決（`config.py` / `sources.py` / `views.py` の `_path()`）。
- `src/engawa_main.py` — 起動口（composition root）。console / `ENGAWA_UI=web` で隅の縁側窓。
- `src/acp.py` / `src/sources.py` / `src/scheduler.py` / `src/views.py` — 現行構成（event-source/scheduler・adr/0013）。`views.py` に `ConsoleView` と `WebView`（pywebview・poll方式）。
- `src/conversation.py` — 3人会話の部屋（State パターン・adr/0015 **Inc1/Inc2 実装済み**）。`src/game.py` + `src/game_rlcard.py` — ゲームの Port＆Adapter（adr/0017。rlcard は `game_rlcard` に隔離・任意依存）。
- `assets/sprite.json` + `assets/chacha.png` — 茶々スプライト（差し替え可能な皮・adr/0010）。今は Gemini 三毛猫ベース10コマ（口パク/まばたき/にっこり/耳ピンは0基準の自作差分）。`assets/raw/` は Gemini 生成元 PNG（gitignore・現行は chacha.png を使用）。
- `topic_sources.json`（root） — 客人の世間話トピックの取得先ホワイトリスト（config主導・adr/0014）。
- `engawa.json`（root・**個人設定＝gitignore**／雛形は `engawa.json.sample`） + `src/config.py` — アプリ挙動の設定（model/guest/間合い/topic）。優先順位 **env(ENGAWA_*) > engawa.json > 既定**。キーは入れない(adr/0002)。`.env`/`.env.example` と同じ流儀（端末ごとに調整・全キー任意＝消せばコード既定）。
- `poc/engawa_p1/p2/p3_*.py` — 各フェーズの検証済み基準点。**温存・触らない**。
- `docs/adr/`（0001〜0018）, `docs/TECH_RULES.md`, `docs/Backlog.md`
- `docs/engawa-acp-spec.md` — ピボット前の**旧構想 仕様書 v1**（adr/0004 で転換・adr/0016 で降格）。歴史的参照として温存・**現行仕様ではない**。
- `legacy/app.py` — 方向転換前の**旧実装**（adr/0004 で捨てた「AI雑談ルーム」・API直叩き）。退避済み・現行と無関係・**参照しない**。

---

## 起動

- **console（端末）**: `python src/engawa_main.py`（リポジトリ直下から実行）
- **web（隅の縁側窓・frameless）**: cmd で `set "ENGAWA_UI=web" && python src/engawa_main.py`（`$env:` は PowerShell 専用・cmd は `set`・空白混入回避でクォート）
- **認証**: 先に `claude` と codex(ChatGPT) にサブスクでログイン。API キーは子 env から除去（adr/0002）。
- **主な env つまみ**: `ENGAWA_UI=web` / `ENGAWA_MODEL`（茶々=Claude のモデル・例 `opus`/`claude-opus-4-8`/`opus[1m]`）,`ENGAWA_CODEX_MODEL`（客人=codex のモデル）/ `ENGAWA_GUEST_PROB`,`ENGAWA_GUEST_FROM_HOUR`（自発来訪）/ `ENGAWA_TOPIC_PROB`,`ENGAWA_TOPIC_REFRESH_MIN`,`ENGAWA_TOPIC_CONFIG`（トピック）/ `ENGAWA_UI_CORNER`,`ENGAWA_UI_EASYDRAG`（窓）/ `ENGAWA_SPRITE_CONFIG`（スプライト）/ `ENGAWA_TICK_MIN/MAX`,`ENGAWA_ARC_PROB`（間合い）。**これらは `engawa.json` にも書ける＝永続（env が優先・adr原則4のconfig主導）**
  - モデル指定の仕組み: 住人は子 env の `ANTHROPIC_MODEL`（Claude Code が尊重）、客人は `CODEX_CONFIG`（codex-acp が Codex 設定へマージ）に載せる。**未指定はアダプタ既定のまま（現状の挙動を変えない）**。サブスク認証でも有効。
- **スラッシュ**: `/codex <人格>`（客人召喚）/ `/blackjack [見る]`（茶々とブラックジャック・私+茶々／「見る」で茶々がディーラーと・客人 codex は基本不要・要 `pip install rlcard`・ADR-0017）/ `/arc [雀|猫|風]`（箱庭再生・デバッグ）/ `/model`（今のモデル表示・住人/客人）/ `/help` / `/quit`

---

## どう動くか（1枚で）

```
茶々（縁側の住人・1人称・関西寄り・長命セッション）
  ← 実環境イベント（時刻・大阪の天気）        … 自発のつぶやき（実天気が真実・adr/0012）
  ← 箱庭イベント＝アーク（雀/猫/風・起承転結）  … 単調さ破り。実天気に従属（adr/0011,0012）
  ← ユーザーの話しかけ（通常テキスト）         … cancel優先で割り込み（アーク中でも背景継続）
  ← 客人 Codex の来訪（/codex or 自発）        … 世間話に時節トピックを注入（adr/0008,0011,0014）

入力2系統:  通常テキスト → 茶々への話しかけ / スラッシュ → 縁側への操作
出力:  View ポート（ConsoleView / WebView）。web は茶々スプライトが state→コマ＋呼吸でアニメ、
        客人来訪は「庭先の気配＋茶々の反応」で演出（adr/0009,0010 / Inc4）。
```

- 茶々の人格は **cwd の CLAUDE.md**（アダプタに渡す方・このファイルとは別物）で注入。客人の人格は CLAUDE.md でなく**召喚時に prompt へ動的注入**（adr/0008）。
- 全イベントは最終的に茶々の **同一の長命セッション** に流れ、文脈が地続きになる。

---

## 守ること（原則）

1. **課金事故を出さない。** 子プロセスから API キー（`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`）を必ず除去。サブスク認証で動かす（adr/0002）。
2. **茶々を「住人」から外さない。** コーディング助手化・過剰な長文・毎ターン律儀な名言は人格破壊。「黙っていい・短くていい」。
3. **AI同士の*自律・無際限*会話に戻さない。** 客人は環境イベント＝来訪（常駐させない・滞在は有界・adr/0008）。※「人間アンカーで有界な3人会話」は **adr/0015 で Inc1/Inc2 実装済み**（部屋＝State パターンで必ず人間待ちへ戻る・自律往復は依然禁止）。
4. **設計判断を勝手に覆さない。** adr/ に却下理由付きで残る。変えるなら新 ADR（Superseded で旧を残す）。取得先/アセットはコードに埋めず config（`topic_sources.json` / `sprite.json`）。
5. **LLM/ツール仕様は思い込みで書かず、都度確認する。** ACPのcapabilityは initialize 応答を読んで分岐。

---

## いま茶々（人格側 CLAUDE.md）に書いてある定義

> アダプタの cwd に置く CLAUDE.md。Engawa リポジトリの本ファイルとは役割が別。

- 縁側に住む一人格「茶々」。コーディング/ツールはしない。
- くだけた関西寄り、基本は短く独り言。話しかけられたら軽く応える。
- 何も言いたくない時は「……」で流してよい。毎回気の利いたことを言おうとしない。
- 改行や空行で段落分けせず、ひと続きの短い独り言で。AIだと前置きせず茶々として過ごす。

---

## 次にやること（残り・詳細は Backlog.md / adr）

P1〜P5 は実装・主要経路は実 codex/resident E2E 済み。残りは磨きと新章:

- **【大物・adr/0015】3人会話**: 客人(visitor)に *人間アンカーで有界な* 3人会話（私↔茶々／私↔客人／茶々↔客人／3人 の全組合せ）。「部屋」方式・宛先で応答者が決まる。**Inc1/Inc2 実装済み**（`src/conversation.py`＝State パターン／Scheduler 結線・`docs/Backlog.md:37-38`）。**残り Inc3＝cancel優先の部屋内統合・茶々の room ストリーミング・実 codex の3人会話 E2E（実機）**。**最難関＝ターン管理**（連続AIターン上限で自律往復に戻さない）。
- **【仕上げ】茶々スプライト微調整**: Gemini 三毛猫(8コマ・座布団なし)＋自作差分2＝**現行10コマ**。AI 故にコマ間が不連続なので、今は各 state **単一コマ＋CSS呼吸**で運用（ぴくつき回避）。まばたきは「コマ0の目だけ閉じ版」を自作すれば滑らかに足せる。生成→抜き手順は Backlog 参照。
- **【構想】背景の時間帯バージョン**: Gemini で朝/昼/夕/夜の和室を作り、`tod` で切替・茶々を上に合成（環境反応の核と地続き）。
- **【トピック】やわらかRSS の実 URL 精査**（tenki.jp サプリ等・今は時節 local のみ稼働で十分弾む）。
- **【技術的負債】** node 取り残し刈り / SQLite 永続化 / 天気座標の大阪固定→設定化 / 茶々用 CLAUDE.md の別ディレクトリ運用 / 体感ナレーション層。
- **【運用】GUI の見た目はユーザー目視で確認**（Chrome 拡張未接続でこちらから描画確認不可・ロジックは `node --check`＋ユニットで担保）。

**既知の宿題（Open Questions）**: 長命セッションの compaction/fork 閾値 ／ `/codex <自由テキスト>` のプロンプトインジェクション（配布時のみ・検討メモは Backlog）／ 客人の作り込み度 ／「茶々が反応しない（……）」の UI 表現。

---

## 参照
- 設計判断と却下理由 → **docs/adr/README.md**（0001〜0018）
- 動くタスクの在庫 → **docs/Backlog.md**
- 技術仕様・規約・境界 → **docs/TECH_RULES.md**（旧構想は `docs/engawa-acp-spec.md`・adr/0016 で降格）
