"""T0236-T1/T2: `--resume <Name>`, the live-seat refusal (rule 25), fresh-spawn name guard, `names --list`."""
import json
import sys
from types import ModuleType
from datetime import datetime, timezone

import pytest

from liteharness import agent_names, cli, config, fleet_policy, resume_seat

AID = "11111111-1111-4111-8111-111111111111"
CID = "22222222-2222-4222-8222-222222222222"
NAME = "Mason-Next"


@pytest.fixture
def world(tmp_path, monkeypatch):
    data = tmp_path / "data"
    convo = data / ".convos" / CID
    convo.mkdir(parents=True)
    (convo / "convo.jsonl").write_text('{"type":"meta"}\n')
    (convo / "settings.json").write_text(json.dumps({
        "seat_id": AID, "seat_name": NAME, "backend": "codex", "model": "gpt-6-sol",
        "thinking_level": "high", "execution": {"backend": "codex", "thinking_level": "high"}}))
    root = tmp_path / "harness"
    (root / "agents").mkdir(parents=True)
    project = tmp_path / "GameProject"
    project.mkdir()
    # An OFFLINE record: what the fixed sweep leaves behind for a named agent.
    (root / "agents" / f"{AID}.json").write_text(json.dumps({
        "agent_id": AID, "name": "Mason", "tier": "worker", "model": "gpt-6-sol",
        "backend": "codex", "thinking_level": "high", "spawned_by": "leader-1", "session_pid": 4242,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "exited_at": datetime.now(timezone.utc).isoformat(), "status": "offline"}))
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(data))
    monkeypatch.setattr(config, "get_root", lambda: root)
    monkeypatch.setattr(fleet_policy, "gate", lambda backend, model, thinking: None)
    assert tmp_path in agent_names.index_path().parents, "names.json would hit the real ~/.liteharness"
    agent_names.record_name(NAME, AID, CID, str(project), backend="codex", model="gpt-6-sol")
    return root, project


def _bridge(calls):
    def bridge(method, path, body=None, **_kw):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        calls.append((method, path, body))
        if path == "/pty/list":
            return {"sessions": []}
        if path == "/session/list":
            return {"sessions": []}
        if path == "/harness/spawn/resolve":
            return {"ok": True, "request": {"args": ["--system-prompt-file", "w.md"], "env": {}}}
        return {"newSessionId": "pty-new"}
    return bridge


def test_legacy_archive_refused_resume_by_name_defaults_cwd_and_convo_from_the_index(world, monkeypatch):
    root, project = world
    # Registry/index pointers do not authorize a mutable legacy LiteTUI thread.
    before = (root / 'names.json').read_bytes()
    calls = []
    monkeypatch.setattr(cli, '_bridge_request', lambda *a, **kw: calls.append(a) or {})
    with pytest.raises(SystemExit):
        cli.cmd_spawn(split_mode=True, resume_agent_id=NAME.lower(), cwd=str(project), tier='worker')
    assert calls == []
    assert (root / 'names.json').read_bytes() == before


def test_legacy_archive_refused_explicit_cwd_beats_the_index_cwd(world, monkeypatch):
    root, project = world
    # Registry/index pointers do not authorize a mutable legacy LiteTUI thread.
    before = (root / 'names.json').read_bytes()
    calls = []
    monkeypatch.setattr(cli, '_bridge_request', lambda *a, **kw: calls.append(a) or {})
    with pytest.raises(SystemExit):
        cli.cmd_spawn(split_mode=True, resume_agent_id=NAME, cwd=str(project), tier='worker')
    assert calls == []
    assert (root / 'names.json').read_bytes() == before


