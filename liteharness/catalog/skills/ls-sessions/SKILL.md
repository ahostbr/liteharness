---
name: ls-sessions
description: Use when saving, restoring, listing, or checking status of Windows Terminal Claude, Codex, and Copilot sessions. Triggers on 'save sessions', 'restore sessions', 'session status', 'resume my terminals', 'save my terminals', 'reload terminals'.
---

# Sessions

Save and restore Windows Terminal layouts with active Claude, Codex, and Copilot CLI sessions.

## Commands

| Argument                                       | Action                                                                              |
| ---------------------------------------------- | ----------------------------------------------------------------------------------- |
| `save [name]`                                  | Snapshot all running terminal agent sessions (auto-names with timestamp if omitted) |
| `restore [name]`                               | Reopen terminals and resume sessions from snapshot (latest if omitted)              |
| `restore [name] --layout windows\|tabs\|panes` | Override the restore layout                                                         |
| `restore [name] --dry-run`                     | Print Windows Terminal commands without launching them                              |
| `list`                                         | List all saved snapshots                                                            |
| `status`                                       | Show currently running terminal agent sessions grouped by terminal                  |

## Requirements

**`session_manager.py`** — Python script that drives all session operations.

- **Bundled location:** `${CLAUDE_SKILL_DIR}/session_manager.py`
- **Dependencies:** `pip install psutil` (process discovery)
- Windows Terminal (`wt.exe`) must be installed and on PATH.

If `session_manager.py` is missing, you will see a `FileNotFoundError`. Install the
liteharness plugin via `pip install liteharness` or point the script path to your own
session manager that accepts the same CLI arguments.

## How It Works

Runs `${CLAUDE_SKILL_DIR}/session_manager.py` with the given arguments.

- **Save** discovers Claude, Codex, and Copilot CLI processes via psutil, maps them to local session metadata, traces process tree to Windows Terminal, deduplicates duplicate child processes, writes snapshot JSON
- **Restore** launches `wt.exe` with the configured layout. Default is one top-level Windows Terminal window per saved session.
- Layouts: `windows` opens one window per agent, `tabs` preserves grouped tabs, `panes` opens one window with split panes.
- Snapshots stored in `~/.liteharness/sessions/snapshots/`
- Default layout is configured in `~/.liteharness/sessions/config.json`

## Usage

```
/sessions save morning-layout
/sessions restore morning-layout
/sessions restore morning-layout --layout tabs
/sessions restore morning-layout --dry-run
/sessions status
/sessions list
```

When invoked without arguments, show status.
