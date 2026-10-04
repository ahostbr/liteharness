"""T0236-T1: the persistent name index -- ONE name, ONE agent, ONE conversation.

`~/.liteharness/names.json` is the durable join `name -> agent_id -> convo_id -> cwd`
that neither the presence registry (no conversation, no cwd) nor LiteTUI's settings
(seat_id only, no name lookup) provides. It survives the presence sweep, because a
presence file is transient and this is not.

Shape (keys are the name AS GIVEN; lookup is case-insensitive; a name is unique):

    { "<Name>": {"agent_id": str, "convo_id": str, "cwd": str, "backend": str | null,
                 "model": str | null, "created_at": iso8601, "last_active_at": iso8601} }

Invariants enforced by `record_name`:
  * a name maps to exactly one agent and exactly one conversation; a different agent, or
    a different conversation, is refused unless `takeover=True` (explicit, never implied);
  * an agent id carries exactly one name: recording it under a new name renames it.

Every write is atomic (temp file + replace) and serialised by a lock file. A corrupt
index is REPORTED (IndexCorrupt), never silently replaced.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path

from . import config

INDEX_FIELDS = ("agent_id", "convo_id", "cwd", "backend", "model", "created_at", "last_active_at")
_LOCK_WAIT_SECONDS = 5.0
_LOCK_STALE_SECONDS = 30.0


class NameIndexError(ValueError):
    """Base for refusals the CLI turns into `SPAWN REFUSED` / a non-zero exit."""


class NameTaken(NameIndexError):
    """The name already belongs to a different agent or conversation."""


class IndexCorrupt(NameIndexError):
    """names.json exists but is unreadable; refusing to guess or overwrite it."""


def index_path() -> Path:
    return config.get_root() / "names.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    path = index_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise IndexCorrupt(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise IndexCorrupt(f"{path} is not valid JSON ({exc}); fix or remove it by hand") from exc
    if not isinstance(data, dict):
        raise IndexCorrupt(f"{path} must hold a JSON object")
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


@contextmanager
def _locked():
    lock = index_path().with_name("names.json.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                    lock.unlink(missing_ok=True)  # a crashed writer; the index itself is atomic
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise NameIndexError(f"names index is locked by another writer ({lock})")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _find_key(data: dict, name: str) -> str | None:
    wanted = name.strip().lower()
    for key in data:
        if key.lower() == wanted:
            return key
    return None


def _entry(name: str, row: dict) -> dict:
    out = {"name": name}
    out.update({field: row.get(field) for field in INDEX_FIELDS})
    return out


def resolve_name(name: str) -> dict | None:
    """The entry for `name` (case-insensitive) or None.

    Returns `{"name", "agent_id", "convo_id", "cwd", "backend", "model", "created_at",
    "last_active_at"}` -- `name` is the spelling the index stores. Never raises for an
    unknown name; raises IndexCorrupt for an unreadable index.
    """
    if not isinstance(name, str) or not name.strip():
        return None
    data = _load()
    key = _find_key(data, name)
    return _entry(key, data[key]) if key else None


def name_for_agent(agent_id: str) -> str | None:
    for key, row in _load().items():
        if row.get("agent_id") == agent_id:
            return key
    return None


def list_names() -> list[dict]:
    """Every entry, sorted by name (case-insensitive)."""
    data = _load()
    return [_entry(k, data[k]) for k in sorted(data, key=str.lower)]


def record_name(name: str, agent_id: str, convo_id: str, cwd: str | None, *,
                backend: str | None = None, model: str | None = None,
                takeover: bool = False) -> dict:
    """Bind `name` to (agent_id, convo_id, cwd), or refresh that binding.

    A repeat with the same agent and conversation only refreshes cwd/backend/model and
    `last_active_at` (created_at is kept). See the module docstring for the refusals.
    """
    if not isinstance(name, str) or not name.strip():
        raise NameIndexError("a name is required")
    name = name.strip()
    if not agent_id or not convo_id:
        raise NameIndexError("agent_id and convo_id are required to record a name")
    with _locked():
        data = _load()
        key = _find_key(data, name)
        now = _now()
        if key is not None:
            held = data[key]
            if not takeover and held.get("agent_id") != agent_id:
                raise NameTaken(f"name {key!r} already belongs to agent {held.get('agent_id')} "
                                f"(conversation {held.get('convo_id')}); resume it instead")
            if not takeover and held.get("convo_id") != convo_id:
                raise NameTaken(f"name {key!r} already owns conversation {held.get('convo_id')}, "
                                f"not {convo_id}; one name owns one conversation")
            created = held.get("created_at") or now
            name = key  # the spelling first recorded is the name; a re-record never re-cases it
            del data[key]
        else:
            created = now
        for other, row in list(data.items()):  # an agent id carries exactly one name
            if row.get("agent_id") == agent_id:
                del data[other]
        data[name] = {"agent_id": agent_id, "convo_id": convo_id, "cwd": cwd,
                      "backend": backend, "model": model,
                      "created_at": created, "last_active_at": now}
        config.atomic_write_json(index_path(), data)
        return _entry(name, data[name])


def _convo_seat_ids() -> set[str] | None:
    """seat_id of every conversation on disk (LiteTUI's own data root).

    None means CANNOT TELL: the data root does not resolve, cannot be listed, or a
    conversation's settings.json is unreadable (it could be the asked-about agent's).
    That is not the same fact as "no conversation owns it", and callers must not treat it so.
    """
    try:
        from .resume_seat import _convo_root
        root = _convo_root()
    except ValueError:
        return None
    seats: set[str] = set()
    try:
        for path in root.glob("*/settings.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            seat = data.get("seat_id") if isinstance(data, dict) else None
            if isinstance(seat, str) and seat:
                seats.add(seat)
    except OSError:
        return None
    return seats


def is_litetui_seat(presence: dict | None) -> bool:
    """A presence record written by a LiteTUI seat (`cli` or `backend` is litetui)."""
    if not isinstance(presence, dict):
        return False
    return "litetui" in (str(presence.get("cli") or "").lower(), str(presence.get("backend") or "").lower())


def owns_conversation(agent_id: str, presence: dict | None = None) -> bool:
    """True when `agent_id` is in the name index or is the `seat_id` of a conversation.

    The sweep's question. Fails TOWARD keeping where it matters: an unreadable index always
    counts as owning. When the LiteTUI data root cannot be read (unset and `litetui` not
    importable in the hook's python, unlistable, or an unreadable settings.json) the answer
    is UNKNOWN, and unknown keeps a LiteTUI seat -- only a LiteTUI seat can own a LiteTUI
    conversation, so a Claude Code record with an unknown data root owns none (the old
    behaviour). With no `presence` given the caller cannot say what the agent is: keep.
    The index itself needs no data root, which is why `names --backfill` exists.
    """
    if not agent_id:
        return False
    try:
        if any(row.get("agent_id") == agent_id for row in _load().values()):
            return True
    except IndexCorrupt:
        return True
    seats = _convo_seat_ids()
    if seats is None:
        return True if presence is None else is_litetui_seat(presence)
    return agent_id in seats


def backfill_from_convos(data_root, apply: bool = False) -> dict:
    """Record the UNAMBIGUOUS (seat_name, seat_id, conversation) triples found on disk.

    Reads `<data_root>/.convos/*/settings.json` (+ requires its convo.jsonl). Dry run unless
    `apply`. Nothing ambiguous is guessed: a name carried by several conversations, a seat
    carrying several conversations, a name already in the index for another agent, or an
    agent already indexed under another name is reported under `conflicts` and skipped.
    settings.json holds no cwd, so entries are written with cwd None (a resume then takes
    --cwd, or the old PTY's cwd, as for any resume).

    Returns {applied, data_root, scanned, skipped:{no_seat,no_convo_jsonl,unreadable},
             recorded:[...], already:[...], conflicts:[...]}.
    """
    convos = Path(data_root) / ".convos"
    if not convos.is_dir():
        raise NameIndexError(f"no .convos directory under {data_root}")
    skipped = {"no_seat": 0, "no_convo_jsonl": 0, "unreadable": 0}
    scanned = 0
    candidates: list[dict] = []
    for settings in sorted(convos.glob("*/settings.json")):
        scanned += 1
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skipped["unreadable"] += 1
            continue
        if not isinstance(data, dict):
            skipped["unreadable"] += 1
            continue
        seat_id = str(data.get("seat_id") or "").strip()
        seat_name = str(data.get("seat_name") or "").strip()
        if not seat_id or not seat_name:
            skipped["no_seat"] += 1
            continue
        if not (settings.parent / "convo.jsonl").is_file():
            skipped["no_convo_jsonl"] += 1
            continue
        candidates.append({"name": seat_name, "agent_id": seat_id, "convo_id": settings.parent.name,
                           "backend": data.get("backend"), "model": data.get("model")})

    by_name: dict[str, list[dict]] = {}
    by_seat: dict[str, list[dict]] = {}
    for c in candidates:
        by_name.setdefault(c["name"].lower(), []).append(c)
        by_seat.setdefault(c["agent_id"], []).append(c)
    conflicts: list[dict] = []
    reported: set[tuple[str, str]] = set()
    clean: list[dict] = []
    for c in candidates:
        if len(by_name[c["name"].lower()]) > 1:
            kind, key, group, reason = "name", c["name"].lower(), by_name[c["name"].lower()], "ambiguous-name"
        elif len(by_seat[c["agent_id"]]) > 1:
            kind, key, group, reason = "seat", c["agent_id"], by_seat[c["agent_id"]], "ambiguous-seat"
        else:
            clean.append(c)
            continue
        if (kind, key) not in reported:
            reported.add((kind, key))
            conflicts.append({"name": group[0]["name"], "reason": reason,
                              "convos": sorted(g["convo_id"] for g in group),
                              "agent_ids": sorted({g["agent_id"] for g in group})})

    recorded: list[dict] = []
    already: list[dict] = []
    with (_locked() if apply else nullcontext()):
        index = _load()
        now = _now()
        for c in clean:
            key = _find_key(index, c["name"])
            if key is not None:
                held = index[key]
                if held.get("agent_id") == c["agent_id"] and held.get("convo_id") == c["convo_id"]:
                    already.append(c)
                else:
                    conflicts.append({"name": c["name"], "reason": "name-in-index",
                                      "convos": [c["convo_id"], held.get("convo_id")],
                                      "agent_ids": [c["agent_id"], held.get("agent_id")]})
                continue
            holder = next((k for k, row in index.items() if row.get("agent_id") == c["agent_id"]), None)
            if holder is not None:
                conflicts.append({"name": c["name"], "reason": "agent-in-index",
                                  "convos": [c["convo_id"]], "agent_ids": [c["agent_id"]],
                                  "indexed_as": holder})
                continue
            index[c["name"]] = {"agent_id": c["agent_id"], "convo_id": c["convo_id"], "cwd": None,
                                "backend": c["backend"], "model": c["model"],
                                "created_at": now, "last_active_at": now}
            recorded.append(c)
        if apply and recorded:
            config.atomic_write_json(index_path(), index)
    return {"applied": bool(apply), "data_root": str(data_root), "scanned": scanned,
            "skipped": skipped, "recorded": recorded, "already": already, "conflicts": conflicts}