def test_unknown_name_exits_nonzero_naming_the_name(world, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_bridge_request", lambda *a, **k: calls.append(a) or {})
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(split_mode=True, resume_agent_id="Nobody-Here")
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "no agent named Nobody-Here" in out and "liteharness names --list" in out
    assert calls == []


def test_legacy_archive_refused_a_live_named_seat_is_refused_with_an_inbox_hint_and_never_closed(world, monkeypatch):
    root, project = world
    # Registry/index pointers do not authorize a mutable legacy LiteTUI thread.
    before = (root / 'names.json').read_bytes()
    calls = []
    monkeypatch.setattr(cli, '_bridge_request', lambda *a, **kw: calls.append(a) or {})
    with pytest.raises(SystemExit):
        cli.cmd_spawn(split_mode=True, resume_agent_id=NAME, cwd=str(project), tier='worker')
    assert calls == []
    assert (root / 'names.json').read_bytes() == before


def test_kill_old_is_not_available_to_a_named_resume(world, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_bridge_request", _bridge(calls))
    with pytest.raises(SystemExit):
        cli.cmd_spawn(split_mode=True, resume_agent_id=NAME, kill_old=True)
    assert calls == []
    assert "never closes a seat" in capsys.readouterr().out


def test_legacy_archive_refused_raw_agent_id_resume_still_works_and_records_the_name(world, monkeypatch):
    root, project = world
    # Registry/index pointers do not authorize a mutable legacy LiteTUI thread.
    before = (root / 'names.json').read_bytes()
    calls = []
    monkeypatch.setattr(cli, '_bridge_request', lambda *a, **kw: calls.append(a) or {})
    with pytest.raises(SystemExit):
        cli.cmd_spawn(split_mode=True, resume_agent_id=AID, cwd=str(project), tier='worker')
    assert calls == []
    assert (root / 'names.json').read_bytes() == before


def test_a_registry_id_is_an_id_and_anything_else_is_a_name(world):
    assert resume_seat.resolve_target(AID, None) == (AID, None, None)
    agent_id, convo_id, entry = resume_seat.resolve_target(NAME, None)
    assert (agent_id, convo_id, entry["name"]) == (AID, CID, NAME)
    assert resume_seat.resolve_target(NAME, "explicit")[1] == "explicit"


# -- fresh LiteTUI spawn ---------------------------------------------------------

@pytest.fixture
def fresh(world, monkeypatch):
    root, project = world
    calls = []

    def bridge(method, path, body=None, **_kw):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        calls.append((method, path, body))
        if path == "/harness/spawn/resolve":
            return {"ok": True, "agentId": "33333333-3333-4333-8333-333333333333", "request": {
                "shell": "litetui.exe", "args": [], "env": {}, "harnessAgentId": "33333333-3333-4333-8333-333333333333"}}
        return {"ok": True, "newSessionId": "pty-9"}

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    monkeypatch.setattr(fleet_policy, "verify_seat", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_rename_canvas_seat", lambda *a, **k: None)
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "leader-1")
    return calls, project


def _fresh(project, **kw):
    base = dict(spawn_cli="litetui", split_mode=True, tier="worker", cwd=str(project),
                backend="codex", model="gpt-6-sol", thinking_level="high")
    base.update(kw)
    return cli.cmd_spawn(**base)


def _born(tmp_path, seat_id="33333333-3333-4333-8333-333333333333"):
    born = tmp_path / "data" / ".convos" / "born-convo"
    born.mkdir(parents=True)
    (born / "settings.json").write_text(json.dumps({"seat_id": seat_id}))


def test_fresh_spawn_under_a_taken_name_is_refused_and_points_at_resume(fresh, capsys):
    calls, project = fresh
    with pytest.raises(SystemExit) as exc:
        _fresh(project, name=NAME)
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "already belongs to agent" in out and f"--resume {NAME}" in out
    assert calls == []


def test_legacy_index_diagnostic_fresh_spawn_with_a_new_name_records_it_against_its_born_conversation(fresh, tmp_path):
    calls, project = fresh
    _born(tmp_path)
    _index_diagnostic(project, name="Brand-New")
    entry = agent_names.resolve_name("Brand-New")
    assert (entry["agent_id"], entry["convo_id"], entry["cwd"]) == (
        "33333333-3333-4333-8333-333333333333", "born-convo", str(project.resolve()))


def test_legacy_index_diagnostic_fresh_spawn_takeover_rebinds_the_name(fresh, tmp_path):
    calls, project = fresh
    _born(tmp_path)
    _index_diagnostic(project, name=NAME, takeover=True)
    assert agent_names.resolve_name(NAME)["agent_id"] == "33333333-3333-4333-8333-333333333333"


def test_legacy_index_diagnostic_fresh_spawn_without_a_born_conversation_still_succeeds_and_warns(fresh, monkeypatch, capsys):
    calls, project = fresh
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks) * 6.0)
    _index_diagnostic(project, name="No-Convo-Yet")
    assert agent_names.resolve_name("No-Convo-Yet") is None
    assert "name index not updated" in capsys.readouterr().err


