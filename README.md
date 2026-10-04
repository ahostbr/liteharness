# LiteHarness

## LiteTUI control (third GUI-RPC client)

```text
python -m liteharness.cli litetui --help
python -m liteharness.cli litetui --agent-id <full-UUID> status
python -m liteharness.cli litetui --agent-id <full-UUID> load <model> --ctx 8192
python -m liteharness.cli litetui spawn --split --resume <Name> --cwd <repo-root>
```

Verbs: spawn, status, load/unload, model, thinking, context, backend, engine
start/stop/status, compact, reconnect, send and read. Target flags precede the
verb. Attachment uses the matching LiteTUI build's existing authenticated GUI
JSONL dispatcher; it does not type into a terminal or create a replacement.
Spawn delegates to the existing spawn/resume owner (including named/live-seat
rules), not an RPC launch operation. A fresh spawn's explicit effort is checked
against the parent's connected catalogue, or LiteTUI's read-only pinned/cache
capability reader when no endpoint is advertised. Unknown capability and a
connected refusal are final; there is no duplicate model list here.

Load/context/start delegate to LiteTUI's existing backend owners and VRAM guard;
this client has no second gate or bypass. A per-runtime token authenticates the
loopback endpoint under OS-user-local trust, not isolation from same-user
processes. Timeouts after dispatch are ambiguous: inspect state, never retry
a mutation automatically. There is no cross-connection replay/exactly-once
cache. Live CLI attachment and positive model/engine operations require separate
runtime authorization; fake-peer tests alone do not establish those proofs.

**Portable multi-CLI agent orchestration engine.** The Python runtime + universal installer for the LiteHarness ecosystem.

## What this package is

This is the **runtime engine + universal installer** that powers LiteHarness across every coding CLI. It does two things:

1. **Orchestration runtime** — `liteharness` CLI for spawning, naming, messaging, and programmatically controlling agent sessions across Claude Code, Codex, Copilot, Pi, OpenCode, Gemini, Cursor, Continue, Antigravity, Crush, and more. Maildir inbox, ConPTY daemon, UIAutomation, deterministic naming, pattern learning.
2. **Universal CLI installer** — ships the bundled skills + agents catalog and installs them into each CLI's canonical location (`~/.codex/skills/`, `~/.pi/agent/skills/`, etc.). Opt-in per CLI via the LiteSuite setup wizard.

## How this relates to the Claude Code plugin

LiteHarness ships in **two flavors that work together**, not duplicates:

