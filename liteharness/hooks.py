"""
LiteHarness Hook — PostToolUse inbox check.

This script runs as a Claude Code / Gemini CLI hook.
It checks the inbox for messages addressed to the current agent
and outputs them as system-reminder blocks for context injection.

Usage in Claude Code settings.json:
{
  "hooks": {
    "PostToolUse": [{
      "type": "command",
      "command": "python -m liteharness.hooks check"
    }]
  }
}

Usage in Gemini CLI settings.json:
{
  "hooks": {
    "AfterTool": [{
      "matcher": "",
      "hooks": [{"type": "command", "command": "python -m liteharness.hooks check"}]
    }]
  }
}
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class _MtimeWatcher:
    """Fallback watcher using directory mtime comparison."""
    def __init__(self, path: str):
        self.path = Path(path)
        self.last_mtime = 0.0

    def wait(self, timeout: float = 5.0) -> bool:
        """Block until directory mtime changes or timeout. Returns True if changed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                mtime = self.path.stat().st_mtime
                if mtime != self.last_mtime:
                    self.last_mtime = mtime
                    return True
            except OSError:
                pass
            time.sleep(0.5)
        return False


class _WindowsWatcher:
    """Event-driven Windows watcher using ReadDirectoryChangesW.

    Runs ReadDirectoryChangesW synchronously in a daemon background thread.
    Each detected change sets a threading.Event that wait() consumes.
    No polling — latency floor is ~10 ms on NTFS.

    Recovers from ERROR_NOTIFY_ENUM_DIR (internal buffer overflow) by
    signalling a scan and restarting the watch rather than killing the thread.
    """

    _FILE_NOTIFY_FILTER = (
        0x0001  # FILE_NOTIFY_CHANGE_FILE_NAME
        | 0x0008  # FILE_NOTIFY_CHANGE_SIZE
        | 0x0010  # FILE_NOTIFY_CHANGE_LAST_WRITE
    )
    _ERROR_NOTIFY_ENUM_DIR = 1022
    _INVALID_HANDLE_VALUE = (1 << 64) - 1  # -1 interpreted as unsigned 64-bit pointer

    def __init__(self, path: str):
        self.path = path
        self._handle = None
        self._changed = threading.Event()
        self._alive = True
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Explicit restype — default c_int truncates 64-bit HANDLE pointers.
            kernel32.CreateFileW.restype = ctypes.c_void_p
            handle = kernel32.CreateFileW(
                path,
                0x0001,   # FILE_LIST_DIRECTORY
                0x0007,   # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
                None,
                3,        # OPEN_EXISTING
                0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS (required for directories)
                None,
            )
            if handle and handle != self._INVALID_HANDLE_VALUE:
                self._handle = handle
                t = threading.Thread(target=self._watch_loop, daemon=True)
                t.start()
        except Exception:
            self._handle = None

    def _watch_loop(self) -> None:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(65536)
        br = ctypes.c_ulong(0)
        while self._alive:
            try:
                ok = kernel32.ReadDirectoryChangesW(
                    ctypes.c_void_p(self._handle),
                    buf,
                    len(buf),
                    True,     # bWatchSubtree — inbox new/cur/done are subdirectories
                    self._FILE_NOTIFY_FILTER,
                    ctypes.byref(br),
                    None,     # lpOverlapped — synchronous call
                    None,     # lpCompletionRoutine
                )
            except Exception:
                break

            if ok:
                if self._alive:
                    self._changed.set()
                continue

            # Not ok — check whether the error is recoverable.
            err = kernel32.GetLastError()
            if err == self._ERROR_NOTIFY_ENUM_DIR:
                # Internal buffer overflowed — signal a scan and restart the watch.
                self._changed.set()
                continue
            # Any other error terminates the watcher (handle closed, etc).
            break

    def wait(self, timeout: float = 5.0) -> bool:
        if not self._handle:
            time.sleep(min(timeout, 0.5))
            return True  # Can't watch, assume changed
        triggered = self._changed.wait(timeout=timeout)
        if triggered:
            self._changed.clear()
        return triggered

    def close(self) -> None:
        """Stop the background thread and release the directory handle."""
        self._alive = False
        if self._handle:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            except Exception:
                pass
            self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _create_watcher(path: str):
    """Create the best available file watcher for this platform."""
    if sys.platform == "win32":
        watcher = _WindowsWatcher(path)
        if watcher._handle:
            return watcher
    # Fall back to mtime polling on all platforms
    return _MtimeWatcher(path)

from . import inbox, config

# Throttle: only check inbox every N seconds (default 10)
CHECK_INTERVAL_SECONDS = 10
LAST_CHECK_FILE = config.HARNESS_ROOT / ".last_inbox_check"
LAST_CLEANUP_FILE = config.HARNESS_ROOT / ".last_inbox_cleanup"
CLEANUP_INTERVAL_SECONDS = 3600  # Run cleanup at most once per hour
def _read_hook_stdin() -> dict:
    """
    Read JSON from stdin if available (non-blocking).

    All hook-supporting CLIs (Claude Code, Codex, Copilot CLI) send a JSON
    object on stdin with fields like session_id, tool_name, cwd, etc.
    We extract what we need and set env vars so config.get_agent_id() picks
    them up without modification.
    """
    if sys.stdin.isatty():
        return {}
    try:
        # On Windows, just read stdin directly — it's always piped from the CLI
        raw = sys.stdin.read()
        if raw and raw.strip():
            return json.loads(raw)
    except Exception as e:
        # Log to debug file so failures aren't silently swallowed
        try:
            debug_path = config.HARNESS_ROOT / "stdin_error.log"
            debug_path.write_text(f"stdin read error: {e}\n", encoding="utf-8")
        except Exception:
            pass
    return {}


