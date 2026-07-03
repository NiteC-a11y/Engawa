# Engawa（縁側）

[日本語](README.md) | **English**

> A Tamagotchi-like desktop companion app — you share your day with "Chacha," an AI resident who lives in the corner of your screen.

*Engawa* (縁側) is the wooden veranda that wraps around a traditional Japanese house — the in-between place where you sit and watch the weather go by. Chacha is **not a chat assistant**. She simply *lives* on that veranda: she murmurs at the time of day and the weather, and answers lightly when you talk to her. Now and then a guest (Codex) drops by wearing a role, and chats with Chacha for a while — then leaves.

This is a personal experiment aiming for "**a single environment-reactive resident + two-way interaction + occasional guest visits**." It runs on **your own machine's Claude / ChatGPT subscription auth** (personal use — no metered API billing by design).

---

## How it works

```
Chacha (the veranda's resident — first person, Kansai-ish, long-lived session)
  ← real environment events (time of day, weather in Osaka)   … spontaneous murmurs (real weather is the truth)
  ← diorama events = "arcs" (sparrow / cat / wind, story beats)… breaks the monotony; subordinate to real weather
  ← you talking to her (plain text)                           … interrupts, cancel-first
  ← a guest visit from Codex (/codex, or an evening drop-in)  … seasonal topics woven into the small talk
```

- Every event flows into Chacha's **single long-lived session**, so context stays continuous.
- Chacha's persona is injected via the `CLAUDE.md` handed to the adapter. The guest's persona is injected dynamically into the prompt at summon time.
- While you are away from the keyboard, Chacha plays "stand-in for the absent human" to keep the room alive — **but with a budget, so it always terminates (bounded)** ([ADR-0025](docs/adr/0025-resident-fills-in-for-absent-human-bounded.md)). Never regressing into unbounded, autonomous AI-to-AI chatter is the core design principle.

---

## Requirements

| Type | Detail |
|---|---|
| Python | 3.10+ (developed/verified on 3.13) |
| Node.js | ACP adapters are launched via `npx` (`@agentclientprotocol/claude-agent-acp` / `codex-acp`) |
| Auth (resident) | Logged in to [Claude Code](https://claude.com/claude-code) with a subscription (Pro/Max) |
| Auth (guest) | Logged in to Codex / ChatGPT with a subscription |
| Optional | `pywebview` (the corner veranda window UI), `rlcard` (opponent AI for `/game`) |

> No API keys are used. `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are intentionally stripped from child processes so everything runs on subscription auth alone.

---

## Setup & run

```bash
# 1) Authenticate first
claude          # log in to Claude Code with your subscription
# (log in to Codex / ChatGPT the same way)

# 2) Prepare personal config (optional — every key is optional; delete to fall back to defaults)
cp engawa.json.sample engawa.json        # use "copy" on Windows

# 3) Run
python src/engawa_main.py                # console (terminal)
```

To run as the **corner veranda window (frameless web UI)** (requires `pywebview`):

```bat
:: Windows / cmd
set "ENGAWA_UI=web" && python src/engawa_main.py
```

Windows launcher `.bat` files are also included:

- `engawa.bat` — everyday launch in the corner veranda window
- `engawa-debug.bat` — with debug logging (`engawa.log`) plus a separate log-tailing window

---

## Usage

Plain text is **talking to Chacha**; input starting with `/` is a **command to the veranda**.

| Command | Description |
|---|---|
| `/codex <persona>` | Summon a guest (Codex). It visits wearing the given persona |
| `/game <id> [見る]` | Mini-games (`blackjack` / `uno` / `leduc`). "見る" = spectate. Requires `pip install rlcard` |
| `/arc [雀\|猫\|風]` | Replay a diorama event (arc) — for debugging |
| `/model` | Show the current models (resident / guest) |
| `/font [scale\|save]` | Live-adjust the web font size in-app (`/font save` persists it) |
| `/help` / `/quit` | Help / quit |

---

## Configuration

Behavior is tuned via `engawa.json` (personal config, **git-ignored**). Precedence is **environment variables (`ENGAWA_*`) > `engawa.json` > code defaults**. Every key is optional; missing/broken values all fall back to code defaults.

See [`engawa.json.sample`](engawa.json.sample) for the template and the meaning of each field (model / visit frequency / stand-in / pacing / topics / ACP timeouts / UI, etc.). The sources for the guest's small-talk topics are managed as a whitelist in [`topic_sources.json`](topic_sources.json).

Common environment variables:

```
ENGAWA_UI=web              launch in the corner veranda window
ENGAWA_MODEL=opus          model for the resident (Chacha = Claude)
ENGAWA_CODEX_MODEL=...      model for the guest (codex)
ENGAWA_GUEST_PROB=0.1       probability of a spontaneous visit
ENGAWA_DEBUG=1              record key lifecycle events to engawa.log
```

---

## Layout

```
src/           the app itself (engawa_main / acp / sources / scheduler / views / prompts / conversation / game …)
assets/        Chacha's sprite (sprite.json + chacha.png)
docs/adr/      design decisions and why alternatives were rejected (ADRs 0001–0025)
docs/          TECH_RULES.md (tech spec & boundaries) / Backlog.md (task inventory) / class-diagram.md
poc/           verified reference points for each phase (preserved)
CLAUDE.md      the canonical picture of the current whole (a developer-facing guide)
```

For deeper design background (note: the docs below are written in Japanese):

- **[CLAUDE.md](CLAUDE.md)** — the canonical overview, principles, and current status
- **[docs/adr/](docs/adr/README.md)** — design decisions and rejected alternatives (e.g. why it pivoted away from autonomous AI-to-AI chat = ADR-0004)
- **[docs/TECH_RULES.md](docs/TECH_RULES.md)** — tech spec, conventions, boundaries
- **[docs/Backlog.md](docs/Backlog.md)** — the inventory of remaining tasks

---

## Status

The main paths — environment reactivity, two-way interaction, guest visits, and the pixel-art UI — are implemented, and both summoned and spontaneous visits have been E2E-verified against the real Codex. This is a personal, experimental project, so the spec may change without notice.

Chacha (current sprite):

![Chacha's sprite](assets/chacha.png)

---

## License

A personal project with no license set at this time. Terms for use and redistribution are to be decided later.