def test_legacy_index_diagnostic_fresh_spawn_uses_bridge_data_root_instead_of_callers_root(fresh, monkeypatch, tmp_path):
    calls, project = fresh
    authoritative = tmp_path / "bridge-data"
    born = authoritative / ".convos" / "bridge-convo"
    born.mkdir(parents=True)
    (born / "settings.json").write_text(json.dumps({"seat_id": "33333333-3333-4333-8333-333333333333"}), encoding="utf-8")
    _born(tmp_path)  # A different root can even contain the same seat id.
    bridge = cli._bridge_request

    def with_root(method, path, body=None, **kw):
        response = bridge(method, path, body, **kw)
        if path == "/harness/spawn/resolve":
            response["liteTuiDataRoot"] = str(authoritative)
        return response

    monkeypatch.setattr(cli, "_bridge_request", with_root)
    _index_diagnostic(project, name="Bridge-Root")
    assert agent_names.resolve_name("Bridge-Root")["convo_id"] == "bridge-convo"


def test_legacy_index_diagnostic_old_bridge_without_root_or_env_warns_unresolved_without_waiting(fresh, monkeypatch, capsys):
    calls, project = fresh
    monkeypatch.delenv("LITETUI_DATA_ROOT", raising=False)
    monkeypatch.setitem(sys.modules, "litetui.paths", None)
    monkeypatch.setattr(cli.time, "sleep", lambda _: pytest.fail("an unresolved root cannot improve by waiting"))
    _index_diagnostic(project, name="Unresolved")
    captured = capsys.readouterr()
    assert "root unresolved" in captured.err
    assert "LITETUI_DATA_ROOT" in captured.err and "liteTuiDataRoot" in captured.err
    assert "33333333-3333-4333-8333-333333333333" in captured.out  # Launch still succeeds on an old bridge.
    assert agent_names.resolve_name("Unresolved") is None


def test_legacy_index_diagnostic_known_root_without_conversation_warns_timeout_not_unresolved(fresh, capsys):
    calls, project = fresh
    _index_diagnostic(project, name="Not-Born")
    err = capsys.readouterr().err
    assert "no matching conversation after 0" in err
    assert "root unresolved" not in err
    assert "33333333-3333-4333-8333-333333333333" in err and ".convos" in err


def test_legacy_index_diagnostic_duplicate_conversations_warn_ambiguous_without_binding(fresh, tmp_path, capsys):
    calls, project = fresh
    _born(tmp_path)
    other = tmp_path / "data" / ".convos" / "other-convo"
    other.mkdir()
    (other / "settings.json").write_text(json.dumps({"seat_id": "33333333-3333-4333-8333-333333333333"}), encoding="utf-8")
    _index_diagnostic(project, name="Ambiguous")
    err = capsys.readouterr().err
    assert "ambiguous" in err and "born-convo" in err and "other-convo" in err
    assert "root unresolved" not in err and "no matching conversation" not in err
    assert agent_names.resolve_name("Ambiguous") is None


def test_legacy_index_diagnostic_conversation_born_during_wait_is_recorded(fresh, tmp_path, monkeypatch):
    calls, project = fresh
    monkeypatch.setattr(cli, "FRESH_NAME_WAIT_SECONDS", 2.0)
    ticks = iter([0.0, 0.0, 0.5])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cli.time, "sleep", lambda _: _born(tmp_path))
    _index_diagnostic(project, name="Born-Later")
    assert agent_names.resolve_name("Born-Later")["convo_id"] == "born-convo"


def test_legacy_index_diagnostic_old_bridge_can_use_importable_litetui_root(fresh, monkeypatch, tmp_path):
    calls, project = fresh
    _born(tmp_path)
    monkeypatch.delenv("LITETUI_DATA_ROOT", raising=False)
    fake_paths = ModuleType("litetui.paths")
    fake_paths.data_root = lambda: tmp_path / "data"
    monkeypatch.setitem(sys.modules, "litetui.paths", fake_paths)
    _index_diagnostic(project, name="Import-Root")
    assert agent_names.resolve_name("Import-Root")["convo_id"] == "born-convo"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_legacy_index_diagnostic_blank_bridge_root_falls_back_to_environment(fresh, monkeypatch, tmp_path, blank):
    calls, project = fresh
    _born(tmp_path)
    bridge = cli._bridge_request

    def with_blank_root(method, path, body=None, **kw):
        response = bridge(method, path, body, **kw)
        if path == "/harness/spawn/resolve":
            response["liteTuiDataRoot"] = blank
        return response

    monkeypatch.setattr(cli, "_bridge_request", with_blank_root)
    _index_diagnostic(project, name="Blank-Root")
    assert agent_names.resolve_name("Blank-Root")["convo_id"] == "born-convo"


