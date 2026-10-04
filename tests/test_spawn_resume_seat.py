"""LiteTUI resume owns the old seat identity, conversation and execution settings."""
import json
import builtins
import sys
from datetime import datetime, timezone
from types import ModuleType

import pytest

from liteharness import cli, resume_seat

AID = "66666666-6666-4666-8666-666666666666"
CID = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def seat(tmp_path, monkeypatch):
    data = tmp_path / "data"
    convo = data / ".convos" / CID
    convo.mkdir(parents=True)
    (convo / "convo.jsonl").write_text('{"type":"meta"}\n')
    settings = {"seat_id": AID, "seat_name": "Old-next", "backend": "codex",
                "model": "gpt-6-sol", "thinking_level": "high",
                "execution": {"thinking_level": "high", "backend": "codex"}}
    (convo / "settings.json").write_text(json.dumps(settings))
    root = tmp_path / "harness"
    (root / "agents").mkdir(parents=True)
    registry = {"agent_id": AID, "name": "Old", "tier": "worker",
                "model": "gpt-6-sol", "backend": "codex", "thinking_level": "high",
                "spawned_by": "parent-id", "session_pid": 123, "canvas_session_id": "pty-old",
                "registered_at": datetime.now(timezone.utc).isoformat()}
    (root / "agents" / f"{AID}.json").write_text(json.dumps(registry))
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(data))
    monkeypatch.setattr(cli.config, "get_root", lambda: root)
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: None)
    from liteharness import fleet_policy
    monkeypatch.setattr(fleet_policy, "gate", lambda backend, model, thinking: None)
    project = tmp_path / "project"
    project.mkdir()
    return root, settings, registry, project


def test_lookup_uses_registry_name_and_convo_execution_not_stale_name(seat):
    _, settings, registry, _ = seat
    assert resume_seat.lookup(AID, None) == (CID, settings, registry)
    assert resume_seat.lookup(AID, CID)[0] == CID


def test_lookup_explicit_convo_wins_ambiguous_agent_search(seat, tmp_path):
    root, _, _, _ = seat
    convo = tmp_path / "data" / ".convos" / "second"
    convo.mkdir()
    (convo / "settings.json").write_text(json.dumps({"seat_id": AID}))
    (convo / "convo.jsonl").write_text('{}')
    with pytest.raises(ValueError, match="2 matching conversations"):
        resume_seat.lookup(AID, None)
    assert resume_seat.lookup(AID, CID)[0] == CID


def test_resolve_edits_native_request_and_preserves_three_reads(seat, monkeypatch):
    root, original, registry, project = seat
    calls = []

    def bridge(method, path, body=None):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        calls.append((method, path, body))
        if path == "/pty/list":
            return {"sessions": [{"id": "pty-old", "pid": 123, "cwd": str(project)}]}
        if path == "/harness/spawn/resolve":
            return {"ok": True, "request": {"args": ["--model", "gpt-6-sol", "--system-prompt-file", "worker.md"],
                                             "env": {"LITEHARNESS_AGENT_ID": "new-id"},
                                             "harnessAgentId": "new-id"}}
        return {"newSessionId": "pty-new"}

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    cli.cmd_spawn(split_mode=True, split_pane="canvas-pane-1", resume_agent_id=AID)
    resolve = calls[1][2]
    assert resolve == {"cli": "litetui", "name": "Old", "tier": "worker", "model": "gpt-6-sol",
                       "backend": "codex", "thinkingLevel": "high", "cwd": str(project), "spawnedBy": "parent-id"}
    split = calls[2][2]
    assert split["paneId"] == "canvas-pane-1"
    assert split["cwd"] == str(project)
    assert split["launch"]["args"] == ["--model", "gpt-6-sol", "--convo", CID]
    assert split["launch"]["env"]["LITEHARNESS_AGENT_ID"] == AID
    assert split["launch"]["harnessAgentId"] == AID
    assert json.loads((root / "agents" / f"{AID}.json").read_text())["thinking_level"] == "high"
    assert json.loads((project.parent / "data" / ".convos" / CID / "settings.json").read_text()) == original


