"""Injected fixtures only: no live process calls, PTYs, bridge or inbox effects."""
from dataclasses import replace
import json

import pytest

from liteharness import config, retirement as r


@pytest.fixture
def seat():
    processes = {10: r.ProcessIdentity(10, 1, 1000),
                 20: r.ProcessIdentity(20, 10, 2000),
                 30: r.ProcessIdentity(30, 20, 3000)}
    registry = {"agent": {"agent_id": "agent", "name": "Agent", "cli": "claude-code",
                          "session_pid": 20, "session_process_started_at": 2000,
                          "spawned_by": "leader", "cwd": "C:/own"}}
    sessions = [{"id": "seat", "pid": 10, "createdAt": 5000, "harnessAgentId": "agent"}]
    calls = []

    def measure(pid):
        calls.append(pid)
        try:
            return processes[pid]
        except KeyError:
            raise r.RetirementRefused("process_measurement_unconfirmed")
    return registry, sessions, processes, calls, measure


@pytest.mark.parametrize("backend", ["claude-code", "litetui"])
def test_caller_binds_real_provider_ancestor_not_env_or_js_birth(seat, backend):
    registry, sessions, _, calls, measure = seat
    registry["agent"]["cli"] = backend
    identity = r.bind_caller(registry, sessions, measure, caller_pid=30, explicit_id="agent")
    assert (identity.agent_id, identity.pid, identity.started_at_ms) == ("agent", 20, 2000)
    assert identity.root == r.ProcessIdentity(10, 1, 1000)
    assert identity.session_created_at == 5000
    assert 1 not in calls  # never inspect unrelated Electron/system ancestors


def test_claiming_other_agent_cannot_mint_caller_authority(seat):
    registry, sessions, _, _, measure = seat
    with pytest.raises(r.RetirementRefused, match="environment_identity_mismatch"):
        r.bind_caller(registry, sessions, measure, caller_pid=30, explicit_id="other")


def test_changed_registered_birth_refuses_even_with_same_pid_uuid(seat):
    registry, sessions, processes, _, measure = seat
    processes[20] = replace(processes[20], started_at_ms=2100)
    with pytest.raises(r.RetirementRefused, match="registered_process_birth_changed"):
        r.bind_caller(registry, sessions, measure, caller_pid=30)


def test_shared_registry_owner_is_ambiguous(seat):
    registry, sessions, _, _, measure = seat
    registry["other"] = {**registry["agent"], "agent_id": "other"}
    with pytest.raises(r.RetirementRefused, match="caller_identity_missing_or_ambiguous"):
        r.bind_caller(registry, sessions, measure, caller_pid=30)


@pytest.mark.parametrize("mutation, reason", [
    ("second-agent-seat", "pty_association_missing_or_ambiguous"),
    ("shared-root", "pty_generation_shared_or_ambiguous"),
    ("wrong-root", "agent_not_under_pty_root"),
    ("malformed-list", "pty_list_unconfirmed"),
    ("reused-parent", "process_parent_reused"),
    ("cycle", "process_ancestry_cycle"),
])
def test_membership_boundaries_fail_closed(seat, mutation, reason):
    registry, sessions, processes, _, measure = seat
    if mutation == "second-agent-seat":
        sessions.append({**sessions[0], "id": "second", "pid": 40})
    elif mutation == "shared-root":
        sessions.append({**sessions[0], "id": "second", "harnessAgentId": "other"})
    elif mutation == "wrong-root":
        sessions[0]["pid"] = 40
        processes[10] = replace(processes[10], parent_pid=0)
    elif mutation == "malformed-list":
        sessions.append(None)
    elif mutation == "reused-parent":
        processes[10] = replace(processes[10], started_at_ms=2500)
    elif mutation == "cycle":
        processes[20] = replace(processes[20], parent_pid=20)
    with pytest.raises(r.RetirementRefused, match=reason):
        r.bind_caller(registry, sessions, measure, caller_pid=30)


@pytest.mark.parametrize("field, value", [("session_pid", True),
    ("session_process_started_at", float("nan")), ("session_process_started_at", 0),
    ("session_process_started_at", True), ("name", ""), ("spawned_by", "../leader")])
def test_malformed_registry_identity_cannot_be_evidence(seat, field, value):
    registry, sessions, _, _, measure = seat
    registry["agent"][field] = value
    with pytest.raises(r.RetirementRefused):
        r.bind_seat("agent", registry, sessions, measure)


def test_read_registry_does_not_create_or_guess_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "get_agent_id", lambda: pytest.fail("identity guess forbidden"))
    with pytest.raises(r.RetirementRefused, match="registry_unavailable"):
        r.read_registry()
    assert not list(tmp_path.iterdir())
    (tmp_path / "agents").mkdir()
    path = tmp_path / "agents" / "agent.json"
    path.write_text(json.dumps({"agent_id": "other"}), encoding="utf-8")
    with pytest.raises(r.RetirementRefused, match="registry_identity_ambiguous"):
        r.read_registry()


@pytest.mark.parametrize("mutation", ["registry", "root", "session"])
def test_frozen_identity_revalidation_detects_mutation(seat, mutation):
    registry, sessions, processes, _, measure = seat
    before = r.bind_caller(registry, sessions, measure, caller_pid=30)
    if mutation == "registry":
        registry["agent"]["cwd"] = "changed"
    elif mutation == "root":
        processes[10] = replace(processes[10], started_at_ms=1001)
    else:
        sessions[0]["createdAt"] = 5001
    after = r.bind_caller(registry, sessions, measure, caller_pid=30)
    with pytest.raises(r.RetirementRefused, match="identity_changed_during_retirement"):
        r.require_same(before, after)
