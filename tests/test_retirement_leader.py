"""Leader workflow fixtures. No live signals, HTTP, PTYs or provider work."""
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from liteharness import retirement as r, retirement_leader as leader


def envelope(target, now=100, receipt_id="fresh", **body_changes):
    body = {"type": "ACKIDLE", "agentId": target.agent_id,
            "sessionId": target.session_id, "pid": target.pid,
            "startedAtMs": target.started_at_ms, "handoffWritten": True,
            "handoff": {"path": "fixture", "size": 1, "sha256": "fixture"}, **body_changes}
    return {"id": receipt_id, "from": target.agent_id, "to": "leader",
            "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "body": json.dumps(body)}


@pytest.fixture
def workflow(monkeypatch):
    caller = r.SeatIdentity("leader", "leader-seat", 50, 5000,
                           r.ProcessIdentity(45, 0, 4000), 6000, "leader-row", None)
    target = r.SeatIdentity("agent", "seat", 20, 2000,
                           r.ProcessIdentity(10, 0, 1000), 3000, "agent-row", "leader")
    state = {"caller": caller, "target": target, "messages": [], "clock": 100.0,
             "sent": [], "calls": [], "posted": False, "ack": True,
             "response": {"ok": True, "retirement": {"status": "terminal-closed",
                          "agentExited": True, "terminalClosed": True}},
             "gone": True, "leaf": False, "mutate_get": None}
    monkeypatch.setattr(leader, "read_registry", lambda: {"agent": {"agent_id": "agent"}, "leader": {"agent_id": "leader"}})
    monkeypatch.setattr(leader, "bind_caller", lambda *a, **kw: state["caller"])
    monkeypatch.setattr(leader, "bind_seat", lambda *a, **kw: state["target"])
    monkeypatch.setattr(leader, "_explicit_id", lambda: "leader")
    monkeypatch.setattr(leader, "persisted_receipts", lambda: list(state["messages"]))
    monkeypatch.setattr(leader, "_verify_ack_handoff", lambda *a: None)
    monkeypatch.setattr(leader.time, "time", lambda: state["clock"])
    monkeypatch.setattr(leader.time, "monotonic", lambda: state["clock"])
    monkeypatch.setattr(leader.time, "sleep", lambda seconds: state.update(clock=state["clock"] + seconds))
    monkeypatch.setattr(leader, "process_absent", lambda pid: state["gone"])

    def send(sender, recipient, body, **kw):
        state["sent"].append((sender, recipient, json.loads(body)))
        if state["ack"]:
            state["messages"].append(envelope(state["target"], state["clock"]))
        return "request"
    monkeypatch.setattr(leader.inbox, "send", send)

    def bridge(method, path, body=None):
        state["calls"].append((method, path, body))
        assert method != "DELETE" and path not in ("/pty/write", "/pty/talk")
        if (method, path) == ("GET", "/pty/list"):
            if state["mutate_get"] and len(state["calls"]) > 1 and not state["posted"]:
                state["mutate_get"]()
            return {"sessions": []}
        if (method, path) == ("GET", "/context"):
            return {"activePanes": [{"type": "terminal", "hasTerminal": True,
                    "leaves": [{"sessionIds": ["seat"] if state["posted"] and state["leaf"] else ["other"]}]}],
                    "hiddenPanes": []}
        assert (method, path) == ("POST", "/pty/retire")
        state["posted"] = True
        return state["response"]
    state["bridge"] = bridge
    return state


def posts(state):
    return [call for call in state["calls"] if call[0] == "POST"]


def test_default_asks_exact_seat_and_posts_one_real_fresh_ack(workflow):
    result = leader.retire("agent", False, workflow["bridge"])
    assert result["ok"] and all(result["verification"].values())
    assert workflow["sent"][0][:2] == ("leader", "agent")
    assert workflow["sent"][0][2]["type"] == "RETIRE_REQUEST"
    assert len(posts(workflow)) == 1
    body = posts(workflow)[0][2]
    assert "force" not in body and body["idleAck"]["receiptId"] == "fresh"
    assert body["leaderId"] == "leader" and body["expectedAgentId"] == "agent"


def test_explicit_force_does_not_mint_or_wait_for_ack(workflow):
    result = leader.retire("agent", True, workflow["bridge"])
    assert result["ok"] and not workflow["sent"]
    assert posts(workflow)[0][2] == {"sessionId": "seat", "expectedAgentId": "agent", "leaderId": "leader", "force": True,
        "retireProcess": {"pid": 20, "startedAtMs": 2000}, "registryIdentity": {"agent_id": "agent"}}


@pytest.mark.parametrize("force", [False, True])
def test_payload_uses_exact_measured_birth_and_frozen_registry_json(workflow, monkeypatch, force):
    row = {"agent_id": "agent", "session_pid": 20, "name": "Agent", "cwd": "C:/own",
           "spawned_by": "leader", "registered_at": "registered", "started_at": None}
    monkeypatch.setattr(leader, "read_registry", lambda: {"agent": row, "leader": {"agent_id": "leader"}})
    workflow["target"] = replace(workflow["target"], started_at_ms=2000.37)
    original = dict(row)
    # Mutation during context awaits cannot rewrite the frozen caller claim.
    bridge = workflow["bridge"]
    def mutated(method, path, body=None):
        if path == "/context" and not workflow["posted"]:
            row["registered_at"] = "replacement"
        return bridge(method, path, body)
    assert leader.retire("agent", force, mutated)["ok"]
    body = posts(workflow)[0][2]
    assert body["retireProcess"] == {"pid": 20, "startedAtMs": 2000.37}
    assert body["registryIdentity"] == original
    assert body["registryIdentity"]["started_at"] is None
    assert "missing_key" not in body["registryIdentity"]
    assert "terminalIdentity" not in body and "root" not in body


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("case", ["not-spawner", "self"])
def test_force_never_bypasses_spawner_or_self_identity(workflow, force, case):
    if case == "not-spawner":
        workflow["target"] = replace(workflow["target"], leader_id="other")
    else:
        workflow["target"] = replace(workflow["target"], agent_id="leader")
    with pytest.raises(r.RetirementRefused):
        leader.retire(workflow["target"].agent_id, force, workflow["bridge"])
    assert not workflow["sent"] and not posts(workflow)


def test_fresh_ack_timeout_is_bounded_55s_and_has_no_force_fallback(workflow):
    workflow["ack"] = False
    with pytest.raises(r.RetirementRefused, match="timeout_no_force_fallback"):
        leader.retire("agent", False, workflow["bridge"])
    assert workflow["clock"] == 155 and len(workflow["sent"]) == 1
    assert not posts(workflow)


@pytest.mark.parametrize("changed", ["caller", "target"])
def test_awaited_final_get_identity_change_refuses_before_post(workflow, changed):
    workflow["mutate_get"] = lambda: workflow.update({changed: replace(workflow[changed], session_id="replacement")})
    with pytest.raises(r.RetirementRefused, match="identity_changed"):
        leader.retire("agent", False, workflow["bridge"])
    assert not posts(workflow)


def test_final_get_receipt_mutation_refuses_before_post(workflow):
    workflow["mutate_get"] = lambda: workflow["messages"].clear()
    with pytest.raises(r.RetirementRefused, match="idle_ack_changed_before_post"):
        leader.retire("agent", False, workflow["bridge"])
    assert not posts(workflow)


@pytest.mark.parametrize("response", [{"ok": False, "error": "transport_unknown"},
    {"ok": False, "retirement": {"status": "agent-exited-terminal-open", "agentExited": True, "terminalClosed": False}}])
def test_partial_or_unknown_never_retries(workflow, response):
    workflow["response"] = response
    assert not leader.retire("agent", False, workflow["bridge"])["ok"]
    assert len(posts(workflow)) == 1


@pytest.mark.parametrize("incomplete", ["process", "leaf"])
def test_confirmed_close_but_independent_verification_incomplete_is_reported(workflow, incomplete):
    if incomplete == "process":
        workflow["gone"] = False
    else:
        workflow["leaf"] = True
    result = leader.retire("agent", True, workflow["bridge"])
    assert not result["ok"] and result["response"]["retirement"]["terminalClosed"]
    assert result["error"].endswith("no_retry") and len(posts(workflow)) == 1


@pytest.mark.parametrize("case", ["stale", "future", "pre-request", "pre-birth", "old-id", "duplicate", "wrong-recipient", "wrong-sender", "wrong-session", "wrong-birth", "false-handoff", "bool-pid"])
def test_bound_ack_adversarial_evidence_refuses(workflow, case):
    target = workflow["target"]
    receipt = envelope(target, 100)
    receipts = [receipt]
    old = set()
    requested = 99
    now = 101
    if case == "stale": now = 160.001
    elif case == "future": now = 99
    elif case == "pre-request": requested = 100.001
    elif case == "pre-birth": receipt["timestamp"] = datetime.fromtimestamp(1, timezone.utc).isoformat()
    elif case == "old-id": old.add("fresh")
    elif case == "duplicate": receipts.append(dict(receipt))
    elif case == "wrong-recipient": receipt["to"] = "other"
    elif case == "wrong-sender": receipt["from"] = "other"
    else:
        changes = {"wrong-session": {"sessionId": "other"}, "wrong-birth": {"startedAtMs": 2001},
                   "false-handoff": {"handoffWritten": False}, "bool-pid": {"pid": True}}
        receipts = [envelope(target, **changes[case])]
    assert leader.bound_ack(receipts, target, "leader", requested, old, now=now) is None


def test_exact_60second_ack_boundary_and_multiple_new_receipts(workflow):
    target = workflow["target"]
    receipt = envelope(target)
    assert leader.bound_ack([receipt], target, "leader", 99, set(), now=160)
    with pytest.raises(r.RetirementRefused, match="ambiguous"):
        leader.bound_ack([receipt, envelope(target, receipt_id="second")], target, "leader", 99, set(), now=101)


@pytest.mark.parametrize("reply,expected", [
    ({"activePanes": [], "hiddenPanes": []}, None),
    ({"activePanes": [{"type": "terminal"}], "hiddenPanes": []}, None),
    ({"activePanes": [{"type": "browser"}], "hiddenPanes": []}, None),
    ({"activePanes": [{"type": "terminal", "leaves": [{"sessionIds": ["other"]}]}], "hiddenPanes": []}, False),
    ({"activePanes": [], "hiddenPanes": [{"type": "terminal", "leaves": [{"sessionIds": ["seat"]}]}]}, True),
    ({"activePanes": []}, None),
    ({"activePanes": [{"type": "terminal", "leaves": []}], "hiddenPanes": []}, None),
    ({"activePanes": [{"type": "terminal", "leaves": [{"sessionIds": [5]}]}], "hiddenPanes": []}, None),
    ({"activePanes": [{"type": "terminal", "leaves": [{"sessionIds": []}]}], "hiddenPanes": []}, False),
])
def test_context_membership_is_scoped_and_unknown_is_not_absence(reply, expected):
    assert leader.context_has_session(reply, "seat") is expected


@pytest.mark.parametrize("surface", ["activePanes", "hiddenPanes"])
@pytest.mark.parametrize("malformed", [{"type": "terminal"}, {"type": "terminal", "leaves": []},
    {"type": "terminal", "leaves": [{"sessionIds": "other"}]}, None])
def test_one_valid_terminal_does_not_repair_other_returned_metadata(surface, malformed):
    reply = {"activePanes": [{"type": "terminal", "leaves": [{"sessionIds": ["other"]}]}],
             "hiddenPanes": []}
    reply[surface].append(malformed)
    assert leader.context_has_session(reply, "seat") is None


@pytest.mark.parametrize("reply", [
    {"activePanes": [], "hiddenPanes": []},
    {"activePanes": [{"type": "terminal"}], "hiddenPanes": []},
    {"activePanes": [{"type": "browser"}], "hiddenPanes": []},
    {"ok": False, "error": "unavailable"},
])
@pytest.mark.parametrize("force", [False, True])
def test_unavailable_terminal_inventory_is_not_refusal_or_success(workflow, reply, force):
    bridge = workflow["bridge"]
    def unavailable(method, path, body=None):
        if path == "/context":
            return reply
        return bridge(method, path, body)
    result = leader.retire("agent", force, unavailable)
    assert not result["ok"] and result["verification"]["leafGone"] is None
    assert all(result["verification"][key] is True for key in ("ptyGone", "processGone", "rootGone"))
    assert result["message"] == "terminal closed; canvas leaf could not be confirmed (terminal list unavailable)"
    assert result["error"].endswith("no_retry") and len(posts(workflow)) == 1


def test_confirmed_returned_leaf_absence_is_not_global_canvas_claim(workflow):
    result = leader.retire("agent", True, workflow["bridge"])
    assert result["ok"] and result["verification"]["leafGone"] is True
    assert "returned terminal leaves (not global canvas confirmation)" in result["message"]
