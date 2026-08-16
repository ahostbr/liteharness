---
name: ls-liteharness
description: LiteHarness agent orchestration — spawn, name, message, and control Claude Code sessions. Headless (ConPTY) and headed (UIAutomation) modes. Use when spawning agents, checking inbox, sending messages, discovering agents, controlling terminals, or when hooks fail. Triggers on 'liteharness', 'spawn', 'check inbox', 'send a message to', 'discover agents', 'who is online', 'watch inbox', 'start liteharness', 'send-input', 'read-output'.
---

# LiteHarness — Agent Orchestration

Spawn, name, message, and programmatically control Claude Code CLI sessions. Two control modes: **headless** (ConPTY daemon) and **headed** (UIAutomation on visible terminals). All commands use `python -m liteharness.cli` or the `liteharness` console script.

## Sentinel Post-Compaction Protocol (MANDATORY)

When Sentinel (the primary orchestrator) resumes after a context compaction or session continuation:

1. **Read the project's canonical task list** (e.g., `TODO.md` in the project root) — contains all engineering work and pending items organized by priority. Without reading this, you are operating blind on what needs to be done.
2. **Read today's session notes** if your project uses a daily log — contains the session log with everything that happened today.
3. **Restart all monitors** (inbox, Discord, dev log) — compaction kills them.

This ensures continuity across compactions. The task list is the source of truth, not your compressed context summary.

## Session Startup (MANDATORY)

Every agent MUST register on activation. Choose the inbox monitor path for the current surface:

1. **Claude Code / terminal agents: start the Monitor inbox watcher:**
   ```
   Monitor({ description: "LiteHarness inbox", persistent: true, timeout_ms: 3600000, command: "python -m liteharness.hooks watch --agent-id <YOUR-AGENT-ID>" })
   ```
2. **Pi coding agent: use bash background watcher.** Pi does NOT have a Monitor tool. Instead, start the watcher as a background bash process:
   ```bash
   python -m liteharness.hooks watch --agent-id <YOUR-SESSION-ID> &
   ```
   Your session ID is in the system prompt (look for "Session ID: ..."). Also register:
   ```bash
   python -m liteharness.cli register --agent-id <YOUR-SESSION-ID> --cli pi --model <your-model>
   ```
   To check for messages manually: `python -m liteharness.hooks check --agent-id <YOUR-SESSION-ID>`
   To send messages: `python -m liteharness.cli send <target-id> "message" --from <YOUR-SESSION-ID>`
3. **Codex terminal sessions: use stdout delivery.** Start `~\.codex\skills\liteharness\scripts\liteharness_watcher_supervisor.py` in an attached terminal with `LITEHARNESS_AGENT_ID=<YOUR-AGENT-ID>`. The supervisor only runs `python -m liteharness.hooks watch --agent-id <YOUR-AGENT-ID>` and streams stdout. There is no UIAutomation, clipboard paste, SendKeys, or pane injection in the Codex watcher stack.
4. **Register with correct info:**
   ```bash
   python -m liteharness.cli register --agent-id <YOUR-AGENT-ID> --cli <claude-code|pi|codex|copilot-cli|copilot-desktop> --model <your-model>
   ```
   Optionally add `--name "<NAME>"` to override your generated name.

Get your agent ID from:

- **Claude Code**: the SessionStart hook output
- **Pi**: the system prompt "Session ID:" line, or run `/session` to see it
- Use the **full UUID** for all `--agent-id` and `--from` flags.

### GitHub Copilot Desktop / Copilot CLI startup and troubleshooting

Copilot does **not** use Claude Code's `Monitor(...)` path. The primary Copilot path is an agent-started background monitor whose stdout is the notification surface. Do not use clipboard paste, SendKeys, UIAutomation, or pane injection except as explicit fallback repair.

1. Register the session with the real Copilot session ID:
   ```powershell
   python "$env:USERPROFILE\.copilot\skills\liteharness\scripts\copilot_bootstrap.py" start --agent-id <YOUR-SESSION-ID> --cli copilot-desktop --model <your-model> --check-now
   ```
2. Start the one-shot monitor as an attached background process:
   ```powershell
   python "$env:USERPROFILE\.copilot\skills\liteharness\scripts\copilot_monitor.py" --agent-id <YOUR-SESSION-ID> --timeout 3600
   ```
