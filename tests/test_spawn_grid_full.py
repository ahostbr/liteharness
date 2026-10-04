"""T919 refusal diagnostics; T0258 now falls through full panes, never unknown failures.

the user 2026-09-25: "... tell the agent it must ask for a leaf to be ready in that
terminal ... that same message should tell them to report the spawn error up the
chain to leader > and you".

The bridge is mocked; no terminal, pane or agent is ever created.
"""
import pytest

from liteharness import cli

EXPECTED = (
    "SPAWN REFUSED: grid_full — pane canvas-pane-26 already shows 12/12 terminals, "
    "the most a human can watch. Do not retry. Ask your leader for a leaf to be freed "
    "in this pane or for a new panel, then spawn into it. Report this spawn "
    "error up the chain now: to your leader by inbox, and your leader reports it to the orchestrator."
)


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    """A fake bridge: records every call and answers /canvas/split from `split`."""
    calls: list[tuple[str, str]] = []
    state = {"split": {}}

    def fake(method, path, body=None):
        calls.append((method, path))
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-26", "leafCount": 1}]}
        if path == "/canvas/split":
            return state["split"]
        if path == "/pty/write":
            return {"ok": True}
        return {"ok": True}

    monkeypatch.setattr(cli, "_bridge_request", fake)
    # Never the real ~/.claude.json (item 5 has its own tests).
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "leader-under-test")
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    (tmp_path / "agents").mkdir()
    return calls, state


def _spawn():
    cli.cmd_spawn(split_mode=True, split_pane="canvas-pane-26", cwd=".", prompt="x", name="W")


def test_the_message_is_one_constant_with_three_blanks():
    assert cli.GRID_FULL_MESSAGE.format(paneId="canvas-pane-26", count=12, max=12) == EXPECTED


def test_grid_full_moves_on_but_failed_new_creation_does_not_retry(bridge, capsys):
    calls, state = bridge
    state["split"] = {"ok": False, "error": "grid_full", "max": 8, "count": 8,
                      "paneId": "canvas-pane-26"}
    # This fake /pty/create has no session id: no launch or second creation follows.
    with pytest.raises(SystemExit) as exit_:
        _spawn()
    assert exit_.value.code == 1
    assert "falling back" in capsys.readouterr().out
    assert [p for method, p in calls if method == "POST"] == ["/canvas/split", "/pty/create"]


def test_count_and_max_land_in_their_own_slots_after_the_cap_was_lowered():
    result = {"ok": False, "error": "grid_full", "max": 12, "count": 13, "paneId": "p-1"}
    assert "pane p-1 already shows 13/12 terminals" in cli._split_refusal(result, None)


def test_any_failed_split_is_an_error_never_a_new_pane(bridge, capsys):
    calls, state = bridge
    state["split"] = {"ok": False, "error": "Split failed - no result from store"}
    with pytest.raises(SystemExit) as exit_:
        _spawn()
    assert exit_.value.code == 1
    out = capsys.readouterr().out
    assert "Creation was not retried" in out
    assert "split fallback" not in out
    assert "/canvas/claude" not in [p for _, p in calls]
    assert "/canvas/terminal" not in [p for _, p in calls]


def test_control_a_split_with_room_types_the_launch(bridge, monkeypatch):
    # One under the cap: the bridge hands back a leaf and the agent is launched in it.
    calls, state = bridge
    state["split"] = {"ok": True, "newLeafId": "leaf-9", "newSessionId": "pty-1-2"}
    # Registration never appears; make the 90 s wait end at once.
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    with pytest.raises(SystemExit):  # T916-E: no registration is a failed spawn
        _spawn()
    paths = [p for _, p in calls]
    assert paths[0].startswith("/context?") and paths[1] == "/canvas/split"
    assert "/pty/write" in paths
    assert "/canvas/claude" not in paths


def test_the_bridge_keeps_the_refusal_fields_through_an_http_error(monkeypatch):
    # The bridge answers grid_full with HTTP 409; the fields must survive it.
    import io
    import json
    import urllib.error

    body = {"ok": False, "error": "grid_full", "max": 12, "count": 12, "paneId": "p"}

    def raise_409(*_a, **_k):
        raise urllib.error.HTTPError("u", 409, "Conflict", {}, io.BytesIO(json.dumps(body).encode()))

    monkeypatch.setattr("urllib.request.urlopen", raise_409)
    assert cli._bridge_request("POST", "/canvas/split", {}) == body
