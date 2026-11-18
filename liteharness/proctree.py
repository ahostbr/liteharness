"""Kill a process and its TRUE descendants, never a stranger that reused a pid (T1337).

Windows reuses pids, and a process keeps the ParentProcessId of a parent that died long
ago. `taskkill /PID <p> /T` walks that field, so any tree kill can reach an OLDER,
unrelated process whose dead parent's pid was later handed to something in the tree.

Measured 2026-11-17: the LiteSuite PTY daemon (electron, pty-daemon.js, started 11-15)
named pid 27484 as its parent; 27484 was by then a conhost created 11-17 inside seat
Kubrick-T1335's tree. A `pty-kill` of that seat (`taskkill /T`) would have killed the
daemon and every PTY seat it hosts. seat_close.ps1 refused only because it happened to
see electron in the tree.

The rule: a child counts only if it was created at or after its parent.
"""
from __future__ import annotations

from typing import Iterable, NamedTuple


class Proc(NamedTuple):
    pid: int
    ppid: int
    create_time: float


def true_descendants(root: int, procs: Iterable[Proc]) -> list[int]:
    """Pids below `root`, breadth-first, keeping only children younger than their parent.

    A child older than its recorded parent is a pid-reuse artefact: it and everything
    under it are skipped. Pure, so the rule can be tested without real processes.
    """
    by_pid = {p.pid: p for p in procs}
    if root not in by_pid:
        return []
    kids: dict[int, list[Proc]] = {}
    for p in by_pid.values():
        if p.pid != p.ppid:  # pid 0 on Windows names itself
            kids.setdefault(p.ppid, []).append(p)
    out: list[int] = []
    seen = {root}
    queue = [root]
    while queue:
        parent = by_pid[queue.pop(0)]
        for child in kids.get(parent.pid, []):
            if child.pid in seen or child.create_time < parent.create_time:
                continue
            seen.add(child.pid)
            out.append(child.pid)
            queue.append(child.pid)
    return out


def snapshot() -> list[Proc]:
    import psutil

    procs: list[Proc] = []
    for p in psutil.process_iter(["pid", "ppid", "create_time"]):
        info = p.info
        if info.get("create_time") is None or info.get("ppid") is None:
            continue
        procs.append(Proc(info["pid"], info["ppid"], info["create_time"]))
    return procs


def kill_true_tree(pid: int) -> bool:
    """Kill `pid` and its true descendants, deepest first. True when `pid` is gone."""
    import psutil

    for target in reversed(true_descendants(pid, snapshot())):
        try:
            psutil.Process(target).kill()
        except psutil.Error:
            pass
    try:
        root = psutil.Process(pid)
        root.kill()
        root.wait(timeout=5)
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False
    return not psutil.pid_exists(pid)