def test_alive_refuses_before_resolve_and_does_not_kill(seat, monkeypatch, capsys):
    _, _, _, project = seat
    calls = []
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: 123)
    monkeypatch.setattr(cli, "_bridge_request", lambda method, path, body=None: (
        calls.append((method, path)) or {"sessions": [{"id": "pty-old", "cwd": str(project), "pid": 123}]}
    ))
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(split_mode=True, resume_agent_id=AID)
    assert exc.value.code == 2
    assert "still running" in capsys.readouterr().out
    assert calls == [("GET", "/pty/list")]


def test_kill_waits_for_pid_to_exit_before_launch(seat, monkeypatch):
    _, _, _, project = seat
    calls = []
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: 123)
    from liteharness import hooks
    monkeypatch.setattr(hooks, "_pid_alive", lambda _: True)
    monkeypatch.setattr(resume_seat.time, "sleep", lambda _: None)
    def bridge(method, path, body=None, **kwargs):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if method == "DELETE":
            assert kwargs["lifecycle_origin"] == "liteharness-resume:kill-old"
        calls.append((method, path))
        if path == "/pty/list":
            return {"sessions": [{"id": "pty-old", "cwd": str(project), "pid": 123}]}
        if path == "/harness/spawn/resolve":
            return {"ok": True, "request": {"args": ["--system-prompt-file", "worker.md"], "env": {}}}
        return {"ok": True}
    monkeypatch.setattr(cli, "_bridge_request", bridge)
    with pytest.raises(ValueError, match="still alive after PTY close"):
        resume_seat.spawn_resume(agent_id=AID, convo_id=None, pane=None, direction=None,
                                 cwd=None, name=None, tier=None, model=None, backend=None,
                                 thinking_level=None, spawned_by=None, kill_old=True, prompt=None)
    assert calls == [("GET", "/pty/list"), ("POST", "/harness/spawn/resolve"),
                     ("DELETE", "/pty/pty-old")]


def test_unknown_cwd_refuses_instead_of_guessing_data_root(seat, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_bridge_request", lambda method, path, body=None: (
        calls.append(path) or {"sessions": []}))
    with pytest.raises(ValueError, match="cwd unknown: pass --cwd"):
        resume_seat.spawn_resume(agent_id=AID, convo_id=None, pane=None, direction=None,
                                 cwd=None, name=None, tier=None, model=None, backend=None,
                                 thinking_level=None, spawned_by=None, kill_old=False, prompt=None)
    assert calls == ["/pty/list", "/session/list"]


def test_claude_extra_resume_is_unchanged_and_native_flag_is_distinct():
    assert cli._parse_spawn_args(["--split", "--args", "--resume old-id"]) == {
        "split_mode": True, "additional_args": "--resume old-id"}
    assert cli._parse_spawn_args(["--split", "--resume", AID]) == {
        "split_mode": True, "resume_agent_id": AID}


def test_session_launch_record_links_old_agent_to_pty_cwd(seat, monkeypatch):
    _, _, _, project = seat
    calls = []
    def bridge(method, path, body=None):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        calls.append(path)
        if path == "/pty/list":
            return {"sessions": [{"id": "another-pty", "cwd": str(project), "pid": 999}]}
        if path == "/session/list":
            return {"sessions": [{"sessionId": "another-pty", "agentId": AID}]}
        if path == "/harness/spawn/resolve":
            assert body["cwd"] == str(project)
            return {"ok": True, "request": {"args": ["--system-prompt-file", "worker.md"],
                                             "env": {}, "harnessAgentId": "new"}}
        return {"newSessionId": "pty-new"}
    monkeypatch.setattr(cli, "_bridge_request", bridge)
    cli.cmd_spawn(split_mode=True, resume_agent_id=AID)
    assert calls == ["/pty/list", "/session/list", "/harness/spawn/resolve", "/canvas/split"]


