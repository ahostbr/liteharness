"""T1150: the LiteHarness owner, not the retired Suite spawn adapter, owns floor/seat cleanup.

Every bridge, floor and presence observation is fake. No shell, daemon, or live seat.
"""
import json

import pytest

from liteharness import cli, fleet_policy


@pytest.fixture
def world(monkeypatch, tmp_path):
    calls = []
    state = {"pre": None, "verify": None, "create": None, "delete": {"success": True}}
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "caller-1")
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    monkeypatch.setenv('LITETUI_DATA_ROOT', str(tmp_path))

    def gate(backend, model, thinking):
        calls.append(("gate", backend, model, thinking))
        return state["pre"]

    def verify(agent_id, root, **kw):
        calls.append(("verify", agent_id, root, kw))
        if isinstance(state["verify"], Exception):
            raise state["verify"]
        return state["verify"]

    def bridge(method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if path == "/harness/spawn/resolve":
            return {"ok": True, "agentId": "11111111-1111-4111-8111-111111111111", "request": {
                "shell": "litetui.exe", "args": [], "env": {}, "harnessAgentId": "11111111-1111-4111-8111-111111111111"}}
        if path in ("/pty/create", "/canvas/split"):
            if isinstance(state["create"], Exception):
                raise state["create"]
            return state["create"] if state["create"] is not None else ({"session_id": "pty-7", "pid": 1} if path == "/pty/create"
                                        else {"ok": True, "newLeafId": "leaf-9", "newSessionId": "pty-9"})
        if path == "/canvas/rename-terminal":
            return {"ok": True}
        if method == "DELETE":
            if isinstance(state["delete"], Exception):
                raise state["delete"]
            return state["delete"]
        raise AssertionError((method, path, body))

    monkeypatch.setattr(fleet_policy, "gate", gate)
    monkeypatch.setattr(fleet_policy, "verify_seat", verify)
    monkeypatch.setattr(cli, "_bridge_request", bridge)
    return calls, state, tmp_path


def spawn(**kw):
    try:
        cli.cmd_spawn(spawn_cli="litetui", name="Seat", tier="reviewer",
                      pty_mode=not kw.pop("split", False), split_mode=kw.pop("split_mode", False),
                      backend=kw.pop("backend", "codex"), model=kw.pop("model", "gpt-6-sol"),
                      thinking_level=kw.pop("thinking_level", "high"), **kw)
    except SystemExit as exc:
        return exc.code
    return 0


def ops(calls, name):
    return [item for item in calls if item[0] == name]


def test_pre_floor_refusal_does_not_resolve_or_create(world, capsys):
    calls, state, _ = world
    state["pre"] = "FLEET FLOOR: model below gpt-6-sol"
    assert spawn(model="gpt-5.6-sol", thinking_level="medium") == 2
    assert calls == [("gate", "codex", "gpt-5.6-sol", "medium")]
    assert "model below" in capsys.readouterr().out


@pytest.mark.parametrize("split,session", [(False, "pty-7"), (True, "pty-9")])
@pytest.mark.parametrize("failure", ["SEAT FAILED FLOOR: resolved gpt-5.6-sol/medium",
                                      "SEAT FAILED MISMATCH: requested gpt-6-sol",
                                      RuntimeError("presence unreadable")])
def test_mismatch_or_verification_exception_deletes_only_new_session(world, capsys, split, session, failure):
    calls, state, root = world
    state["verify"] = failure
    assert spawn(split=split, split_mode=split) == 2
    assert ops(calls, "DELETE") == [("DELETE", f"/pty/{session}", None)]
    assert len(ops(calls, "verify")) == 1
    _, agent, verified_root, kw = ops(calls, "verify")[0]
    assert agent == "11111111-1111-4111-8111-111111111111" and verified_root == root
    assert kw["wait"] == 90 and kw["backend"] == "codex"
    assert kw["expect_model"] == "gpt-6-sol" and kw["expect_thinking"] == "high"
    assert f"kill requested for session {session}; process reap pending" in capsys.readouterr().out


@pytest.mark.parametrize("split,session", [(False, "pty-7"), (True, "pty-9")])
def test_verified_seat_kept_and_reported(world, capsys, split, session):
    calls, _, _ = world
    assert spawn(split=split, split_mode=split) == 0
    assert ops(calls, "DELETE") == []
    assert json.loads(capsys.readouterr().out)["agent_id"] == "11111111-1111-4111-8111-111111111111"
    assert len(ops(calls, "verify")) == 1


def test_ungoverned_request_allows_silent_but_checks_model(world):
    calls, _, _ = world
    assert spawn(backend="local", model="qwen-x", thinking_level='off') == 0
    kw = ops(calls, "verify")[0][3]
    assert kw["allow_silent"] is True and kw["expect_model"] == "qwen-x"


@pytest.mark.parametrize("model", [None, "local-auto"])
def test_implicit_codex_preflight_and_no_implicit_verify_backend(world, model):
    calls, _, _ = world
    assert spawn(backend=None, model=model, thinking_level=None) == 2
    assert calls[0] == ("gate", "codex", None, None)
    assert ops(calls, "verify") == []  # fresh owned homes require settled execution


@pytest.mark.parametrize("model", ["qwen-x", "gpt-oss-120b"])
@pytest.mark.parametrize("split", [False, True])
def test_ungoverned_request_resolved_to_codex_pin_fails_and_deletes_exact_session(world, split, model):
    calls, state, _ = world
    state["verify"] = "SEAT FAILED FLOOR: seat reported codex gpt-5.6-sol/medium"
    assert spawn(split=split, split_mode=split, backend='local', model=model, thinking_level='off') == 2
    expected = "pty-9" if split else "pty-7"
    assert ops(calls, "DELETE") == [("DELETE", f"/pty/{expected}", None)]
    verify = ops(calls, "verify")[0][3]
    assert verify["allow_silent"] is True and verify["expect_model"] == model


def test_grid_full_never_verifies_or_deletes_another_seat(world, capsys):
    calls, state, _ = world
    state["create"] = {"ok": False, "error": "grid_full", "count": 6, "max": 6}
    assert spawn(split=True, split_mode=True) == 2
    assert ops(calls, "verify") == [] and ops(calls, "DELETE") == []
    assert "grid_full" in capsys.readouterr().out


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("creation", [
    {"ok": False, "error": "bridge_failed"},
    {"ok": False, "error": "transport_error"},
    "not-a-response", RuntimeError("socket dropped after commit"),
])
def test_uncertain_creation_names_orphans_without_deleting_or_verifying(world, capsys, split, creation):
    calls, state, _ = world
    state["create"] = creation
    assert spawn(split=split, split_mode=split) == 2
    assert ops(calls, "verify") == [] and ops(calls, "DELETE") == []
    out = capsys.readouterr().out
    assert "launch may have created a session; check GET /pty/orphans" in out
    assert "killed session" not in out


@pytest.mark.parametrize("cleanup", [{"ok": False, "error": "kill failed"},
                                     {"error": "not found"}, {}, RuntimeError("bridge disconnected")])
def test_cleanup_failure_is_fatal_not_reported_as_killed(world, capsys, cleanup):
    calls, state, _ = world
    state["verify"] = "SEAT FAILED FLOOR"
    state["delete"] = cleanup
    assert spawn() == 2
    assert ops(calls, "DELETE") == [("DELETE", "/pty/pty-7", None)]
    out = capsys.readouterr().out
    assert "FATAL CLEANUP FAILED" in out and "killed session" not in out


def test_missing_session_id_never_claims_success(world, capsys):
    calls, state, _ = world
    state["create"] = {"ok": True, "newLeafId": "leaf-9"}
    assert spawn(split=True, split_mode=True) == 2
    assert ops(calls, "verify") == []
    out = capsys.readouterr().out
    assert "no session id" in out and "check GET /pty/orphans" in out
    assert ops(calls, "DELETE") == []