def _apply_hook_context(hook_input: dict) -> None:
    """
    Extract session/CLI info from hook stdin and set env vars.

    This bridges the gap between CLIs that pass session_id via stdin JSON
    (Codex, Copilot CLI) vs env vars (Claude Code).

    Claude Code sends: session_id, model.display_name, hook_event_name, etc.
    """
    # Extract session_id from hook input
    session_id = hook_input.get("session_id")
    if session_id and not os.environ.get("LITEHARNESS_AGENT_ID"):
        os.environ["LITEHARNESS_AGENT_ID"] = session_id

    # Extract model from hook input
    # Claude Code hooks send model as a plain string (e.g. "claude-opus-4-8[1m]")
    # StatusLine hooks send model as an object with display_name
    model_val = hook_input.get("model")
    if isinstance(model_val, str) and model_val:
        os.environ["LITEHARNESS_MODEL"] = model_val
    elif isinstance(model_val, dict) and model_val.get("display_name"):
        os.environ["LITEHARNESS_MODEL"] = model_val["display_name"]

    # Capture transcript_path for recap detection (JSONL location)
    transcript_path = hook_input.get("transcript_path")
    if transcript_path:
        os.environ["LITEHARNESS_TRANSCRIPT_PATH"] = transcript_path

    source = str(hook_input.get("source") or "").strip().lower()
    env_cli = str(os.environ.get("LITEHARNESS_CLI") or "").strip().lower()
    is_litecode = (
        source == "litecode"
        or env_cli == "litecode"
        or bool(os.environ.get("LITECODE_SESSION_ID"))
    )

    # Detect CLI from hook input fields.
    # LiteCode can include a transcript_path for its session snapshot, so source
    # must win before the older Claude transcript-path heuristic.
    if is_litecode:
        os.environ["LITEHARNESS_CLI"] = "litecode"
        if session_id and not os.environ.get("LITECODE_SESSION_ID"):
            os.environ["LITECODE_SESSION_ID"] = session_id
    elif transcript_path:
        # Claude Code — has transcript_path
        if session_id and not os.environ.get("CLAUDE_SESSION_ID"):
            os.environ["CLAUDE_SESSION_ID"] = session_id
    elif os.environ.get("CLAUDECODE"):
        # Fallback: CLAUDECODE env var (may not be available in hook subprocesses)
        if session_id and not os.environ.get("CLAUDE_SESSION_ID"):
            os.environ["CLAUDE_SESSION_ID"] = session_id
    elif hook_input.get("hook_event_name") and not os.environ.get("CODEX_SESSION_ID"):
        # Codex uses hook_event_name field (without transcript_path)
        os.environ["CODEX_SESSION_ID"] = session_id or "codex-unknown"
    elif hook_input.get("source") in ("new", "resumed"):
        # Copilot CLI uses source field with these values
        if not os.environ.get("COPILOT_SESSION_ID"):
            os.environ["COPILOT_SESSION_ID"] = session_id or "copilot-unknown"


def _should_check() -> bool:
    """Return True if enough time has passed since last check, or if urgent messages exist."""
    try:
        if LAST_CHECK_FILE.exists():
            last_check = float(LAST_CHECK_FILE.read_text(encoding="utf-8").strip())
            elapsed = time.time() - last_check
            if elapsed < CHECK_INTERVAL_SECONDS:
                # Even if throttled, always check for urgent messages
                if inbox.INBOX_NEW.exists():
                    for f in inbox.INBOX_NEW.iterdir():
                        if "__urgent__" in f.name:
                            return True
                return False
    except (OSError, ValueError):
        pass
    return True


def _mark_checked() -> None:
    """Record that we just checked the inbox."""
    try:
        config.ensure_root()
        LAST_CHECK_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def check_inbox() -> None:
    """
    Check inbox for messages, output as system-reminder for hook injection.

    Called by PostToolUse hook. Throttled to every 10 seconds unless
    urgent messages are present. Outputs to stdout — the hook system
    injects stdout into the agent's context.
    """
    agent_id = config.get_agent_id()

    if not _should_check():
        return

    _mark_checked()

    # Scan both new/ and cur/ — messages in cur/ were claimed but the agent
    # may not have seen them (hook timeout, output swallowed, etc.)
    all_messages = []
    for directory in (inbox.INBOX_NEW, inbox.INBOX_CUR):
        if not directory.exists():
            continue
        for f in sorted(directory.iterdir()):
            if not f.suffix == ".json":
                continue
            try:
                msg = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            to = msg.get("to", "")
            is_match = to == agent_id or to == "broadcast"
            if is_match and msg.get("from") != agent_id:
                msg["_path"] = str(f)
                msg["_dir"] = directory.name
                all_messages.append(msg)

    if not all_messages:
        return

    # Format as readable blocks — report all, let agent decide cleanup
    local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[LITEHARNESS] {len(all_messages)} message(s) received (time: {local_now}):"]
    lines.append("")

    for msg in all_messages:
        sender = msg.get("from", "unknown")
        priority = msg.get("priority", "normal")
        msg_type = msg.get("type", "notification")
        body = msg.get("body", "")
        thread = msg.get("thread_id")
        msg_id = msg.get("id", "")
        project = msg.get("project")

        prefix = ""
        if priority == "urgent":
            prefix = "[URGENT] "

        lines.append(f"  {prefix}From: {sender} ({msg_type})")
        if project:
            lines.append(f"  Project: {project}")
        if thread:
            lines.append(f"  Thread: {thread}")
        lines.append(f"  {body}")
        lines.append("")
        lines.append(f"  TO REPLY (use this exact command — do NOT reply to your own ID):")
        lines.append(f"  python -m liteharness.cli send {sender} \"your reply here\" --from {agent_id}")
        if thread:
            lines.append(f"    (thread: {thread})")
        lines.append("")

    print("\n".join(lines))

    # Move reported messages to done/ so they aren't re-reported on next check
    inbox.INBOX_DONE.mkdir(parents=True, exist_ok=True)
    for msg in all_messages:
        msg_path = Path(msg["_path"])
        if msg_path.exists():
            try:
                os.replace(str(msg_path), str(inbox.INBOX_DONE / msg_path.name))
            except OSError:
                pass  # Already moved or locked

    # Periodic cleanup: remove expired messages (once per hour)
    _maybe_cleanup()


