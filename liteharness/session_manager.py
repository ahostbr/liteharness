#!/usr/bin/env python
"""
Terminal Agent Session Manager
==============================
Save and restore Windows Terminal agent sessions for Claude, Codex, and Copilot.

Usage:
    python session_manager.py save [name]
    python session_manager.py restore [name] [--layout windows|tabs|panes] [--dry-run]
    python session_manager.py list
    python session_manager.py status
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:
    print("ERROR: psutil required. Install with: pip install psutil")
    sys.exit(1)


SCHEMA_VERSION = 2
VALID_LAYOUTS = {"windows", "tabs", "panes"}

from . import config

SESSION_ROOT = config.get_root() / "sessions"
CONFIG_PATH = SESSION_ROOT / "config.json"
SNAPSHOTS_DIR = SESSION_ROOT / "snapshots"

CLAUDE_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
COPILOT_SESSION_STATE_DIR = Path.home() / ".copilot" / "session-state"

PROCESS_ATTRS = ["pid", "name", "cmdline", "cwd", "create_time"]

SAFE_ARGS = {
    "claude": {
        "--model": "value",
        "--permission-mode": "value",
        "--name": "value",
        "--dangerously-skip-permissions": "bool",
        "--allow-dangerously-skip-permissions": "bool",
    },
    "codex": {
        "-m": "value",
        "--model": "value",
        "--ask-for-approval": "value",
        "--sandbox": "value",
        "--profile": "value",
        "--oss": "bool",
        "--dangerously-bypass-approvals-and-sandbox": "bool",
        "--no-alt-screen": "bool",
    },
    "copilot": {
        "--model": "value",
        "--name": "value",
        "-n": "value",
        "--mode": "value",
        "--allow-all": "bool",
        "--allow-all-tools": "bool",
        "--yolo": "bool",
        "--plan": "bool",
        "--autopilot": "bool",
    },
}


# -- Config -----------------------------------------------------------------

def ensure_session_dirs():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    config = {"default_layout": "windows"}
    ensure_session_dirs()
    if not CONFIG_PATH.exists():
        return config

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: Could not read config {CONFIG_PATH}: {exc}")
        return config

    layout = loaded.get("default_layout")
    if layout in VALID_LAYOUTS:
        config["default_layout"] = layout
    else:
        print(f"WARNING: Ignoring invalid default_layout in config: {layout!r}")
    return config


# -- Discovery ---------------------------------------------------------------

def find_agent_sessions():
    sessions = []
    sessions.extend(find_claude_sessions())
    sessions.extend(find_codex_sessions())
    sessions.extend(find_copilot_sessions())
    return _dedupe_sessions(sessions)


def find_claude_sessions():
    raw = []

    for proc in psutil.process_iter(PROCESS_ATTRS):
        try:
            info = proc.info
            if (info.get("name") or "").lower() != "claude.exe":
                continue

            pid = info["pid"]
            session_file = CLAUDE_SESSIONS_DIR / f"{pid}.json"
            if not session_file.exists():
                continue

            with open(session_file, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            if session_data.get("kind") != "interactive":
                continue

            try:
                parent = psutil.Process(proc.ppid())
                if parent.name().lower() == "claude.exe":
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            terminal = _trace_to_terminal(proc)
            raw.append(_make_session(
                cli="claude",
                pid=pid,
                session_id=session_data.get("sessionId"),
                cwd=info.get("cwd"),
                name=session_data.get("name", ""),
                model=_first_launch_arg_value(info.get("cmdline") or [], "--model"),
                started_at=_coerce_epoch(session_data.get("startedAt")),
                launch_args=_extract_launch_args(info.get("cmdline") or [], "claude"),
                terminal=terminal,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, json.JSONDecodeError, OSError):
            continue

    return _dedupe_sessions(raw)


def find_codex_sessions():
    metadata = _load_codex_metadata()
    raw = []

    for proc in psutil.process_iter(PROCESS_ATTRS):
        try:
            info = proc.info
            if not _is_codex_cli_process(info):
                continue

            cmdline = info.get("cmdline") or []
            session_id = _extract_resume_id(cmdline, "codex")
            meta = metadata.get(session_id) if session_id else None
            ambiguous = False
            if meta is None:
                meta, ambiguous = _match_metadata_for_process(info, list(metadata.values()))
            if ambiguous:
                print(f"WARNING: Skipping ambiguous Codex process {info.get('pid')}.")
                continue
            if meta is None and not session_id:
                print(f"WARNING: Skipping Codex process {info.get('pid')} with no session metadata.")
                continue

            terminal = _trace_to_terminal(proc)
            raw.append(_make_session(
                cli="codex",
                pid=info["pid"],
                session_id=session_id or meta.get("session_id"),
                cwd=info.get("cwd") or (meta or {}).get("cwd"),
                name=(meta or {}).get("name", ""),
                model=_first_launch_arg_value(cmdline, "--model") or _first_launch_arg_value(cmdline, "-m") or (meta or {}).get("model"),
                started_at=(meta or {}).get("started_at") or info.get("create_time"),
                launch_args=_extract_launch_args(cmdline, "codex"),
                terminal=terminal,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    return _dedupe_sessions(raw)


def find_copilot_sessions():
    metadata = _load_copilot_metadata()
    raw = []

    for proc in psutil.process_iter(PROCESS_ATTRS):
        try:
            info = proc.info
            if not _is_copilot_cli_process(info):
                continue

            cmdline = info.get("cmdline") or []
            session_id = _extract_resume_id(cmdline, "copilot")
            meta = metadata.get(session_id) if session_id else None
            ambiguous = False
            if meta is None:
                meta, ambiguous = _match_metadata_for_process(info, list(metadata.values()))
            if ambiguous:
                print(f"WARNING: Skipping ambiguous Copilot process {info.get('pid')}.")
                continue
            if meta is None and not session_id:
                print(f"WARNING: Skipping Copilot process {info.get('pid')} with no session metadata.")
                continue

            terminal = _trace_to_terminal(proc)
            raw.append(_make_session(
                cli="copilot",
                pid=info["pid"],
                session_id=session_id or meta.get("session_id"),
                cwd=info.get("cwd") or (meta or {}).get("cwd"),
                name=(meta or {}).get("name", ""),
                model=_first_launch_arg_value(cmdline, "--model") or (meta or {}).get("model"),
                started_at=(meta or {}).get("started_at") or info.get("create_time"),
                launch_args=_extract_launch_args(cmdline, "copilot"),
                terminal=terminal,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    return _dedupe_sessions(raw)


def _make_session(cli, pid, session_id, cwd, name, model, started_at, launch_args, terminal):
    terminal_pid = terminal.get("terminal_pid")
    return {
        "cli": cli,
        "pid": pid,
        "session_id": session_id,
        "cwd": str(cwd or Path.cwd()),
        "name": name or "",
        "model": model or "",
        "started_at": started_at,
        "launch_args": launch_args or [],
        "terminal": terminal,
        "terminal_pid": terminal_pid,
        "terminal_group": str(terminal_pid or f"detached:{pid}"),
    }


def _trace_to_terminal(proc):
    try:
        current = proc
        shell_pid = None
        shell_name = None
        terminal_pid = None

        while current:
            parent_pid = current.ppid()
            if parent_pid == 0:
                break
            try:
                parent = psutil.Process(parent_pid)
                pname = parent.name().lower()

                if pname in ("powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe",
                             "zsh.exe", "fish.exe", "nu.exe", "wsl.exe"):
                    shell_pid = parent.pid
                    shell_name = parent.name()

                if pname == "windowsterminal.exe":
                    terminal_pid = parent.pid
                    return {
                        "terminal_pid": terminal_pid,
                        "shell_pid": shell_pid,
                        "shell_name": shell_name,
                    }

                current = parent
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    return {
        "terminal_pid": terminal_pid,
        "shell_pid": shell_pid,
        "shell_name": shell_name,
    }


def _load_codex_metadata(limit=500):
    metadata = {}
    if not CODEX_SESSIONS_DIR.exists():
        return metadata

    files = sorted(CODEX_SESSIONS_DIR.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for path in files:
        meta = _read_codex_metadata_file(path)
        if meta:
            metadata[meta["session_id"]] = meta
    return metadata


def _read_codex_metadata_file(path):
    session_meta = None
    model = ""

    try:
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if idx >= 80:
                    break
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if item.get("type") == "session_meta":
                    payload = item.get("payload", {})
                    session_meta = {
                        "cli": "codex",
                        "session_id": payload.get("id"),
                        "cwd": payload.get("cwd"),
                        "name": "",
                        "model": "",
                        "started_at": _parse_time(payload.get("timestamp")),
                    }
                elif item.get("type") == "turn_context":
                    payload = item.get("payload", {})
                    model = payload.get("model") or model
    except OSError:
        return None

    if not session_meta or not session_meta.get("session_id"):
        return None

    session_meta["model"] = model
    return session_meta


def _load_copilot_metadata():
    metadata = {}
    if not COPILOT_SESSION_STATE_DIR.exists():
        return metadata

    for session_dir in COPILOT_SESSION_STATE_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        events = session_dir / "events.jsonl"
        meta = _read_copilot_metadata_file(events)
        if meta:
            metadata[meta["session_id"]] = meta
    return metadata


def _read_copilot_metadata_file(path):
    session_meta = None
    model = ""

    try:
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if idx >= 120:
                    break
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = item.get("type")
                data = item.get("data", {})
                if event_type == "session.start":
                    context = data.get("context", {})
                    session_meta = {
                        "cli": "copilot",
                        "session_id": data.get("sessionId") or path.parent.name,
                        "cwd": context.get("cwd"),
                        "name": "",
                        "model": "",
                        "started_at": _parse_time(data.get("startTime") or item.get("timestamp")),
                    }
                elif event_type == "session.model_change":
                    model = data.get("newModel") or model
    except OSError:
        return None

    if not session_meta or not session_meta.get("session_id"):
        return None

    session_meta["model"] = model
    return session_meta


def _match_metadata_for_process(info, metadata):
    cwd = _norm_path(info.get("cwd"))
    create_time = info.get("create_time") or 0
    scored = []

    for meta in metadata:
        meta_cwd = _norm_path(meta.get("cwd"))
        if cwd and meta_cwd and cwd != meta_cwd:
            continue
        started_at = meta.get("started_at") or 0
        delta = abs(float(create_time or 0) - float(started_at or 0))
        scored.append((round(delta, 3), meta))

    if not scored:
        return None, False

    scored.sort(key=lambda item: item[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, True
    return scored[0][1], False


def _is_codex_cli_process(info):
    name = (info.get("name") or "").lower()
    text = _cmd_text(info.get("cmdline") or []).lower()
    if "windowsapps\\openai.codex" in text or "app\\codex.exe" in text:
        return False
    return (
        name == "codex.exe"
        or "@openai\\codex" in text
        or "@openai/codex" in text
        or "codex.js" in text
    )


def _is_copilot_cli_process(info):
    name = (info.get("name") or "").lower()
    text = _cmd_text(info.get("cmdline") or []).lower()
    return name == "copilot.exe" or "@github\\copilot" in text or "@github/copilot" in text


def _dedupe_sessions(sessions):
    seen = {}
    for session in sessions:
        sid = session.get("session_id")
        key = (session.get("cli"), sid or f"pid:{session.get('pid')}")
        existing = seen.get(key)
        if existing is None:
            seen[key] = session
            continue

        has_terminal = session.get("terminal_pid") is not None
        existing_has_terminal = existing.get("terminal_pid") is not None
        if has_terminal and not existing_has_terminal:
            seen[key] = session
    return list(seen.values())


# -- Command-line extraction -------------------------------------------------

def _extract_launch_args(cmdline, cli):
    safe = SAFE_ARGS.get(cli, {})
    args = []
    tokens = list(cmdline or [])
    skip_next = False

    for i, token in enumerate(tokens):
        if i == 0 or skip_next:
            skip_next = False
            continue

        if cli == "codex" and token == "resume":
            skip_next = True
            continue
        if token in ("--resume", "-r", "--continue"):
            skip_next = token in ("--resume", "-r")
            continue
        if token.startswith("--resume="):
            continue

        flag, eq, value = token.partition("=")
        mode = safe.get(flag)
        if not mode:
            continue

        if mode == "bool":
            args.append(token)
            continue

        if eq:
            args.append(token)
        elif i + 1 < len(tokens):
            next_value = tokens[i + 1]
            if not next_value.startswith("-"):
                args.extend([token, next_value])
                skip_next = True

    return args


def _extract_resume_id(cmdline, cli):
    tokens = list(cmdline or [])
    for i, token in enumerate(tokens):
        if cli == "codex" and token == "resume" and i + 1 < len(tokens):
            return tokens[i + 1]
        if token in ("--resume", "-r") and i + 1 < len(tokens):
            return tokens[i + 1]
        if token.startswith("--resume="):
            return token.split("=", 1)[1]
    return None


def _first_launch_arg_value(cmdline, flag):
    tokens = list(cmdline or [])
    for i, token in enumerate(tokens):
        if token == flag and i + 1 < len(tokens):
            return tokens[i + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    return ""


# -- Save / Restore ----------------------------------------------------------

def save_snapshot(name=None):
    ensure_session_dirs()
    sessions = find_agent_sessions()
    if not sessions:
        print("No running terminal agent sessions found.")
        return None

    if name is None:
        name = datetime.now().strftime("%Y%m%d_%H%M%S")

    config = load_config()
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "saved_at": datetime.now().isoformat(),
        "default_layout": config["default_layout"],
        "sessions": [],
    }

    for session in sessions:
        snapshot["sessions"].append({
            "cli": session["cli"],
            "session_id": session["session_id"],
            "cwd": session["cwd"],
            "name": session["name"],
            "model": session["model"],
            "launch_args": session["launch_args"],
            "terminal_pid": session["terminal_pid"],
            "terminal_group": session["terminal_group"],
        })

    filepath = SNAPSHOTS_DIR / f"{name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    print(f"Saved {len(sessions)} session(s) to: {filepath}")
    for session in snapshot["sessions"]:
        label = _session_label(session)
        print(f"  [{session['cli']}: {label}] {session['cwd']}")

    return filepath


def restore_snapshot(name=None, layout=None, dry_run=False):
    filepath = _resolve_snapshot_path(name)
    if filepath is None:
        return

    with open(filepath, "r", encoding="utf-8") as f:
        snapshot = _normalize_snapshot(json.load(f))

    sessions = snapshot["sessions"]
    if not sessions:
        print("Snapshot has no sessions.")
        return

    selected_layout = layout or snapshot.get("default_layout") or load_config()["default_layout"]
    if selected_layout not in VALID_LAYOUTS:
        print(f"Invalid layout: {selected_layout}. Valid layouts: {', '.join(sorted(VALID_LAYOUTS))}")
        return

    commands = build_restore_commands(sessions, selected_layout)
    print(f"Restoring {len(sessions)} session(s) from '{snapshot['name']}' with layout '{selected_layout}'...")

    for idx, cmd in enumerate(commands, 1):
        print(f"\n  Command {idx}: {_display_command(cmd)}")

    if dry_run:
        return commands

    for idx, cmd in enumerate(commands, 1):
        try:
            subprocess.Popen(cmd, shell=False)
            if idx < len(commands):
                time.sleep(0.5)
        except FileNotFoundError:
            print("  ERROR: wt.exe not found. Is Windows Terminal installed?")
            return
        except Exception as exc:
            print(f"  ERROR launching terminal: {exc}")
            return

    return commands


def _resolve_snapshot_path(name):
    ensure_session_dirs()
    if name is None:
        snaps = sorted(SNAPSHOTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not snaps:
            print("No snapshots found.")
            return None
        filepath = snaps[-1]
        print(f"Restoring most recent snapshot: {filepath.stem}")
        return filepath

    filepath = SNAPSHOTS_DIR / f"{name}.json"
    if not filepath.exists():
        print(f"Snapshot not found: {filepath}")
        return None
    return filepath


def _normalize_snapshot(snapshot):
    if snapshot.get("schema_version") == SCHEMA_VERSION:
        normalized = dict(snapshot)
        normalized["default_layout"] = normalized.get("default_layout") or load_config()["default_layout"]
        normalized["sessions"] = [_normalize_session(s) for s in normalized.get("sessions", [])]
        return normalized

    return {
        "schema_version": SCHEMA_VERSION,
        "name": snapshot.get("name", "legacy"),
        "saved_at": snapshot.get("saved_at"),
        "default_layout": snapshot.get("default_layout") or load_config()["default_layout"],
        "sessions": [_normalize_session(s) for s in snapshot.get("sessions", [])],
    }


def _normalize_session(session):
    cli = session.get("cli") or "claude"
    terminal_pid = session.get("terminal_pid")
    launch_args = session.get("launch_args")
    if launch_args is None:
        launch_args = session.get("flags", [])

    return {
        "cli": cli,
        "session_id": session.get("session_id"),
        "cwd": session.get("cwd") or str(Path.cwd()),
        "name": session.get("name", ""),
        "model": session.get("model", ""),
        "launch_args": _sanitize_launch_args(launch_args or [], cli),
        "terminal_pid": terminal_pid,
        "terminal_group": str(session.get("terminal_group") or terminal_pid or "unknown"),
    }


# -- Restore command builders ------------------------------------------------

def build_restore_commands(sessions, layout):
    if layout == "windows":
        return [["wt.exe", "-w", "new"] + _wt_segment(session) for session in sessions]
    if layout == "tabs":
        return _build_tab_commands(sessions)
    if layout == "panes":
        return [_build_pane_command(sessions)]
    raise ValueError(f"Unknown layout: {layout}")


def _build_tab_commands(sessions):
    groups = {}
    for session in sessions:
        groups.setdefault(session.get("terminal_group") or "unknown", []).append(session)

    commands = []
    for group in groups.values():
        cmd = ["wt.exe", "-w", "new"]
        for idx, session in enumerate(group):
            if idx:
                cmd.append(";")
            cmd.extend(_wt_segment(session))
        commands.append(cmd)
    return commands


def _build_pane_command(sessions):
    if not sessions:
        return ["wt.exe", "-w", "new"]

    cmd = ["wt.exe", "-w", "new"] + _wt_segment(sessions[0])
    for session in sessions[1:]:
        cmd.append(";")
        cmd.extend(_wt_segment(session, action="split-pane", split="-V"))
    return cmd


def _wt_segment(session, action="new-tab", split=None):
    segment = [action]
    if split:
        segment.append(split)
    segment.extend([
        "--title", _terminal_title(session),
        "-d", session["cwd"],
        "--",
    ])
    segment.extend(_agent_command(session))
    return segment


def _agent_command(session):
    cli = session.get("cli") or "claude"
    session_id = session.get("session_id")
    launch_args = list(session.get("launch_args") or [])

    if cli == "claude":
        return ["claude", "--resume", session_id] + launch_args
    if cli == "codex":
        return ["codex"] + launch_args + ["resume", session_id]
    if cli == "copilot":
        return ["copilot", f"--resume={session_id}"] + launch_args
    raise ValueError(f"Unsupported CLI: {cli}")


def _sanitize_launch_args(args, cli):
    safe = SAFE_ARGS.get(cli, {})
    tokens = list(args or [])
    sanitized = []
    skip_next = False

    for i, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue

        flag, eq, _value = token.partition("=")
        mode = safe.get(flag)
        if not mode:
            continue
        if mode == "bool":
            sanitized.append(token)
            continue
        if eq:
            sanitized.append(token)
            continue
        if i + 1 < len(tokens) and not str(tokens[i + 1]).startswith("-"):
            sanitized.extend([token, tokens[i + 1]])
            skip_next = True

    return sanitized


def _terminal_title(session):
    label = _session_label(session)
    return f"{session.get('cli', 'agent')}: {label}"


# -- Reporting ---------------------------------------------------------------

def list_snapshots():
    ensure_session_dirs()
    snaps = sorted(SNAPSHOTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not snaps:
        print("No snapshots saved.")
        return

    for snap in snaps:
        try:
            with open(snap, encoding="utf-8") as f:
                data = _normalize_snapshot(json.load(f))
            count = len(data.get("sessions", []))
            saved = data.get("saved_at", "?")
            layout = data.get("default_layout", "?")
            clis = ",".join(sorted({s.get("cli", "claude") for s in data.get("sessions", [])}))
            print(f"  {snap.stem:<25} {count} session(s)  layout={layout:<7} cli={clis or '-'}  saved {saved}")
        except (json.JSONDecodeError, OSError):
            print(f"  {snap.stem:<25} (corrupt)")


def show_status():
    sessions = find_agent_sessions()
    if not sessions:
        print("No running terminal agent sessions.")
        return

    groups = {}
    for session in sessions:
        groups.setdefault(session.get("terminal_group") or "detached", []).append(session)

    for group_id, group in groups.items():
        print(f"\n  Terminal group {group_id}:")
        for session in group:
            sid = (session.get("session_id") or "?")[:8]
            name = session.get("name") or "(unnamed)"
            model = f" model={session['model']}" if session.get("model") else ""
            age = _age_label(session.get("started_at"))
            print(f"    [{session['cli']}:{sid}] {name:<30} {session['cwd']}{model}{age}")


def _session_label(session):
    return session.get("name") or (session.get("session_id") or "unknown")[:8]


def _age_label(started_at):
    if not started_at:
        return ""
    try:
        delta = datetime.now(timezone.utc).timestamp() - float(started_at)
    except (TypeError, ValueError):
        return ""
    if delta < 0:
        return ""
    hours = int(delta // 3600)
    mins = int((delta % 3600) // 60)
    return f" ({hours}h{mins}m ago)"


def _display_command(cmd):
    return subprocess.list2cmdline([str(part) for part in cmd])


# -- Small utilities ---------------------------------------------------------

def _cmd_text(cmdline):
    return " ".join(str(part) for part in (cmdline or []))


def _norm_path(path):
    if not path:
        return ""
    return str(Path(path)).rstrip("\\/").lower()


def _parse_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _coerce_epoch(value)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _coerce_epoch(value):
    if value is None:
        return None
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None
    if epoch > 100000000000:
        epoch = epoch / 1000
    return epoch


# -- CLI ---------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(description="Save and restore terminal agent sessions.")
    subparsers = parser.add_subparsers(dest="command")

    save = subparsers.add_parser("save", help="Snapshot running terminal agent sessions.")
    save.add_argument("name", nargs="?")

    restore = subparsers.add_parser("restore", help="Restore a saved terminal agent snapshot.")
    restore.add_argument("name", nargs="?")
    restore.add_argument("--layout", choices=sorted(VALID_LAYOUTS))
    restore.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("list", help="List saved snapshots.")
    subparsers.add_parser("status", help="Show running terminal agent sessions.")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "save":
        save_snapshot(args.name)
    elif args.command == "restore":
        restore_snapshot(args.name, layout=args.layout, dry_run=args.dry_run)
    elif args.command == "list":
        list_snapshots()
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
