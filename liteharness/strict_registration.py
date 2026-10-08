"""Opt-in pre-write guard for folder-owned registration (T0308/T0332).

Folder/session ownership remains strict. Unreadable unrelated presence rows get
bounded rereads, then a validated saved names.json snapshot decides relevance.
Unknown IDs may be skipped ONLY with a valid index: their unknown names are an
explicitly accepted risk, not proof of noncollision (a843f346/cf988697/d9459069).
Own-ID and known name claims still refuse. Registry/index reads never repair,
rename, take over, delete, or serialize noncompliant external writers.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .agent_store import StoreError, _unlinked, name_key, valid_id, valid_name

_READ_ATTEMPTS = 4
_READ_DELAY_SECONDS = 0.25  # three delays: 0.75 seconds per unreadable neighbor


def pid_alive(pid: object) -> bool:
    """Unknown/invalid/inaccessible is occupied, only known absence is dead."""
    if type(pid) is not int or pid <= 0:
        raise StoreError("Strict registration requires an inspectable owning PID")
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception as exc:
        raise StoreError("Owning PID cannot be inspected") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate names index key")
        result[key] = value
    return result


def _saved_names(root: Path) -> dict[str, str]:
    """Read the actual names--list snapshot, not its filtering convenience API.

    Missing/unreadable/corrupt/malformed/ambiguous index is NOT an unknown ID.
    No index writer, lock, live registry lookup or repair is called here.
    """
    path = _unlinked(root / "names.json")
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, ValueError) as exc:
        raise StoreError("Saved names index cannot be inspected") from exc
    if not isinstance(data, dict):
        raise StoreError("Saved names index is malformed")
    names = set()
    result = {}
    for name, entry in data.items():
        try:
            canonical = name_key(name)
        except StoreError as exc:
            raise StoreError("Saved names index is malformed") from exc
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("agent_id"), str)
            or not entry["agent_id"]
            or entry["agent_id"] != entry["agent_id"].strip()
        ):
            raise StoreError("Saved names index is malformed")
        identity = entry["agent_id"]
        if canonical in names or identity in result:
            raise StoreError("Saved names index is ambiguous")
        names.add(canonical)
        result[identity] = canonical
    return result


def _read_row(path: Path, *, own_id: bool) -> object:
    attempts = 1 if own_id else _READ_ATTEMPTS
    for attempt in range(attempts):
        _unlinked(path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if attempt == attempts - 1:
                raise
            time.sleep(_READ_DELAY_SECONDS)
    raise AssertionError("Bounded registry read must return or raise")


def _skip_diagnostic(identity: str, *, known_name: bool) -> None:
    # Never copy corrupt content, arbitrary filenames, paths or index names.
    try:
        safe_id = valid_id(identity)
    except StoreError:
        safe_id = "noncanonical-id"
    reason = "index-different-name" if known_name else "valid-index-id-absent"
    print(
        f"Strict registration skipped unreadable unrelated presence: id={safe_id} "
        f"reason={reason} attempts={_READ_ATTEMPTS}",
        file=sys.stderr,
    )


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
    root = Path(root)
    agents = _unlinked(root / "agents")
    try:
        entries = list(agents.iterdir())
    except FileNotFoundError:
        return  # lookup never creates a registry
    except OSError as exc:
        raise StoreError("Registry cannot be inspected") from exc
    saved_names = None
    for path in entries:
        if path.suffix != ".json":
            continue
        _unlinked(path)
        try:
            row = _read_row(path, own_id=path.stem == agent_id)
        except StoreError:
            raise  # linked/reparse paths are invalid, never an unreadable claim
        except (OSError, ValueError) as exc:
            if path.stem == agent_id:
                raise StoreError("Strict registry row is unreadable") from exc
            if saved_names is None:
                saved_names = _saved_names(root)
            saved_name = saved_names.get(path.stem)
            if saved_name == name_key(name):
                raise StoreError(
                    "Strict saved name is held by unreadable presence"
                ) from exc
            _skip_diagnostic(path.stem, known_name=saved_name is not None)
            continue
        if not isinstance(row, dict) or row.get("agent_id") != path.stem:
            raise StoreError("Strict registry identity is malformed")
        same_id = row["agent_id"] == agent_id
        candidate = row.get("name")
        # Readable legacy IDs need not be UUIDs. Missing/invalid readable names
        # remain fatal; index fallback does not waive readable claim validation.
        same_name = name_key(candidate) == name_key(name)
        if not same_id and not same_name:
            continue
        prior_pid = row.get("session_pid")
        if (same_id and type(prior_pid) is int and prior_pid > 0
                and prior_pid == session_pid):
            continue  # held child lease, not registry fields, proves ownership
        if pid_alive(row.get("session_pid")):
            raise StoreError("Strict agent identity/name is held by another live owner")