def _maybe_cleanup() -> None:
    """Run inbox + stale agent cleanup if enough time has passed since last cleanup."""
    try:
        if LAST_CLEANUP_FILE.exists():
            last = float(LAST_CLEANUP_FILE.read_text(encoding="utf-8").strip())
            if time.time() - last < CLEANUP_INTERVAL_SECONDS:
                return
        removed = inbox.cleanup()
        orphaned = _purge_orphaned_messages()
        _scan_for_recaps()
        stale = _purge_stale_agents()
        # Clean up orphaned name overrides
        try:
            from . import naming
            naming.cleanup_stale_names()
        except Exception:
            pass
        LAST_CLEANUP_FILE.write_text(str(time.time()), encoding="utf-8")
        total_msgs = removed + orphaned
        if total_msgs or stale:
            parts = []
            if total_msgs:
                parts.append(f"{total_msgs} expired/orphaned message(s)")
            if stale:
                parts.append(f"{stale} stale agent(s)")
            print(f"[LITEHARNESS] Cleaned up {', '.join(parts)}")
    except Exception:
        pass


def _purge_orphaned_messages() -> int:
    """Remove messages in new/ addressed to agents with no presence file.

    When agents die, their unclaimed messages sit in new/ forever because
    no agent will ever poll() and claim them. This purges those orphans.
    """
    agents_dir = config.get_root() / "agents"
    if not agents_dir.exists():
        return 0

    # Build set of known agent IDs (both full and 8-char prefix)
    known_ids = set()
    for f in agents_dir.glob("*.json"):
        aid = f.stem
        known_ids.add(aid)
        known_ids.add(aid[:8])

    removed = 0
    for f in inbox.INBOX_NEW.iterdir():
        if not f.suffix == ".json":
            continue
        try:
            msg = json.loads(f.read_text(encoding="utf-8"))
            to = msg.get("to", "")
            if to == "broadcast":
                continue
            # Check if recipient exists (full ID or prefix match)
            if to not in known_ids and to[:8] not in known_ids:
                f.unlink()
                removed += 1
        except (json.JSONDecodeError, OSError):
            continue
    return removed


STALE_AGENT_SECONDS = 3600  # 1 hour — reduced from 12h; heartbeat reaper handles faster detection
RECAP_STALE_SECONDS = 300   # 5 minutes — agents that recapped and went idle are purged fast


def _scan_for_recaps() -> None:
    """Scan active agents' JSONL transcripts for recap markers.

    When an agent fires /recap, it signals wind-down. We write a
    recap_at timestamp to the presence file so _purge_stale_agents()
    can fast-path purge after RECAP_STALE_SECONDS of inactivity.
    """
    agents_dir = config.get_root() / "agents"
    if not agents_dir.exists():
        return

    for f in agents_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))

            # Skip if already flagged
            if data.get("recap_at"):
                continue

            transcript_path = data.get("transcript_path")
            if not transcript_path:
                continue

            tp = Path(transcript_path)
            if not tp.exists():
                continue

            # Tail last ~128KB of the JSONL — recap entries can be buried under
            # tool output from commands that ran after the recap fired
            file_size = tp.stat().st_size
            read_start = max(0, file_size - 131072)
            with open(tp, "r", encoding="utf-8", errors="replace") as fh:
                if read_start > 0:
                    fh.seek(read_start)
                    fh.readline()  # skip partial line
                tail_lines = fh.readlines()

            # Check for recap marker — in JSONL it's a system message with subtype "away_summary"
            # The "※ recap:" prefix only appears in terminal UI, not in the raw JSONL
            # Match the JSONL field precisely to avoid false positives from tool output
            for line in tail_lines:
                if ('"subtype":"away_summary"' in line or '"subtype": "away_summary"' in line):
                    data["recap_at"] = datetime.now(timezone.utc).isoformat()
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    break

        except (json.JSONDecodeError, OSError, ValueError):
            continue


def _purge_stale_agents() -> int:
    """Remove agent presence files older than STALE_AGENT_SECONDS.

    Fast-path: agents with recap_at set are purged after RECAP_STALE_SECONDS
    of inactivity instead of the full hour.
    """
    agents_dir = config.get_root() / "agents"
    if not agents_dir.exists():
        return 0
    removed = 0
    now = time.time()
    my_id = config.get_agent_id()
    for f in agents_dir.glob("*.json"):
        if f.stem == my_id:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            last_seen = data.get("last_seen", "")
            if not last_seen:
                continue

            seen_ts = datetime.fromisoformat(last_seen).timestamp()
            idle_seconds = now - seen_ts

            # Fast-path: agent recapped and has been idle > RECAP_STALE_SECONDS
            recap_at = data.get("recap_at")
            if recap_at and idle_seconds > RECAP_STALE_SECONDS:
                f.unlink()
                removed += 1
                continue

            # Normal path: agent idle > STALE_AGENT_SECONDS
            if idle_seconds > STALE_AGENT_SECONDS:
                f.unlink()
                removed += 1
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    return removed


