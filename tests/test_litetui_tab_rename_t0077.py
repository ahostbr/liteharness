"""T0077: a LiteTUI seat cannot re-assert its own tab, so the spawner's rename is retried.

Every bridge, floor and presence observation is fake. No shell, daemon, or live seat.
"""
import pytest

from liteharness import cli, fleet_policy

SEAT_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def world(monkeypatch, tmp_path):
    calls = []
    state = {"rename_failures": 0, "rename_exception": None}
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "caller-1")
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    def bridge(method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if path == "/harness/spawn/resolve":
            return {"ok": True, "agentId": SEAT_ID, "request": {
                "shell": "litetui.exe", "args": [], "env": {}, "harnessAgentId": SEAT_ID}}
        if path in ("/pty/create", "/canvas/split"):
            return ({"session_id": "pty-7", "pid": 1} if path == "/pty/create"
                    else {"ok": True, "newLeafId": "leaf-9", "newSessionId": "pty-9"})
        if path == "/canvas/rename-terminal":
            if state["rename_exception"]:
                raise state["rename_exception"]
            if state["rename_failures"] > 0:
                state["rename_failures"] -= 1
                return {"ok": False, "error": "No terminal session 'pty-9'"}
            return {"ok": True}
        raise AssertionError((method, path, body))

    monkeypatch.setattr(fleet_policy, "gate", lambda *a: None)
    monkeypatch.setattr(fleet_policy, "verify_seat", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_bridge_request", bridge)
    return calls, state


def spawn():
    try:
        cli.cmd_spawn(spawn_cli="litetui", name="Seat", tier="reviewer", pty_mode=False,
                      split_mode=True, backend="codex", model="gpt-6-sol", thinking_level="high")
    except SystemExit as exc:
        return exc.code
    return 0


def renames(calls):
    return [c for c in calls if c[1] == "/canvas/rename-terminal"]


def test_a_rename_that_misses_twice_lands_on_the_third_try(world, capsys):
    calls, state = world
    state["rename_failures"] = 2
    assert spawn() == 0
    assert len(renames(calls)) == 3
    assert all(c[2] == {"sessionId": "pty-9", "title": "Seat"} for c in renames(calls))
    assert [c for c in calls if c[0] == "sleep"] == [("sleep", 2.0), ("sleep", 2.0)]
    assert "canvas title not updated" not in capsys.readouterr().err


def test_a_rename_that_never_lands_is_reported_honestly_and_the_spawn_still_succeeds(world, capsys):
    calls, state = world
    state["rename_failures"] = 99
    assert spawn() == 0
    assert len(renames(calls)) == 4
    err = capsys.readouterr().err
    assert "canvas title not updated after retries" in err
    assert "registration will retry" not in err  # it does not, for LiteTUI


def test_a_transport_error_is_a_retry_not_a_crash(world, capsys):
    calls, state = world
    state["rename_exception"] = ConnectionError("bridge restarting")
    assert spawn() == 0
    assert len(renames(calls)) == 4
    assert "ConnectionError: bridge restarting" in capsys.readouterr().err


def test_the_first_success_does_not_sleep(world):
    calls, _ = world
    assert spawn() == 0
    assert len(renames(calls)) == 1 and [c for c in calls if c[0] == "sleep"] == []
