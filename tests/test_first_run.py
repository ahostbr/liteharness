"""FirstRun owner: project cwd, visible session, accepted name, refusal truth."""
import json
from pathlib import Path

import pytest

from liteharness import first_run


def setup_owner(monkeypatch, tmp_path, *, registered=True, view=True, model="unknown"):
    calls = []
    root = tmp_path / "fleet"
    (root / "agents").mkdir(parents=True)
    monkeypatch.setattr(first_run.config, "get_root", lambda: root)
    monkeypatch.setattr(first_run.naming, "is_name_taken", lambda name: None)
    monkeypatch.setattr(first_run.fleet_policy, "gate", lambda *a: None)
    monkeypatch.setattr(first_run.fleet_policy, "verify_seat", lambda *a, **k: None)
    monkeypatch.setattr(first_run, "_rename_canvas_seat", lambda *a: None)
    monkeypatch.setattr(first_run, "_visible_view", lambda sid: {"pane_id": "pane-A", "leaf_id": "leaf-A"} if view else None)
    monkeypatch.setattr(first_run, "_presence", lambda aid: {"name": "Warden", "tier": "orchestrator", "model": model, "cwd": str(tmp_path.resolve())} if registered else {})
    def bridge(method, path, body=None):
        calls.append((method, path, body))
        if path == "/harness/spawn/resolve":
            return {"ok": True, "agentId": "agent-1", "request": {
                "shell": "claude.exe", "args": ["/liteharness:ls-init-liteharness Warden"],
                "env": {}, "cwd": str(tmp_path),
            }}
        if path == "/pty/create":
            return {"session_id": "pty-1"}
        if path == "/pty/pty-1":
            return {"success": True, "reap": "pending"}
        return {"ok": True}
    monkeypatch.setattr(first_run, "_bridge_request", bridge)
    return calls, root


def test_project_process_and_accepted_identity(monkeypatch, tmp_path):
    calls, root = setup_owner(monkeypatch, tmp_path)
    result = first_run.bootstrap("Warden", str(tmp_path), wait=2, sleep=lambda _: None)
    assert result == {"ok": True, "agent_id": "agent-1", "session_id": "pty-1",
                      "pane_id": "pane-A", "name": "Warden", "cwd": str(tmp_path.resolve())}
    resolved = calls[0][2]
    assert resolved == {"cli": "claude", "name": "Warden", "tier": "orchestrator",
                        "cwd": str(tmp_path.resolve()), "prompt": "/liteharness:ls-init-liteharness Warden"}
    created = calls[1][2]
    assert created["cwd"] == str(tmp_path.resolve())
    assert created["args"] == ["/liteharness:ls-init-liteharness Warden"]
    assert created["env"]["LITEHARNESS_AGENT_ID"] == "agent-1"
    assert not created["env"]["LITEHARNESS_SPAWNED_BY"]
    row = json.loads((root / "agents" / "agent-1.json").read_text())
    assert row["canvas_session_id"] == "pty-1" and row["pane_id"] == "pane-A"


def test_no_project_or_taken_name_refuses_before_resolve(monkeypatch, tmp_path):
    calls, _ = setup_owner(monkeypatch, tmp_path)
    assert not first_run.bootstrap("Warden", "relative")["ok"]
    monkeypatch.setattr(first_run.naming, "is_name_taken", lambda _: "agent-other")
    assert not first_run.bootstrap("Warden", str(tmp_path))["ok"]
    assert calls == []


@pytest.mark.parametrize("registered_cwd", [None, "/other/project"])
def test_registered_cwd_must_match_process_cwd(monkeypatch, tmp_path, registered_cwd):
    calls, _ = setup_owner(monkeypatch, tmp_path)
    monkeypatch.setattr(first_run, "_presence", lambda _: {
        "name": "Warden", "tier": "orchestrator", "cwd": registered_cwd,
    })
    result = first_run.bootstrap("Warden", str(tmp_path), wait=2, sleep=lambda _: None)
    assert not result["ok"] and "identity mismatch" in result["error"]
    assert calls[-1][:2] == ("DELETE", "/pty/pty-1")