def test_explicit_cwd_and_flags_override_disk_values(seat, monkeypatch, tmp_path):
    _, _, _, _ = seat
    project = tmp_path / "different-project"
    project.mkdir()
    seen = []
    def bridge(method, path, body=None):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if path == "/pty/list":
            return {"sessions": []}
        if path == "/session/list":
            return {"sessions": []}
        if path == "/harness/spawn/resolve":
            seen.append(body)
            return {"ok": True, "request": {"args": ["--system-prompt-file", "worker.md"], "env": {}}}
        return {"newSessionId": "pty-new"}
    monkeypatch.setattr(cli, "_bridge_request", bridge)
    cli.cmd_spawn(split_mode=True, resume_agent_id=AID, resume_convo_id=CID, cwd=str(project),
                  name="New", tier="leader", model="gpt-6-sol", backend="codex",
                  thinking_level="high", spawned_by="new-parent")
    assert seen[0] == {"cli": "litetui", "name": "New", "tier": "leader",
                       "model": "gpt-6-sol", "backend": "codex", "thinkingLevel": "high",
                       "cwd": str(project), "spawnedBy": "new-parent"}


def test_convo_alone_uses_its_seat_id_and_registry(seat, monkeypatch):
    _, _, _, project = seat
    seen = []

    def bridge(method, path, body=None):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if path == "/pty/list":
            return {"sessions": []}
        if path == "/session/list":
            return {"sessions": []}
        if path == "/harness/spawn/resolve":
            seen.append(body)
            return {"ok": True, "request": {"args": [], "env": {}}}
        seen.append(body)
        return {"newSessionId": "pty-new"}

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    cli.cmd_spawn(split_mode=True, resume_convo_id=CID, cwd=str(project))
    assert seen[0]["name"] == "Old"
    assert seen[1]["launch"]["harnessAgentId"] == AID
    assert seen[1]["launch"]["args"] == ["--convo", CID]


def test_kill_closes_old_pty_then_launches_when_owner_exits(seat, monkeypatch):
    _, _, _, project = seat
    calls = []
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: 123)
    from liteharness import hooks
    monkeypatch.setattr(hooks, "_pid_alive", lambda _: False)

    def bridge(method, path, body=None, **kwargs):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if method == "DELETE":
            assert kwargs["lifecycle_origin"] == "liteharness-resume:kill-old"
        calls.append((method, path))
        if path == "/pty/list":
            return {"sessions": [{"id": "pty-old", "cwd": str(project), "pid": 123}]}
        if path == "/harness/spawn/resolve":
            return {"ok": True, "request": {"args": ["--system-prompt-file", "worker.md"], "env": {}}}
        if path.startswith("/pty/"):
            return {"success": True}
        return {"newSessionId": "pty-new"}

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    cli.cmd_spawn(split_mode=True, resume_agent_id=AID, kill_old=True)
    assert calls == [("GET", "/pty/list"), ("POST", "/harness/spawn/resolve"),
                     ("DELETE", "/pty/pty-old"), ("POST", "/canvas/split")]


def test_below_floor_thinking_refuses_before_resolver_or_split(seat, monkeypatch, tmp_path):
    _, _, _, project = seat
    from liteharness import fleet_policy
    # Exercise the real policy gate against an isolated floor and model cache.
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"floors": {"codex": {"models": ["gpt-6-sol"],
                                                    "min_model": "gpt-6-sol",
                                                    "thinking_levels": ["low", "medium", "high"],
                                                    "min_thinking_level": "medium"}}}))
    monkeypatch.setattr(fleet_policy, "gate", lambda backend, model, thinking:
                        fleet_policy.check(backend, model, thinking, path=policy))
    calls = []
    monkeypatch.setattr(cli, "_bridge_request", lambda method, path, body=None:
                        calls.append(path) or {"sessions": [{"id": "pty-old", "pid": 123,
                                                              "cwd": str(project)}]})
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(split_mode=True, resume_agent_id=AID, thinking_level="low")
    assert exc.value.code == 2
    assert calls == ["/pty/list"]


def test_old_pty_matches_litetui_launcher_through_python_parent(monkeypatch):
    import psutil

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return {48832: 30, 115132: 20, 133900: 10}[self.pid]

        def parent(self):
            parent = {48832: 115132, 115132: 133900, 133900: None}[self.pid]
            return Process(parent) if parent else None

    monkeypatch.setattr(psutil, "Process", Process)
    sessions = [{"id": "other", "pid": 42, "cwd": "/wrong"},
                {"id": "pty-13", "pid": 133900, "cwd": "/project"}]
    registry = {"session_pid": 48832, "canvas_session_id": None}
    assert resume_seat._old_pty(registry, sessions) == sessions[1]
    # A known canvas link is first choice even if another session has the pid.
    assert resume_seat._old_pty({**registry, "canvas_session_id": "other"}, sessions) == sessions[0]


