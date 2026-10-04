"""T0258: real spawn entry points, fake bridge/presence only; never a live launch."""
import json

import pytest

from liteharness import cli, fleet_policy


def pane(ident, count=1, session=None):
    return {"id": ident, "type": "terminal", "hasTerminal": count > 0,
            "leafCount": count, "leaves": [
                {"leafId": f"{ident}-{i}", "sessionIds": [session or f"{ident}-pty-{i}"]}
                for i in range(count)]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    state = {"panes": [pane("other"), pane("fleet", session="parent-pty"),
                       pane("own", session="caller-pty")], "refusals": {}, "calls": []}
    for ident, fields in {
        "caller": {"pane_id": "own", "canvas_session_id": "caller-pty", "spawned_by": "parent"},
        "parent": {"pane_id": "fleet", "canvas_session_id": "parent-pty"},
    }.items():
        (agents / f"{ident}.json").write_text(json.dumps({"agent_id": ident, **fields}))
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    monkeypatch.setattr(cli.config, "get_agent_id", lambda: "caller")
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "caller")
    for key in ("LITESUITE_PANE_ID", "LITESUITE_CANVAS_SESSION", "LITEHARNESS_SPAWNED_BY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_record_fresh_name", lambda *a, **kw: None)
    monkeypatch.setattr(fleet_policy, "gate", lambda *a: None)
    monkeypatch.setattr(fleet_policy, "verify_seat", lambda *a, **k: None)
    monkeypatch.setattr(cli.inbox, "send", lambda **k: "fake-mail")

    def bridge(method, path, body=None):
        state["calls"].append((method, path, body))
        if path.startswith("/context"):
            if callable(state.get("context")):
                return state["context"]()
            return state.get("context", {"activePanes": state["panes"],
                                         "hiddenPanes": [pane("hidden")]})
        if path == "/harness/spawn/resolve":
            return {"ok": True, "agentId": "child", "request": {
                "shell": "litetui.exe", "args": ["--model", "fake"], "env": {},
                "cwd": str(tmp_path), "harnessAgentId": "child"}}
        if path == "/canvas/split":
            ident = body["paneId"]
            if ident in state["refusals"]:
                refusal = state["refusals"][ident]
                if isinstance(refusal, Exception):
                    raise refusal
                return refusal
            target = next((p for p in state["panes"] if p["id"] == ident), None)
            if target is None or target["leafCount"] == 0:
                return {"ok": False, "error": "no_terminals"}
            if target["leafCount"] >= 8:
                return {"ok": False, "error": "grid_full", "count": 8, "max": 8}
            return {"ok": True, "newSessionId": "new-pty", "newLeafId": "new-leaf"}
        if path == "/pty/create":
            if "new_result" in state:
                return state["new_result"]
            state["panes"].append(pane("new-pane", session="new-pty"))
            return {"session_id": "new-pty", "pid": 123}
        if path == "/pty/write":
            # Simulate the hook registering the exact session that was launched.
            (agents / "child.json").write_text(json.dumps({
                "agent_id": "child", "canvas_session_id": "new-pty", "pane_id": "stale"}))
            return {"ok": True}
        if path == "/canvas/rename-terminal":
            return {"ok": True}
        if path in ("/pty/list", "/session/list"):
            return {"sessions": []}
        raise AssertionError((method, path, body))

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    state["root"] = tmp_path
    return state


def spawn(world, **kw):
    cli.cmd_spawn(split_mode=True, cwd=str(world["root"]), **kw)


def placements(world):
    return [(path, body) for _, path, body in world["calls"]
            if path in ("/canvas/split", "/pty/create")]


def test_own_pane_first_even_when_other_panes_come_first(world):
    spawn(world)
    assert placements(world) == [("/canvas/split", {"paneId": "own", "agentId": "caller",
                                                  "cwd": str(world["root"])})]


@pytest.mark.parametrize("count", [8, 0])
def test_full_or_nonterminal_own_pane_uses_fleet_before_other(world, count):
    world["panes"][-1] = pane("own", count)
    spawn(world)
    assert placements(world)[0][1]["paneId"] == "fleet"
    assert len(placements(world)) == 1


def test_other_current_pane_before_new_and_hidden_pane_never_used(world):
    world["panes"] = [pane("other"), pane("own", 8), pane("fleet", 8)]
    spawn(world)
    assert placements(world)[0][1]["paneId"] == "other"


@pytest.mark.parametrize("backend", ["claude", "litetui"])
def test_no_room_opens_one_terminal_only(world, backend):
    world["panes"] = [pane("own", 8), pane("fleet", 8)]
    options = {"spawn_cli": "litetui", "name": "NewSeat", "tier": "worker"} if backend == "litetui" else {}
    spawn(world, **options)
    assert [path for path, _ in placements(world)] == ["/pty/create"]
    body = placements(world)[0][1]
    assert body["cwd"] == str(world["root"])
    if backend == "litetui":
        assert body["shell"] == "litetui.exe" and body["harnessAgentId"] == "child"


