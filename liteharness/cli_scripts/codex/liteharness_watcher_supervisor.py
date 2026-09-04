#!/usr/bin/env python3
"""Single attached Codex stdout consumer (legacy supervisor entrypoint).

Launch in an attached tool terminal and read its output. Process liveness alone
does not prove host-level asynchronous delivery. No detached child or UI injection.
"""
from __future__ import annotations
import argparse
import os
import re
import stat
import sys
from pathlib import Path
from liteharness import config, hooks, inbox

def configure_root(root: Path) -> None:
    config.HARNESS_ROOT = root
    config.CONFIG_PATH = root / "config.json"
    inbox.INBOX_ROOT = root / "inbox"
    for name in ("NEW", "CUR", "DONE", "TMP"):
        setattr(inbox, f"INBOX_{name}", inbox.INBOX_ROOT / name.lower())

def attached_stdout() -> bool:
    """Accept a terminal or tool-captured pipe; reject pythonw/null/log files."""
    try:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetFileType.argtypes = [wintypes.HANDLE]
            kernel.GetFileType.restype = wintypes.DWORD
            kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetConsoleMode.restype = wintypes.BOOL
            handle = msvcrt.get_osfhandle(sys.stdout.fileno())
            mode = wintypes.DWORD()
            return bool(kernel.GetFileType(handle) == 3 or kernel.GetConsoleMode(handle, ctypes.byref(mode)))
        return bool(sys.stdout.isatty() or stat.S_ISFIFO(os.fstat(sys.stdout.fileno()).st_mode))
    except (AttributeError, OSError, ValueError):
        return False

def acquire(handle) -> None:
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", default=os.environ.get("LITEHARNESS_AGENT_ID") or os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--root", type=Path, default=config.get_root())
    parser.add_argument("--model", default=os.environ.get("LITEHARNESS_MODEL"))
    args = parser.parse_args(argv)
    if not args.agent_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.agent_id):
        parser.error("an explicit valid --agent-id or LITEHARNESS_AGENT_ID is required")
    if not attached_stdout():
        print("[LITEHARNESS] Refusing an unattached inbox consumer. Launch in a tool terminal with captured stdout.", file=sys.stderr)
        return 2
    configure_root(args.root.resolve())
    os.environ["LITEHARNESS_AGENT_ID"] = args.agent_id
    if args.model:
        os.environ["LITEHARNESS_MODEL"] = args.model
    os.environ.setdefault("LITEHARNESS_TIER", "worker")
    state = config.get_root() / "codex_sessions" / "monitors"
    state.mkdir(parents=True, exist_ok=True)
    record = state / f"{args.agent_id}.json"
    # OS-held lock releases on process death; stale PID files never own the lock.
    with (state / f"{args.agent_id}.lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        try:
            acquire(handle)
        except OSError:
            print("[LITEHARNESS] A stdout watcher already owns this agent's inbox.", file=sys.stderr)
            return 3
        config.atomic_write_json(record, {"agent_id": args.agent_id, "pid": os.getpid(), "delivery": "stdout"})
        try:
            hooks.update_heartbeat(agent_id=args.agent_id, is_watcher=True)
            hooks.watch_inbox(override_agent_id=args.agent_id)
        except KeyboardInterrupt:
            return 0
        finally:
            record.unlink(missing_ok=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
