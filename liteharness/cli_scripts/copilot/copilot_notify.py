#!/usr/bin/env python3
"""Bootstrap the standalone LiteHarness inbox watcher for Copilot CLI."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from copilot_targeting import (
    load_saved_target,
    save_pinned_target,
    save_target,
    select_existing_target,
)


SKILL_ROOT = Path.home() / ".copilot" / "skills" / "liteharness"
STATE_ROOT = SKILL_ROOT / "state"
PID_PATH = STATE_ROOT / "copilot_inbox_supervisor.pid"
HEARTBEAT_PATH = STATE_ROOT / "copilot_inbox_supervisor.heartbeat.json"
WATCHER_PATH = SKILL_ROOT / "scripts" / "copilot_watcher_supervisor.py"
TARGET_PATH = STATE_ROOT / "copilot_inbox_target.json"
MAX_HEARTBEAT_AGE_SEC = 20
HARNESS_AGENTS = Path.home() / ".liteharness" / "agents"


def read_pid() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        data = json.loads(PID_PATH.read_text(encoding="utf-8"))
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


def heartbeat_is_fresh() -> bool:
    data = read_heartbeat()
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, (int, float)):
        return False
    return (time.time() - float(updated_at)) <= MAX_HEARTBEAT_AGE_SEC


def read_heartbeat() -> dict:
    if not HEARTBEAT_PATH.exists():
        return {}
    try:
        data = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def target_agent_id() -> str | None:
    target = load_saved_target()
    if not target:
        return None
    agent_id = target.get("agent_id")
    return agent_id if isinstance(agent_id, str) and agent_id else None


def bind_target_agent(agent_id: str) -> None:
    target = load_saved_target() or {}
    target["agent_id"] = agent_id
    save_target(target)


def stop_pid(pid: int | None) -> None:
    if not isinstance(pid, int) or pid <= 0 or not pid_is_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def resolve_agent_id(explicit_agent_id: str | None) -> str | None:
    if explicit_agent_id:
        return explicit_agent_id

    env_agent_id = os.environ.get("LITEHARNESS_AGENT_ID")
    if env_agent_id:
        return env_agent_id

    current_wt_session = os.environ.get("WT_SESSION")
    copilot_agents: list[str] = []
    if HARNESS_AGENTS.exists():
        for path in HARNESS_AGENTS.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("cli") != "copilot-cli":
                continue
            agent_id = data.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id:
                agent_id = path.stem
            if current_wt_session and data.get("wt_session") == current_wt_session:
                return agent_id
            copilot_agents.append(agent_id)

    if len(copilot_agents) == 1:
        return copilot_agents[0]
    return None


def capture_target(
    agent_id: str,
    *,
    window_handle: int | None,
    pane_id: int | None,
) -> dict | None:
    if os.environ.get("LITESUITE_BRIDGE_TOKEN"):
        return None

    previous = load_saved_target()
    target = select_existing_target(
        agent_id,
        previous=previous if previous and previous.get("agent_id") == agent_id else None,
        window_handle=window_handle,
        pane_id=pane_id,
    )
    if not target:
        return None
    target["wt_session"] = os.environ.get("WT_SESSION")
    saved = save_target(target)
    if window_handle is not None and pane_id is not None:
        save_pinned_target(saved)
    return saved


def ensure_watcher_running(agent_id: str) -> None:
    existing_pid = read_pid()
    heartbeat = read_heartbeat()
    target_agent = target_agent_id()
    watcher_pid = heartbeat.get("watcher_pid")
    if existing_pid and pid_is_alive(existing_pid) and heartbeat_is_fresh():
        if target_agent == agent_id:
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

    bind_target_agent(agent_id)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", help="Full LiteHarness agent ID to watch.")
    parser.add_argument("--window-handle", type=int, help="Optional WT window handle override.")
    parser.add_argument("--pane-id", type=int, help="Optional WT pane id override.")
    args = parser.parse_args()

    agent_id = resolve_agent_id(args.agent_id)
    if not agent_id:
        print("Unable to resolve a Copilot LiteHarness agent ID.", file=sys.stderr)
        return 1

    ensure_watcher_running(agent_id)
    target = capture_target(
        agent_id,
        window_handle=args.window_handle,
        pane_id=args.pane_id,
    )
    if not target:
        print("Unable to capture a Windows Terminal target for Copilot.", file=sys.stderr)
        return 1

    print(json.dumps(target, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
