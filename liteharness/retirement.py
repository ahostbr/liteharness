"""Identity-bound cooperative retirement, never legacy pty-kill fallback.

Local registry/inbox plus authenticated bridge are cooperative trust, not
cryptographic sender authentication. Process measurements use targeted native
psutil calls in this short-lived CLI; no global census or shell subprocess.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from typing import Callable

from . import config


class RetirementRefused(ValueError):
    """Unconfirmed evidence must not authorize a receipt or destructive action."""


_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,127}\Z")
_IDENTITY_FIELDS = (
    "agent_id", "name", "session_pid", "session_process_started_at", "spawned_by",
    "cwd", "cli", "backend", "canvas_session_id", "session_run_id",
)


def _id(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RetirementRefused("invalid_agent_identity")
    return value


def _pid(value: object) -> int:
    if type(value) is not int or not 0 < value <= 0xFFFFFFFF:
        raise RetirementRefused("invalid_process_identity")
    return value


def _birth(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetirementRefused("invalid_process_birth")
    if not math.isfinite(value) or value <= 0:
        raise RetirementRefused("invalid_process_birth")
    return float(value)


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    started_at_ms: float


def measure_process(pid: int) -> ProcessIdentity:
    """No signals, no process_iter; PID+birthday measured before/after parent read."""
    import psutil
    try:
        process = psutil.Process(_pid(pid))
        started = _birth(process.create_time() * 1000)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            raise RetirementRefused("process_exit_unconfirmed")
        parent = process.ppid()
        # psutil caches create_time on a Process object. A second object must
        # independently resolve PID birth, or the apparent recheck is no check.
        fresh = psutil.Process(pid)
        if (fresh.create_time() * 1000 != started or not fresh.is_running()
                or fresh.status() == psutil.STATUS_ZOMBIE or fresh.ppid() != parent):
            raise RetirementRefused("process_changed_during_measurement")
        return ProcessIdentity(pid, parent, started)
    except psutil.Error as exc:
        raise RetirementRefused("process_measurement_unconfirmed") from exc


def ancestry(pid: int, measure: Callable[[int], ProcessIdentity],
             stop_pids: tuple[int, ...] = ()) -> tuple[ProcessIdentity, ...]:
    """Bounded actual chain; cycles and a newer parent are never membership proof."""
    chain: list[ProcessIdentity] = []
    seen: set[int] = set()
    for _ in range(128):
        if pid in seen:
            raise RetirementRefused("process_ancestry_cycle")
        seen.add(pid)
        current = measure(_pid(pid))
        if current.pid != pid:
            raise RetirementRefused("process_measurement_mismatch")
        _birth(current.started_at_ms)
        if chain and current.started_at_ms > chain[-1].started_at_ms:
            raise RetirementRefused("process_parent_reused")
        chain.append(current)
        if current.parent_pid == 0 or current.pid in stop_pids:
            return tuple(chain)
        pid = current.parent_pid
    raise RetirementRefused("process_ancestry_limit")


def read_registry() -> dict[str, dict]:
    """Read only existing presence, never create/guess config identity."""
    directory = config.get_root() / "agents"
    try:
        files = sorted(directory.glob("*.json"))
        if not files:
            raise RetirementRefused("registry_unavailable")
        result = {}
        for path in files:
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                raise RetirementRefused("registry_path_unconfirmed")
            with path.open("rb") as handle:
                raw = handle.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise RetirementRefused("registry_too_large")
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise RetirementRefused("registry_malformed")
            agent_id = _id(row.get("agent_id"))
            if path.name != agent_id + ".json" or agent_id in result:
                raise RetirementRefused("registry_identity_ambiguous")
            result[agent_id] = row
        return result
    except (OSError, ValueError) as exc:
        if isinstance(exc, RetirementRefused):
            raise
        raise RetirementRefused("registry_unavailable") from exc


@dataclass(frozen=True)
class SeatIdentity:
    agent_id: str
    session_id: str
    pid: int
    started_at_ms: float
    root: ProcessIdentity
    session_created_at: float
    registry_identity: str
    leader_id: str | None


def _registry_identity(row: dict) -> str:
    return json.dumps({key: row.get(key) for key in _IDENTITY_FIELDS},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def bind_seat(agent_id: str, registry: dict[str, dict], sessions: object,
              measure: Callable[[int], ProcessIdentity] = measure_process) -> SeatIdentity:
    """Unique registry/PTY join with birth and ancestry, not PTY JS creation time."""
    agent_id = _id(agent_id)
    row = registry.get(agent_id)
    if not isinstance(row, dict) or row.get("agent_id") != agent_id:
        raise RetirementRefused("agent_not_registered")
    if not isinstance(row.get("name"), str) or not row["name"].strip():
        raise RetirementRefused("agent_name_unconfirmed")
    pid = _pid(row.get("session_pid"))
    registered_birth = _birth(row.get("session_process_started_at"))
    if not isinstance(sessions, list) or any(not isinstance(s, dict) for s in sessions):
        raise RetirementRefused("pty_list_unconfirmed")
    matching = [s for s in sessions if s.get("harnessAgentId") == agent_id]
    if len(matching) != 1:
        raise RetirementRefused("pty_association_missing_or_ambiguous")
    session = matching[0]
    session_id = session.get("id")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        raise RetirementRefused("pty_identity_unconfirmed")
    root_pid = _pid(session.get("pid"))
    if sum(s.get("id") == session_id or s.get("pid") == root_pid for s in sessions) != 1:
        raise RetirementRefused("pty_generation_shared_or_ambiguous")
    chain = ancestry(pid, measure, (root_pid,))
    agent = chain[0]
    # psutil/native cross-source Windows rounding is <=1ms; never infer from registered_at.
    tolerance = 1 if os.name == "nt" else 0
    if abs(agent.started_at_ms - registered_birth) > tolerance:
        raise RetirementRefused("registered_process_birth_changed")
    roots = [p for p in chain if p.pid == root_pid]
    if len(roots) != 1:
        raise RetirementRefused("agent_not_under_pty_root")
    leader = row.get("spawned_by")
    if leader is not None:
        leader = _id(leader)
    return SeatIdentity(agent_id, session_id, pid, agent.started_at_ms, roots[0],
                        _birth(session.get("createdAt")), _registry_identity(row), leader)


def bind_caller(registry: dict[str, dict], sessions: object,
                measure: Callable[[int], ProcessIdentity] = measure_process,
                *, caller_pid: int | None = None, explicit_id: str | None = None) -> SeatIdentity:
    """Own provider must be an actual ancestor; env/config UUID alone is not proof."""
    if not isinstance(sessions, list) or any(not isinstance(s, dict) for s in sessions):
        raise RetirementRefused("pty_list_unconfirmed")
    roots = tuple(_pid(s.get("pid")) for s in sessions)
    chain = ancestry(os.getpid() if caller_pid is None else caller_pid, measure, roots)
    ancestors = {p.pid: p.started_at_ms for p in chain}
    matching = [agent_id for agent_id, row in registry.items()
                if type(row.get("session_pid")) is int and row["session_pid"] in ancestors]
    if len(matching) != 1:
        raise RetirementRefused("caller_identity_missing_or_ambiguous")
    identity = bind_seat(matching[0], registry, sessions, measure)
    if identity.started_at_ms != ancestors[identity.pid]:
        raise RetirementRefused("caller_process_changed")
    if explicit_id is not None and _id(explicit_id) != identity.agent_id:
        raise RetirementRefused("caller_environment_identity_mismatch")
    return identity


def require_same(before: SeatIdentity, after: SeatIdentity) -> None:
    if before != after:
        raise RetirementRefused("identity_changed_during_retirement")
