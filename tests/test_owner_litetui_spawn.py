"""Owner launches use LiteTUI's memory mode, never a synthesized charter prompt."""
import subprocess
from unittest.mock import Mock

import pytest

from liteharness import agent_names, cli, config, fleet_policy, owner, roles

AGENT_ID = "11111111-1111-4111-8111-111111111111"
OLD_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def seat(tmp_path, monkeypatch):
    root = tmp_path / "harness"
    data = tmp_path / "data"
    data.mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()
    subprocess.run(["git", "init", "-q", str(memory)], check=True)
    for filename in owner.OWNER_FILES:
        (memory / filename).write_text("widgets\n", encoding="utf-8")
    monkeypatch.setattr(config, "get_root", lambda: root)
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(data))
    monkeypatch.setattr(owner, "owner_prompt", Mock(side_effect=AssertionError("LiteTUI injects memory")))
    monkeypatch.setattr(agent_names, "resolve_name", Mock(return_value=None))
    monkeypatch.setattr(fleet_policy, "gate", Mock(return_value=None))
    monkeypatch.setattr(fleet_policy, "verify_seat", Mock(return_value=None))
    monkeypatch.setattr(cli, "_rename_canvas_seat", Mock(return_value=None))
    bridge = Mock(side_effect=lambda method, path, body=None: {
        "/harness/spawn/resolve": {
            "ok": True, "agentId": AGENT_ID, "liteTuiDataRoot": str(data),
            "request": {"shell": "litetui", "args": [], "env": {}, "harnessAgentId": AGENT_ID},
        },
        "/pty/create": {"session_id": "fixture-session"},
    }[path])
    place = Mock(return_value={"ok": True, "newSessionId": "fixture-session"})
    monkeypatch.setattr(cli, "_bridge_request", bridge)
    monkeypatch.setattr(cli, "_place_split", place)
    kwargs = dict(spawn_cli="litetui", split_mode=True, cwd=str(tmp_path),
                  owner_title="Alpha", memory_dir=str(memory), spawned_by=OLD_ID,
                  backend="codex", model="fixture", thinking_level="high")
    return kwargs, bridge, place, data, root


@pytest.mark.parametrize("mode", ["split", "pty"])
def test_owner_launch_passes_memory_identity_and_execution(seat, mode):
    kwargs, bridge, place, data, root = seat
    kwargs.update(split_mode=mode == "split", pty_mode=mode == "pty",
                  name="Alpha", tier="owner")
    cli.cmd_spawn(**kwargs)
    method, path, body = bridge.call_args_list[0].args
    assert (method, path) == ("POST", "/harness/spawn/resolve")
    assert body["name"] == "Alpha" and body["tier"] == roles.wire_tier("owner")
    assert "prompt" not in body and "cognitive" not in body
    launch = place.call_args.kwargs["launch"] if mode == "split" else bridge.call_args.args[2]
    args = launch["args"]
    for flag, value in (("--owner", "Alpha"), ("--memory", kwargs["memory_dir"]),
                        ("--backend", "codex"), ("--model", "fixture"),
                        ("--thinking-level", "high"), ("--tool-profile", "autonomous")):
        assert args.count(flag) == 1
        assert args[args.index(flag) + 1] == value
    assert not {"--create-agent", "--agent-id", "--agent"}.intersection(args)
    assert launch["env"]["LITEHARNESS_AGENT_ID"] == AGENT_ID
    assert launch["env"]["LITEHARNESS_NAME"] == "Alpha"
    assert launch["env"]["LITETUI_DATA_ROOT"] == str(data)
    assert launch["harnessAgentId"] == AGENT_ID
    assert not (data / ".agents").exists() and not root.exists()
    owner.owner_prompt.assert_not_called()


def assert_refused(seat, capsys, why):
    kwargs, bridge, place, data, root = seat
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(**kwargs)
    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "SPAWN REFUSED:" in output and "Nothing was spawned." in output
    assert why in output
    bridge.assert_not_called()
    place.assert_not_called()
    assert not (data / ".agents").exists() and not root.exists()


@pytest.mark.parametrize("missing", owner.OWNER_FILES)
def test_missing_memory_file_refuses_before_resolver(seat, tmp_path, capsys, missing):
    memory = tmp_path / "incomplete"
    memory.mkdir()
    for filename in owner.OWNER_FILES:
        if filename != missing:
            (memory / filename).write_text("widgets\n", encoding="utf-8")
    seat[0]["memory_dir"] = str(memory)
    assert_refused(seat, capsys, missing)
    agent_names.resolve_name.assert_not_called()


