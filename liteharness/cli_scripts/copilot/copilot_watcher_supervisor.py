#!/usr/bin/env python3
"""Keep the Copilot LiteHarness inbox watcher alive on Windows."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path


SKILL_ROOT = Path.home() / ".copilot" / "skills" / "liteharness"
STATE_ROOT = SKILL_ROOT / "state"
SUPERVISOR_PID_PATH = STATE_ROOT / "copilot_inbox_supervisor.pid"
SUPERVISOR_HEARTBEAT_PATH = STATE_ROOT / "copilot_inbox_supervisor.heartbeat.json"
LOG_PATH = STATE_ROOT / "copilot_inbox_watcher.log"
WATCHER_PATH = SKILL_ROOT / "scripts" / "copilot_inbox_watcher.py"
RESTART_DELAY_SEC = 2.0


def append_log(message: str) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def write_pid_file() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": time.time()}
    SUPERVISOR_PID_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def remove_pid_file() -> None:
    try:
        if SUPERVISOR_PID_PATH.exists():
            data = json.loads(SUPERVISOR_PID_PATH.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                SUPERVISOR_PID_PATH.unlink()
    except (OSError, json.JSONDecodeError):
        pass


def write_heartbeat(*, watcher_pid: int | None = None, last_error: str | None = None) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "updated_at": time.time(),
        "watcher_pid": watcher_pid,
        "last_error": last_error,
    }
    SUPERVISOR_HEARTBEAT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def watcher_launcher() -> str:
    python_exe = Path(sys.executable)
    if python_exe.name.lower() == "python.exe":
        pythonw = python_exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable


def launch_watcher() -> subprocess.Popen[bytes]:
    creationflags = 0
    for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW"):
        creationflags |= getattr(subprocess, name, 0)

    launcher = watcher_launcher()
    append_log(f"starting watcher via {launcher}")
    return subprocess.Popen(
        [launcher, str(WATCHER_PATH)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
        cwd=str(WATCHER_PATH.parent),
    )


def main() -> int:
    write_pid_file()
    atexit.register(remove_pid_file)
    append_log("supervisor started")

    watcher: subprocess.Popen[bytes] | None = None
    while True:
        try:
            if watcher is None or watcher.poll() is not None:
                if watcher is not None:
                    append_log(f"watcher exited with code {watcher.returncode}; restarting")
                    time.sleep(RESTART_DELAY_SEC)
                watcher = launch_watcher()
            write_heartbeat(watcher_pid=watcher.pid if watcher else None)
            time.sleep(5.0)
        except Exception as exc:
            append_log(f"supervisor loop failed: {exc!r}")
            write_heartbeat(watcher_pid=watcher.pid if watcher else None, last_error=repr(exc))
            time.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
