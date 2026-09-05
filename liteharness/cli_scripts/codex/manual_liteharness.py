#!/usr/bin/env python3
"""Manual LiteHarness bootstrap for Codex sessions without working hooks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import liteharness.config as config
import liteharness.hooks as hooks
import liteharness.inbox as inbox


DEFAULT_ROOT = Path.home() / ".liteharness"
SESSION_STATE_DIR = "codex_sessions"
LEGACY_AGENT_FILE = "codex_agent.json"
CODEX_DESKTOP_ORIGINATOR = "Codex Desktop"
CODEX_DESKTOP_CLI = "codex-desktop"
CODEX_MANUAL_CLI = "codex-cli-manual"
CODEX_MONITOR_STATE_ROOT = Path.home() / ".codex" / "memories" / "liteharness"
CODEX_NOTIFY_SCRIPT = (
    Path.home() / ".codex" / "skills" / "liteharness" / "scripts" / "liteharness_notify.py"
)
MAX_MONITOR_HEARTBEAT_AGE_SEC = 30.0


def configure_root(root: Path) -> Path:
    root = root.resolve()

    config.HARNESS_ROOT = root
    config.CONFIG_PATH = root / "config.json"

    inbox.INBOX_ROOT = root / "inbox"
    inbox.INBOX_NEW = inbox.INBOX_ROOT / "new"
    inbox.INBOX_CUR = inbox.INBOX_ROOT / "cur"
    inbox.INBOX_DONE = inbox.INBOX_ROOT / "done"
    inbox.INBOX_TMP = inbox.INBOX_ROOT / "tmp"

    hooks.LAST_CHECK_FILE = root / ".last_inbox_check"
    return root


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_project() -> str:
    return str(Path.cwd())


def detect_process_context() -> dict:
    if os.name != "nt":
        return {}
    script = r"""