def test_memory_outside_git_refuses_before_resolver(seat, tmp_path, capsys):
    memory = tmp_path / "unversioned"
    memory.mkdir()
    for filename in owner.OWNER_FILES:
        (memory / filename).write_text("widgets\n", encoding="utf-8")
    seat[0]["memory_dir"] = str(memory)
    assert_refused(seat, capsys, "git repository")
    agent_names.resolve_name.assert_not_called()


def test_absent_memory_directory_refuses_before_resolver(seat, tmp_path, capsys):
    seat[0]["memory_dir"] = str(tmp_path / "absent")
    assert_refused(seat, capsys, "directory")


def test_relative_memory_refuses_even_when_it_exists(seat, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    seat[0]["memory_dir"] = "memory"
    assert_refused(seat, capsys, "absolute")
    agent_names.resolve_name.assert_not_called()


@pytest.mark.parametrize("title", ['bad"title', " padded", "a" * 65, "two\nlines"])
def test_bad_title_refuses_before_resolver(seat, capsys, title):
    seat[0]["owner_title"] = title
    assert_refused(seat, capsys, "title")
    agent_names.resolve_name.assert_not_called()


@pytest.mark.parametrize("override,why", [
    ({"prompt": "other charter"}, "--prompt"),
    ({"cognitive": "other"}, "--cognitive"),
    ({"name": "Beta"}, "--name"),
    ({"tier": "builder"}, "--tier"),
    ({"resume_agent_id": "Alpha"}, "--resume"),
    ({"resume_convo_id": AGENT_ID}, "--convo"),
    ({"owner_title": None}, "--owner"),
    ({"memory_dir": None}, "--memory"),
    ({"spawn_cli": "codex"}, "--cli"),
])
def test_conflicting_or_unpaired_owner_flags_refuse_before_resolver(seat, capsys, override, why):
    seat[0].update(override)
    assert_refused(seat, capsys, why)
    agent_names.resolve_name.assert_not_called()


def test_held_title_refuses_with_successor_advice(seat, capsys):
    agent_names.resolve_name.return_value = {
        "name": "Alpha", "agent_id": OLD_ID, "convo_id": AGENT_ID, "cwd": seat[0]["cwd"],
    }
    assert_refused(seat, capsys, "--takeover")


def test_takeover_allows_successor_without_closing_old_seat(seat):
    kwargs, bridge, place, _, _ = seat
    agent_names.resolve_name.return_value = {
        "name": "Alpha", "agent_id": OLD_ID, "convo_id": AGENT_ID, "cwd": kwargs["cwd"],
    }
    cli.cmd_spawn(**kwargs, takeover=True)
    assert bridge.call_count == 1
    assert bridge.call_args.args[:2] == ("POST", "/harness/spawn/resolve")
    assert place.call_args.kwargs["launch"]["env"]["LITEHARNESS_AGENT_ID"] == AGENT_ID


@pytest.mark.parametrize("missing", ["backend", "model", "thinking_level"])
def test_owner_still_requires_explicit_execution(seat, capsys, missing):
    kwargs, _, place, data, _ = seat
    kwargs[missing] = None
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(**kwargs)
    assert exc.value.code == 2
    assert "explicit backend/model/thinking level" in capsys.readouterr().out
    place.assert_not_called()
    assert not (data / ".agents").exists()


def test_owner_still_passes_the_fleet_policy_gate(seat, capsys):
    fleet_policy.gate.return_value = "SPAWN REFUSED: fixture floor. Nothing was spawned."
    assert_refused(seat, capsys, "fixture floor")
    fleet_policy.gate.assert_called_once_with("codex", "fixture", "high")


def test_owner_help_explains_memory_and_successor_identity():
    descriptions = {flag: description for flag, _, _, description in cli.SPAWN_FLAGS}
    assert "litetui" in descriptions["--owner"]
    for text in ("--takeover", "old", "id", "NAME"):
        assert text in descriptions["--owner"]
    for text in ("absolute", "git", "SOUL.md", "DOMAIN.md", "NOW.md"):
        assert text in descriptions["--memory"]