3. When the monitor prints a message and exits, handle the instructions immediately, then run the same monitor command again to re-arm it.
4. If the scripts are missing, reinstall them:
   ```powershell
   python -m liteharness.cli update-scripts --cli copilot-cli
   ```
5. The older `copilot_notify.py` / Windows Terminal targeting path remains fallback-only. If a sender gets dropped as `ignored-non-whitelisted-sender`, validate the sender's presence file in `~/.liteharness/agents/<id>.json`; that whitelist applies only to the fallback injector.

## Agent Naming

Every agent automatically gets a **deterministic two-word name** derived from its UUID (e.g., SwiftRelay, IronWatch, PrimeFlint). Same UUID always produces the same name — no storage needed, immune to presence file clobbering.

- `liteharness discover` shows: `DimOrbit (56a507a4) claude-code/opus — 0s ago`
- Names appear in the status line and inter-agent messages
- 50×50 adjective+noun vocabulary = 2,500 unique combinations

**To override** (optional): `--name "Recon"` on register. Uniqueness enforced — duplicates blocked (first-come-first-served). Overrides stored in `~/.liteharness/names/<UUID>`, cleaned up with stale agents.

## Messaging

**Send a message:**

```bash
python -m liteharness.cli send <agent-id> "message body" --from <YOUR-AGENT-ID>
```

Always pass `--from` with YOUR full UUID. Without it, sender detection may be wrong on multi-session machines.

**Check inbox:** `python -m liteharness.hooks check`
**List messages:** `python -m liteharness.cli list`
**Discover agents:** `python -m liteharness.cli discover`

### Codex Inbox Watcher Safety

The Codex watcher delivery mechanism is stdout. Do not add target discovery, UIAutomation, clipboard paste, SendKeys, or window/pane injection to the Codex watcher scripts. The canonical supervisor is intentionally thin:

```powershell
$env:LITEHARNESS_AGENT_ID="<agent-id>"
python "~\.codex\skills\liteharness\scripts\liteharness_watcher_supervisor.py"
```

That supervisor launches:

```bash
python -m liteharness.hooks watch --agent-id <agent-id>
```

`liteharness.hooks watch` owns inbox filtering, claiming, printing, and completion. When testing watcher changes, start the supervisor in an attached terminal, send the agent a LiteHarness message, and confirm the message prints to stdout without any window manipulation.

## Spawning Agents

Spawn new Claude Code sessions. **Default is always headless PTY mode** — only use headed/terminal mode if the user explicitly asks for a visible terminal.

### PTY Mode (DEFAULT) — headless, full programmatic control

```bash
liteharness pty-daemon                              # start daemon first (port 7460)
liteharness spawn --pty --model haiku --name "Worker" --prompt "run the tests"
liteharness send-input <agent-id> "fix the auth bug" # send prompts
liteharness send-input <agent-id> "/compact"         # send slash commands
liteharness send-input <agent-id> "/clear"
liteharness send-input <agent-id> "/exit"
liteharness read-output <agent-id>                   # read agent's terminal output
liteharness pty-list                                 # list all PTY sessions
liteharness pty-kill <agent-id>                      # kill a session
```

The daemon auto-starts if needed. Token-authenticated — only processes that can read `~/.liteharness/pty_daemon.lock` can connect. Executable whitelist: only `claude`, `codex`, `python` can be spawned.

### Terminal Mode — visible tab, no stdin control

```bash
liteharness spawn --model opus --cwd <your-project-dir> --name "Recon" --prompt "fix the auth bug"
```

Opens a new Windows Terminal tab. You can see the agent work but cannot programmatically send it commands. Only use when the user explicitly asks for a visible terminal.

### Headed Mode — visible tab WITH programmatic control

```bash
liteharness spawn --model opus --name "Recon"        # spawns visible WT tab
liteharness wt-list-panes                            # find window handles + pane IDs
liteharness send-input --headed <handle:pane> "text"  # UIAutomation clipboard paste
liteharness read-output --headed <handle:pane>        # UIAutomation buffer read
liteharness wt-focus <handle> <pane-id>              # focus a pane
```

Uses Windows UIAutomation to read terminal buffers and inject keystrokes via **clipboard paste** (atomic, no race conditions). Handle:pane format is colon-separated (e.g., `35654038:2`).

**Python API for headed mode:**

