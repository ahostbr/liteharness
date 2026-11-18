"""Which bridge PTY a seat's CLI process runs in, proven from the process tree (T1350).

A registry row's `canvas_session_id` was only ever its stored value or the seat's env, and
neither is evidence: the stored value outlives its PTY, and the env is inherited by every
process started under the seat. Measured 2026-11-18: a seat's row named a PTY that had been
killed, through many re-registrations, while its CLI was a direct child of another PTY's
shell. Everything that reads the row (compaction, kill, placement, tab rename) then aimed at
a session that did not exist.

The proof is narrow on purpose. The session is proven only when the CLI process IS the PTY's
own process, or is a DIRECT child of the PTY's shell. A more distant ancestor proves nothing:
a CLI started from a seat's tool shell, and every seat of a pty daemon or an app that was
itself started from inside a seat, has that host's pane shell among its ancestors. Accepting
any ancestor would hand those rows the host's pane, which is the hole the never-downgrade
rule in `hooks.register_presence` exists to close.

Anything unreadable, ambiguous or malformed is "not proven". Not proven never changes a row.
"""
from __future__ import annotations

import json
import ntpath
import os
import urllib.request
from typing import Callable

from .process_ancestry import process_ancestry

# The same names `checkpoint_restore.SHELLS` trusts; a test holds the two equal.
SHELLS = frozenset({'powershell.exe', 'pwsh.exe', 'cmd.exe', 'bash.exe', 'sh.exe'})
# A PTY row is stamped just after its process is created, on the same clock.
BIRTH_SLACK_SECONDS = 1.0


def pty_sessions(url: str, token: str, timeout: float = 2.0) -> list | None:
    """The bridge's `GET /pty/list` rows, or None for any transport or shape failure."""
    request = urllib.request.Request(f"{url}/pty/list", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.loads(response.read())
    except (OSError, ValueError):
        return None
    sessions = answer.get("sessions") if isinstance(answer, dict) else None
    return sessions if isinstance(sessions, list) else None


def proven_session(agent_id: str, session_pid: int, fetch: Callable[[], list | None]) -> str | None:
    """The id of the one PTY that `session_pid` provably runs in, else None.

    `fetch` returns the bridge's PTY rows. It is called only after the process tree has shown
    that this registration belongs to `session_pid`, so a refusal costs no network call.
    """
    if type(session_pid) is not int or session_pid <= 0:
        return None
    # 1. The registering process descends from the CLI through live processes, each parent
    #    born no later than its child. A stale or reused pid in the row fails here.
    if not process_ancestry(os.getpid(), stop_at=session_pid):
        return None
    sessions = fetch()
    if not isinstance(sessions, list):
        return None
    by_pid: dict[int, list[dict]] = {}
    for row in sessions:
        if isinstance(row, dict) and type(row.get("pid")) is int and row["pid"] > 0:
            by_pid.setdefault(row["pid"], []).append(row)
    # 2. The PTY's process is the CLI itself (one identity) or its direct parent (two). The walk
    #    stops at the PTY's pid and is refused unless every process on it is live and birth-ordered.
    #    At most one pid can sit at each distance, and the CLI's own PTY outranks its parent's.
    paths = [(path, rows) for pid, rows in by_pid.items()
             if 1 <= len(path := process_ancestry(session_pid, stop_at=pid)) <= 2]
    if not paths:
        return None
    path, rows = min(paths, key=lambda found: len(found[0]))
    if len(rows) != 1:
        return None
    row = rows[0]
    session, created = row.get("id"), row.get("createdAt")
    if not isinstance(session, str) or not session:
        return None
    # 3. The row is not another seat's, and a parent PTY is a plain shell: an agent process that
    #    is itself a PTY never lends its pane to a CLI it spawned.
    if row.get("harnessAgentId") not in (None, "", agent_id):
        return None
    if len(path) == 2:
        shell = row.get("shell")
        if not isinstance(shell, str) or ntpath.basename(shell).lower() not in SHELLS:
            return None
    # 4. The process holding the row's pid is the one the row was created for, not a later
    #    process that was handed the pid of a dead shell the bridge still lists.
    if type(created) not in (int, float) or path[-1].started_at > created / 1000 + BIRTH_SLACK_SECONDS:
        return None
    return session
