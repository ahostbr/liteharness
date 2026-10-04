"""Opt-in pre-write guard for folder-owned registration (T0308).

This validates caller-supplied presence fields and conservatively inspects the
registry. It cannot verify a lease in a separate child's process: the host MUST
hold/revalidate AgentSession around the official CLI adapter. session_pid is the
owning CHILD, not this CLI process. Registry presence is never folder execution
or naming authority. No timeout, heartbeat-age eviction, rename, or deletion.
Noncompliant external registry writers are not serialized by this precheck.
"""
from __future__ import annotations

import json
from pathlib import Path

from .agent_store import StoreError, _unlinked, name_key, valid_id, valid_name


def pid_alive(pid: object) -> bool:
    """Unknown/invalid/inaccessible is occupied, only known absence is dead."""
    if type(pid) is not int or pid <= 0:
        raise StoreError("Strict registration requires an inspectable owning PID")
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception as exc:
        raise StoreError("Owning PID cannot be inspected") from exc


def validate(root: Path, *, agent_id: str, name: str | None, session_pid: int | None,
             backend: str | None, model: str | None, thinking_level: str | None,
             takeover: bool) -> None:
    """Fail BEFORE any writes; same ID/same owner may idempotently refresh."""
    agent_id, name = valid_id(agent_id), valid_name(name)
    if takeover or any(not isinstance(v, str) or not v.strip()
                       for v in (backend, model, thinking_level)):
        raise StoreError("Strict registration requires complete execution and no takeover")
    if not pid_alive(session_pid):
        raise StoreError("Strict registration owner is not a live process")
    agents = _unlinked(Path(root) / "agents")
    try:
        entries = list(agents.iterdir())
    except FileNotFoundError:
        return  # lookup never creates a registry
    except OSError as exc:
        raise StoreError("Registry cannot be inspected") from exc
    for path in entries:
        if path.suffix != ".json":
            continue
        _unlinked(path)
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("Strict registry row is unreadable") from exc
        if not isinstance(row, dict) or row.get("agent_id") != path.stem:
            raise StoreError("Strict registry identity is malformed")
        same_id = row["agent_id"] == agent_id
        candidate = row.get("name")
        # Legacy registry IDs need not be UUIDs, but a missing/invalid name cannot
        # rule out a collision. Diagnose rather than silently skip that row.
        same_name = name_key(candidate) == name_key(name)
        if not same_id and not same_name:
            continue
        prior_pid = row.get("session_pid")
        if (same_id and type(prior_pid) is int and prior_pid > 0
                and prior_pid == session_pid):
            continue  # held child lease, not registry fields, proves ownership
        # No exited_at/last_seen shortcuts: unknown PID must not be overwritten.
        if pid_alive(row.get("session_pid")):
            raise StoreError("Strict agent identity/name is held by another live owner")