def test_missing_psutil_refuses_unknown_cwd_without_crashing(seat, monkeypatch, capsys):
    _, _, registry, project = seat
    registry["canvas_session_id"] = None
    monkeypatch.setitem(sys.modules, "psutil", None)
    calls = []

    def bridge(method, path, body=None):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        calls.append(path)
        if path == "/pty/list":
            return {"sessions": [{"id": "pty-13", "pid": 133900, "cwd": str(project)}]}
        return {"sessions": []}

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(split_mode=True, resume_agent_id=AID)
    assert exc.value.code == 2
    assert "cwd unknown: pass --cwd" in capsys.readouterr().out
    assert calls == ["/pty/list", "/session/list"]


def test_ancestor_pty_supplies_cwd_and_kill_target(seat, monkeypatch):
    _, _, registry, project = seat
    registry["canvas_session_id"] = None
    registry["session_pid"] = 48832
    monkeypatch.setattr(resume_seat, "_ancestor_pids", lambda _pid: [48832, 115132, 133900])
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: 48832)
    from liteharness import hooks
    monkeypatch.setattr(hooks, "_pid_alive", lambda _: False)
    calls = []

    def bridge(method, path, body=None, **kwargs):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if method == "DELETE":
            assert kwargs["lifecycle_origin"] == "liteharness-resume:kill-old"
        calls.append((method, path, body))
        if path == "/pty/list":
            return {"sessions": [{"id": "pty-13", "pid": 133900, "cwd": str(project)}]}
        if path == "/harness/spawn/resolve":
            assert body["cwd"] == str(project)
            return {"ok": True, "request": {"args": ["--system-prompt-file", "worker.md"], "env": {}}}
        if path == "/pty/pty-13":
            return {"success": True}
        return {"newSessionId": "pty-new"}

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    cli.cmd_spawn(split_mode=True, resume_agent_id=AID, kill_old=True)
    assert [(method, path) for method, path, _ in calls] == [
        ("GET", "/pty/list"), ("POST", "/harness/spawn/resolve"),
        ("DELETE", "/pty/pty-13"), ("POST", "/canvas/split")]


def test_no_ancestor_match_keeps_unknown_cwd_and_alive_kill_refusals(seat, monkeypatch):
    _, _, _, project = seat
    monkeypatch.setattr(resume_seat, "_ancestor_pids", lambda _pid: [48832, 115132, 133900])
    monkeypatch.setattr(cli, "_bridge_request", lambda method, path, body=None: (
        {"sessions": [{"id": "other", "pid": 42, "cwd": str(project)}]}
        if path == "/pty/list" else {"sessions": []}))
    assert resume_seat._old_pty({"session_pid": 48832}, [{"id": "other", "pid": 42}]) is None
    with pytest.raises(ValueError, match="cwd unknown: pass --cwd"):
        resume_seat.spawn_resume(agent_id=AID, convo_id=None, pane=None, direction=None,
                                 cwd=None, name=None, tier=None, model=None, backend=None,
                                 thinking_level=None, spawned_by=None, kill_old=True, prompt=None)
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: 48832)
    def bridge(method, path, body=None):
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if path == "/pty/list":
            return {"sessions": [{"id": "other", "pid": 42, "cwd": str(project)}]}
        if path == "/session/list":
            return {"sessions": []}
        if path == "/harness/spawn/resolve":
            return {"ok": True, "request": {"args": [], "env": {}}}
        raise AssertionError(f"unexpected bridge call: {path}")

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    with pytest.raises(ValueError, match="no matching PTY session to close"):
        resume_seat.spawn_resume(agent_id=AID, convo_id=None, pane=None, direction=None,
                                 cwd=str(project), name=None, tier=None, model=None, backend=None,
                                 thinking_level=None, spawned_by=None, kill_old=True, prompt=None)


