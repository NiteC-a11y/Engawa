# Engawa クラス図（Mermaid）

`src/` の現行構成を Mermaid 記法で整理したもの。Port & Adapter 境界（ADR-0013 / 0015 / 0017）を含む。

> 実装は ABC ではなく duck typing（`NotImplementedError` / `...`）のため、ポートは `<<Port>>` として表記。

---

## 全体構成（Composition Root → Mediator）

```mermaid
classDiagram
    direction TB

    class engawa_main {
        +run_console()
        +run_web()
        -_build(resident, view) Scheduler
    }

    class Scheduler {
        +resident: AcpAgent
        +sources: EventSource[]
        +idle: WeatherSource
        +view: View
        +active_source: EventSource
        +active_guest: GuestSource
        +room: Room
        +games: GameController
        +run()
        -_inject(narration)
        -_command(line)
        -_start_room()
        -_restart_resident()
        -_maybe_step_away()
        -_return_from_away()
    }

    class CommandRouter {
        <<Command パターン · ADR-0029 P1>>
        +has(name) bool
        +dispatch(ctx, line, parts) bool
    }

    class GameController {
        <<Controller · ADR-0029 P3>>
        +active bool
        +over bool
        +start(game_id, watch)
        +on_tick()
        +on_user_input(line) bool
        +abort_by_user()
        +close()
    }

    class RoomSpeakerFactory {
        <<SpeakerFactory · ADR-0029 P4a>>
        +speakers() Speakers
        +cancel_inflight() bool
        +resident_timed_out: bool
        +guest_timed_out: bool
    }

    engawa_main ..> Scheduler : 組み立て
    engawa_main ..> AcpAgent : spawn_resident
    engawa_main ..> View : ConsoleView / WebView

    Scheduler --> AcpAgent : 住人注入
    Scheduler o-- EventSource : 複数
    Scheduler --> WeatherSource : idle/fallback
    Scheduler --> View : 出力・入力
    Scheduler --> Room : 来訪中のみ
    Scheduler ..> GameController : 対局を委譲（tick/入力/開始/shutdown）
    Scheduler ..> CommandRouter : slash 委譲（/font /daynight・未登録は if/elif）
    GameController --> GameSession : 対局中のみ
    GameController ..> Scheduler : preempt/bump_beat/resident（callback で結ぶ）
    Scheduler ..> RoomSpeakerFactory : room の Speaker 生成（種/timeout 凝集・resident_speak seam）
    RoomSpeakerFactory ..> Room : Speaker を供給
```

> **⚠ この図は ADR-0029 のリファクタ進行中**（Scheduler を薄い Orchestrator＋controller 群へ段階抽出）。**Phase 1=CommandRouter／Phase 2=`active` 意味分離（`active_source`/`active_guest`）／Phase 3=GameController／Phase 4a=RoomSpeakerFactory／P5=speak 一本化（_speak_locked）済み＝**ここで打ち切り**（P4b/P5full/P6 は費用対効果で保留）**（対局の生成/進行/終了を委譲・Scheduler 状態への結び目は callback で注入・後方互換で `Scheduler.game` プロパティ）。以後 Phase 4〜6 で VisitController・ResidentSessionManager・Tick/Input の CoR 化が進むと、Scheduler の箱（`_start_room`/`_maybe_step_away` 等）はそれぞれの controller へ移り縮む。**背景の昼夜（`daynight.py`・ADR-0028）は `WebView` が使う純関数**＝下記「Port & Adapter ①」/ 責務テーブル参照。

---

## Port & Adapter ① — View（出力・入力ポート）

ADR-0013 ③。`Scheduler` は `View` だけ知り、console / web / テスト用実装を差し替える。