$ErrorActionPreference = 'Stop'
$startPid = [int]$env:LITEHARNESS_START_PID
$seen = @{}
$rows = @()
$currentPid = $startPid
while ($currentPid -and -not $seen.ContainsKey($currentPid)) {
  $seen[$currentPid] = $true
  $row = Get-CimInstance Win32_Process -Filter "ProcessId=$currentPid"
  if (-not $row) { break }
  $rows += [pscustomobject]@{
    pid = [int]$row.ProcessId
    parent = [int]$row.ParentProcessId
    name = [string]$row.Name
    cmdline = if ($null -ne $row.CommandLine) { [string]$row.CommandLine } else { '' }
  }
  $currentPid = [int]$row.ParentProcessId
}
$rows | ConvertTo-Json -Depth 4 -Compress
"""
    env = os.environ.copy()
    env["LITEHARNESS_START_PID"] = str(os.getpid())
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return {}
    context: dict = {"ancestry": rows}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).lower()
        cmdline = str(row.get("cmdline", "")).lower()
        if name == "codex.exe" or "@openai/codex" in cmdline:
            context.setdefault("codex_pid", row.get("pid"))
        if name == "windowsterminal.exe":
            context.setdefault("wt_process_pid", row.get("pid"))
    return context


def is_codex_desktop() -> bool:
    return (
        os.environ.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "").strip().lower()
        == CODEX_DESKTOP_ORIGINATOR.lower()
    )


def current_surface() -> str:
    return "desktop" if is_codex_desktop() else "terminal"


def current_cli() -> str:
    return CODEX_DESKTOP_CLI if is_codex_desktop() else CODEX_MANUAL_CLI


def current_originator() -> str | None:
    return CODEX_DESKTOP_ORIGINATOR if is_codex_desktop() else None


def identity_fields() -> dict[str, str]:
    fields = {
        "cli": current_cli(),
        "surface": current_surface(),
    }
    originator = current_originator()
    if originator:
        fields["originator"] = originator
    return fields


def with_identity_header(agent_id: str, body: str) -> str:
    if not is_codex_desktop():
        return body
    header = f"[FROM: Codex Desktop | agent_id={agent_id} | project={current_project()}]"
    if body.startswith(header):
        return body
    return f"{header}\n{body}"


def codex_session_key() -> str | None:
    for env_name in ("CODEX_THREAD_ID", "WT_SESSION"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return None


def session_state_path() -> Path | None:
    session_key = codex_session_key()
    if not session_key:
        return None
    safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session_key)
    return config.get_root() / SESSION_STATE_DIR / f"{safe_key}.json"


def load_saved_agent_id() -> str:
    candidates: list[Path] = []
    session_path = session_state_path()
    if session_path is not None:
        candidates.append(session_path)
    candidates.append(config.get_root() / LEGACY_AGENT_FILE)

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        agent_id = str(data.get("agent_id", "")).strip()
        if agent_id:
            return agent_id
    return ""


def persist_agent_id(agent_id: str) -> None:
    config.ensure_root()
    session_path = session_state_path()
    payload = {
        "agent_id": agent_id,
        "thread_id": os.environ.get("CODEX_THREAD_ID"),
        "wt_session": os.environ.get("WT_SESSION"),
        "project": current_project(),
        "updated_at": utc_now(),
        **identity_fields(),
    }
    if session_path is not None:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Keep a small legacy breadcrumb for humans, but do not rely on it for session identity.
    legacy_payload = {
        "agent_id": agent_id,
        "last_session_key": codex_session_key(),
        "updated_at": payload["updated_at"],
        **identity_fields(),
    }
    (config.get_root() / LEGACY_AGENT_FILE).write_text(json.dumps(legacy_payload, indent=2), encoding="utf-8")


def ensure_agent_id(*, fresh: bool) -> str:
    explicit = (
        os.environ.get("LITEHARNESS_AGENT_ID", "").strip()
        or os.environ.get("CODEX_SESSION_ID", "").strip()
        or os.environ.get("CODEX_THREAD_ID", "").strip()
    )
    existing = "" if fresh else (explicit or load_saved_agent_id())
    agent_id = existing or f"codex-{uuid.uuid4().hex[:12]}"
    persist_agent_id(agent_id)
    os.environ["LITEHARNESS_AGENT_ID"] = agent_id
    return agent_id


def write_presence(agent_id: str) -> Path:
    agents_dir = config.get_root() / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{agent_id}.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}

    model = config.get_model()
    if model == "unknown" and existing.get("model"):
        model = existing["model"]

    identity = identity_fields()

    process_context = detect_process_context()
    detected_pid = process_context.get("codex_pid")
    existing_session_pid = existing.get("session_pid")
    session_pid = detected_pid or (
        existing_session_pid if isinstance(existing_session_pid, int) and existing_session_pid > 0 else None
    )
    presence = {
        "agent_id": agent_id,
        "model": model,
        "project": current_project(),
        "started_at": existing.get("started_at") or utc_now(),
        "last_seen": utc_now(),
        "pid": detected_pid or os.getpid(),
        **identity,
    }
    if session_pid:
        presence["session_pid"] = session_pid
    if process_context.get("wt_process_pid"):
        presence["wt_process_pid"] = process_context["wt_process_pid"]
    wt_session = os.environ.get("WT_SESSION") or existing.get("wt_session")
    if wt_session:
        presence["wt_session"] = wt_session
    transcript_path = existing.get("transcript_path")
    if transcript_path:
        presence["transcript_path"] = transcript_path
    path.write_text(json.dumps(presence, indent=2), encoding="utf-8")
    return path


def refresh_codex_inbox_watcher(agent_id: str) -> None:
    """Compatibility helper: registration cannot spawn an attached consumer."""
    print(f"[LITEHARNESS] Start the stdout watcher in an attached tool terminal: "
          f"python -u {Path(__file__).resolve()} watch")


def agent_slug(agent_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in agent_id)


def codex_monitor_state_path(agent_id: str, suffix: str) -> Path:
    return CODEX_MONITOR_STATE_ROOT / f"codex_inbox_{agent_slug(agent_id)}_{suffix}"


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def pid_is_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        except (AttributeError, OSError, TypeError):
            return False
        if not handle:
            # ERROR_ACCESS_DENIED still proves that the PID exists; an invalid
            # or exited PID normally reports ERROR_INVALID_PARAMETER instead.
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop_monitor_pid(pid: object) -> None:
    if not isinstance(pid, int) or pid <= 0 or not pid_is_alive(pid):
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return


def codex_monitor_status(agent_id: str) -> dict:
    record = read_json_file(config.get_root() / "codex_sessions" / "monitors" / f"{agent_id}.json")
    presence = read_json_file(config.get_root() / "agents" / f"{agent_id}.json")
    if record.get("agent_id") == agent_id and record.get("delivery") in {"stdout", "desktop-turn"}:
        pid = record.get("pid")
        stamp = None
        if presence.get("watcher_pid") == pid:
            try:
                stamp = datetime.fromisoformat(presence["last_seen"]).timestamp()
            except (ValueError, KeyError, TypeError):
                pass
        return dict(agent_id=agent_id, supervisor_pid=pid, supervisor_alive=pid_is_alive(pid),
                    supervisor_updated_at=stamp, watcher_pid=pid, watcher_alive=pid_is_alive(pid),
                    watcher_updated_at=stamp, watcher_last_scan_at=stamp, last_error=None,
                    target={}, delivery=record["delivery"])
    # Read legacy records only to support explicit migration/stop; never launch them.
    sup = read_json_file(codex_monitor_state_path(agent_id, "supervisor.pid"))
    beat = read_json_file(codex_monitor_state_path(agent_id, "supervisor.heartbeat.json"))
    watcher = read_json_file(codex_monitor_state_path(agent_id, "watcher.pid"))
    pid = watcher.get("pid") or beat.get("watcher_pid")
    return dict(agent_id=agent_id, supervisor_pid=sup.get("pid"),
                supervisor_alive=pid_is_alive(sup.get("pid")), supervisor_updated_at=None,
                watcher_pid=pid, watcher_alive=pid_is_alive(pid), watcher_updated_at=None,
                watcher_last_scan_at=None, last_error=None, target={}, delivery="legacy")


def format_age(timestamp: object) -> str:
    if not isinstance(timestamp, (int, float)):
        return "missing"
    age = max(0.0, time.time() - float(timestamp))
    freshness = "fresh" if age <= MAX_MONITOR_HEARTBEAT_AGE_SEC else "stale"
    return f"{age:.1f}s ago ({freshness})"


def timestamp_is_fresh(timestamp: object) -> bool:
    return isinstance(timestamp, (int, float)) and (
        time.time() - float(timestamp)
    ) <= MAX_MONITOR_HEARTBEAT_AGE_SEC


def stop_codex_monitor(agent_id: str) -> None:
    status = codex_monitor_status(agent_id)
    stop_monitor_pid(status.get("supervisor_pid"))
    if status.get("watcher_pid") != status.get("supervisor_pid"):
        stop_monitor_pid(status.get("watcher_pid"))


def update_presence_heartbeat(agent_id: str) -> None:
    path = config.get_root() / "agents" / f"{agent_id}.json"
    if not path.exists():
        return
    try:
        presence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    presence["last_seen"] = utc_now()
    try:
        path.write_text(json.dumps(presence, indent=2), encoding="utf-8")
    except OSError:
        return


def claim_messages(agent_id: str) -> list[dict]:
    claimed = []
    for message in inbox.poll(agent_id):
        if inbox.claim(message):
            claimed.append(message)
    return claimed


def print_messages(messages: list[dict]) -> None:
    print(f"[LITEHARNESS] {len(messages)} message(s) received:")
    print("")
    for msg in messages:
        sender = msg.get("from", "unknown")
        priority = msg.get("priority", "normal")
        msg_type = msg.get("type", "notification")
        body = msg.get("body", "")
        thread = msg.get("thread_id")
        project = msg.get("project")
        prefix = "[URGENT] " if priority == "urgent" else ""

        print(f"  {prefix}From: {sender} ({msg_type})")
        if project:
            print(f"  Project: {project}")
        if thread:
            print(f"  Thread: {thread}")
        print(f"  {body}")
        print("")
        print(f"  To reply, run: python scripts/manual_liteharness.py send {sender} \"your reply here\"")
        if thread:
            print(f"    Use --thread-id {thread}")
        print("")

        if msg_type == "notification":
            inbox.complete(msg)


def cmd_start(args: argparse.Namespace) -> int:
    agent_id = ensure_agent_id(fresh=args.fresh_agent)
    presence_path = write_presence(agent_id)
    print("[LITEHARNESS] Session registered (no watcher was spawned or stopped).")
    print(f"  Agent ID: {agent_id}")
    print(f"  Presence: {presence_path}")
    print("  Inbox delivery requires one attached tool terminal running:")
    print(f"    python -u \"{Path(__file__).resolve()}\" watch")
    print("  Retain the tool session ID and read its stdout after major tool use.")
    print("  Desktop wake: get the current real turn id with read_thread, then watch --turn-id <id>.")
    print("  Terminal-only stdout delivery does not provide automatic desktop wake.")
    if args.check_now:
        return cmd_check(args)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    agent_id = config.get_agent_id()
    status = codex_monitor_status(agent_id)
    if status["delivery"] in {"stdout", "desktop-turn"} and status["watcher_alive"]:
        print(f"[LITEHARNESS] Attached {status['delivery']} watcher owns delivery; check did not claim messages.")
        return 0
    claimed = claim_messages(agent_id)
    if not claimed:
        update_presence_heartbeat(agent_id)
        print(f"[LITEHARNESS] No new messages for {agent_id}.")
        return 0
    print_messages(claimed)
    update_presence_heartbeat(agent_id)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from liteharness.cli_scripts.codex.liteharness_watcher_supervisor import main as watch_main
    options = ["--agent-id", config.get_agent_id(), "--root", str(config.get_root()),
               "--delivery", getattr(args, "delivery", "auto")]
    if getattr(args, "turn_id", None):
        options += ["--turn-id", args.turn_id]
    return watch_main(options)


def cmd_discover(args: argparse.Namespace) -> int:
    from liteharness.cli import cmd_discover as canonical_discover

    canonical_discover()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    agent_id = config.get_agent_id()
    msg_id = inbox.send(
        from_agent=agent_id,
        to_agent=args.to_agent,
        body=with_identity_header(agent_id, args.message),
        thread_id=args.thread_id,
        priority=args.priority,
        msg_type=args.message_type,
        project=current_project(),
        cli=current_cli(),
        model=config.get_model(),
        surface=current_surface(),
        originator=current_originator(),
    )
    update_presence_heartbeat(agent_id)
    print(f"[LITEHARNESS] Sent {msg_id} to {args.to_agent}.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    agent_id = config.get_agent_id()
    total_new_count = 0
    addressed_new_count = 0
    if inbox.INBOX_NEW.exists():
        for path in inbox.INBOX_NEW.glob("*.json"):
            total_new_count += 1
            try:
                msg = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if msg.get("to") in (agent_id, "broadcast"):
                addressed_new_count += 1
    print("[LITEHARNESS] Status")
    print(f"  Root: {config.get_root()}")
    print(f"  Agent ID: {agent_id}")
    print(f"  CLI: {current_cli()}")
    print(f"  Surface: {current_surface()}")
    print(f"  Inbox new/ total: {total_new_count}")
    print(f"  Inbox new/ for this agent: {addressed_new_count}")
    print(f"  Project: {current_project()}")
    return 0


def print_codex_monitor_status(agent_id: str) -> None:
    status = codex_monitor_status(agent_id)
    print("[LITEHARNESS] Codex inbox status")
    print(f"  Agent ID: {agent_id}")
    print(f"  Delivery: {status['delivery']}")
    print(f"  Watcher: pid={status['watcher_pid']} alive={status['watcher_alive']}")
    print(f"  Heartbeat: {format_age(status['watcher_updated_at'])}")
    print(f"  Attached process health: {'running' if codex_monitor_is_ready(status) else 'not ready'}")
    if status["delivery"] == "desktop-turn":
        print("  Delivery proof: a new task turn containing the nonce and its recipient acknowledgement.")
        print("  Desktop wake transport: app-tools pipe to this exact thread; recipient ack required.")
        base = config.get_root() / "codex_sessions" / "delivery" / agent_id
        counts = {}
        for path in (base / "ledger").glob("*.json"):
            phase = read_json_file(path).get("state", "invalid")
            counts[phase] = counts.get(phase, 0) + 1
        print(f"  Delivery states: {counts}")
    else:
        print("  Receipt proof: read an actual message from the attached tool session.")
        print("  Automatic desktop wake: not provided by stdout delivery.")
    if status["delivery"] == "legacy" and (status["watcher_alive"] or status["supervisor_alive"]):
        print("  Legacy detached monitor detected. Stop it explicitly before starting watch in an attached tool terminal.")


def codex_monitor_is_ready(status: dict) -> bool:
    return bool(status.get("delivery") in {"stdout", "desktop-turn"} and status["watcher_alive"]
                and timestamp_is_fresh(status["watcher_updated_at"]))


def cmd_attached_health(args: argparse.Namespace) -> int:
    """Expose Codex monitor readiness in an attached, non-claiming process."""
    agent_id = config.get_agent_id()
    print(
        f"[LITEHARNESS] Attached health monitor for {agent_id}; "
        "this process never claims inbox messages.",
        flush=True,
    )
    last_state: str | None = None
    try:
        while True:
            status = codex_monitor_status(agent_id)
            state = "READY" if codex_monitor_is_ready(status) else "NOT_READY"
            if state != last_state:
                stamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{stamp}] LiteHarness attached health: {state}", flush=True)
                print_codex_monitor_status(agent_id)
                sys.stdout.flush()
                last_state = state
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        print("[LITEHARNESS] Attached health monitor stopped.", flush=True)
        return 0


def cmd_codex_monitor(args: argparse.Namespace) -> int:
    agent_id = config.get_agent_id()
    if args.action == "start":
        return cmd_watch(args)
    if args.action == "restart":
        stop_codex_monitor(agent_id)
        return cmd_watch(args)
    if args.action == "stop":
        stop_codex_monitor(agent_id)
        print(f"[LITEHARNESS] Requested Codex monitor stop for {agent_id}.")
        return 0
    if args.action == "status":
        print_codex_monitor_status(agent_id)
        return 0
    if args.action == "list":
        print(f"[LITEHARNESS] Codex monitor files under {CODEX_MONITOR_STATE_ROOT}:")
        if not CODEX_MONITOR_STATE_ROOT.exists():
            print("  none")
            return 0
        for path in sorted(CODEX_MONITOR_STATE_ROOT.glob("codex_inbox_*")):
            print(f"  {path.name}")
        return 0
    if args.action == "logs":
        path = codex_monitor_state_path(agent_id, "watcher.log")
        print(f"[LITEHARNESS] Codex monitor log: {path}")
        if not path.exists():
            print("  no log file")
            return 0
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            print(f"  unable to read log: {exc}")
            return 1
        for line in lines[-max(1, args.lines) :]:
            print(f"  {line}")
        return 0
    raise AssertionError(f"Unhandled Codex monitor action: {args.action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manual LiteHarness bootstrap for Codex sessions."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="LiteHarness runtime root. Defaults to the global ~/.liteharness root.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Register this Codex session with LiteHarness.")
    start.add_argument(
        "--fresh-agent",
        action="store_true",
        help="Force a new agent ID for the current Codex session.",
    )
    start.add_argument(
        "--reuse-agent",
        action="store_true",
        help="Compatibility no-op: start now reuses the saved session agent by default.",
    )
    start.add_argument(
        "--check-now",
        action="store_true",
        help="Run an immediate inbox check after registering presence.",
    )
    start.set_defaults(func=cmd_start)

    check = subparsers.add_parser("check", help="Poll the inbox once.")
    check.set_defaults(func=cmd_check)

    watch = subparsers.add_parser("watch", help="Watch the inbox continuously.")
    watch.add_argument("--turn-id", help="Real originating Codex turn id from read_thread")
    watch.add_argument("--delivery", choices=["auto", "stdout", "desktop-turn"], default="auto")
    watch.set_defaults(func=cmd_watch)

    attached_health = subparsers.add_parser(
        "attached-health",
        help="Show Codex monitor readiness in an attached process without claiming inbox messages.",
    )
    attached_health.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between readiness checks (default: 5).",
    )
    attached_health.set_defaults(func=cmd_attached_health)

    discover = subparsers.add_parser("discover", help="List agents registered under the current root.")
    discover.set_defaults(func=cmd_discover)

    send = subparsers.add_parser("send", help="Send a LiteHarness message.")
    send.add_argument("to_agent", help="Target agent ID or broadcast.")
    send.add_argument("message", help="Message body.")
    send.add_argument(
        "--thread-id",
        default=None,
        help="Optional thread ID for replies or grouped conversations.",
    )
    send.add_argument(
        "--priority",
        choices=["normal", "urgent"],
        default="normal",
        help="Message priority.",
    )
    send.add_argument(
        "--message-type",
        choices=["notification", "request", "reply"],
        default="notification",
        help="Semantic message type.",
    )
    send.set_defaults(func=cmd_send)

    status = subparsers.add_parser("status", help="Show the current runtime status.")
    status.set_defaults(func=cmd_status)

    codex_monitor = subparsers.add_parser(
        "codex-monitor",
        help="Manage the Codex-only background LiteHarness inbox monitor.",
    )
    codex_monitor.add_argument(
        "action",
        choices=["start", "stop", "restart", "status", "list", "logs"],
        help="Codex monitor action.",
    )
    codex_monitor.add_argument(
        "--lines",
        type=int,
        default=30,
        help="Number of log lines to show for the logs action.",
    )
    codex_monitor.add_argument("--turn-id", help="Real originating Codex turn id from read_thread")
    codex_monitor.add_argument("--delivery", choices=["auto", "stdout", "desktop-turn"], default="auto")
    codex_monitor.set_defaults(func=cmd_codex_monitor)

    return parser


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        import io

        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args()
    configure_root(args.root)
    agent_id = (
        os.environ.get("LITEHARNESS_AGENT_ID", "").strip()
        or os.environ.get("CODEX_SESSION_ID", "").strip()
        or os.environ.get("CODEX_THREAD_ID", "").strip()
        or load_saved_agent_id()
    )
    if agent_id:
        os.environ["LITEHARNESS_AGENT_ID"] = agent_id
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
