"""Self-only ACK producer. The enclosing seat must wait after acknowledgement."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from . import inbox
from .retirement import (RetirementRefused, bind_caller, measure_process,
                         read_registry, require_same)
from .retirement_handoff import (owned_handoff_paths, verify_committed_handoff,
                                 verify_handoff)

ACK_TTL_SECONDS = 60


def persisted_receipts() -> list[dict]:
    """Read existing maildir copies without claim/move/cleanup or directory creation."""
    found = []
    for directory in (inbox.INBOX_NEW, inbox.INBOX_CUR, inbox.INBOX_DONE):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                    raise RetirementRefused("receipt_path_unconfirmed")
                with path.open("rb") as handle:
                    raw = handle.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise RetirementRefused("receipt_too_large")
                row = json.loads(raw)
                if isinstance(row, dict):
                    found.append(row)
            except (OSError, ValueError) as exc:
                if isinstance(exc, RetirementRefused):
                    raise
                raise RetirementRefused("receipt_store_unreadable") from exc
    return found


def receipt_time(row: dict) -> float:
    try:
        value = row.get("timestamp")
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        return stamp.timestamp()
    except (AttributeError, TypeError, ValueError) as exc:
        raise RetirementRefused("receipt_timestamp_unconfirmed") from exc


def _sessions(bridge) -> list:
    reply = bridge("GET", "/pty/list")
    if (not isinstance(reply, dict) or reply.get("ok") is False
            or not isinstance(reply.get("sessions"), list)
            or any(not isinstance(s, dict) for s in reply["sessions"])):
        raise RetirementRefused("pty_list_unconfirmed")
    return reply["sessions"]


def _explicit_id() -> str | None:
    identities = {os.environ[key] for key in ("LITEHARNESS_AGENT_ID", "LITESUITE_AGENT_ID")
                  if os.environ.get(key)}
    if len(identities) > 1:
        raise RetirementRefused("caller_environment_identity_conflicts")
    return next(iter(identities), None)


def ack_idle(handoff: str | None, bridge) -> dict:
    """Verify existing handoff and own process; never save/overwrite another seat."""
    sessions = _sessions(bridge)
    registry = read_registry()
    explicit = _explicit_id()
    identity = bind_caller(registry, sessions, measure_process, explicit_id=explicit)
    if not identity.leader_id or identity.leader_id == identity.agent_id:
        raise RetirementRefused("the_leader_that_spawned_it_is_unconfirmed")
    row = registry[identity.agent_id]
    if row.get("cli") not in ("claude-code", "claude", "litetui") and row.get("spawn_mode") != "litetui":
        raise RetirementRefused("unsupported_handoff_backend")
    leader = registry.get(identity.leader_id)
    if not isinstance(leader, dict) or leader.get("agent_id") != identity.leader_id:
        raise RetirementRefused("the_leader_that_spawned_it_is_not_registered")
    roots, default = owned_handoff_paths(identity.agent_id, row)
    selected = Path(handoff) if handoff else default
    if selected is None:
        raise RetirementRefused("handoff_required_pass_--handoff_path")
    if not selected.is_absolute():
        selected = Path(row["cwd"]) / selected
    evidence = verify_handoff(selected, roots)
    if row.get("cli") in ("claude-code", "claude"):
        verify_committed_handoff(evidence, Path(row["cwd"]))
    # File I/O and GET can yield: re-read registry, process and exact PTY generation.
    fresh_sessions = _sessions(bridge)
    fresh_registry = read_registry()
    require_same(identity, bind_caller(fresh_registry, fresh_sessions, measure_process,
                                       explicit_id=_explicit_id()))
    current_leader = fresh_registry.get(identity.leader_id)
    if not isinstance(current_leader, dict) or current_leader.get("agent_id") != identity.leader_id:
        raise RetirementRefused("the_leader_that_spawned_it_changed_before_ack")
    if verify_handoff(Path(evidence.path), roots) != evidence:
        raise RetirementRefused("handoff_changed_before_ack")
    body = {"type": "ACKIDLE", "agentId": identity.agent_id,
            "sessionId": identity.session_id, "pid": identity.pid,
            "startedAtMs": identity.started_at_ms, "handoffWritten": True,
            "handoff": evidence.body()}
    receipt_id = inbox.send(identity.agent_id, identity.leader_id, json.dumps(body),
                            msg_type="RESULT", ttl_minutes=1,
                            cli=row.get("cli", "unknown"), model=row.get("model", "unknown"))
    matches = [r for r in persisted_receipts() if r.get("id") == receipt_id]
    if len(matches) != 1:
        raise RetirementRefused("persisted_ack_missing_or_ambiguous")
    receipt = matches[0]
    now = time.time()
    stamp = receipt_time(receipt)
    if (receipt.get("from") != identity.agent_id or receipt.get("to") != identity.leader_id
            or receipt.get("body") != json.dumps(body) or not identity.started_at_ms / 1000 <= stamp <= now
            or now - stamp > ACK_TTL_SECONDS):
        raise RetirementRefused("persisted_ack_identity_unconfirmed")
    return {"ok": True, "receiptId": receipt_id, "leaderId": identity.leader_id,
            "ack": {key: body[key] for key in ("agentId", "sessionId", "pid", "startedAtMs")},
            "handoff": evidence.body(),
            "instruction": "Wait without doing more work. Only the leader that spawned it may retire this seat."}


def command_ack_idle(argv: list[str], bridge) -> int:
    parser = argparse.ArgumentParser(prog="liteharness ack-idle",
        description="Verify your already-written handoff, acknowledge idle, then wait. No self-retirement.")
    parser.add_argument("--handoff", help="existing own handoff path; required unless one owned LiteTUI conversation resolves")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(ack_idle(args.handoff, bridge)))
        return 0
    except RetirementRefused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