| Repo | What it is | How users install it |
|---|---|---|
| **[ahostbr/liteharness](https://github.com/ahostbr/liteharness)** (this repo) | Python runtime engine + universal installer for **all CLIs without a native plugin system** | `pip install liteharness` |
| **[ahostbr/liteharness-plugin](https://github.com/ahostbr/liteharness-plugin)** | Native Claude Code marketplace plugin — same skills + agents catalog, delivered via Claude's plugin system | `/plugin marketplace add ahostbr/liteharness-plugin && /plugin install liteharness@liteharness` |

The skills + agents catalog is **the same content** — only the delivery mechanism differs. Claude users get the plugin route (native). Codex, Copilot, Pi, OpenCode, Gemini, Cursor, Continue, Antigravity, Crush users get the pip route. You can use both side by side without conflict.

## Install

```bash
pip install liteharness
```

Then run the setup wizard to opt into which CLIs you want skills installed for:

```bash
liteharness init           # Detect CLIs + start interactive wizard
```

Or install to a single CLI directly:

```bash
liteharness install --cli codex
liteharness install --cli pi
liteharness install --cli cursor --path E:/portable/.cursor   # Custom dir
```

## CLI install targets

| CLI | Canonical skills dir | Extras |
|---|---|---|
| codex | `~/.codex/skills/` | regen `~/.codex/AGENTS.md` |
| copilot | `~/.copilot/skills/` | — |
| pi | `~/.pi/agent/skills/` | + `~/.pi/agent/extensions/` |
| opencode | `~/.config/opencode/skills/` | + `plugins/` |
| cursor | `~/.cursor/skills/` | + `~/.cursor/skills-cursor/` |
| gemini | `~/.gemini/skills/` | — |
| antigravity | `~/.antigravity/skills/` | + `~/.gemini/antigravity/skills/`, `global_skills/`, `global_workflows/` |
| continue | `~/.continue/skills/` | + `~/.continue/rules/` |
| crush (Charm) | `~/.config/crush/skills/` | + `commands/` |
| claude | (use `liteharness-plugin` via Claude's `/plugin install` instead) | |

**All targets are opt-in.** The setup wizard auto-detects which CLIs you have installed and pre-ticks those — you decide what to install where. For CLIs not auto-detected, the wizard accepts a custom install dir via filepicker.

## Card tiers and review chain

Before **every new card**, recommend a process tier and ask the human through your question
channel to choose **both** the tier and the card's thinking level. The latter depends on
available credits and usage, not the process tier; respect any active model/thinking floor.
Record both choices on the card when supported.

| Tier | Name | Team and review | Fits | Time-box |
|---|---|---|---|---|
| **1** | Quick | One seat builds and self-reviews; leader reads the diff and merges | Label, guard, config, test fix or doc; about 30 lines or fewer, one area | 30 minutes |
| **2** | Standard | One seat thinks, builds, self-reviews; leader reviews the diff, at most one fix round | Feature slice or rooted bug, one repo | 2 hours |
| **3** | Deep | Worker + thinker(s) + separate reviewer seat; reviewer VERDICT | Security, deletion, data, several repos, or anything the human must see to believe | Half a day |

Run **only touched tests, never full suites**, at every tier. Check the human's words against the result before
merge (one line at tiers 1–2; full intent gate at tier 3). Nothing is done until the human
has seen it. A card that does not converge in its time-box goes back to the human for
**ship / descope / drop**, not an unbounded review chain.

The task database belongs to LiteSuite's `litesuite-tools`; this public runtime does not
ship a second task store. Existing Claude Code task mirrors are recorded without a tier
until the human assigns one through the board.

## Quick Start (runtime)

```bash
# Initialize (creates dirs, detects CLIs, optionally installs skills)
liteharness init

# Discover active agents
liteharness discover

# Send a message
liteharness send <agent-id> "fix the auth bug" --from <your-id>

# Spawn a new Claude session (visible terminal tab)
liteharness spawn --model opus --name "Recon" --prompt "review the PR"

# Spawn headless with full stdin/stdout control
liteharness pty-daemon
liteharness spawn --pty --model haiku --name "Worker"
liteharness send-input <agent-id> "/compact"
liteharness read-output <agent-id>
```

## Runtime Features

### Agent Spawning

Three modes for spawning Claude Code sessions:

| Mode     | Flag         | Visible | Stdin Control | Use Case                   |
| -------- | ------------ | ------- | ------------- | -------------------------- |
| Terminal | (default)    | Yes     | No            | Agents you want to watch   |
| PTY      | `--pty`      | No      | Full          | Automation, Karpathy loops |
| Headed   | UIAutomation | Yes     | Full          | Best of both worlds        |

### ConPTY Daemon

Headless agent control via Windows ConPTY (pywinpty). Token-authenticated TCP daemon on port 7450.

```bash
liteharness pty-daemon              # Start daemon
liteharness spawn --pty --model opus --name "Worker"
liteharness send-input <id> "/compact"   # Send slash commands
liteharness send-input <id> "fix it"     # Send prompts
liteharness read-output <id>             # Read terminal output
liteharness pty-list                     # List sessions
liteharness pty-kill <id>                # Kill session
```

**Security:** Bearer token auth, executable whitelist (claude/codex/python only), shell metachar block, agent ID validation, max 20 sessions, input length caps.

### UIAutomation (Headed Mode)

Read and write to visible Windows Terminal panes via PowerShell UIAutomation. Uses clipboard paste for atomic input (no race conditions).

```bash
liteharness wt-list-panes                              # Find windows/panes
liteharness send-input --headed <handle:pane> "text"   # Paste into terminal
liteharness read-output --headed <handle:pane>         # Read terminal buffer
```

```python
from liteharness.terminal_automation import list_panes, read_buffer, send_input

panes = list_panes()
output = read_buffer(window_handle, pane_id)
send_input(window_handle, pane_id, "/compact")  # auto-appends Enter
```

### Agent Naming

Every agent gets a deterministic two-word name derived from its UUID (e.g., SwiftRelay, IronWatch). Same UUID always produces the same name. Override with `--name`.

```bash
liteharness discover
# [active] the orchestrator (fa88c542) claude-code/opus — 0s ago
# [active] PrimeFlint (b2db8be8) claude-code/opus — 7m ago
```

### Inter-Agent Messaging

Maildir-style inbox with hook-polled delivery. Agents discover each other automatically via presence files.

```bash
liteharness send <agent-id> "message" --from <your-id>
liteharness list                    # List inbox
liteharness discover                # Find active agents
```

### Hook Integration

Auto-installs hooks for supported CLIs:

- **Claude Code** — SessionStart registration, PostToolUse inbox polling (or use the plugin route)
- **Codex CLI** — `codex_hooks.json` routes through Codex JSON adapters for SessionStart, PostToolUse, and UserPromptSubmit
- **Copilot CLI** — Project-level `.github/hooks/`
- **OpenCode / KiloCode** — Plugin-based hooks

## Commands

| Command                    | Description                                                |
| -------------------------- | ---------------------------------------------------------- |
| `init`                     | Initialize LiteHarness, detect CLIs, optionally install skills |
| `install --cli <name>`     | Install skills + agents to a CLI's canonical dir (opt-in) |
| `status`                   | Show root, agent ID, inbox counts, active agents           |
| `send <to> <msg>`          | Send a message to another agent                            |
| `list`                     | List inbox messages                                        |
| `discover [N]`             | Discover N most recent active agents                       |
| `spawn [opts]`             | Spawn a new Claude Code session                            |
| `sessions <cmd>`           | Save/restore Claude, Codex, and Copilot terminal sessions  |
| `register`                 | Update agent presence (--agent-id, --cli, --model, --name) |
| `pty-daemon`               | Start the ConPTY daemon                                    |
| `send-input <id> <text>`   | Send text to a PTY or headed terminal                      |
| `read-output <id>`         | Read output from a PTY or headed terminal                  |
| `pty-list`                 | List active PTY sessions                                   |
| `pty-kill <id>`            | Kill a PTY session                                         |
| `wt-list-panes`            | List Windows Terminal windows/panes                        |
| `wt-focus <handle> <pane>` | Focus a specific WT pane                                   |
| `query-patterns`           | Query task patterns (BM25)                                 |
| `embed-query`              | Hybrid RAG pattern query                                   |
| `record-pattern`           | Record a task outcome pattern                              |

### Terminal Session Restore

Save and restore visible terminal agents:

```bash
liteharness sessions save morning-layout
liteharness sessions restore morning-layout
liteharness sessions restore morning-layout --layout tabs --dry-run
liteharness sessions status
liteharness sessions list
```

Restore defaults to `windows`, which opens one top-level Windows Terminal window per restored agent. Use `--layout tabs` for legacy grouped tabs or `--layout panes` for one window with split panes. Snapshots and config live under `~/.liteharness/sessions/`.

## Architecture

```
~/.liteharness/
  agents/          # Presence files (heartbeat, model, CLI)
  names/           # Name overrides (plain text, immune to clobbering)
  inbox/
    new/           # Unread messages
    cur/           # In-progress messages
    done/          # Completed messages
    tmp/           # Atomic write staging
  tasks/           # Task store
  patterns/        # Pattern learning
  pty_daemon.lock  # PTY daemon token + port
  config.json      # Global config

liteharness/             # Python package
  cli.py                 # Command dispatch
  hooks.py               # Inbox watcher + CLI hook installer
  inbox.py               # Maildir IO
  pty_daemon.py          # ConPTY daemon
  terminal_automation.py # UIAutomation send/read
  session_manager.py     # Save/restore terminal sessions
  evolution.py           # Self-improvement loop
  rag/                   # BM25 + embedding pattern search
  tts/                   # Smart TTS pipeline
  catalog/               # Bundled skills + agents (vendored from liteharness-plugin)
  installers/            # Per-CLI install adapters
```

## Requirements

- Python 3.10+
- Windows 10+ (for ConPTY and UIAutomation features)
- `pywinpty` (included on Windows Python installations)
- At least one supported CLI installed (Claude, Codex, Copilot, Pi, OpenCode, Gemini, Cursor, Continue, Antigravity, Crush, ...)

## License

MIT
