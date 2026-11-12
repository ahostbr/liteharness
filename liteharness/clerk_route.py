"""Opt-in clerk routing. Missing or invalid configuration leaves addressing alone.

Who the orchestrator is gets decided when a message is routed, from the live registry. The
`orchestrator_id` in clerk.json is advisory: an orchestrator gets a new id on every restart, so a
stored id is stale by design and is never trusted to route mail.
"""
import json
import logging
import re
import time
from datetime import datetime

from . import config, roles
from .naming import NAME_HELD_SECONDS as _NAME_LIVE_STALE_SECONDS

_log = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def load_clerk_config() -> dict | None:
    """The stored configuration, validated. `orchestrator_id` and `orchestrator_name` are optional."""
    try:
        cfg = json.loads((config.get_root() / "clerk.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(cfg, dict) or not isinstance(cfg.get("enabled"), bool)
            or not isinstance(cfg.get("clerk_id"), str) or not _SAFE_ID.fullmatch(cfg["clerk_id"])
            or not isinstance(cfg.get("direct_senders"), list)
            or not all(isinstance(sender, str) for sender in cfg["direct_senders"])):
        return None
    stored = cfg.get("orchestrator_id")
    if stored is not None and not (isinstance(stored, str) and _SAFE_ID.fullmatch(stored)):
        return None
    if not isinstance(cfg.get("orchestrator_name"), (str, type(None))):
        return None
    return cfg


def _row(agent_id) -> dict:
    if not isinstance(agent_id, str) or not _SAFE_ID.fullmatch(agent_id):
        return {}
    try:
        row = json.loads((config.get_root() / "agents" / f"{agent_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return row if isinstance(row, dict) else {}


def orchestrator_name(cfg: dict) -> str | None:
    """The name the orchestrator answers to: recorded in the config, else the one on the stored id's old row."""
    for name in (cfg.get("orchestrator_name"), _row(cfg.get("orchestrator_id")).get("name")):
        if isinstance(name, str) and name.strip():
            return name
    return None


def _claim(agent_id: str, row: dict, cfg: dict, name: str | None) -> int | None:
    """How an agent claims to be the orchestrator; a lower number is a stronger claim, None is no claim.

    0: its registry row declares the orchestrator role (what `discover` and the role table use).
    1: it holds the orchestrator's recorded name. A resumed orchestrator can come back with another
       role on its row while it keeps its name, so the role alone would lose it.
    2: it is the stored, advisory id.
    """
    if agent_id == cfg.get("clerk_id"):
        return None
    if roles.row_role(row, "") == "orchestrator":
        return 0
    if name and row.get("name") == name:
        return 1
    return 2 if agent_id == cfg.get("orchestrator_id") else None


def is_live_orchestrator(agent_id, cfg: dict) -> bool:
    """Is this one addressee a live orchestrator? Reads that agent's own row, not the whole registry."""
    row = _row(agent_id)
    return bool(row) and _claim(agent_id, row, cfg, orchestrator_name(cfg)) is not None and clerk_is_live(agent_id)


def live_orchestrators(cfg: dict) -> list[str]:
    """Live agents with the strongest claim present, most recent heartbeat first (then by id)."""
    name = orchestrator_name(cfg)
    found = []
    try:
        paths = list((config.get_root() / "agents").glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rank = _claim(path.stem, row, cfg, name) if isinstance(row, dict) else None
        if rank is not None:
            found.append((rank, str(row.get("last_seen") or ""), path.stem))
    for rank in (0, 1, 2):
        live = sorted(((seen, agent) for claim, seen, agent in found if claim == rank and clerk_is_live(agent)),
                      reverse=True)
        if live:
            return [agent for _seen, agent in live]
    return []


def resolve_orchestrator(cfg: dict) -> str | None:
    """The one live orchestrator to report to, or None. Several live: the most recent heartbeat, and it is logged."""
    live = live_orchestrators(cfg)
    if len(live) > 1:
        _log.warning("clerk: %d live orchestrators (%s); using %s, the most recent heartbeat",
                     len(live), ", ".join(live), live[0])
    return live[0] if live else None


def routing_config(to) -> dict | None:
    """The configuration for ONE send: its `orchestrator_id` is the addressee when that addressee is a
    live orchestrator, and None otherwise, so `route_recipient` routes nothing (fail open)."""
    cfg = load_clerk_config()
    if not cfg or not cfg["enabled"]:
        return cfg
    return {**cfg, "orchestrator_id": to if is_live_orchestrator(to, cfg) else None}


def route_recipient(frm, to, direct, forwarded_for, cfg, is_live):
    """One pure decision, shared contract with LiteSuite's clerk-route.ts."""
    if (cfg and cfg["enabled"] and to == cfg["orchestrator_id"]
            and frm != cfg["clerk_id"] and not direct and not forwarded_for
            and frm not in cfg["direct_senders"] and is_live(cfg["clerk_id"])):
        return cfg["clerk_id"], cfg["orchestrator_id"]
    return to, None


def clerk_is_live(agent_id: str) -> bool:
    """Keep malformed presence from blocking mail; do not change CLI liveness."""
    try:
        return _agent_record_live(agent_id)
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return False


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

