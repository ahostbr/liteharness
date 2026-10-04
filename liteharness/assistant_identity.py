"""Read-only Stop ownership consistency, not same-user cryptographic authority."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_TIERS = frozenset({"orchestrator", "leader", "worker", "thinker", "reviewer"})


def _positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate registry field")
        result[key] = value
    return result


def _read(path):
    row = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object)
    if not isinstance(row, dict):
        raise ValueError("registry object required")
    agent_id = row.get("agent_id")
    if (not isinstance(agent_id, str) or not _ID.fullmatch(agent_id)
            or path.stem != agent_id or row.get("tier") not in _TIERS):
        raise ValueError("registry identity required")
    return row


def _process(api, pid):
    if type(pid) is not int or pid <= 0:
        raise ValueError("process id required")
    process = api.Process(pid)
    birth = process.create_time() * 1000
    if not _positive_number(birth) or not process.is_running():
        raise ValueError("process birth required")
    if process.status() == api.STATUS_ZOMBIE:
        raise ValueError("process is not live")
    return process, birth


def _nearest_cli(api, hook_pid):
    process, child_birth = _process(api, hook_pid)
    seen = {hook_pid}
    snapshots = [(hook_pid, child_birth)]
    for _ in range(64):
        pid = process.ppid()
        if type(pid) is not int or pid <= 0 or pid in seen:
            raise ValueError("unproven ancestry")
        seen.add(pid)
        process, birth = _process(api, pid)
        if birth > child_birth:
            raise ValueError("ancestry birth mismatch")
        snapshots.append((pid, birth))
        # The executable itself must be recognized. An arbitrary command line
        # mentioning a CLI, a common shell, or an outer CLI cannot nominate it.
        executable = process.exe()
        if not isinstance(executable, str) or not executable:
            raise ValueError("executable unavailable")
        name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name in {"claude", "claude.exe", "claude-code", "claude-code.exe"}:
            for previous_pid, previous_birth in snapshots:
                if _process(api, previous_pid)[1] != previous_birth:
                    raise ValueError("ancestry process changed")
            return pid, birth
        child_birth = birth
    raise ValueError("ancestry depth exceeded")


def _live_row(api, row):
    pid, birth = row.get("session_pid"), row.get("session_process_started_at")
    if type(pid) is not int or pid <= 0 or not _positive_number(birth):
        raise ValueError("registered process required")
    try:
        actual = _process(api, pid)[1]
    except api.NoSuchProcess:
        return False
    if abs(actual - birth) > 1:
        raise ValueError("registered process changed")
    return True


def _fields(row):
    return tuple(row.get(key) for key in (
        "agent_id", "tier", "pane_id", "session_pid", "session_process_started_at"))


def _own_assistant_proof(hook_input, root, *, process_api=None, hook_pid=None):
    """Return only this directly mapped live singleton orchestrator, or None.

    No environment nomination, adoption, registry writes or cross-seat selection.
    The final re-read reduces races; it cannot make a same-user registry atomic.
    """
    try:
        if sys.platform not in {"win32", "linux", "darwin"}:
            return None
        if not isinstance(hook_input, dict):
            return None
        agent_id = hook_input.get("session_id")
        if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
            return None
        if process_api is None:
            import psutil as process_api
        pid, birth = _nearest_cli(process_api, os.getpid() if hook_pid is None else hook_pid)
        directory = Path(root) / "agents"
        own_path = directory / (agent_id + ".json")
        own = _read(own_path)
        pane_id = own.get("pane_id")
        if (own.get("tier") != "orchestrator" or not isinstance(pane_id, str)
                or not pane_id.strip() or type(own.get("session_pid")) is not int
                or own["session_pid"] != pid
                or not _positive_number(own.get("session_process_started_at"))
                or abs(own["session_process_started_at"] - birth) > 1
                or not _live_row(process_api, own)):
            return None
        live = []
        for path in directory.iterdir():
            if path.suffix != ".json":
                continue
            row = _read(path)
            if row["tier"] == "orchestrator" and _live_row(process_api, row):
                live.append(row["agent_id"])
        if live != [agent_id]:
            return None
        if (_fields(_read(own_path)) != _fields(own)
                or _process(process_api, pid)[1] != birth):
            return None
        return (agent_id, pane_id), (_fields(own), pid, birth)
    except Exception:
        # Unknown liveness, inspection/import/read failures and malformed rows
        # are refusal, never evidence that a second orchestrator is irrelevant.
        return None


def own_assistant_identity(hook_input, root, *, process_api=None, hook_pid=None):
    proof = _own_assistant_proof(hook_input, root, process_api=process_api, hook_pid=hook_pid)
    return proof[0] if proof is not None else None


def assistant_generation(hook_input, root):
    """Callsite-private immutable proof for exact generation recheck before HTTP."""
    return _own_assistant_proof(hook_input, root)
