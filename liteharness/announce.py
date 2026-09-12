"""Tell every live orchestrator when a seat joins the board or takes a new name.

Ryan, 2026-09-12 11:5x: a second Codex Desktop task (SilentCrypt) had been
editing LiteSuite for an hour before the orchestrator saw it - on a screenshot,
not on the board. `discover` answers when asked; nothing PUSHED the arrival.
"add something to registration of new agents that auto alerts the orch".

The alert is an ordinary inbox message FROM the new agent TO each live
orchestrator, so it has an id, a timestamp and a body on disk like any other
traffic, and the orchestrator's existing watcher delivers it. Registration must
never fail because of it: every path here swallows its own errors.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

from . import config, inbox

# Mirrors cmd_discover / DEFAULT_PRESENCE_STALE_MS: a heartbeat older than this
# is not a live seat, whatever its pid says.
STALE_SECONDS = 600


def live_orchestrators(exclude_id: str) -> list[dict]:
    """Presence rows with tier=orchestrator that discover would call live."""
    from .hooks import _pid_alive, _read_presence

    agents_dir = config.get_root() / "agents"
    if not agents_dir.exists():
        return []
    now = time.time()
    rows: list[dict] = []
    for f in agents_dir.glob("*.json"):
        if f.name.endswith(".tmp"):
            continue
        row = _read_presence(f)
        if not row or row.get("tier") != "orchestrator":
            continue
        if row.get("agent_id") == exclude_id or row.get("exited_at"):
            continue
        try:
            age = now - datetime.fromisoformat(row.get("last_seen", "")).timestamp()
        except ValueError:
            age = float("inf")
        if age > STALE_SECONDS:
            continue
        pid = row.get("session_pid")
        # Absent pid falls back to freshness, exactly as discover does.
        if pid and not _pid_alive(pid):
            continue
        rows.append(row)
    return rows


def announce_registration(presence: dict, *, event: str) -> list[str]:
    """Send one notification per live orchestrator. Returns the message ids.

    `event` is a short past-tense phrase: "registered", "took the name X".
    """
    agent_id = str(presence.get("agent_id") or "")
    if not agent_id:
        return []
    # Test fixtures spawn real headless children (LiteSuite's LiteTuiAdapter
    # tests: four LiteTUI seats per gate run, measured 2026-09-12 12:0x) that
    # register like any seat. The fixture sets this so the board is not told.
    if os.environ.get("LITEHARNESS_NO_ANNOUNCE", "").strip() not in ("", "0", "false"):
        return []
    sent: list[str] = []
    try:
        targets = live_orchestrators(exclude_id=agent_id)
    except Exception:
        return []
    if not targets:
        return []
    body = (
        f"NEW ON THE BOARD: {presence.get('name') or '?'} ({agent_id}) {event}. "
        f"tier {presence.get('tier') or '?'}, {presence.get('cli') or '?'}/{presence.get('model') or '?'}, "
        f"cwd {presence.get('cwd') or os.getcwd()}, "
        f"spawned by {presence.get('spawned_by') or 'nobody (started directly)'}."
    )
    for orch in targets:
        try:
            sent.append(inbox.send(
                agent_id, str(orch["agent_id"]), body,
                msg_type="notification",
                cli=str(presence.get("cli") or "unknown"),
                model=str(presence.get("model") or "unknown"),
            ))
        except Exception:
            continue
    return sent
