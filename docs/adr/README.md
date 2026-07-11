# Engawa — Architecture Decision Records

縁側(Engawa)の設計判断ログ。1判断=1ファイル。CLAUDE.md（現行全体像の正本・adr/0016）と対で読む。

| ADR | タイトル | ステータス |
|---|---|---|
| 0001 | ローカルagent駆動は MCP でなく ACP を使う | Accepted |
| 0002 | サブスク認証を継承し、APIキーは使わない（各自 BYO サブスク） | Accepted（制約付き） |
| 0003 | 住人の人格は cwd の CLAUDE.md で注入する | Accepted（P1実証） |
| 0004 | 環境反応型の単体住人にする（マルチエージェント会話からの転換） | Accepted |
| 0005 | 住人のセッションは長命にする | Accepted（P2実証） |
| 0006 | ユーザー割り込みは cancel 優先 | Accepted（P3実装） |
| 0007 | 入力を2系統に分ける（話しかけ / スラッシュ操作） | Accepted |
| 0008 | Codex は「役を着せて呼ぶ客人」にする | Accepted（一方向割り切りは0015で見直し・有界/非常駐の核は維持） |
| 0009 | 不透明な隅ウィンドウにする（透過しない） | Accepted |
| 0010 | ドット絵は差し替え可能なアセット層として分離する | Accepted |
| 0011 | イベントは「アーク（1〜Nフェーズ）」で表す（単発・箱庭・来訪を統一） | Accepted（実装済み・P3.5） |
| 0012 | 天気は実天気が真実・箱庭は従属（状態は実天気/手触りは箱庭） | Accepted |
| 0013 | イベント源/スケジューラのアーキテクチャ（ADR-0011 の実装構造） | Accepted（実装済み） |
| 0014 | 客人の世間話に外部トピックを注入（取得先はホワイトリスト） | Accepted（実装） |
| 0015 | 客人(visitor)に人間アンカーで有界な3人会話を解禁（環境イベントに相互作用モード） | Accepted（Inc1/Inc2 実装済み・Inc3/実 codex E2E は残） |
| 0016 | ドキュメントの正本を CLAUDE.md に定め、spec v1 を旧構想として降格 | Accepted |
| 0017 | ゲームは Game ポート＋RLCard アダプタで受け、AI が既存ゲームに参加（初の任意外部依存 rlcard） | Accepted（実装中） |
| 0018 | リポジトリを src/・assets/・poc/・docs/ に整理（設定と CLAUDE.md は root 維持） | Accepted（実施済み） |
| 0019 | presentation は「意味 state ＋ config 駆動の差し替えアセット」で統一（sprite/sound/scene 共通） | Accepted（方針確定・実装これから） |
| 0020 | モデルは config で選ぶ（住人=ANTHROPIC_MODEL / 客人=CODEX_CONFIG・未指定はアダプタ既定） | Accepted（実装） |
| 0021 | ACP は用途別 timeout ＋ 役割別の段階回復で守る（adapter 無応答で永久待ちしない） | Accepted（実装・S1/S2） |
| 0022 | 茶々の「声」は voice バンドルで差し替える（方言/言語を base⟂voice 分離・config 主導・既定 ja-osaka） | Accepted（方針確定・実装これから） |
| 0023 | ソース修正はテスト同梱・走らせて緑が必須（Stop フックで強制） | Accepted（実装） |
| 0024 | 客人複数化＝Mediator＋話す順 Strategy＋AIターン予算で N人会話へ（要 turn 管理再設計・Inc3 後） | Proposed（構想） |
| 0025 | 人間待ちの間、茶々が“人間役の代打”で場をつなぐ（予算付き有界・0015 の歯止めを追補・fill_cap で終端保証） | Accepted（実装） |
| 0026 | LLM 接続は Agent ポートで中立化（ACP と API の2アダプタ・MCP は直交レイヤ・0001 の refine） | Accepted（Agent ポート＋2アダプタ実装: ACP/OpenAI互換API・住人 backend 選択可） |
| 0027 | 茶々の「中座」で長命セッションを定期リフレッシュ（世界観に溶かした劣化根治・0005 の refine） | Accepted（実装済み） |
| 0028 | 背景の昼夜は tint 層＋補間で表す（時間帯別 scene.png 差し替えは特別な一枚だけ・0010/0019 に乗る） | Accepted（実装済み） |
| 0029 | Scheduler を薄い Orchestrator に戻す（責務を controller 群へ段階抽出・トップレベル分岐は Chain of Responsibility・0013 の refine） | Accepted（P1〜4a＋speak一本化 実装済み・839→584行／残 P4b/P5full/P6 は費用対効果で保留） |

形式は Michael Nygard 風（背景 / 決定 / 代替案 / 影響 / 備考）。