```mermaid
classDiagram
    direction TB

    class View {
        <<Port>>
        +turn_start(who, kind, label, voice)
        +chunk(text)
        +turn_end()
        +system(msg)
        +say(speaker, text)
        +game_open(title)
        +game_update(snapshot, lines)
        +game_close()
        +set_font(scale) bool
        +current_font() float?
        +inputs() AsyncIterator
    }

    class ConsoleView {
        +inputs() stdin
    }

    class WebView {
        -_log: queue
        +api: _WebApi
        +poll(since)
        +send(text, to)
        +resize_window(w, h)
        +set_font(scale)
        +current_font()
    }

    class CaptureView {
        +events: list
        +feed(line)
    }

    class _WebApi {
        +poll(since)
        +send(text, to)
        +close()
        +resize(w, h)
    }

    class _GameApi {
        +poll(since)
        +close()
    }

    View <|-- ConsoleView
    View <|-- WebView
    View <|-- CaptureView
    WebView *-- _WebApi : js_api
    WebView *-- _GameApi : 観戦窓
```

---

## Port & Adapter ② — ACP（エージェント接続）

ADR-0013 ②＋**ADR-0026**。LLM 接続の中立ポート `Agent`（`prompt/cancel/close`）の背後に**2アダプタ**: `AcpAgent`（外部 `claude-agent-acp`/`codex-acp` を包む・Claude Code サブスク）と `OpenAIAgent`（ローカル OpenAI 互換 API＝LM Studio 等・API はステートレスなので履歴を自前保持）。`Scheduler` は `Agent` と中立 `AgentTimeoutError` だけを知り実体を知らない＝住人/客人とも backend を `ENGAWA_RESIDENT_BACKEND`/`ENGAWA_GUEST_BACKEND` で選択（客人 openai は persona を prompt 注入・住人と同じ endpoint 共有）。

```mermaid
classDiagram
    direction TB

    class ACPClient {
        +proc: Process
        +on_chunk: callback
        +request(method, params, timeout)
        +prompt(text, on_chunk)
        +cancel()
    }

    class AcpAgent {
        <<Facade>>
        +proc: Process
        +client: ACPClient
        +sessionId: str
        +caps: dict
        +model: str
        +spawn(cmd, cwd, model)$ AcpAgent
        +spawn_resident()$ AcpAgent
        +spawn_guest()$ AcpAgent
        +prompt(text, on_chunk)
        +cancel()
        +close()
    }

    class OpenAIAgent {
        <<API adapter>>
        +base_url: str
        +model / reported_model
        +_messages: list
        +spawn_resident()$ OpenAIAgent
        +prompt(text, on_chunk)
        +cancel()
        +close()
    }

    class Agent {
        <<Port · ADR-0026>>
        +model / reported_model / last_stop_reason
        +prompt(text, on_chunk)
        +cancel()
        +close()
    }

    class AgentTimeoutError {
        <<Exception>>
    }

    class ACPTimeoutError {
        <<Exception>>
    }

    AcpAgent ..|> Agent : ACP 実装
    OpenAIAgent ..|> Agent : OpenAI 互換API 実装（履歴自前保持）
    ACPTimeoutError --|> AgentTimeoutError
    AcpAgent *-- ACPClient
    AcpAgent ..> ACPTimeoutError : prompt timeout
    Scheduler ..> Agent : resident / guest（中立ポート・acp を import しない）
    engawa_main ..> AcpAgent : spawn（実体を注入）
    GuestSource ..> AcpAgent : 使い捨て客人
```

---

## Port & Adapter ③ — EventSource（環境イベント源）

ADR-0013 ①。`Scheduler` が抽選・駆動する「源」のポート。

```mermaid
classDiagram
    direction TB

    class EventSource {
        <<Port>>
        +key: str
        +cooldown_ticks: int
        +eligible(ctx) bool
        +next_phase(ctx) Narration|SILENT|None
        +reset()
        +close()
    }

    class BoxGardenArc {
        +phases: Phase[]
        +idx, gap, age
    }

    class WeatherSource {
        +prev_desc: str
    }

    class GuestSource {
        +persona: str
        +agent: AcpAgent
        +eligible(ctx) 夕方×確率
        +ensure_agent() codex spawn
    }

    class Phase {
        +tag: str
        +narrate: str|callable
        +react: bool
    }

    class Narration {
        <<Value>>
        +text, kind, label, voice
    }

    class SILENT {
        <<番兵>>
    }

    EventSource <|-- BoxGardenArc
    EventSource <|-- WeatherSource
    EventSource <|-- GuestSource
    BoxGardenArc *-- Phase
    EventSource ..> Narration : 生成
    EventSource ..> SILENT : 無言ビート
```