def test_malformed_resolve_and_cleanup_are_named_refusals(monkeypatch, tmp_path):
    setup_owner(monkeypatch, tmp_path)
    monkeypatch.setattr(first_run, "_bridge_request", lambda *a: None)
    result = first_run.bootstrap("Warden", str(tmp_path))
    assert not result["ok"] and "nothing spawned" in result["error"]

    monkeypatch.setattr(first_run, "_bridge_request", lambda *a: None)
    # A malformed kill response must retain the session identity rather than raising.
    result = first_run._cleanup("pty-1", "verification failed")
    assert not result["ok"] and "FATAL CLEANUP FAILED for pty-1" in result["error"]


def test_agent_parent_not_copied_into_human_seat(monkeypatch, tmp_path):
    calls, _ = setup_owner(monkeypatch, tmp_path)
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "agent-attacker")
    monkeypatch.setenv("LITEHARNESS_SPAWNED_BY", "agent-attacker")
    assert first_run.bootstrap("Warden", str(tmp_path), wait=2, sleep=lambda _: None)["ok"]
    assert not calls[1][2]["env"]["LITEHARNESS_SPAWNED_BY"]
    assert calls[1][2]["env"]["LITEHARNESS_AGENT_ID"] == "agent-1"


def test_missing_visible_leaf_requests_kill_without_claiming_reap(monkeypatch, tmp_path):
    calls, _ = setup_owner(monkeypatch, tmp_path, view=False)
    ticks = iter([0, 0, 2])
    result = first_run.bootstrap("Warden", str(tmp_path), wait=1, sleep=lambda _: None,
                                 clock=lambda: next(ticks))
    assert not result["ok"] and "reap pending" in result["error"]
    assert calls[-1][:2] == ("DELETE", "/pty/pty-1")


def test_only_active_canvas_leaf_counts_as_visible(monkeypatch):
    monkeypatch.setattr(first_run, "_bridge_request", lambda *a: {
        "ok": True, "activePanes": [],
        "hiddenPanes": [{"id": "hidden", "leaves": [{"leafId": "leaf", "sessionIds": ["pty-1"]}]}],
    })
    assert first_run._visible_view("pty-1") is None


def test_resolver_must_keep_exact_init_prompt_without_forced_model(monkeypatch, tmp_path):
    calls, _ = setup_owner(monkeypatch, tmp_path)
    original = first_run._bridge_request
    def changed(method, path, body=None):
        response = original(method, path, body)
        if path == "/harness/spawn/resolve":
            response["request"]["args"] = ["--model", "custom", "wrong prompt"]
        return response
    monkeypatch.setattr(first_run, "_bridge_request", changed)
    result = first_run.bootstrap("Warden", str(tmp_path))
    assert not result["ok"] and "prompt/model" in result["error"]
    assert len(calls) == 1


def test_bridge_view_failure_requests_kill(monkeypatch, tmp_path):
    calls, _ = setup_owner(monkeypatch, tmp_path)
    monkeypatch.setattr(first_run, "_visible_view", lambda _: (_ for _ in ()).throw(RuntimeError("bridge unavailable")))
    result = first_run.bootstrap("Warden", str(tmp_path), wait=2, sleep=lambda _: None)
    assert not result["ok"] and "bridge unavailable" in result["error"]
    assert calls[-1][:2] == ("DELETE", "/pty/pty-1")


def test_rename_failure_preserves_registered_session_for_repair(monkeypatch, tmp_path):
    calls, _ = setup_owner(monkeypatch, tmp_path)
    monkeypatch.setattr(first_run, "_rename_canvas_seat", lambda *a: (_ for _ in ()).throw(RuntimeError("rename failed")))
    result = first_run.bootstrap("Warden", str(tmp_path), wait=2, sleep=lambda _: None)
    assert not result["ok"] and result["session_id"] == "pty-1" and "retained" in result["error"]
    assert all(method != "DELETE" for method, _, _ in calls)


def test_reported_governed_model_failure_requests_kill(monkeypatch, tmp_path):
    calls, _ = setup_owner(monkeypatch, tmp_path, model="gpt-5.6-sol")
    monkeypatch.setattr(first_run.fleet_policy, "verify_seat", lambda *a, **k: "SEAT FAILED FLOOR")
    result = first_run.bootstrap("Warden", str(tmp_path), wait=2, sleep=lambda _: None)
    assert not result["ok"] and "SEAT FAILED FLOOR" in result["error"]
    assert calls[-1][:2] == ("DELETE", "/pty/pty-1")
