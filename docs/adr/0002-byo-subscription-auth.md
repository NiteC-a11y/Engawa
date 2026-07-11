# ADR-0002: サブスク認証を継承し、APIキーは使わない（個人利用限定）

- ステータス: Accepted（制約付き）
- 日付: 2026-06-26
- 関連: ADR-0001

## 背景 / 課題
従量課金を避け、手元の Claude Code(Max) サブスク認証で茶々を、ChatGPT サブスクで Codex を動かしたい（Pencil/Zed と同じ BYO 型）。

## 決定
各アダプタの子プロセスにサブスク認証を継承させ、**`ANTHROPIC_API_KEY` は子プロセスから除去**する。アカウント取り違え防止に **`CLAUDE_CONFIG_DIR` で個人プロファイルを固定できるようにする**（既定は継承・ユーザが明示した時だけ固定＝opt-in。理由は下の制約と実装メモ）。**個人利用限定**とする。

## 検討した代替案
- **APIキー直叩き**: 確実だが従量課金。製品配布時はこちらが必須。

## 影響 / 制約
- 認証情報の優先順位でAPIキーがOAuthより優先されるため、キーが残ると意図せずAPI課金になる（`claude -p` がキーを継いで高額請求に至る事例が知られる）。除去必須。
- 組織アカウントと個人アカウントが同一メールに紐づくと Claude Code が組織側を掴む落とし穴。`CLAUDE_CONFIG_DIR`で個人プロファイルを固定し、VPN/SSO状態に左右されないようにする。ただし多くのユーザは既定location（`~/.claude`）に認証があり、そこを別ディレクトリへ**ハードコード固定すると逆に認証が壊れる**。よって既定は未設定＝親の `~/.claude` を継承（現状維持）とし、ユーザが `ENGAWA_CLAUDE_CONFIG_DIR`（`engawa.json[auth].claude_config_dir`）を明示した時だけ、住人（Claude）の子 env に固定する。
- **ToS**: サードパーティ製品が claude.ai ログインを同梱するのは未承認では禁止。Engawaを配布するなら各ユーザBYOかAPIキーが要る。
- 6/15のプログラマティック/インタラクティブ分離は一時停止中で、今は全てサブスク枠から引かれる。再開したら自律ループ分が別枠になる前提を持つ。「サブスク無料」前提で durable な設計を組まない。

## 実装メモ（2026-07-11・決定と実装の整合）
当初この ADR は `CLAUDE_CONFIG_DIR` 固定を「決定」と書いたが、実装は保留され TECH_RULES で「任意」に軟化・poc にコメントアウトの痕跡だけ、という乖離があった。これを **opt-in で実装**して解消:

- **キー除去**（`ANTHROPIC_API_KEY` / 客人は `OPENAI_API_KEY` も）は `acp._child_env` の `drop_keys` で**常時有効**（既定挙動・退行不可）。
- **`CLAUDE_CONFIG_DIR` 固定**は `acp._config_dir_env` / `_resident_extra_env` / module 定数 `RESIDENT_CLAUDE_CONFIG_DIR` で実装。**既定は空＝注入せず親を継承＝現状維持**。`ENGAWA_CLAUDE_CONFIG_DIR` か `engawa.json[auth].claude_config_dir` を設定した時だけ、**住人（Claude）の子 env にのみ** `CLAUDE_CONFIG_DIR` として渡す（config 主導・原則4）。
- **客人（Codex）は別 CLI＝無関係**なので `spawn_guest` には渡さない（住人側のみ）。
- テスト: `tests/test_acp.py` の `TestConfigDirEnv` / `TestMergeEnv` / `TestResidentExtraEnv`（未設定＝注入なし・設定時＝注入・住人のみ・キー除去が退行しない）。

## 備考
- GPT側(OpenAI)のコストはこの決定では解決しない。
