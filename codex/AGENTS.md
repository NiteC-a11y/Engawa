# Engawa Review Instructions

このディレクトリは `C:\dev\Engawa` のレビュー成果物置き場。

## 依頼の前提

- レビュー対象の成果物は `C:\dev\Engawa` 直下にある。
- ソースコードを正本として扱う。
- ドキュメントレビューを先に行い、その後にソースコードレビューを行う。
- レビュー結果は Markdown で `C:\dev\Engawa\codex` 配下に出力する。

## 現行ソースの中心

まず以下を正本として読む。

- `C:\dev\Engawa\engawa_main.py`
- `C:\dev\Engawa\acp.py`
- `C:\dev\Engawa\scheduler.py`
- `C:\dev\Engawa\sources.py`
- `C:\dev\Engawa\views.py`
- `C:\dev\Engawa\config.py`
- `C:\dev\Engawa\engawa.json`
- `C:\dev\Engawa\sprite.json`
- `C:\dev\Engawa\topic_sources.json`

`engawa_p1_acp_poc.py`, `engawa_p2_ambient.py`, `engawa_p3_interactive.py`, `legacy\app.py` は現行の中心ではなく、基準点または旧実装として扱う。

## ドキュメントレビュー対象

以下をソースコードと照合する。

- `C:\dev\Engawa\CLAUDE.md`
- `C:\dev\Engawa\TECH_RULES.md`
- `C:\dev\Engawa\Backlog.md`
- `C:\dev\Engawa\engawa-acp-spec.md`
- `C:\dev\Engawa\adr\README.md`
- `C:\dev\Engawa\adr\*.md`

`CLAUDE.md` は現行全体像の正本。`engawa-acp-spec.md` は ADR-0016 により旧構想として降格済みなので、現行仕様として扱わない。

## レビュー観点

ドキュメントレビューでは次を優先する。

- ソースコードと矛盾している記述
- 未実装なのに実装済みに見える記述
- 正本や参照先の混乱
- 旧構想と現行仕様の境界が曖昧な箇所
- アセット、設定、環境変数、起動手順の不一致

ソースコードレビューでは次を優先する。

- ハング、リソースリーク、プロセス終了漏れ
- async / thread / pywebview 境界の競合
- ACP JSON-RPC の pending request、cancel、permission 応答の堅牢性
- 設定値の型や範囲検証
- プロンプトインジェクション面と権限境界
- テスト不在または回帰検知不能な箇所

## 最低限の確認

可能ならファイル一覧を確認する。

```powershell
rg --files C:\dev\Engawa
```

主要 Python ファイルは pycache を作らない形で構文確認する。

```powershell
python -B -c "import pathlib; files=['acp.py','config.py','engawa_main.py','scheduler.py','sources.py','views.py']; root=pathlib.Path(r'C:\dev\Engawa'); [compile((root/f).read_text(encoding='utf-8'), str(root/f), 'exec') for f in files]; print('OK', len(files))"
```

スプライト枚数を確認する場合は PNG IHDR と `sprite.json` の `frame_w` / `frame_h` を照合する。

## 出力形式

`C:\dev\Engawa\codex\review-YYYY-MM-DD.md` のような名前で出力する。

構成は原則として以下。

1. 前提
2. ドキュメントレビュー
3. ソースコードレビュー
4. 良い点
5. 確認したこと

指摘は重大度順に並べ、可能な限り `file:line` 形式で根拠を付ける。