def test_legacy_index_diagnostic_malformed_bridge_root_does_not_scan_environment(fresh, monkeypatch, tmp_path, capsys):
    calls, project = fresh
    _born(tmp_path)
    bridge = cli._bridge_request

    def with_bad_root(method, path, body=None, **kw):
        response = bridge(method, path, body, **kw)
        if path == "/harness/spawn/resolve":
            response["liteTuiDataRoot"] = 42
        return response

    monkeypatch.setattr(cli, "_bridge_request", with_bad_root)
    _index_diagnostic(project, name="Malformed-Root")
    assert "root unresolved" in capsys.readouterr().err
    assert agent_names.resolve_name("Malformed-Root") is None


@pytest.mark.parametrize("relative", [".", "relative-data"] + (["C:foo", r"\foo"] if sys.platform == "win32" else []))
def test_legacy_index_diagnostic_relative_bridge_root_never_binds_a_caller_local_conversation(
        fresh, monkeypatch, tmp_path, capsys, relative):
    calls, project = fresh
    _born(tmp_path)  # The actual conversation lives under the populated env root.
    caller_data = project if relative == "." else project / "relative-data"
    old = caller_data / ".convos" / "old-convo"
    old.mkdir(parents=True)
    (old / "settings.json").write_text(json.dumps({"seat_id": "33333333-3333-4333-8333-333333333333"}), encoding="utf-8")
    monkeypatch.chdir(project)
    bridge = cli._bridge_request

    def with_relative_root(method, path, body=None, **kw):
        response = bridge(method, path, body, **kw)
        if path == "/harness/spawn/resolve":
            response["liteTuiDataRoot"] = relative
        return response

    monkeypatch.setattr(cli, "_bridge_request", with_relative_root)
    _index_diagnostic(project, name="Relative-Root")
    captured = capsys.readouterr()
    assert agent_names.resolve_name("Relative-Root") is None
    assert "root unresolved" in captured.err
    assert "absolute" in captured.err
    assert "33333333-3333-4333-8333-333333333333" in captured.out


def test_legacy_index_diagnostic_nonexistent_absolute_bridge_root_never_falls_back_to_populated_env(
        fresh, monkeypatch, tmp_path, capsys):
    calls, project = fresh
    _born(tmp_path)
    absent = tmp_path / "absent-data"
    assert absent.is_absolute() and not absent.exists()
    bridge = cli._bridge_request

    def with_absent_root(method, path, body=None, **kw):
        response = bridge(method, path, body, **kw)
        if path == "/harness/spawn/resolve":
            response["liteTuiDataRoot"] = str(absent)
        return response

    monkeypatch.setattr(cli, "_bridge_request", with_absent_root)
    _index_diagnostic(project, name="Absent-Root")
    captured = capsys.readouterr()
    assert agent_names.resolve_name("Absent-Root") is None
    assert "no matching conversation after 0" in captured.err
    assert str(absent) in captured.err and "root unresolved" not in captured.err
    assert "33333333-3333-4333-8333-333333333333" in captured.out


def test_takeover_is_refused_outside_a_fresh_litetui_spawn(world):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(spawn_cli="claude", cwd=".", takeover=True)
    assert exc.value.code == 2


# -- names --list ---------------------------------------------------------------

def test_names_list_prints_every_entry_with_the_documented_columns(world, capsys):
    cli.cmd_names(["--list"])
    out = capsys.readouterr().out
    for want in (NAME, AID, CID, "codex/gpt-6-sol", "GameProject"):
        assert want in out


def test_names_list_json_and_usage(world, capsys):
    cli.cmd_names(["--list", "--json"])
    assert json.loads(capsys.readouterr().out)[0]["agent_id"] == AID
    with pytest.raises(SystemExit) as exc:
        cli.cmd_names([])
    assert exc.value.code == 2


# -- Claude Code resume (T0277) --------------------------------------------------

