# ADR-0018: リポジトリのレイアウトを src/・assets/・poc/・docs/ に分け、設定は root 維持

- ステータス: Accepted（実施済み・2026-06-29）
- 日付: 2026-06-29
- 関連: ADR-0010（アセット層の分離）, ADR-0014（取得先は config）, ADR-0016（CLAUDE.md が正本）, 原則4（取得先/アセットはコードに埋めず config）

## 背景 / 課題
直下に Python が 12 本（現行9＋PoC3）、巨大 PNG 7枚（Gemini 生成元・計 ~33MB・gitignore 済み）、設定/アセット/文書が混在し、**root が散らかって「どれが現行の中心か」が一目で分からなく**なってきた。整理したいが、設定/アセットの読み込みが **`__file__` 基準**（各モジュールが自分の隣を見る）なので、素朴にコードだけ移すと**サイレントにコード既定へフォールバックして壊れる**（`config.py`→`engawa.json` / `sources.py`→`topic_sources.json` / `views.py`→`sprite.json`＋隣の `chacha.png`）。

## 決定
1. **コードは `src/`** … 現行ランタイム9本（`engawa_main` / `acp` / `sources` / `scheduler` / `views` / `config` / `conversation` / `game` / `game_rlcard`）。フラット import のまま（`python src/engawa_main.py` で `src/` が `sys.path[0]` になり相互 import 維持）。
2. **実使用アセットは `assets/`** … `sprite.json` + `chacha.png`。Gemini 生成元の生 PNG は `assets/raw/`（gitignore 継続）。
3. **PoC基準点は `poc/`** … `engawa_p1/p2/p3_*.py`。**温存・触らない**（検証済みの戻り先）方針は不変（ADR/CLAUDE.md）。`legacy/` は別物（捨てた旧実装）として据え置き分離。
4. **文書は `docs/`** … `Backlog.md` / `TECH_RULES.md` / `engawa-acp-spec.md` / `adr/`。
5. **ユーザーが触る設定（`engawa.json` / `topic_sources.json`）と `CLAUDE.md` は root 維持。** 設定は日常編集対象で発見性を優先、`CLAUDE.md` は Claude Code / codex が**リポジトリ直下から自動で読む正本**のため動かせない。
6. **設定/アセットは `src/` から repo-root 基準で解決する。** `_path()` 系を `dirname(dirname(__file__))`（＝`src/` の親）基準へ変更。`engawa.json`/`topic_sources.json` は root、`sprite.json`+`chacha.png` は `assets/`。env つまみ（`ENGAWA_CONFIG` / `ENGAWA_TOPIC_CONFIG` / `ENGAWA_SPRITE_CONFIG`）の上書きは従来通り優先。

## 検討した代替案
- **設定/アセットも `src/` に同梱**（`_path()` 無改修）: コード変更ゼロだが、ユーザー設定が `src/` に埋もれ発見性が落ちる。却下（設定は root が筋・ADR-0014/原則4の config 主導と相性が悪い）。
- **設定を `config/` フォルダに集約**: root はさらに片付くが、`engawa.json` の docstring「リポジトリ直下」運用と env 運用の周知を変える必要。今回は見送り（root 維持を採用）。
- **`poc/` と `legacy/` を `archive/` に統合**: 退避物を一掃できるが、「検証済み基準点（戻り先）」と「捨てた旧実装」は**性質が違う**ので分離を維持。

## 影響 / 帰結
- 起動コマンドが `python engawa_main.py` → **`python src/engawa_main.py`**（リポジトリ直下から実行）。CLAUDE.md / Backlog / codex AGENTS.md の手順を追従更新。
- `tests/` の `sys.path` 挿入を repo-root → **`repo-root/src`** へ変更（`__init__.py`＋各 `test_*.py`）。テストは 121 件緑で移行前後の挙動同一を確認。
- 移動は `git mv` で**履歴を rename として保持**。巨大 PNG は未追跡のまま `.gitignore`/`.claudeignore` を `/assets/raw/` へ更新。
- `adr/NNNN` 形式の**番号引用は元々パスでない shorthand**なので本文ではそのまま温存。実ファイルへのナビ参照（`docs/adr/README.md` 等）と起動コマンドのみ更新。**ADR/Backlog の履歴本文は歴史記録として書き換えない。**

## 備考
- 検証: `python -m unittest discover -s tests -t .`（121 OK）／`py_compile src/*.py`／`engawa_main` import スモーク／`_path()` 実解決（engawa.json=root, topic_sources=root, sprite=assets, chacha.png 読込で dataUri 生成）を確認。
- GUI の見た目はこちらから描画確認不可（Chrome 拡張未接続）。ロジックはテスト＋スモークで担保し、最終の見た目はユーザー目視。