def test_missing_litetui_root_refuses_instead_of_using_a_machine_path(monkeypatch):
    monkeypatch.delenv("LITETUI_DATA_ROOT", raising=False)
    original_import = builtins.__import__

    def without_litetui(name, *args, **kwargs):
        if name == "litetui.paths":
            raise ImportError("LiteTUI not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_litetui)
    with pytest.raises(ValueError, match="LiteTUI data root unknown: set LITETUI_DATA_ROOT"):
        resume_seat.lookup(AID, CID)


@pytest.mark.parametrize("configured", ["relative-history", "."] + (["C:history", "/history", chr(92) + "history"] if sys.platform == "win32" else []))
def test_explicit_relative_root_refuses_from_every_cwd_without_import_fallback(
        seat, tmp_path, monkeypatch, configured):
    _, settings, _, _ = seat
    fake_paths = ModuleType("litetui.paths")
    fake_paths.data_root = lambda: pytest.fail("invalid explicit env must not use import fallback")
    monkeypatch.setitem(sys.modules, "litetui.paths", fake_paths)
    monkeypatch.setenv("LITETUI_DATA_ROOT", configured)
    errors = []
    for folder in ("caller-one", "caller-two"):
        caller = tmp_path / folder
        caller.mkdir()
        # Both caller directories contain a plausible but different local transcript.
        wrong = (caller if configured == "." else caller / "relative-history") / ".convos" / CID
        wrong.mkdir(parents=True)
        (wrong / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        (wrong / "convo.jsonl").write_text(json.dumps({"type": "meta"}), encoding="utf-8")
        monkeypatch.chdir(caller)
        with pytest.raises(ValueError, match="LITETUI_DATA_ROOT.*absolute path or ~/") as exc:
            if configured in ("relative-history", "."):
                resume_seat.lookup(AID, CID)
            else:
                # Resolve only the path kind; do not probe drive/root-relative storage.
                resume_seat._convo_root()
        errors.append(str(exc.value))
    assert errors[0] == errors[1]


def test_home_shorthand_root_is_stable_across_caller_directories(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LITETUI_DATA_ROOT", "~/history")
    for folder in ("caller-one", "caller-two"):
        caller = tmp_path / folder
        caller.mkdir()
        monkeypatch.chdir(caller)
        assert resume_seat._convo_root() == home / "history" / ".convos"


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_absent_or_blank_root_keeps_litetui_owned_import_fallback(tmp_path, monkeypatch, configured):
    fake_paths = ModuleType("litetui.paths")
    fake_paths.data_root = lambda: tmp_path / "owned-data"
    monkeypatch.setitem(sys.modules, "litetui.paths", fake_paths)
    if configured is None:
        monkeypatch.delenv("LITETUI_DATA_ROOT", raising=False)
    else:
        monkeypatch.setenv("LITETUI_DATA_ROOT", configured)
    assert resume_seat._convo_root() == tmp_path / "owned-data" / ".convos"


def test_absolute_env_root_still_takes_precedence_over_owned_import(tmp_path, monkeypatch):
    fake_paths = ModuleType("litetui.paths")
    fake_paths.data_root = lambda: pytest.fail("explicit absolute env must take precedence")
    monkeypatch.setitem(sys.modules, "litetui.paths", fake_paths)
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(tmp_path / "explicit"))
    assert resume_seat._convo_root() == tmp_path / "explicit" / ".convos"


def test_relative_env_indexer_warns_and_sweep_treats_ownership_as_unknown(seat, tmp_path, monkeypatch, capsys):
    from liteharness import agent_names
    monkeypatch.setenv("LITETUI_DATA_ROOT", "relative-history")
    wrong = tmp_path / "relative-history" / ".convos" / CID
    wrong.mkdir(parents=True)
    (wrong / "settings.json").write_text(json.dumps({"seat_id": AID}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cli._record_fresh_name("Wrong-Root", AID, str(tmp_path), "codex", "gpt-6-sol", False, wait=0)
    assert agent_names.resolve_name("Wrong-Root") is None
    err = capsys.readouterr().err
    assert "root unresolved" in err and "absolute path or ~/" in err
    # UNKNOWN must retain a LiteTUI record, but not an unrelated CLI record.
    assert agent_names.owns_conversation("another-id", {"cli": "litetui"}) is True
    assert agent_names.owns_conversation("another-id", {"cli": "claude-code"}) is False