def test_explicit_pane_with_room_wins_and_direction_is_preserved(world):
    spawn(world, split_pane="other", split_direction="horizontal")
    body = placements(world)[0][1]
    assert body["paneId"] == "other" and body["direction"] == "horizontal"


@pytest.mark.parametrize("explicit,count", [("old-merged", None), ("other", 8), ("other", 0)])
def test_unusable_explicit_pane_falls_back_and_says_so(world, capsys, explicit, count):
    if count is not None:
        world["panes"][0] = pane("other", count)
    spawn(world, split_pane=explicit)
    assert placements(world)[0][1]["paneId"] == "own"
    out = capsys.readouterr().out
    assert explicit in out and "falling back" in out.lower()


def test_live_session_beats_stale_pane_and_actual_pane_reaches_child(world):
    world["panes"][-1]["id"] = "merged-destination"
    spawn(world, split_pane="old-merged")
    assert placements(world)[0][1]["paneId"] == "merged-destination"
    typed = next(body["data"] for _, path, body in world["calls"] if path == "/pty/write")
    assert "$env:LITESUITE_PANE_ID='merged-destination'" in typed
    child = json.loads((world["root"] / "agents/child.json").read_text())
    assert child["pane_id"] == "merged-destination"


@pytest.mark.parametrize("error", ["grid_full", "no_terminals"])
def test_precreation_refusal_tries_next_pane(world, error):
    world["refusals"]["own"] = {"ok": False, "error": error}
    spawn(world)
    assert [body["paneId"] for _, body in placements(world)] == ["own", "fleet"]


@pytest.mark.parametrize("error", ["launch_state_unknown", "partial_split_cleanup_failed", "transport_error"])
def test_uncertain_creation_never_retries(world, error):
    world["refusals"]["own"] = {"ok": False, "error": error}
    with pytest.raises(SystemExit):
        spawn(world)
    assert len(placements(world)) == 1


def test_context_failure_cannot_be_treated_as_empty_canvas(world):
    world["context"] = {"error": "renderer unavailable"}
    with pytest.raises(SystemExit):
        spawn(world)
    assert placements(world) == []


def test_every_candidate_refuses_grid_full_then_only_new_pane_launches(world):
    for target in world["panes"]:
        world["refusals"][target["id"]] = {"error": "grid_full", "count": 8, "max": 8}
    spawn(world)
    assert [body["paneId"] for path, body in placements(world) if path == "/canvas/split"] == ["own", "fleet", "other"]
    assert [path for path, _ in placements(world)].count("/pty/create") == 1
    writes = [body for _, path, body in world["calls"] if path == "/pty/write"]
    assert len(writes) == 1 and writes[0]["session_id"] == "new-pty"
    assert "$env:LITESUITE_PANE_ID='new-pane'" in writes[0]["data"]


@pytest.mark.parametrize("requested", [None, "self", "self:caller"])
def test_codex_without_pane_uses_exact_discovered_pane(world, monkeypatch, requested):
    monkeypatch.setattr(cli, "_codex_cwd_refusal", lambda _: None)
    monkeypatch.setattr(cli, "_codex_session_process", lambda _: {"pid": 123})
    def verified(*args, **kwargs):
        kwargs["result"].update(sid="codex-session", model="model", effort="high", turn_id="turn")
        return None
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verified)
    spawn(world, spawn_cli="codex", model="model", thinking_level="high", split_pane=requested)
    assert placements(world)[0][1]["paneId"] == "own"
    assert any(path == "/pty/write" for _, path, _ in world["calls"])


def test_resume_stale_no_terminals_pane_lands_in_fleet(world, monkeypatch):
    from liteharness import resume_seat
    agent, convo = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    monkeypatch.setattr(resume_seat, "resolve_target", lambda *a: (agent, convo, None))
    monkeypatch.setattr(resume_seat, "lookup", lambda *a: (convo, {}, {
        "agent_id": agent, "name": "Old", "tier": "worker", "model": "model",
        "backend": "codex", "thinking_level": "high", "spawned_by": "caller"}))
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: None)
    world["panes"][-1] = pane("own", 0)
    world["refusals"]["other"] = {"error": "no_terminals", "ok": False}
    spawn(world, resume_agent_id=agent, split_pane="other")
    assert [body["paneId"] for _, body in placements(world)] == ["other", "fleet"]
    launch = placements(world)[-1][1]["launch"]
    assert launch["harnessAgentId"] == agent and launch["args"][-2:] == ["--convo", convo]


@pytest.mark.parametrize("parent_count,expected", [(1, "fleet"), (8, "sibling-pane")])
def test_parent_then_sibling_before_unrelated_pane(world, parent_count, expected):
    world["panes"] = [pane("other"), pane("sibling-pane", session="sibling-pty"),
                      pane("fleet", parent_count, "parent-pty"), pane("own", 8)]
    (world["root"] / "agents/sibling.json").write_text(json.dumps({
        "spawned_by": "parent", "canvas_session_id": "sibling-pty", "pane_id": "old-sibling"}))
    spawn(world)
    assert placements(world)[0][1]["paneId"] == expected


