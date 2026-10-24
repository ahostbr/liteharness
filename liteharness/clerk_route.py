"""Opt-in clerk routing. Missing or invalid configuration leaves addressing alone."""
import json
import re
import time
from datetime import datetime

from . import config
from .naming import NAME_HELD_SECONDS as _NAME_LIVE_STALE_SECONDS


def load_clerk_config() -> dict | None:
    try:
        cfg = json.loads((config.get_root() / "clerk.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(cfg, dict) or not isinstance(cfg.get("enabled"), bool)
            or any(not isinstance(cfg.get(key), str)
                   or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cfg[key])
                   for key in ("orchestrator_id", "clerk_id"))
            or not isinstance(cfg.get("direct_senders"), list)
            or not all(isinstance(sender, str) for sender in cfg["direct_senders"])):
        return None
    return cfg


def route_recipient(frm, to, direct, forwarded_for, cfg, is_live):
    """One pure decision, shared contract with LiteSuite's clerk-route.ts."""
    if (cfg and cfg["enabled"] and to == cfg["orchestrator_id"]
            and frm != cfg["clerk_id"] and not direct and not forwarded_for
            and frm not in cfg["direct_senders"] and is_live(cfg["clerk_id"])):
        return cfg["clerk_id"], cfg["orchestrator_id"]
    return to, None


def _superseded_by_later_registration(agent_id: str, data: dict) -> bool:
    """True if a LATER registration already owns this record's `session_pid`.

    🔴 THE DEFECT THIS ENDS (T141-P). `_dedupe_by_session_pid` has always known this
    rule — its own docstring says "The existing liveness check cannot catch this:
    BOTH rows carry the same LIVE pid, so `_pid_alive` is correctly True for each."
    That sentence describes a real failure and nothing acted on it, so `discover`
    and `register --takeover` disagreed about the same record.

    ⭐ MEASURED 2026-09-02. A Claude Code session was cleared and a new session
    started IN THE SAME TERMINAL. Both presence rows carried
    `session_pid` 446104 and `wt_session` 85c3ba5a — the pid of the LIVE successor.
    The dead predecessor's own `pid` (267232) and watcher (25660) were both gone,
    yet `_agent_record_live` returned True, because the only pid it consulted
    belonged to the agent asking for the name. **The liveness probe was measuring
    the claimant.** `discover` printed "superseded by a later registration on the
    same PID" for the very same record in the very same second.

    ⬜ Same rule, one implementation, so the two can no longer drift: a later
    `registered_at` on the same `session_pid` wins, exactly as the roster decides.
    """
    session_pid = data.get("session_pid")
    if not session_pid:
        return False  # unknown owner is never grouped — see _dedupe_by_session_pid
    mine = str(data.get("registered_at") or "")
    agents_dir = config.get_root() / "agents"
    try:
        entries = list(agents_dir.glob("*.json"))
    except OSError:
        return False
    for entry in entries:
        if entry.stem == agent_id:
            continue
        try:
            other = json.loads(entry.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if other.get("session_pid") != session_pid or other.get("exited_at"):
            continue
        # String compare on the ISO timestamp, the SAME comparison the roster uses.
        if str(other.get("registered_at") or "") > mine:
            return True
    return False


def _agent_record_live(agent_id: str) -> bool:
    """True if the agent's presence has an ALIVE owning session_pid, has not been
    superseded on that pid, and was seen within the name-holding bar
    (naming.NAME_HELD_SECONDS, the one is_name_taken uses). So name-takeover never
    steals a name from a live agent, however quiet — only from a dead ghost
    squatting the registry (dead pid, or no pid at all)."""
    from .hooks import _pid_alive, _record_belongs_to_process

    path = config.get_root() / "agents" / f"{agent_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if data.get("exited_at"):
        return False
    try:
        age = time.time() - datetime.fromisoformat(data.get("last_seen", "")).timestamp()
    except (ValueError, TypeError):
        return False
    if age > _NAME_LIVE_STALE_SECONDS:
        return False
    session_pid = data.get("session_pid")
    if not session_pid:  # orphaned watcher: no immutable owning session = not live
        return False
    if not _pid_alive(session_pid):
        return False
    # T1027 (Dijkstra L1): an alive pid proves nothing if Windows REUSED it after
    # the holder died — the record must have been written while THIS process ran.
    # Without it the 12 h bar would let a reused pid squat a dead seat's name.
    if not _record_belongs_to_process(agent_id, session_pid):
        return False
    # A pid can outlive the agent that registered under it: `/clear` and `/resume`
    # both reuse the terminal's process. Freshness cannot separate them either —
    # an orphaned `watch-auto` keeps heartbeating a record whose session is gone.
    return not _superseded_by_later_registration(agent_id, data)