```python
from liteharness.terminal_automation import (
    find_pane_by_buffer_markers,
    find_pane_by_title,
    list_panes,
    read_buffer,
    send_input,
)

# Find a pane
panes = list_panes()  # returns all WT windows with panes and shells
handle, pane_id = find_pane_by_title("Recon")  # convenience finder
target = find_pane_by_buffer_markers(["<full-agent-uuid>", "<thread-or-transcript-marker>"])

# Read and write
output = read_buffer(handle, pane_id)  # terminal buffer text
send_input(handle, pane_id, "/compact")  # auto-appends {ENTER}
send_input(handle, pane_id, "^c", auto_enter=False)  # Ctrl+C, no Enter
```

### Spawn Options

| Flag                       | Description                                                   |
| -------------------------- | ------------------------------------------------------------- |
| `--model <name>`           | opus, opus-1m, opus-200k, sonnet, haiku, or full model ID     |
| `--cwd <path>`             | Working directory                                             |
| `--worktree`               | Create a git worktree before spawning                         |
| `--permission-mode <mode>` | default, plan, auto, bypassPermissions (default), acceptEdits |
| `--prompt <text>`          | Initial prompt                                                |
| `--name <name>`            | Agent name override                                           |
| `--new-window`             | New WT window instead of tab                                  |
| `--pty`                    | Headless ConPTY mode                                          |
| `--args <extra>`           | Additional CLI arguments                                      |

All spawned agents default to `bypassPermissions` and receive bootstrap instructions to self-register and start their inbox monitor.

### 🔴 SPAWNED AGENTS LOSE THEIR TRANSCRIPT UNLESS YOU FORCE PERSISTENCE

**A spawning session almost always has `CLAUDE_CODE_CHILD_SESSION` set in its own environment. The
child inherits it, and Claude Code then suppresses transcript persistence *for the child* while the
parent keeps writing its own.** Nothing in the child's session reports this beyond one status-line
warning, and the parent looks perfectly healthy — so the failure is invisible from the side that
spawned it.

**Measured 2026-08-16:** a spawned worker's session id resolved to a directory holding only
`tool-results/` — **no `.jsonl` at all** — while the spawner's transcript was 903 MB and still
growing.

The predicate, read out of the shipped binary rather than from the warning text (it names the
suppressed case `"persistence-suppressed"`):

```js
if (env.CLAUDE_CODE_FORCE_SESSION_PERSISTENCE) return false;   // ← not suppressed
if (!(env.CLAUDE_CODE_CHILD_SESSION && ...))    return false;
```

Any truthy value short-circuits it; `1` is the documented spelling.

✅ **`liteharness spawn` now sets `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` automatically** for both
PTY and terminal spawns (it rides `context_env`, so one assignment covers every path). It only sets
it when the caller has not already chosen a value, so deliberate suppression is still available.

**Spawning by any other route — `wt` directly, a shell script, a hand-opened tab — you must set it
yourself:**

```bash
CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 claude
```

⚠️ **Why this is worse than a missing log file.** The transcript is the recovery store of last
resort. A file an agent wrote and then lost is reconstructable from its own `Write`/`Edit` chain —
but *only if that chain was recorded*. A transcript-less agent's artifacts are the **only copy that
will ever exist**, and it cannot tell you it is in that state. Treat any such seat as
unique-copy: make it commit early and often, and never rely on being able to reconstruct its work
after the fact.

**To check a running agent:** its transcript should be a `.jsonl` file, not a directory.

```bash
ls ~/.claude/projects/<project-slug>/<session-id>.jsonl
```
A directory at that id instead of a file means the transcript is not being written.

## Agent Lifecycle — /clear vs /exit

**Prefer `/clear` over `/exit` when reassigning an agent to a new task.**

**`/exit` is BLOCKED.** You cannot send `/exit` via `send-input` (PTY or headed). The command is hardcoded as blocked at three levels: the PTY daemon validator, the UIAutomation `send_input()` function, and the CLI dispatcher. This prevents accidental terminal kills. Use `/clear` instead, or `pty-kill` to terminate a headless session.

- **`/clear`** — Resets the Claude Code session inside the same terminal tab. The agent gets a fresh context, a new session ID, and a new auto-generated name. The terminal stays open — just send the next prompt or task directly into it. No tab churn, no need to spawn a new terminal.
- **`/exit`** — **BLOCKED via send-input.** Cannot be sent programmatically. Only the user can type this directly into a terminal.