def test_refresh_discovers_merge_destination_before_new_pane(world):
    world["panes"] = [pane("own")]
    world["refusals"]["own"] = {"error": "no_terminals"}
    def context():
        if placements(world):
            world["panes"] = [pane("moved", session="caller-pty")]
        return {"activePanes": world["panes"]}
    world["context"] = context
    spawn(world)
    assert [body["paneId"] for _, body in placements(world)] == ["own", "moved"]


@pytest.mark.parametrize("result", [None, "bad", {"error": "grid_full", "newLeafId": "maybe"},
                                     {"error": "no_terminals", "ok": True},
                                     {"error": "grid_full", "session_id": "maybe"}])
def test_malformed_or_contradictory_split_never_retries(world, result):
    world["refusals"]["own"] = result
    with pytest.raises(SystemExit):
        spawn(world)
    assert len(placements(world)) == 1


def test_creation_transport_exception_does_not_retry(world):
    world["refusals"]["own"] = RuntimeError("connection lost after creation")
    with pytest.raises(RuntimeError):
        spawn(world)
    assert len(placements(world)) == 1


@pytest.mark.parametrize("result", [{"newSessionId": "unvalidated"}, {}, None, "bad"])
def test_malformed_new_terminal_response_cannot_type_or_create_again(world, result):
    world["panes"] = []
    world["new_result"] = result
    with pytest.raises(SystemExit):
        spawn(world)
    assert [path for path, _ in placements(world)] == ["/pty/create"]
    assert not any(path == "/pty/write" for _, path, _ in world["calls"])


@pytest.mark.parametrize("mount", ["delayed", "timeout", "malformed", "error"])
def test_new_codex_shell_waits_for_exact_session_mount(world, monkeypatch, mount):
    world["panes"] = []
    world["new_result"] = {"session_id": "new-pty"}
    polls = []
    def context():
        if not placements(world):
            return {"activePanes": []}
        polls.append(True)
        assert not any(path == "/pty/write" for _, path, _ in world["calls"])
        if mount == "delayed" and len(polls) >= 3:
            return {"activePanes": [pane("exact-new", session="new-pty")]}
        if mount == "malformed":
            return {"activePanes": [{"id": "bad", "leaves": None}]}
        if mount == "error":
            return {"error": "bridge unavailable"}
        return {"activePanes": [pane("unrelated-new", session="someone-else")]}
    world["context"] = context
    monkeypatch.setattr(cli, "_codex_cwd_refusal", lambda _: None)
    monkeypatch.setattr(cli, "_codex_session_process", lambda _: object())
    def verify(*args, **kwargs):
        kwargs["result"].update(sid="codex", model="model", effort="high", turn_id="turn")
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verify)
    if mount == "delayed":
        spawn(world, spawn_cli="codex", model="model", thinking_level="high")
        assert len(polls) == 3
    else:
        with pytest.raises(SystemExit):
            spawn(world, spawn_cli="codex", model="model", thinking_level="high")
        assert not any(path == "/pty/write" for _, path, _ in world["calls"])
    assert [path for path, _ in placements(world)] == ["/pty/create"]


@pytest.mark.parametrize("mount_after", [25, None])
def test_litetui_mount_gets_ten_second_budget_and_timeout_reports_running_seat(
        world, monkeypatch, capsys, mount_after):
    world["panes"] = []
    world["new_result"] = {"session_id": "new-pty"}
    polls, delays, verified = [], [], []
    def context():
        if not placements(world):
            return {"activePanes": []}
        polls.append(True)
        return {"activePanes": ([pane("mounted", session="new-pty")]
                                if mount_after and len(polls) >= mount_after else [])}
    world["context"] = context
    monkeypatch.setattr(cli.time, "sleep", delays.append)
    monkeypatch.setattr(fleet_policy, "verify_seat", lambda *a, **k: verified.append(True))
    if mount_after:
        spawn(world, spawn_cli="litetui", name="NewSeat", tier="worker")
        assert len(polls) == mount_after and verified == [True]
    else:
        with pytest.raises(SystemExit) as exc:
            spawn(world, spawn_cli="litetui", name="NewSeat", tier="worker")
        assert exc.value.code == 2 and verified == []
        assert sum(delays) == pytest.approx(10)
        out = capsys.readouterr().out
        assert "the seat may be running" in out and "new-pty" in out
    assert [path for path, _ in placements(world)] == ["/pty/create"]
    assert not any(method == "DELETE" or path == "/pty/write" for method, path, _ in world["calls"])


@pytest.mark.parametrize("bad_pane", [None, "self", "self:caller"])
def test_codex_launcher_guard_still_rejects_unresolved_placement(world, monkeypatch, bad_pane):
    monkeypatch.setattr(cli, "_codex_cwd_refusal", lambda _: None)
    monkeypatch.setattr(cli, "_place_split", lambda *a, **k: {"newSessionId": "shell", "paneId": bad_pane})
    with pytest.raises(SystemExit) as exc:
        spawn(world, spawn_cli="codex", model="model", thinking_level="high")
    assert exc.value.code == 2
    assert not any(path == "/pty/write" for _, path, _ in world["calls"])
