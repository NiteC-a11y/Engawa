---
description: 縁側の現状を調査して短く要約する（git・テスト・進行中の仕事）
argument-hint: "[docs] などの追加観点（任意）"
---

いま Engawa がどういう状態かを調べて、短く報告して。SessionStart フックの git snapshot を手動で・もう一歩踏み込んで再現するイメージ。

調べること:
1. **git** — `git status -sb` / 直近5コミット / 未 push（`git log @{u}..HEAD`）/ 作業ツリーの差分概要。
2. **テストの緑赤** — `python -m unittest discover -s tests -t . -q` を走らせて PASS/FAIL 件数。
3. **進行中の仕事** — 未 push コミットや差分から「いま何を触っているか」を推定。必要なら `docs/Backlog.md` の頭を覗いて次の一手を確認。
4. **リリース候補** — 最終タグ（`git describe --tags --abbrev=0`）以降の code コミット（`git log <タグ>..HEAD --oneline -- src/`）を数える。溜まっていたら「v*.*.* 切る？」を次の一手に含める（exe は tag push でしか焼かれない＝`release.yml`・docs のみの変更は対象外）。

追加引数 `$ARGUMENTS` があればその観点も見る（例: `docs` ならコード差分と CLAUDE.md/TECH_RULES/Backlog の齟齬を点検＝原則7）。

報告は**短く**。箇条書きで「現状／気になる点／次の一手」の3点に収める。長文の説明はいらない、縁側の住人に現状を耳打ちするくらいの温度で。
