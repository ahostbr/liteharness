"""Bounded, read-only fleet/kanban snapshot for the Claude Mods pane.

Invoke with ``python -m liteharness.mod_fleet``. No agents are spawned and no
board status, intent confirmation, presence, or inbox files are changed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import config
from .cli import _dedupe_by_session_pid
from .hooks import _cc_tasks_db_path, _pid_alive

LIMIT = 100


def read_snapshot(
    agents_dir: Path, tasks_db: Path, *, now: float,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> dict:
    """Match discover's 600s freshness/known-owner semantics; bound UI output."""
    agents = []
    warnings = []
    try:
        paths = sorted(agents_dir.glob("*.json"))
        if not agents_dir.is_dir():
            warnings.append("Agent registry unavailable")
        for path in paths:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                age = now - datetime.fromisoformat(row.get("last_seen", "")).timestamp()
                if age < 0 or age > 600 or row.get("exited_at"):
                    continue
                pid = row.get("session_pid")
                if pid and not pid_alive(pid):
                    continue
                row["_age_seconds"] = age
                agents.append(row)
            except (OSError, ValueError, TypeError, AttributeError):
                warnings.append("Unreadable agent presence")
    except OSError:
        warnings.append("Agent registry unavailable")

    # Reuse discover's authoritative resume-owner collapse before losing PID /
    # registration evidence in the bounded display projection.
    agents.sort(key=lambda row: row["_age_seconds"])
    agents, superseded = _dedupe_by_session_pid(agents)
    agents.sort(key=lambda row: row["_age_seconds"])
    if superseded:
        warnings.append(f"{len(superseded)} superseded presence row(s) excluded")
    agent_count = len(agents)
    agents = [{key: str(row.get(key) or "")[:512] for key in
               ("agent_id", "name", "tier", "model", "cwd")}
              for row in agents[:LIMIT]]

    tasks = []
    task_count = 0
    # mode=ro never creates an absent DB or writes shared board state.
    try:
        with closing(sqlite3.connect(tasks_db.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=1)) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            task_count = con.execute("SELECT COUNT(*) FROM tasks WHERE status != 'done'").fetchone()[0]
            rows = con.execute(
                "SELECT id, title, status, assignee, tier FROM tasks "
                "WHERE status != 'done' ORDER BY priority, id LIMIT ?", (LIMIT,),
            )
            tasks = [dict(zip(("id", "title", "status", "assignee", "tier"),
                              [str(value or "")[:512] for value in row])) for row in rows]
    except (sqlite3.Error, OSError, ValueError):
        warnings.append("Task board unavailable")

    return {"schemaVersion": 1, "sampledAt": int(now * 1000),
            "agents": agents, "agentCount": agent_count,
            "supersededCount": len(superseded),
            "tasks": tasks, "taskCount": task_count,
            "warnings": sorted(set(warnings))}


def main() -> None:
    print(json.dumps(read_snapshot(config.get_root() / "agents", _cc_tasks_db_path(),
                                   now=time.time()), ensure_ascii=True))


if __name__ == "__main__":
    main()
