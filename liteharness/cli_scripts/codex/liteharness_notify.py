#!/usr/bin/env python3
"""Codex notify hook bootstrap for the LiteHarness inbox watcher."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from liteharness import terminal_automation


HARNESS_ROOT = Path.home() / ".liteharness"
AGENT_CONFIG_PATH = HARNESS_ROOT / "codex_agent.json"
SESSION_STATE_DIR = HARNESS_ROOT / "codex_sessions"
STATE_ROOT = Path.home() / ".codex" / "memories" / "liteharness"
WATCHER_PATH = Path(__file__).with_name("liteharness_watcher_supervisor.py")
LEGACY_PID_PATH = STATE_ROOT / "codex_inbox_supervisor.pid"
LEGACY_HEARTBEAT_PATH = STATE_ROOT / "codex_inbox_supervisor.heartbeat.json"
LEGACY_TARGET_PATH = STATE_ROOT / "codex_inbox_target.json"
MAX_HEARTBEAT_AGE_SEC = 20


def agent_slug(agent_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in agent_id)


def state_path(agent_id: str, suffix: str) -> Path:
    return STATE_ROOT / f"codex_inbox_{agent_slug(agent_id)}_{suffix}"


def supervisor_pid_path(agent_id: str) -> Path:
    return state_path(agent_id, "supervisor.pid")


def supervisor_heartbeat_path(agent_id: str) -> Path:
    return state_path(agent_id, "supervisor.heartbeat.json")


def target_path(agent_id: str) -> Path:
    return state_path(agent_id, "target.json")


def read_pid(agent_id: str) -> int | None:
    path = supervisor_pid_path(agent_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = (result.stdout or "").strip()
    if not output or output.startswith("INFO:"):
        return False
    return str(pid) in output


def heartbeat_is_fresh(agent_id: str) -> bool:
    data = read_heartbeat(agent_id)
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, (int, float)):
        return False
    return (time.time() - float(updated_at)) <= MAX_HEARTBEAT_AGE_SEC


def read_heartbeat(agent_id: str) -> dict:
    path = supervisor_heartbeat_path(agent_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_agent_id() -> str | None:
    env_agent_id = os.environ.get("LITEHARNESS_AGENT_ID", "").strip()
    if env_agent_id:
        return env_agent_id

    session_agent_id = read_session_agent_id()
    if session_agent_id:
        return session_agent_id

    if AGENT_CONFIG_PATH.exists():
        try:
            data = json.loads(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        agent_id = data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id

    return None


def read_session_agent_id() -> str | None:
    for env_name in ("CODEX_THREAD_ID", "WT_SESSION"):
        session_key = os.environ.get(env_name, "").strip()
        if not session_key:
            continue
        safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session_key)
        session_path = SESSION_STATE_DIR / f"{safe_key}.json"
        if not session_path.exists():
            continue
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        agent_id = data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    return None


def sync_legacy_agent_config(agent_id: str) -> None:
    try:
        HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "agent_id": agent_id,
            "last_session_key": os.environ.get("CODEX_THREAD_ID") or os.environ.get("WT_SESSION"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cli": "codex-cli-manual",
            "surface": "terminal",
        }
        AGENT_CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def target_agent_id(agent_id: str) -> str | None:
    path = target_path(agent_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    agent_id = data.get("agent_id")
    return agent_id if isinstance(agent_id, str) and agent_id else None


def stop_pid(pid: int | None) -> None:
    if not isinstance(pid, int) or pid <= 0 or not pid_is_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _is_terminal_pane(pane: dict) -> bool:
    """True for any Windows Terminal terminal-control pane.

    Windows Terminal exposes the actual terminal surface as
    ``Microsoft.Terminal.TerminalControl.TermControl`` (full class name) and
    older builds reported it as bare ``TermControl``. Match either, plus any
    future variant via the ``has_text_pattern`` flag the PowerShell scout sets
    when it sees ``*TermControl*``.
    """
    class_name = (pane.get("class_name") or "").strip()
    if "TermControl" in class_name:
        return True
    if pane.get("has_text_pattern"):
        return True
    return False


def capture_focused_target(agent_id: str) -> None:
    if os.environ.get("LITESUITE_BRIDGE_TOKEN"):
        return
    if not os.environ.get("WT_SESSION"):
        return

    try:
        windows = terminal_automation.list_panes()
    except Exception:
        return

    focused_match: tuple[dict, dict] | None = None
    fallback_match: tuple[dict, dict] | None = None

    for window in windows:
        for pane in window.get("panes", []):
            if not _is_terminal_pane(pane):
                continue
            if pane.get("focused"):
                focused_match = (window, pane)
                break
            if fallback_match is None:
                fallback_match = (window, pane)
        if focused_match:
            break

    match = focused_match or fallback_match
    if match is None:
        return

    window, pane = match
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "window_handle": int(window["handle"]),
        "pane_id": int(pane["id"]),
        "window_title": window.get("title", ""),
        "pane_title": pane.get("title", ""),
        "wt_session": os.environ.get("WT_SESSION"),
        "captured_at": time.time(),
        "mode": "windows-terminal-headed",
        "agent_id": agent_id,
        "captured_via": "focused" if focused_match else "fallback",
    }
    target_path(agent_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ensure_watcher_running(agent_id: str) -> None:
    existing_pid = read_pid(agent_id)
    heartbeat = read_heartbeat(agent_id)
    watcher_pid = heartbeat.get("watcher_pid")
    saved_target_agent = target_agent_id(agent_id)
    if existing_pid and pid_is_alive(existing_pid) and heartbeat_is_fresh(agent_id):
        if saved_target_agent == agent_id:
            return

        if isinstance(watcher_pid, int) and watcher_pid != existing_pid:
            stop_pid(watcher_pid)
        stop_pid(existing_pid)

        deadline = time.time() + 5.0
        while time.time() < deadline:
            supervisor_alive = pid_is_alive(existing_pid)
            watcher_alive = isinstance(watcher_pid, int) and pid_is_alive(watcher_pid)
            if not supervisor_alive and not watcher_alive:
                break
            time.sleep(0.25)

    launcher = sys.executable
    python_exe = Path(sys.executable)
    if python_exe.name.lower() == "python.exe":
        pythonw = python_exe.with_name("pythonw.exe")
        if pythonw.exists():
            launcher = str(pythonw)

    creationflags = 0
    for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW"):
        creationflags |= getattr(subprocess, name, 0)

    env = os.environ.copy()
    env["LITEHARNESS_AGENT_ID"] = agent_id

    subprocess.Popen(
        [launcher, str(WATCHER_PATH)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
        cwd=str(WATCHER_PATH.parent),
    )


def main() -> int:
    if len(sys.argv) > 1:
        try:
            payload = json.loads(sys.argv[-1])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            event_type = payload.get("type")
            if event_type and event_type != "agent-turn-complete":
                return 0

    agent_id = read_agent_id()
    if not agent_id:
        return 0

    sync_legacy_agent_config(agent_id)
    ensure_watcher_running(agent_id)
    capture_focused_target(agent_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