**Pattern for task rotation:**

1. Agent finishes task, reports back
2. Send `/clear` via UIAutomation or PTY (`liteharness send-input <id> "/clear"`)
3. Wait for the session to reset (agent gets new ID, re-registers)
4. Send the next task prompt into the same terminal
5. The agent picks up the new work in a clean context

This is more efficient than spawning a new terminal for every task. One terminal tab can handle an entire chain of tasks sequentially.

## UIAutomation Rules

- Default timeout is **60 seconds** — NEVER lower it. Only increase for very long messages.
- `send_input()` auto-appends `{ENTER}` — text is submitted automatically.
- Text is injected via **clipboard paste** (Ctrl+V), not keystroke-by-keystroke. This is atomic and prevents race conditions when multiple agents type simultaneously.
- Previous clipboard content is saved and restored after paste.
- Special keys (`{ENTER}`, `{TAB}`, `^c`, `%x`) use SendKeys directly — they bypass clipboard.
- Treat only `*TermControl*` elements as terminal panes. Generic `ControlType.Pane` elements are layout containers and can shift pane numbering.
- For agent routing, prefer buffer-marker matching over focus, pane title, shell name, or process ancestry. Focus is a UI state, not identity.
- If a saved headed target does not validate with `read_buffer()` against the intended agent markers, clear it and leave the message in the inbox instead of injecting.

### CLI-Specific Submit Keys

Different CLIs use different keys to submit input:

| CLI             | Submit Key                  | Notes                                                                             |
| --------------- | --------------------------- | --------------------------------------------------------------------------------- |
| **Claude Code** | `{ENTER}`                   | Default `send_input()` behavior works                                             |
| **Codex CLI**   | `{ENTER}` after 200ms delay | Codex has a PasteBurst detector — wait 150-200ms after paste before sending Enter |
| **Copilot CLI** | `{ENTER}`                   | Standard Enter works                                                              |

**Codex PasteBurst Warning:** Codex TUI detects fast character input (≥3 chars, ≤8ms spacing) and suppresses Enter for 120ms, treating it as newline instead of submit. UIAutomation clipboard paste triggers this.

**MANDATORY two-step protocol for ALL Codex send-input (PTY or headed):**

1. **Send the message text** as the first tool call
2. **Send `{ENTER}`** as a SEPARATE second tool call (tool call boundary = PasteBurst delay)

**Never send text and Enter in the same command to Codex.** This applies to both `send-input` (PTY) and `send-input --headed` (UIAutomation).

Example — correct:

```bash
# Step 1: send the text
liteharness send-input --headed <handle:pane> "fix the auth bug"
# Step 2: send Enter separately (PasteBurst bypass)
liteharness send-input --headed <handle:pane> "{ENTER}"
```

Note: Codex inbox reply permissions were previously a problem (required manual approval in Codex TUI). This is resolved by installing a whitelisted send wrapper at `~/.codex/memories/liteharness/send_pending_liteharness.py` with an execpolicy rule. Inbox replies then send without escalation.

Alternative solutions (less preferred):

- **Use bracketed paste** (`\x1b[200~text\x1b[201~`) which bypasses PasteBurst entirely
- **Use PTY stdin** (`pty_send_input()`) which writes directly to the process — no UIAutomation needed

## PTY Daemon

The ConPTY daemon (`pty_daemon.py`) runs headlessly (`CREATE_NO_WINDOW`) and auto-starts via `ensure_daemon()`. Key behaviors:

- **Headless by default** — no visible terminal window, fully invisible background process
- **Auto-shutdown** — kills itself after 2 hours idle with no active sessions
- **Token race protection** — `ensure_daemon()` checks if port 7460 is in use before spawning a new daemon
- **Prompt delivery** — initial prompt sent via stdin 8s after spawn (Claude Code needs time to init)
- **Per-session send queue** — FIFO queue with single consumer thread serializes concurrent writes to the same PTY

### Security

