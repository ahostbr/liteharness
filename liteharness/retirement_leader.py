"""Exact spawner retirement workflow. No DELETE, signal or legacy kill fallback."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

from . import inbox
from .retirement import (RetirementRefused, bind_caller, bind_seat, measure_process,
                         read_registry, require_same)
from .retirement_cli import ACK_TTL_SECONDS, _explicit_id, _sessions, persisted_receipts, receipt_time
from .retirement_handoff import owned_handoff_paths, verify_handoff

ACK_WAIT_SECONDS = 55
ACK_POLL_SECONDS = 0.25


def process_absent(pid: int) -> bool:
    """Only targeted native NoSuchProcess is positive absence, never permission failure."""
    import psutil
    try:
        psutil.Process(pid).create_time()
        return False
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def context_has_session(reply: object, session_id: str) -> bool | None:
    """Membership in returned terminal leaves, not global canvas completeness.

    None means terminal metadata is unavailable or malformed. A valid pane
    never repairs missing metadata in another returned terminal pane.
    """
    if not isinstance(reply, dict) or reply.get("ok") is False or reply.get("error"):
        return None
    terminal_seen = False
    malformed = False
    matched = False
    for key in ("activePanes", "hiddenPanes"):
        panes = reply.get(key)
        if not isinstance(panes, list):
            malformed = True
            continue
        for pane in panes:
            if not isinstance(pane, dict):
                malformed = True
                continue
            if (pane.get("type") != "terminal" and pane.get("hasTerminal") is not True
                    and "leaves" not in pane):
                if not isinstance(pane.get("type"), str) or not pane["type"]:
                    malformed = True
                continue
            terminal_seen = True
            leaves = pane.get("leaves")
            if not isinstance(leaves, list) or not leaves:
                malformed = True
                continue
            for leaf in leaves:
                sessions = leaf.get("sessionIds") if isinstance(leaf, dict) else None
                if (not isinstance(sessions, list)
                        or any(not isinstance(s, str) or not s for s in sessions)):
                    malformed = True
                    continue
                matched = matched or session_id in sessions
    if matched:
        return True
    return None if malformed or not terminal_seen else False


def bound_ack(receipts: list[dict], target, leader_id: str, requested_at: float,
              old_ids: set[str], *, now: float) -> tuple[dict, dict] | None:
    counts = Counter(row.get("id") for row in receipts if isinstance(row.get("id"), str))
    valid = []
    for row in receipts:
        receipt_id = row.get("id")
        if (not isinstance(receipt_id, str) or counts[receipt_id] != 1 or receipt_id in old_ids
                or row.get("from") != target.agent_id or row.get("to") != leader_id):
            continue
        try:
            stamp = receipt_time(row)
            if not target.started_at_ms / 1000 <= stamp <= now or stamp < requested_at or now - stamp > ACK_TTL_SECONDS:
                continue
            body = json.loads(row.get("body", ""))
            if not isinstance(body, dict):
                continue
            if (body.get("type") != "ACKIDLE" or body.get("agentId") != target.agent_id
                    or body.get("sessionId") != target.session_id or type(body.get("pid")) is not int
                    or body["pid"] != target.pid or isinstance(body.get("startedAtMs"), bool)
                    or body.get("startedAtMs") != target.started_at_ms or body.get("handoffWritten") is not True):
                continue
            valid.append(({"agentId": target.agent_id, "sessionId": target.session_id,
                           "pid": target.pid, "startedAtMs": target.started_at_ms,
                           "receiptId": receipt_id}, body))
        except (RetirementRefused, ValueError, TypeError):
            continue
    if len(valid) > 1:
        raise RetirementRefused("idle_ack_ambiguous_request_again")
    return valid[0] if valid else None


def _verify_ack_handoff(body: dict, agent_id: str, row: dict) -> None:
    evidence = body.get("handoff")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str):
        raise RetirementRefused("ack_handoff_evidence_required")
    path = Path(evidence["path"])
    if not path.is_absolute() or type(evidence.get("size")) is not int:
        raise RetirementRefused("ack_handoff_evidence_malformed")
    roots, _ = owned_handoff_paths(agent_id, row)
    if verify_handoff(path, roots).body() != evidence:
        raise RetirementRefused("ack_handoff_changed_or_unrecoverable")


def retire(agent_id: str, force: bool, bridge) -> dict:
    """Ask, bounded wait, fresh proof, POST once, independently verify outcome."""
    if type(force) is not bool:
        raise RetirementRefused("force_must_be_explicit_boolean")
    sessions = _sessions(bridge)
    registry = read_registry()
    caller = bind_caller(registry, sessions, measure_process, explicit_id=_explicit_id())
    target = bind_seat(agent_id, registry, sessions, measure_process)
    # Preserve the measured registry JSON, including absent versus present keys.
    # The Suite final owner defines which identity fields are compared.
    registry_identity = json.loads(json.dumps(registry[target.agent_id]))
    if target.agent_id == caller.agent_id:
        raise RetirementRefused("self_retirement_forbidden")
    if target.leader_id != caller.agent_id:
        raise RetirementRefused("only_the_leader_that_spawned_it_may_retire")
    # Observe returned active/hidden views; unavailable metadata is not a refusal.
    context_has_session(bridge("GET", "/context"), target.session_id)
    ack = None
    ack_body = None
    if not force:
        old_ids = {row.get("id") for row in persisted_receipts() if isinstance(row.get("id"), str)}
        requested_at = time.time()
        inbox.send(caller.agent_id, target.agent_id, json.dumps({
            "type": "RETIRE_REQUEST", "agentId": target.agent_id,
            "sessionId": target.session_id, "pid": target.pid,
            "startedAtMs": target.started_at_ms,
            "instruction": "Finish and save your own handoff, run liteharness ack-idle --handoff <path>, then wait without more work."}),
            msg_type="TASK", ttl_minutes=1)
        deadline = time.monotonic() + ACK_WAIT_SECONDS
        while time.monotonic() < deadline:
            candidate = bound_ack(persisted_receipts(), target, caller.agent_id,
                                  requested_at, old_ids, now=time.time())
            if candidate:
                ack, ack_body = candidate
                break
            time.sleep(min(ACK_POLL_SECONDS, max(0, deadline - time.monotonic())))
        if ack is None:
            raise RetirementRefused("fresh_idle_ack_timeout_no_force_fallback")
        _verify_ack_handoff(ack_body, target.agent_id, registry[target.agent_id])
    fresh_sessions = _sessions(bridge)
    fresh_registry = read_registry()
    require_same(caller, bind_caller(fresh_registry, fresh_sessions, measure_process, explicit_id=_explicit_id()))
    require_same(target, bind_seat(agent_id, fresh_registry, fresh_sessions, measure_process))
    if not force:
        # Re-read persisted authority after the final GET, not just its old body.
        final_ack = bound_ack(persisted_receipts(), target, caller.agent_id,
                              requested_at, old_ids, now=time.time())
        if final_ack is None or final_ack[0] != ack:
            raise RetirementRefused("idle_ack_changed_before_post")
        _verify_ack_handoff(final_ack[1], target.agent_id, fresh_registry[target.agent_id])
    # Handoff verification performs file/process work too. Re-measure after it,
    # without another bridge await between these final checks and dispatch.
    final_registry = read_registry()
    require_same(caller, bind_caller(final_registry, fresh_sessions, measure_process, explicit_id=_explicit_id()))
    require_same(target, bind_seat(agent_id, final_registry, fresh_sessions, measure_process))
    body = {"sessionId": target.session_id, "expectedAgentId": target.agent_id,
            "leaderId": caller.agent_id,
            "retireProcess": {"pid": target.pid, "startedAtMs": target.started_at_ms},
            "registryIdentity": registry_identity,
            **({"force": True} if force else {"idleAck": ack})}
    response = bridge("POST", "/pty/retire", body)
    retirement = response.get("retirement") if isinstance(response, dict) else None
    if (not isinstance(retirement, dict) or retirement.get("status") != "terminal-closed"
            or retirement.get("agentExited") is not True or retirement.get("terminalClosed") is not True):
        return {"ok": False, "error": "retirement_refused_partial_or_unknown_no_retry", "response": response}
    # A cleanup500 may already mean process exit; verification never retries POST.
    verification: dict[str, bool | None] = {
        "ptyGone": False, "processGone": False, "rootGone": False, "leafGone": None}
    try:
        remaining = _sessions(bridge)
        verification["ptyGone"] = not any(s.get("id") == target.session_id for s in remaining)
        verification["processGone"] = process_absent(target.pid)
        verification["rootGone"] = process_absent(target.root.pid)
        membership = context_has_session(bridge("GET", "/context"), target.session_id)
        verification["leafGone"] = None if membership is None else not membership
    except RetirementRefused:
        pass
    if verification["leafGone"] is None:
        message = "terminal closed; canvas leaf could not be confirmed (terminal list unavailable)"
    elif verification["leafGone"]:
        message = "terminal closed; target absent from returned terminal leaves (not global canvas confirmation)"
    else:
        message = "terminal closed; target still present in returned terminal leaves"
    return {"ok": all(value is True for value in verification.values()), "response": response,
            "verification": verification, "message": message,
            "error": None if all(value is True for value in verification.values()) else "terminal_closed_verification_incomplete_no_retry"}


def command_retire(argv: list[str], bridge) -> int:
    parser = argparse.ArgumentParser(prog="liteharness retire",
        description="Ask and retire only a seat spawned by this verified caller; no automatic force fallback.")
    parser.add_argument("agent_id")
    parser.add_argument("--force", action="store_true", help="explicit forced/unflushed consent; only the leader that spawned it")
    args = parser.parse_args(argv)
    try:
        result = retire(args.agent_id, args.force, bridge)
        print(json.dumps(result))
        return 0 if result["ok"] else 1
    except RetirementRefused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