---

## Port & Adapter ④ — Game（ゲーム核 + RLCard アダプタ）

ADR-0017。`game.py` は framework 非依存、`game_rlcard.py` が RLCard を `GameAdapter` に合わせる。

```mermaid
classDiagram
    direction TB

    class GameAdapter {
        <<Port>>
        +num_players: int
        +render: Render|null
        +reset()
        +current_player() int
        +is_over() bool
        +legal_moves(player) list
        +state(player) dict
        +play(move)
        +result() list
    }

    class RLCardAdapter {
        -_env: rlcard.Env
    }

    class BlackjackRender {
        +deal(adapter, names)
        +turn(adapter, slot, name)
        +move(name, move, ...)
        +result(adapter, names)
        +snapshot(adapter, names, ...)
    }

    class Player {
        +name: str
        +is_human: bool
        +choose(state, legal_moves) async
    }

    class GameSession {
        +adapter: GameAdapter
        +players: Player[]
        +begin()
        +step() async
        +human_move(move) async
        +waiting_for_human: bool
    }

    class GameError {
        <<Exception>>
    }

    GameAdapter <|-- RLCardAdapter
    RLCardAdapter ..> BlackjackRender : render=
    GameSession --> GameAdapter
    GameSession o-- Player
    Scheduler --> GameSession
    Scheduler ..> game : make/register でレジストリ
```

---

## 3人会話（State パターン + Strategy/DI）

ADR-0015。`Room` が Mediator、`Speaker` が茶々/客人の注入アダプタ、`RoomState` がターン管理。

```mermaid
classDiagram
    direction TB

    class Room {
        <<Mediator>>
        +persona: str
        +resident: Speaker
        +guest: Speaker
        +transcript: Transcript
        +turn_cap: int
        +idle_leave_ticks: int
        +fill_cap: int
        +fill_after: int
        +fill_slowdown: int
        +begin(should_stop)
        +on_human(text, to, should_stop)
        +on_tick(should_stop)
        +preempted: bool
        -_state: RoomState
        -_fill_left: int
        -_stop: callable
    }

    class RoomState {
        <<abstract>>
        +room: Room
        +enter()
        +on_human(text, to)
        +on_tick()
    }

    class Greeting
    class AwaitingHuman
    class Responding
    class ResidentFilling
    class Leaving
    class Closed

    class Speaker {
        <<Strategy/DI>>
        +name: str
        +say(window, kind) async
    }

    class Transcript {
        <<Value>>
        +append(speaker, text)
        +window(n)
        +render(n)
    }

    class Utterance {
        <<Value>>
        +speaker: str
        +text: str
    }

    Room *-- RoomState
    RoomState <|-- Greeting
    RoomState <|-- AwaitingHuman
    RoomState <|-- Responding
    RoomState <|-- ResidentFilling
    RoomState <|-- Leaving
    RoomState <|-- Closed

    Greeting ..> AwaitingHuman : 遷移
    AwaitingHuman ..> Responding : 人間発話
    AwaitingHuman ..> ResidentFilling : 沈黙+予算残（茶々が代打・ADR-0025）
    AwaitingHuman ..> Leaving : 沈黙+予算ゼロ
    Responding ..> AwaitingHuman : turn_cap後（予算リセット）
    ResidentFilling ..> AwaitingHuman : 1往復後
    Leaving ..> Closed

    Room --> Speaker : resident, guest
    Room *-- Transcript
    Transcript o-- Utterance

    Scheduler ..> Speaker : fn注入 AcpAgent.prompt
    Scheduler --> Room
```

---

## レイヤー関係（Port & Adapter の見取り図）