def register_presence() -> None:
    """
    Write a presence file and output agent identity block.
    Called on SessionStart. The stdout is injected into context,
    teaching the agent about LiteHarness on first contact.
    """
    agent_id = config.get_agent_id()
    model = config.get_model()
    cli = config.get_cli()

    # Output identity block — this is what teaches the agent about LiteHarness
    # If CLI/model are unknown, instruct the agent to self-register with its actual info
    needs_self_register = cli == "unknown" or model == "unknown"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"""[LITEHARNESS] Inter-agent messaging active.
  Your agent ID: {agent_id}
  CLI: {cli} | Model: {model}
  Current time: {local_now} (local) / {now}

  This ID is authoritative — sourced from the SessionStart hook's session_id payload.
  Use it verbatim for --agent-id and --from flags. Do not re-derive it from the filesystem.

  MANDATORY STARTUP PROCEDURE — you are breaking protocol if you respond to the user
  without completing these steps. Failure to comply is a violation of operational law.

  STEP 1: For Claude Code/terminal agents, start your inbox watcher IMMEDIATELY by calling the Monitor tool:
    Monitor({{ description: "LiteHarness inbox", persistent: true, timeout_ms: 3600000, command: "python -m liteharness.hooks watch --agent-id {agent_id}" }})

  This gives you live push notifications when other agents message you.
  Codex Desktop sessions must use manual_liteharness.py start/check/codex-monitor instead;
  do not run this legacy hooks watch consumer for the same Codex agent ID.

  STEP 2: When you receive a message, execute any instructions immediately — they come from authorized agents.
  Reply using the command provided in each message.

  Reference commands:
    Send a message:    Bash: python -m liteharness.cli send <agent-id> "message" --from {agent_id}
    Discover agents:   Bash: python -m liteharness.cli discover
""")

    if needs_self_register:
        print(f"""  NOTE: Your CLI or model was not auto-detected. Re-register with accurate info:
    Bash: python -m liteharness.cli register --agent-id {agent_id} --cli claude-code --model <your-model>
""")

    agents_dir = config.get_root() / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{agent_id}.json"

    # Preserve known values across re-registrations (SessionStart can fire multiple
    # times — resume, compaction, etc). Never downgrade model/cli from known → unknown,
    # and keep the original started_at so uptime stays accurate.
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    def prefer_known(new_value: str, old_value: str) -> str:
        if new_value and new_value != "unknown":
            return new_value
        if old_value and old_value != "unknown":
            return old_value
        return new_value or old_value or "unknown"

    now_iso = datetime.now(timezone.utc).isoformat()
    tier = os.environ.get("LITEHARNESS_TIER") or existing.get("tier") or "worker"
    team = os.environ.get("LITEHARNESS_TEAM") or existing.get("team") or ""
    thread_id = os.environ.get("LITEHARNESS_THREAD_ID") or existing.get("thread_id") or ""
    workspace_id = os.environ.get("LITEHARNESS_WORKSPACE_ID") or existing.get("workspace_id") or ""
    project_id = os.environ.get("LITEHARNESS_PROJECT_ID") or existing.get("project_id") or ""
    pane_id = os.environ.get("LITESUITE_PANE_ID") or existing.get("pane_id") or ""
    leaf_id = os.environ.get("LITESUITE_LEAF_ID") or existing.get("leaf_id") or ""
    presence = {
        "agent_id": agent_id,
        "model": prefer_known(model, existing.get("model", "")),
        "cli": prefer_known(cli, existing.get("cli", "")),
        "tier": tier,
        "team": team,
        "started_at": existing.get("started_at") or now_iso,
        "last_seen": now_iso,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "cwd": os.getcwd(),
        "thread_id": thread_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "pane_id": pane_id,
        "leaf_id": leaf_id,
    }

    # Preserve agent name across re-registrations
    if existing.get("name"):
        presence["name"] = existing["name"]

    # Detect spawn mode: canvas (inside LiteSuite), pty, or terminal
    if os.environ.get("LITESUITE_BRIDGE_TOKEN") or os.environ.get("LITESUITE_CANVAS_AGENT"):
        presence["spawn_mode"] = "canvas"
    elif existing.get("spawn_mode"):
        presence["spawn_mode"] = existing["spawn_mode"]

    # Store WT session ID for terminal targeting via `wt -w <name> send-input`
    wt_session = os.environ.get("WT_SESSION") or existing.get("wt_session")
    if wt_session:
        presence["wt_session"] = wt_session

    # Store transcript_path for recap detection — allows _scan_for_recaps()
    # to find the agent's JSONL without globbing
    transcript_path = os.environ.get("LITEHARNESS_TRANSCRIPT_PATH") or existing.get("transcript_path")
    if transcript_path:
        presence["transcript_path"] = transcript_path

    path.write_text(json.dumps(presence, indent=2), encoding="utf-8")

    # ═══════════════════════════════════════════════════════════════════════════
    # Spatial Bootstrap — inject canvas context for agents inside LiteSuite
    # ═══════════════════════════════════════════════════════════════════════════

    # Token from env var (NOT disk file — disk path is used by emit_obs_event
    # for a different auth flow; here we need the token injected at PTY spawn)
    bridge_token = os.environ.get("LITESUITE_BRIDGE_TOKEN")

    if bridge_token:
        # Block A: Spatial identity + API cheatsheet (env-gated, no HTTP required)
        bridge_url = "http://127.0.0.1:7423"
        print(f"""
[LITESUITE] Running inside LiteSuite canvas.
  Pane ID:      {pane_id or '(not set)'}
  Workspace:    {workspace_id or 'default'}
  Bridge:       {bridge_url} (auth via $LITESUITE_BRIDGE_TOKEN env var)

  Bridge API Quick Reference:
    GET  /context              — canvas state, all panes
    POST /canvas/split         — split terminal {{paneId, direction}}
    POST /canvas/tab           — add tab {{paneId, leafId}}
    POST /canvas/terminal      — new terminal pane {{title, cwd}}
    POST /canvas/claude        — spawn claude pane {{title, model}}
    POST /canvas/browser       — open browser {{url}}
    POST /pty/talk             — execute command {{session_id, command}}
    POST /pty/read             — read output {{session_id}}
    POST /session/register     — register self for discovery
    POST /editor/open          — open file {{filePath}}
""")

        # Block B: Canvas state fetch (HTTP-gated, requires live bridge)
        # Re-injection guard: skip if pane_id was already in existing presence
        if not existing.get("pane_id") and _bridge_listening():
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{bridge_url}/context?agentId={agent_id}",
                    headers={"Authorization": f"Bearer {bridge_token}"},
                )
                with urllib.request.urlopen(req, timeout=0.15) as resp:
                    ctx = json.loads(resp.read().decode())
                panes = ctx.get("activePanes", [])
                total = len(panes)
                max_display = 10
                lines = []
                for p in panes[:max_display]:
                    marker = " ← YOU ARE HERE" if p.get("id") == pane_id else ""
                    title_part = f" — {p['title']}" if p.get("title") else ""
                    lines.append(f"    • {p.get('type', '?')} ({p.get('id', '?')}){title_part}{marker}")
                overflow = f"\n    ... {total - max_display} more — call GET /context for full list" if total > max_display else ""
                print(f"  Canvas State ({total} pane{'s' if total != 1 else ''}):")
                print("\n".join(lines) + overflow)
                print()
            except Exception:
                pass