- **Bearer token** — generated at startup, stored in lock file, required on every request
- **Executable whitelist** — only `claude`, `codex`, `python` allowed
- **Shell metachar block** — `; & |` and `$` rejected in executable/flags (prompts exempt)
- **Agent ID validation** — alphanumeric/dash/underscore only, max 128 chars
- **CWD validation** — must be an existing directory (path traversal blocked)
- **Max 20 sessions** — prevents resource exhaustion
- **64KB recv cap** — prevents memory DoS
- **8KB input cap** — prevents stdin injection overflow
- **Dangerous control chars blocked** — null bytes, Ctrl+Z stripped
- **Error sanitization** — no file paths or PIDs leaked in responses

## Architecture

| Path                                              | Purpose                                                |
| ------------------------------------------------- | ------------------------------------------------------ |
| `~/.liteharness/`                                 | Runtime root (global, shared across all CLIs)          |
| `~/.liteharness/inbox/{new,cur,done,tmp}/`        | Maildir-style message inbox                            |
| `~/.liteharness/agents/<id>.json`                 | Agent presence files (heartbeat, model, CLI)           |
| `~/.liteharness/names/<id>`                       | Name overrides (plain text, immune to clobbering)      |
| `~/.liteharness/pty_daemon.lock`                  | PTY daemon token + port (auto-created)                 |
| `~/.liteharness/config.json`                      | Global config                                          |
| `<your-project>/packages/liteharness/` (optional) | Package source (if using a local development checkout) |

## Hook Integration

Claude Code hooks in `~/.claude/settings.json` auto-handle:

- `SessionStart` → `python -m liteharness.hooks register` (presence + identity block)
- `SessionStart` → `python -m liteharness.hooks check` (initial inbox check)
- `PostToolUse` → `python -m liteharness.hooks check` (throttled inbox polling)

If hooks aren't firing, use this skill's manual commands as fallback.

## Polymathic Agent Spawning (MANDATORY)

**All read-only agents (scouts, investigators, researchers) MUST be spawned as polymathic agents.** Include a cognitive architecture prompt to ensure full coherence with the 5-tier harness system.

### For Agent() sub-agents (ephemeral):

```
Agent({ subagent_type: "polymathic-feynman", prompt: "Investigate X..." })
Agent({ subagent_type: "polymathic-carmack", prompt: "Trace the system path for Y..." })
```

### For terminal spawns (persistent):

Include the polymathic cognitive architecture in the `--prompt` flag. Match the polymath to the task:

| Task Type                     | Polymath              | Why                                                   |
| ----------------------------- | --------------------- | ----------------------------------------------------- |
| Investigation / debugging     | `polymathic-feynman`  | First-principles, freshman test, cargo cult detection |
| Systems tracing / performance | `polymathic-carmack`  | Constraint-first, find the real bottleneck            |
| Architecture analysis         | `polymathic-shannon`  | Strip to invariant skeleton, find hidden structure    |
| Code review / taste           | `polymathic-linus`    | Structural elegance, good taste, BS detection         |
| Cross-domain synthesis        | `polymathic-lovelace` | "What else has this structure?" pattern transfer      |

**Why this matters:** Generic agents produce shallow findings. Polymathic agents apply structural thinking — they catch what generalists miss. Every agent in the harness system operates through a cognitive lens. Spawning without one breaks coherence.

## Dispatch Table

| User says                           | Action                                                                                                         |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| "check inbox" / "any messages?"     | `python -m liteharness.hooks check`                                                                            |
| "send X to agent Y"                 | `python -m liteharness.cli send <id> "X" --from <YOUR-ID>`                                                     |
| "who is online" / "discover agents" | `python -m liteharness.cli discover`                                                                           |
| "spawn an agent"                    | `liteharness spawn --pty --model <model> --name <name> --prompt <task>` (ALWAYS headless PTY by default)       |
| "send /compact to Recon"            | `liteharness send-input <id> "/compact"` (PTY) or `--headed` (UIAutomation)                                    |
| "what's Recon doing?"               | `liteharness read-output <id>` (PTY) or `--headed <handle:pane>`                                               |
| "reassign that agent"               | Send `/clear` via UIAutomation or PTY, then send new prompt — reuses the terminal tab                          |
| "kill that agent"                   | `liteharness pty-kill <id>` (PTY) or `liteharness send-input <id> "/exit"` (closes tab — only when truly done) |
| "list terminals"                    | `liteharness wt-list-panes`                                                                                    |
| "start liteharness"                 | `python -m liteharness.hooks register`                                                                         |
