# ADR-0023: ソース修正はテスト同梱・走らせて緑が必須（Stop フックで強制）

- ステータス: Accepted（実装 2026-06-30）
- 日付: 2026-06-30
- 関連: ADR-0013（event-source/Scheduler・YAGNI＋テスト容易性）, ADR-0018（unittest スイート・repo 構成）, ADR-0019（headless テストの維持・GUI 目視とロジック自動の分担）, ADR-0020（config 主導）

## 背景 / 課題

これまでソース修正時にテストを後回しにする場面があり、ユーザーが「テスト無しで直すと、後で別の箇所を変えた時に壊したことに気づけない＝**回帰検知が効かない**」と指摘し、「**テスト実装とテスト実行を必須にして欲しい**」と要請（2026-06-30）。memory／原則の明文化だけでは「忘れたら破れる」ため、harness による**機械的な強制**が要る。

## 決定

1. **ソース修正にはテストを同梱**し、`python -m unittest discover -s tests -t .`（stdlib unittest・GUI/ネット不要）を走らせて**全 PASS を確認してから「完了」とする**（CLAUDE.md 原則6 / TECH_RULES §9）。
2. **テスト困難な GUI/外部依存は、判断ロジックを純関数に切り出してユニット化**する（例: `engawa_main._web_window_kwargs`/`_ui_config`、`views.build_web_html`）。GUI の見た目自体はユーザー目視（ADR-0018/0019 の「目視＝人／ロジック＝自動」分担を維持）。
3. **harness で強制**：`.claude/settings.json`（**project スコープ・committed**）の **Stop フック**が、`src/`・`tests/` に未コミット変更がある時だけテストを実行し、**赤なら exit 2 で完了をブロック**（緑／変更なしは何もしない・~0.5s）。`shell: bash`・`jq` 非依存（当環境に jq 無し）。

## 検討した代替案

- **memory／原則だけ（強制なし）**: エージェントが忘れれば破れる＝「必須」にならない。harness フックで底を作る。
- **PostToolUse（Write|Edit 毎）でテスト実行**: 連続編集の途中で何度も走り、TDD 途中の赤で煩雑。**Stop（ターン終了時・変更がある時だけ）1回**に絞って無駄打ちを避ける。
- **警告のみ（exit 0 ＋ systemMessage）**: 見落とせる。「必須」要請に対し **ブロック（exit 2）** で強制する。
- **local スコープ（`settings.local.json`・gitignore）**: 共有されない。CLAUDE.md 原則6（committed）と揃え **project `settings.json`（committed）** に置く。
- **CI／pre-commit hook**: 個人常駐アプリに CI 基盤は過剰。手元 Stop フック＋手動 commit で足りる（YAGNI・ADR-0013）。配布・チーム化したら昇格を検討（その時は新 ADR）。

## 影響 / 帰結

- src/tests を触ったターンの終わりに自動でテスト＝回帰を即検知。**緑にするまで「完了」と言えない**。
- **純関数抽出の習慣**がつく（GUI 配線もユニット可能に）＝テスト容易性が上がり、ADR-0013 の seam 思想と整合。
- フックは `/hooks` で確認・無効化可。新規 `.claude/settings.json` はセッション開始時に無ければ、設定ウォッチャが拾うまで `/hooks` を一度開く or 再起動が要る場合あり。
- スイートは現状 **159 PASS** を基準線とする（ADR-0018 から継続）。

## 備考

- 「**GUI 目視はユーザー／ロジックは自動テスト**」の分担（ADR-0018/0019）は不変。フックはロジック側だけを守る。
- 配布・チーム化時は CI（GitHub Actions 等）へ昇格を検討（新 ADR で）。