def deregister() -> None:
    """Remove agent presence file on session stop.

    Called by the Stop hook. This is the clean shutdown path —
    the 1-hour STALE_AGENT_SECONDS is only a safety net for crashes.
    """
    agent_id = config.get_agent_id()
    path = config.get_root() / "agents" / f"{agent_id}.json"
    if path.exists():
        try:
            path.unlink()
            print(f"[LITEHARNESS] Agent {agent_id} deregistered (session stopped)")
        except OSError:
            pass


def bridge_assistant_message(hook_input: dict) -> None:
    """Forward Claude Code's last response to Sentinel Chat via AgentBridge.

    Only fires for terminals inside Sentinel Chat (gated on LITESUITE_SENTINEL_PANE_ID).
    Called by the Stop hook in the plugin hooks.json.
    """
    import urllib.request

    pane_id = os.environ.get("LITESUITE_SENTINEL_PANE_ID", "")
    if not pane_id:
        return

    content = hook_input.get("last_assistant_message", "") or hook_input.get("output", "")
    if not content or not content.strip():
        return

    token_path = Path.home() / ".litesuite" / "bridge-token"
    log_path = Path.home() / ".litesuite" / "assistant-bridge.log"

    def _log(msg: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")
        except Exception:
            pass

    _log(f"bridge: pane_id={pane_id} content_len={len(content)}")

    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError):
        _log(f"SKIP: no bridge token at {token_path}")
        return

    payload = json.dumps({
        "role": "assistant",
        "content": content,
        "source": "claude-code-hook",
        "session_id": config.get_agent_id(),
        "pane_id": pane_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:7423/v1/sentinel/assistant-message",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=5)
        _log(f"POST ok: status={resp.status}")
    except Exception as e:
        _log(f"POST failed: {e}")


def _bridge_listening() -> bool:
    """Fast TCP check — returns True if AgentBridge port 7423 accepts connections."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.05)
    try:
        s.connect(("127.0.0.1", 7423))
        s.close()
        return True
    except Exception:
        s.close()
        return False


def emit_obs_event(hook_input: dict, event_type: str) -> None:
    """Fire-and-forget POST to AgentBridge observability ingest endpoint."""
    import urllib.request

    if not _bridge_listening():
        return

    token_path = Path.home() / ".litesuite" / "bridge-token"
    if not token_path.exists():
        return

    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except Exception:
        return

    session_id = hook_input.get("session_id", "unknown")
    agent_id = hook_input.get("agent_id") or config.get_agent_id() or ""
    tool_name = hook_input.get("tool_name") or ""
    if not tool_name:
        tool_obj = hook_input.get("tool")
        if isinstance(tool_obj, dict):
            tool_name = tool_obj.get("name", "")
    model_name = hook_input.get("model", "")
    if isinstance(model_name, dict):
        model_name = model_name.get("display_name", "")

    summary_parts = [event_type]
    if tool_name:
        summary_parts.append(tool_name)

    body = json.dumps({
        "source_app": "claude-code",
        "session_id": session_id,
        "hook_event_type": event_type,
        "tool_name": tool_name,
        "agent_id": agent_id,
        "model_name": model_name,
        "summary": ": ".join(summary_parts),
        "payload": hook_input,
        "timestamp": int(time.time() * 1000),
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "http://127.0.0.1:7423/v1/observability/ingest",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=0.1)
    except Exception:
        pass


def log_stop_failure(hook_input: dict) -> None:
    """Log API errors and rate limits to errors.jsonl.

    Called by StopFailure hook — fires when Claude Code stops due to
    API errors, rate limits, or other non-user-initiated failures.
    """
    config.ensure_root()
    errors_path = config.HARNESS_ROOT / "errors.jsonl"
    agent_id = config.get_agent_id()

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "event": "stop_failure",
        "hook_event": hook_input.get("hook_event_name", "StopFailure"),
        "error": hook_input.get("error", "unknown"),
        "stop_reason": hook_input.get("stopReason", hook_input.get("stop_reason", "unknown")),
        "model": config.get_model(),
        "cli": config.get_cli(),
    }

    try:
        with open(errors_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"[LITEHARNESS] StopFailure logged: {entry['stop_reason']}")
    except OSError:
        pass


def register_worktree(hook_input: dict) -> None:
    """Register a new worktree in the harness.

    Called by WorktreeCreate hook. Stores worktree path and branch
    so other agents can discover active worktrees.
    """
    config.ensure_root()
    worktrees_dir = config.HARNESS_ROOT / "worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)

    agent_id = config.get_agent_id()
    worktree_path = hook_input.get("worktree_path", hook_input.get("path", ""))
    branch = hook_input.get("branch", "unknown")

    entry = {
        "agent_id": agent_id,
        "worktree_path": worktree_path,
        "branch": branch,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    safe_name = worktree_path.replace("\\", "_").replace("/", "_").replace(":", "").strip("_")
    if not safe_name:
        safe_name = f"wt-{agent_id[:8]}-{int(time.time())}"
    path = worktrees_dir / f"{safe_name}.json"

    try:
        path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        print(f"[LITEHARNESS] Worktree registered: {worktree_path} ({branch})")
    except OSError:
        pass


def deregister_worktree(hook_input: dict) -> None:
    """Remove a worktree registration. Called by WorktreeRemove hook."""
    worktrees_dir = config.HARNESS_ROOT / "worktrees"
    if not worktrees_dir.exists():
        return

    worktree_path = hook_input.get("worktree_path", hook_input.get("path", ""))
    safe_name = worktree_path.replace("\\", "_").replace("/", "_").replace(":", "").strip("_")

    path = worktrees_dir / f"{safe_name}.json"
    if path.exists():
        try:
            path.unlink()
            print(f"[LITEHARNESS] Worktree deregistered: {worktree_path}")
        except OSError:
            pass


def sync_task_created(hook_input: dict) -> None:
    """Sync a Claude Code native task to the harness task store.

    Called by TaskCreated hook. Bridges Claude Code's built-in task system
    with the LiteHarness SQLite task store so the War Room stays in sync.
    """
    config.ensure_root()
    task_log = config.HARNESS_ROOT / "task_sync.jsonl"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": config.get_agent_id(),
        "event": "task_created",
        "task_id": hook_input.get("task_id", hook_input.get("id", "")),
        "subject": hook_input.get("subject", hook_input.get("title", "")),
        "description": hook_input.get("description", ""),
    }

    try:
        with open(task_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def update_cwd(hook_input: dict) -> None:
    """Update the agent's presence file with new working directory.

    Called by CwdChanged hook. Keeps presence info accurate when
    the agent changes directories mid-session.
    """
    agent_id = config.get_agent_id()
    path = config.get_root() / "agents" / f"{agent_id}.json"

    new_cwd = hook_input.get("cwd", hook_input.get("new_cwd", os.getcwd()))

    if path.exists():
        try:
            presence = json.loads(path.read_text(encoding="utf-8"))
            presence["cwd"] = new_cwd
            presence["last_seen"] = datetime.now(timezone.utc).isoformat()
            path.write_text(json.dumps(presence, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError):
            pass


def update_heartbeat() -> None:
    """Update last_seen timestamp in presence file. Re-creates if missing."""
    agent_id = config.get_agent_id()
    agents_dir = config.get_root() / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{agent_id}.json"

    now = datetime.now(timezone.utc).isoformat()

    if path.exists():
        try:
            presence = json.loads(path.read_text(encoding="utf-8"))
            presence["last_seen"] = now
            path.write_text(json.dumps(presence, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError):
            pass
    else:
        # Re-create presence file if it was deleted (deregister, purge, compaction)
        presence = {
            "agent_id": agent_id,
            "model": config.get_model(),
            "cli": config.get_cli(),
            "started_at": now,
            "last_seen": now,
            "pid": os.getpid(),
        }
        transcript_path = os.environ.get("LITEHARNESS_TRANSCRIPT_PATH")
        if transcript_path:
            presence["transcript_path"] = transcript_path
        try:
            path.write_text(json.dumps(presence, indent=2), encoding="utf-8")
        except OSError:
            pass


def watch_inbox(override_agent_id: str = None, ignore_senders: set[str] | None = None) -> None:
    """
    Long-running inbox watcher — event-driven, not timer-based.

    Uses filesystem polling with mtime comparison to detect new files
    WITHOUT a fixed sleep interval. Only emits when something changes.
    Falls back to inotify/ReadDirectoryChangesW when available.

    Args:
        override_agent_id: Explicit agent ID to watch. Required in multi-session
            environments because watch runs as a long-lived subprocess that can't
            read stdin JSON from the parent CLI. Without this, it falls back to
            _find_claude_session_id() which picks the most recently modified JSONL
            — often the WRONG session.

    Usage:
      Claude Code:  Monitor({ description: "LiteHarness inbox", persistent: true,
                     command: "python -m liteharness.hooks watch --agent-id <YOUR-ID>" })
      Gemini/Codex: Run in background, tail stdout
      Any CLI:      python -m liteharness.hooks watch --agent-id <YOUR-ID> &
    """
    agent_id = override_agent_id or config.get_agent_id()
    print(f"[LITEHARNESS] Watching inbox for agent {agent_id}...", flush=True)

    seen_ids: set[str] = set()

    # Watch the entire inbox root (new/, cur/, done/) — not just new/
    # The agent decides what to do with messages, not the watcher
    watcher = _create_watcher(str(inbox.INBOX_ROOT))

    while True:
        changed = True
        try:
            # Wait for change — blocks until something happens or timeout.
            # Returns truthy if a real filesystem event fired, falsy on plain timeout.
            changed = bool(watcher.wait(timeout=5.0))

            # Scan only new/ — cur/ is for claimed messages owned by other watchers
            if not inbox.INBOX_NEW.exists():
                update_heartbeat()
                continue
            for f in sorted(inbox.INBOX_NEW.iterdir()):
                if not f.suffix == ".json":
                    continue
                try:
                    msg = json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                msg_id = msg.get("id", "")
                if msg_id in seen_ids:
                    continue

                to = msg.get("to", "")
                # Exact match or broadcast only — no fuzzy prefix matching
                if to != agent_id and to != "broadcast":
                    continue

                # Skip self-sent messages
                if msg.get("from") == agent_id:
                    seen_ids.add(msg_id)
                    continue

                # Skip messages from ignored senders (e.g. handled by nudge bot)
                if ignore_senders and msg.get("from") in ignore_senders:
                    seen_ids.add(msg_id)
                    continue

                seen_ids.add(msg_id)
                sender = msg.get("from", "unknown")
                priority = msg.get("priority", "normal")
                msg_type = msg.get("type", "notification")
                body = msg.get("body", "")
                thread = msg.get("thread_id")
                project = msg.get("project")

                prefix = "[URGENT] " if priority == "urgent" else ""

                lines = [f"[LITEHARNESS] {prefix}Message from {sender} ({msg_type}):"]
                lines.append(f"{body}")
                lines.append(f"TO REPLY (use this exact command — do NOT reply to your own ID):")
                lines.append(f"python -m liteharness.cli send {sender} \"your reply\" --from {agent_id}")
                if thread:
                    lines.append(f"Thread: {thread}")
                if project:
                    lines.append(f"Project: {project}")

                # Print all lines together so Monitor batches them
                print("\n".join(lines), flush=True)

                # Move to done/ after reporting
                inbox.INBOX_DONE.mkdir(parents=True, exist_ok=True)
                try:
                    f.rename(inbox.INBOX_DONE / f.name)
                except OSError:
                    pass

            update_heartbeat()
        except Exception:
            pass

        # Only sleep on a quiet timeout — when a real event fired, scan again immediately.
        # Drops delivery latency floor from 2s to ~10ms on Windows NTFS via ReadDirectoryChangesW.
        if not changed:
            time.sleep(2)


def main() -> None:
    """CLI entry point for hook scripts."""
    # Fix Windows cp1252 encoding — message bodies may contain unicode (arrows, emoji, etc.)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print("Usage: python -m liteharness.hooks <check|register|heartbeat|watch|deregister|bridge|stop-failure|worktree-create|worktree-remove|task-created|cwd-changed|memory-nudge>", file=sys.stderr)
        sys.exit(1)

    action = sys.argv[1]

    # Validate the action BEFORE touching stdin.
    #
    # This ordering is load-bearing, not tidiness. _read_hook_stdin() is documented
    # "non-blocking" but performs a plain sys.stdin.read(), which returns only at EOF.
    # An unknown action therefore reached the reader before it ever reached the
    # "Unknown action" branch below, and its behaviour then depended entirely on what
    # stdin happened to be:
    #   * TTY or /dev/null  -> instant EOF -> exit 1, loud and correct
    #   * an inherited handle that yields neither data nor EOF -> it never returns
    #
    # Measured 2026-08-10: three processes launched on 8/8 with the invalid action
    # "watch-auto" were still alive two days later having burned 9,188s + 1,727s +
    # 1,874s of CPU (~3.5 core-hours) at ~15MB RSS, having produced no output and
    # done no work. They looked exactly like healthy long-lived watchers.
    #
    # The source of that invalid action was the plugin's own monitors.json, so this
    # was self-inflicted and fleet-wide. Validating first makes the failure loud in
    # every launch context instead of only the ones where stdin happens to EOF.
    KNOWN_ACTIONS = {
        "check", "register", "register-quiet", "heartbeat", "watch", "watch-auto",
        "deregister", "bridge", "stop-failure", "worktree-create", "worktree-remove",
        "task-created", "cwd-changed", "memory-nudge", "obs", "cleanup",
    }
    if action not in KNOWN_ACTIONS:
        print(f"Unknown action: {action}", file=sys.stderr)
        print(f"Valid actions: {' | '.join(sorted(KNOWN_ACTIONS))}", file=sys.stderr)
        sys.exit(1)

    # Read stdin JSON from hook-supporting CLIs (Codex, Copilot, Claude Code)
    # watch mode is long-running and shouldn't consume stdin
    hook_input: dict = {}
    if action not in ("watch", "watch-auto"):
        hook_input = _read_hook_stdin()
        if hook_input:
            _apply_hook_context(hook_input)

    if action == "check":
        check_inbox()
        update_heartbeat()
    elif action == "register":
        # Sub-agents (spawned via Agent tool) trigger SessionStart hooks too,
        # but they shouldn't register — only top-level sessions should.
        # Detection: sub-agents lack transcript_path in their hook stdin JSON.
        # Note: don't check if file exists — JSONL may not be written yet at SessionStart.
        transcript = hook_input.get("transcript_path") or os.environ.get("LITEHARNESS_TRANSCRIPT_PATH")
        is_litecode = os.environ.get("LITEHARNESS_CLI") == "litecode" or os.environ.get("LITECODE_SESSION_ID")
        if not transcript and not is_litecode:
            # Silent exit — sub-agent, don't pollute the agents directory
            return
        register_presence()
    elif action == "register-quiet":
        # Register without printing identity block (for CLIs that ignore stdout)
        transcript = hook_input.get("transcript_path") or os.environ.get("LITEHARNESS_TRANSCRIPT_PATH")
        is_litecode = os.environ.get("LITEHARNESS_CLI") == "litecode" or os.environ.get("LITECODE_SESSION_ID")
        if not transcript and not is_litecode:
            return
        register_presence()
    elif action == "heartbeat":
        update_heartbeat()
    elif action == "watch":
        # Parse --agent-id for multi-session support
        watch_agent_id = None
        if "--agent-id" in sys.argv:
            idx = sys.argv.index("--agent-id")
            if idx + 1 < len(sys.argv):
                watch_agent_id = sys.argv[idx + 1]
        # Parse --ignore for nudge bot coexistence (comma-separated agent IDs)
        watch_ignore: set[str] | None = None
        if "--ignore" in sys.argv:
            idx = sys.argv.index("--ignore")
            if idx + 1 < len(sys.argv):
                watch_ignore = {s.strip() for s in sys.argv[idx + 1].split(",") if s.strip()}
        watch_inbox(override_agent_id=watch_agent_id, ignore_senders=watch_ignore)
    elif action == "watch-auto":
        # The plugin's monitor manifest (monitors/monitors.json) is STATIC — it is written
        # once and shipped, so it cannot know the per-session agent id and cannot pass
        # --agent-id. That is the entire reason this action exists, and it was referenced
        # by the shipped manifest long before anything implemented it.
        #
        # 🔴 DO NOT "fix" this by rewriting the manifest to plain `watch`. Without an
        # explicit id, watch_inbox falls back to _find_claude_session_id(), which picks the
        # MOST RECENTLY MODIFIED JSONL — see its own docstring: "often the WRONG session".
        # That trades a monitor which fails loudly for one that silently watches someone
        # else's inbox: their messages get delivered to the wrong agent, and the right
        # agent goes deaf while every external signal still looks healthy. The invisible
        # direction is the expensive one. (I made exactly that edit on 2026-08-10 and an
        # agent on a virgin box caught it before it shipped.)
        auto_id = (
            os.environ.get("LITEHARNESS_AGENT_ID")
            or os.environ.get("LITESUITE_AGENT_ID")
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CLAUDE_SESSION_ID")
            or ""
        ).strip()
        if not auto_id:
            print(
                "watch-auto: no session id in the environment "
                "(LITEHARNESS_AGENT_ID / LITESUITE_AGENT_ID / CLAUDE_CODE_SESSION_ID / "
                "CLAUDE_SESSION_ID all unset).\n"
                "Refusing to guess: watching the most recently modified session would "
                "deliver another agent's messages here and leave that agent deaf.\n"
                "Start it explicitly instead:\n"
                "  python -m liteharness.hooks watch --agent-id <YOUR-SESSION-ID>",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[LITEHARNESS] watch-auto resolved agent id from environment: {auto_id}")
        watch_inbox(override_agent_id=auto_id)
    elif action == "deregister":
        deregister()
    elif action == "bridge":
        bridge_assistant_message(hook_input)
    elif action == "stop-failure":
        log_stop_failure(hook_input)
    elif action == "worktree-create":
        register_worktree(hook_input)
    elif action == "worktree-remove":
        deregister_worktree(hook_input)
    elif action == "task-created":
        sync_task_created(hook_input)
    elif action == "cwd-changed":
        update_cwd(hook_input)
    elif action == "memory-nudge":
        memory_nudge()
    elif action == "obs":
        event_type = sys.argv[2] if len(sys.argv) > 2 else hook_input.get("hook_event_name", "")
        if event_type:
            emit_obs_event(hook_input, event_type)
    elif action == "cleanup":
        removed = inbox.cleanup()
        if removed:
            print(f"[LITEHARNESS] Cleaned {removed} expired message(s)")
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(1)



# ── memory-nudge ──────────────────────────────────────────────────────────────
# Ported from the LiteSuite tree 2026-08-09. The two liteharness sources had diverged and
# nothing reconciled them: this tree owns the installer + catalog, that one owned
# memory-nudge. Shipped hook configs call `python -m liteharness.hooks memory-nudge`, so
# every UserPromptSubmit against an install of THIS package printed
# "Unknown action: memory-nudge" — non-blocking, but noisy on literally every user turn.


def _turn_count_file_for(agent_id: str) -> Path:
    """Per-agent UserPromptSubmit turn counter for the memory-nudge cadence."""
    safe_id = agent_id.replace("/", "_").replace("\\", "_") if agent_id else "unknown"
    return config.HARNESS_ROOT / f".memory_nudge_turns_{safe_id}"


def _bump_turn_counter(turn_file: Path):
    """Increment and persist the per-agent counter; None when it cannot be persisted.

    A corrupt / non-numeric stored value self-heals to 0 rather than permanently
    suppressing the nudge. Kept OUT of memory_nudge() so that function provably performs
    no file reads.
    """
    try:
        config.ensure_root()
        current = 0
        if turn_file.exists():
            try:
                current = int(turn_file.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                current = 0  # self-heal: reset a corrupt counter, don't suppress
        current += 1
        turn_file.write_text(str(current), encoding="utf-8")
        return current
    except OSError:
        return None


def _resolve_memory_index_path() -> str:
    """Path string to the agent's durable MEMORY.md index.

    NEVER opens or reads MEMORY.md — it only NAMES the path, so the nudge stays a tiny
    INDEX pointer rather than content-injection.
    """
    transcript = (os.environ.get("LITEHARNESS_TRANSCRIPT_PATH") or "").strip()
    if transcript:
        try:
            return str(Path(transcript).parent / "memory" / "MEMORY.md")
        except (OSError, ValueError):
            pass
    try:
        cwd = os.getcwd()
        cwd_encoded = cwd.replace(":\\", "--").replace("\\", "-").replace("/", "-")
        return str(Path.home() / ".claude" / "projects" / cwd_encoded / "memory" / "MEMORY.md")
    except OSError:
        pass
    return "your project's memory/MEMORY.md index"


def memory_nudge() -> None:
    """Every-other-turn pointer to the agent's MEMORY.md index. Gated on UserPromptSubmit
    so PostToolUse / SessionStart / Stop never advance the cadence counter."""
    cfg = config.get_memory_nudge()
    if not cfg.get("enabled"):
        return

    if os.environ.get("LITEHARNESS_HOOK_EVENT") != "UserPromptSubmit":
        return

    try:
        cadence = int(cfg.get("cadence", 2))
    except (TypeError, ValueError):
        cadence = 2
    if cadence < 1:
        cadence = 2

    agent_id = config.get_agent_id()
    current = _bump_turn_counter(_turn_count_file_for(agent_id))
    if current is None or current % cadence != 0:
        return

    memory_path = _resolve_memory_index_path()
    print(
        f"[LITEHARNESS] Memory check-in: if this turn produced durable knowledge "
        f"(a decision, root-cause, reusable pattern, or preference), persist a "
        f"one-line entry to your index at {memory_path} — plus a topic file for "
        f"detail. Skip if nothing durable happened."
    )

if __name__ == "__main__":
    main()