@pytest.fixture
def claude_world(world, tmp_path, monkeypatch):
    root, project = world
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    agent_names.record_name(NAME, AID, CID, str(project), backend="claude", model="claude-opus")
    registry = root / "agents" / f"{AID}.json"
    row = json.loads(registry.read_text(encoding="utf-8"))
    row.update(cli="claude-code", backend="claude", model="claude-opus")
    registry.write_text(json.dumps(row), encoding="utf-8", newline="")
    # Claude needs neither LiteTUI data nor a bridge call before its launcher.
    def no_data():
        raise ValueError("LiteTUI data root unknown: set LITETUI_DATA_ROOT")
    monkeypatch.setattr(resume_seat, "_convo_root", no_data)
    def no_bridge(*args, **kwargs):
        pytest.fail("Claude resume must not inspect or close PTYs before launching")
    monkeypatch.setattr(cli, "_bridge_request", no_bridge)
    entrypoint = cli.cmd_spawn
    launches = []
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kwargs: launches.append(kwargs))
    return root, project, entrypoint, launches


@pytest.mark.parametrize("backend", ["claude", "claude/claude-opus"])
def test_claude_resume_uses_index_session_and_original_cwd(claude_world, backend):
    root, project, spawn, launches = claude_world
    agent_names.record_name(NAME, AID, CID, str(project), backend=backend, model="claude-opus")
    before = (root / "names.json").read_bytes()
    spawn(split_mode=True, resume_agent_id=NAME.lower(), split_pane="pane-1",
          split_direction="vertical", tier="worker", spawned_by="leader-1")
    assert len(launches) == 1
    launch = launches[0]
    assert launch["spawn_cli"] == "claude"
    assert launch["additional_args"] == f"--resume {CID}"
    assert launch["cwd"] == str(project)
    assert launch["name"] == NAME and launch["model"] == "claude-opus"
    assert launch["split_mode"] is True and launch["split_pane"] == "pane-1"
    assert launch["split_direction"] == "vertical" and launch["spawned_by"] == "leader-1"
    assert not launch.get("resume_agent_id") and not launch.get("resume_convo_id")
    assert (root / "names.json").read_bytes() == before


@pytest.mark.parametrize("field, expected", [
    ("cwd", "original cwd"), ("convo_id", "conversation id"),
])
def test_claude_resume_refuses_missing_index_metadata(claude_world, capsys, field, expected):
    root, project, spawn, launches = claude_world
    data = json.loads((root / "names.json").read_text(encoding="utf-8"))
    data[NAME].pop(field)
    (root / "names.json").write_text(json.dumps(data), encoding="utf-8", newline="")
    with pytest.raises(SystemExit) as exc:
        spawn(split_mode=True, resume_agent_id=NAME, cwd=str(project), resume_convo_id=CID)
    assert exc.value.code == 2
    assert expected in capsys.readouterr().out
    assert launches == []


@pytest.mark.parametrize("override, expected", [
    ({"cwd": "other-project"}, "original cwd"),
    ({"resume_convo_id": "33333333-3333-4333-8333-333333333333"}, "conversation id"),
])
def test_claude_resume_refuses_conflicting_overrides(claude_world, capsys, override, expected):
    root, project, spawn, launches = claude_world
    with pytest.raises(SystemExit) as exc:
        spawn(split_mode=True, resume_agent_id=NAME, **override)
    assert exc.value.code == 2
    assert expected in capsys.readouterr().out and launches == []


def test_live_claude_resume_points_to_inbox_without_relaunch(claude_world, monkeypatch, capsys):
    root, project, spawn, launches = claude_world
    monkeypatch.setattr(cli, "_live_owner_pid", lambda row: 4242)
    with pytest.raises(SystemExit) as exc:
        spawn(split_mode=True, resume_agent_id=NAME)
    assert exc.value.code == 2
    assert f"live: message {NAME} by inbox instead" in capsys.readouterr().out
    assert launches == []


def test_claude_named_resume_never_kills_old_seat(claude_world, capsys):
    root, project, spawn, launches = claude_world
    with pytest.raises(SystemExit) as exc:
        spawn(split_mode=True, resume_agent_id=NAME, kill_old=True)
    assert exc.value.code == 2
    assert "never closes a seat" in capsys.readouterr().out and launches == []


def _index_diagnostic(project, **kw):
    resolution = cli._bridge_request('POST', '/harness/spawn/resolve', {})
    cli._record_fresh_name(kw['name'], '33333333-3333-4333-8333-333333333333',
        str(project.resolve()), 'codex', 'gpt-6-sol', kw.get('takeover', False),
        data_root=resolution.get('liteTuiDataRoot'))
    print('33333333-3333-4333-8333-333333333333')