```mermaid
flowchart TB
    subgraph Core["Core（framework / UI 非依存）"]
        Scheduler
        game["game.py\nGameAdapter / GameSession"]
        conv["conversation.py\nRoom / RoomState"]
        sources["sources.py\nEventSource"]
        prompts["prompts.py\nLLM文言ビルダー"]
    end

    subgraph Ports["Port（抽象境界）"]
        View
        GameAdapter
        EventSource
        Speaker
    end

    subgraph Adapters["Adapter（差し替え可能な実装）"]
        ConsoleView
        WebView
        RLCardAdapter
        BoxGardenArc
        GuestSource
        AcpAgent["AcpAgent\n(claude/codex ACP)"]
    end

    engawa_main --> Scheduler
    Scheduler --> View
    Scheduler --> EventSource
    Scheduler --> GameAdapter
    Scheduler --> Room
    Scheduler --> AcpAgent
    Scheduler --> prompts
    prompts -.-> sources

    ConsoleView -.-> View
    WebView -.-> View
    RLCardAdapter -.-> GameAdapter
    BoxGardenArc -.-> EventSource
    GuestSource -.-> EventSource
    Room --> Speaker
    Speaker -.-> AcpAgent
```

---

## 設計上のポイント

| 境界 | ポート | 主なアダプタ |
|------|--------|--------------|
| 出力・入力 | `View` | `ConsoleView`, `WebView`, `CaptureView` |
| LLM 接続 | （明示 Port なし） | `AcpAgent` + 外部 ACP アダプタ |
| 環境イベント | `EventSource` | `BoxGardenArc`, `WeatherSource`, `GuestSource` |
| ゲーム | `GameAdapter` | `RLCardAdapter`（+ `BlackjackRender`） |
| 3人会話の発話 | `Speaker`（DI） | Scheduler が `AcpAgent.prompt` を fn として注入 |
| LLM 文言生成 | （Port なし・関数群） | `prompts.py`（注入プロンプト工場・`sources` から分離・`prompts→sources` 一方向） |
| スラッシュコマンド | `Command`／`CommandRouter`（登録制） | `commands.py`（`FontCommand`/`DayNightCommand`＋薄い `CommandContext`・`Scheduler._command` が委譲・adr/0029 Phase 1。残コマンドは controller 抽出に合わせ移行） |
| 背景の昼夜 tint | （Port なし・純関数） | `daynight.py`（時刻→`{tint,glow,lamp}`・`WebView.poll` が大阪時刻で配信→JS が #scene の膜3枚［乗算tint＋月明かりglow＋室内灯lamp］へ・adr/0028。`/daynight` プレビューの仮想時刻解決＝`parse_override`/`override_minute`/`effective_layers` も純関数） |
| デバッグログ | （stdlib logging ラッパ） | `debuglog.py`（`ENGAWA_DEBUG=1`→`engawa.log`・既定オフ＝no-op・各モジュールは `get("<name>")` の子ロガー） |

`engawa_main.py` が composition root で、`Scheduler` が Mediator として各 Port を結線する（ADR-0013）。`prompts.py` は Scheduler だけが呼ぶ LLM 文言ビルダー（`user_narration`/`room_*_prompt`/`game_move_prompt`/中座の `absence_leave`・`absence_return` 等）を `sources.py` から切り出したもの＋茶々ソロ出力の染み出しガード `strip_resident_leak`（純関数・注入文の復唱＋地の思考を表示前に除去）。`debuglog.setup` は composition root が1度だけ呼ぶ（既定オフ＝縁側の窓/console 本文は汚さない）。

## 参照

- `docs/adr/0013-event-source-scheduler-architecture.md`
- `docs/adr/0015-visitor-bounded-three-way-conversation.md`
- `docs/adr/0025-resident-fills-in-for-absent-human-bounded.md` — 人間待ちの間、茶々が代打で場をつなぐ（`ResidentFilling`・有界）
- `docs/adr/0017-games-via-port-and-rlcard-adapter.md`
- `codex/review-cursor-2026-06-30-architecture-boundaries.md` — 本図を基にした境界レビューと Action Items
