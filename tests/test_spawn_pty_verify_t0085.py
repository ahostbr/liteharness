"""T0085: only the launched program is gated; its own presence must verify.

No real daemon, process, registry, model lookup or waiting is used.
"""
import json

import pytest

from liteharness import cli, fleet_policy, pty_daemon


@pytest.mark.parametrize("command", [
    "claude -p 'review the codex adapter'",
    "claude -p 'review litetui --model gpt-5.6-sol'",
    'cmd /c "claude -p \'review the codex adapter\'"',
    "python -m reviewer codex litetui",
    'pwsh -c "claude -p \'review litetui\'"',
])
def test_prompt_arguments_are_not_programs(command):
    assert cli._spawn_floor_refusal(None, command) is None


@pytest.mark.parametrize("program", ["litetui", "codex"])
@pytest.mark.parametrize("wrapper", ["{}", "cmd /c {}", "python -m {}", "pwsh -c {}",
                                     'cmd /c "{}"', 'pwsh -c "{}"'])
def test_program_and_known_wrappers_still_meet_the_floor(program, wrapper):
    why = cli._spawn_floor_refusal(None, wrapper.format(program))
    assert why and why.startswith("SPAWN REFUSED")


@pytest.fixture
def launch(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    monkeypatch.setattr(cli.config, "get_agent_id", lambda: "test-parent")
    monkeypatch.setattr(pty_daemon, "ensure_daemon", lambda: True)
    from liteharness import prompts
    monkeypatch.setattr(prompts, "resolve_cognitive_file", lambda *a, **k: None)
    cache = tmp_path / "codex-home"
    cache.mkdir()
    (cache / "models_cache.json").write_text(json.dumps({"models": [{"slug": "gpt-6.1-sol"}]}))
    calls = []
    verified = []
    now = [0.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    verify = fleet_policy.verify_seat

    def check(agent_id, root, **kwargs):
        verified.append((agent_id, kwargs))
        return verify(agent_id, root, **kwargs)

    monkeypatch.setattr(fleet_policy, "verify_seat", check)

    def run(*, row=None, command=None, kill_result=None, launch_ok=True, delayed=False, duplicate=False,
            name=None, before_presence=None):
        command = command or "litetui --backend codex --model gpt-6.1-sol --thinking-level high"

        def publish(agent_id):
            if before_presence:
                before_presence()
            directory = tmp_path / "agents"
            directory.mkdir(exist_ok=True)
            data = dict(agent_id="real-seat", provisional_id=agent_id,
                        model="gpt-6.1-sol", thinking_level="high", backend="codex")
            data.update(row or {})
            (directory / "real-seat.json").write_text(json.dumps(data), encoding="utf-8")
            if duplicate:
                data["agent_id"] = "second-seat"
                (directory / "second-seat.json").write_text(json.dumps(data), encoding="utf-8")

        def daemon(request):
            calls.append(request)
            if request["cmd"] == "kill":
                return kill_result or {"ok": True}
            assert request["cmd"] == "spawn"
            assert request["env"]["LITEHARNESS_PROVISIONAL_ID"] == request["agent_id"]
            assert (tmp_path / "agents" / f'{request["agent_id"]}.json').is_file()
            if not launch_ok:
                return {"ok": False, "error": "mock launch failed"}
            if row is not False:
                if delayed:
                    def sleep(seconds):
                        now[0] += seconds
                        publish(request["agent_id"])
                    monkeypatch.setattr(cli.time, "sleep", sleep)
                else:
                    publish(request["agent_id"])
            return {"ok": True}

        monkeypatch.setattr(pty_daemon, "send_command", daemon)
        try:
            cli.cmd_spawn(cwd=str(tmp_path), pty_mode=True, exec_cmd=command, name=name)
        except SystemExit as exc:
            return exc.code, calls, verified, now[0]
        return 0, calls, verified, now[0]

    return run


@pytest.mark.parametrize("wrapper", ["{}", "cmd /c {}", "python -m {}", 'pwsh -c "{}"',
                                     'bash -c "{}"', 'sh -c "{}"',
                                     '"C:\\Program Files\\LiteTUI\\litetui.exe"{}'])
def test_pty_launch_verifies_the_joined_seat_not_the_provisional_row(launch, capsys, wrapper):
    command = "litetui --backend codex --model gpt-6.1-sol --thinking-level high"
    if wrapper.startswith('"C:'):
        command = command.removeprefix("litetui")
    code, calls, verified, elapsed = launch(command=wrapper.format(command), delayed=True)
    assert code == 0 and [r["cmd"] for r in calls] == ["spawn"]
    assert verified[0][0] == "real-seat"
    assert verified[0][1]["expect_model"] == "gpt-6.1-sol"
    assert verified[0][1]["expect_thinking"] == "high"
    assert verified[0][1]["backend"] == "codex"
    assert elapsed > 0
    assert "Spawned process" in capsys.readouterr().out


@pytest.mark.parametrize("row", [
    {"model": "gpt-6-astra"},
    {"thinking_level": "medium"},
    {"thinking_level": "ultra"},  # above the floor is still not the request
    False,
    {"provisional_id": "pty-unrelated"},
    {"agent_id": "other-seat"},  # filename and identity must agree
    {"model": "unknown"},
])
def test_mismatch_or_unverified_seat_is_refused_and_only_minted_pty_is_killed(launch, capsys, row, tmp_path):
    code, calls, verified, elapsed = launch(row=row)
    assert code == 2
    assert [r["cmd"] for r in calls] == ["spawn", "kill"]
    assert calls[1]["agent_id"] == calls[0]["agent_id"]
    assert elapsed <= 90
    assert not (tmp_path / "agents" / f'{calls[0]["agent_id"]}.json').exists()
    out = capsys.readouterr().out
    assert "SEAT FAILED" in out and "Spawned process" not in out


def test_failed_kill_is_reported_not_hidden_as_success(launch, capsys, tmp_path):
    code, calls, verified, elapsed = launch(row={"model": "gpt-6-astra"},
                                         kill_result={"ok": False, "error": "mock kill failed"})
    assert code == 2
    out = capsys.readouterr().out
    assert "FATAL CLEANUP FAILED" in out and "mock kill failed" in out
    assert not (tmp_path / "agents" / f'{calls[0]["agent_id"]}.json').exists()


def test_failed_launch_does_not_verify_or_kill(launch, tmp_path):
    code, calls, verified, elapsed = launch(launch_ok=False)
    assert code == 1 and verified == [] and [r["cmd"] for r in calls] == ["spawn"]
    assert not list((tmp_path / "agents").glob("*.json"))


def test_ungoverned_program_does_not_get_a_litetui_presence_wait(launch):
    code, calls, verified, elapsed = launch(command="claude -p 'review the codex adapter'", row=False)
    assert code == 0 and verified == [] and elapsed == 0
    assert [r["cmd"] for r in calls] == ["spawn"]


def test_join_and_model_reporting_share_one_deadline(launch):
    code, calls, verified, elapsed = launch(row={"model": "unknown"}, delayed=True)
    assert code == 2 and elapsed == 90
    assert [r["cmd"] for r in calls] == ["spawn", "kill"]


def test_unreadable_unrelated_rows_do_not_hide_the_join(launch, tmp_path):
    directory = tmp_path / "agents"
    directory.mkdir()
    (directory / "broken.json").write_text("{")
    (directory / "array.json").write_text("[]")
    code, calls, verified, elapsed = launch()
    assert code == 0 and verified[0][0] == "real-seat"


def test_verifier_exception_still_kills_the_minted_pty(launch, monkeypatch, capsys):
    def broken(*a, **k):
        raise ValueError("mock verification error")
    monkeypatch.setattr(fleet_policy, "verify_seat", broken)
    code, calls, verified, elapsed = launch()
    assert code == 2 and [r["cmd"] for r in calls] == ["spawn", "kill"]
    assert calls[1]["agent_id"] == calls[0]["agent_id"]
    assert "mock verification error" in capsys.readouterr().out


def test_ambiguous_join_is_refused_instead_of_selecting_a_seat(launch, capsys):
    code, calls, verified, elapsed = launch(duplicate=True)
    assert code == 2 and verified == []
    assert [r["cmd"] for r in calls] == ["spawn", "kill"]
    assert "multiple seats claim" in capsys.readouterr().out


@pytest.mark.parametrize("program", ["litetui", "codex"])
@pytest.mark.parametrize("wrapper", ["cmd /c", "pwsh -c"])
def test_wrapper_keeps_a_quoted_executable_path_as_one_program(program, wrapper):
    command = rf'{wrapper} "C:\Program Files\Seat\{program}.exe"'
    assert cli._spawn_floor_refusal(None, command).startswith("SPAWN REFUSED")


@pytest.mark.parametrize("shell", [
    "bash", "sh", "bash.exe", "sh.exe", "/bin/bash", "/bin/sh",
    r"C:\tools\bash.exe", r"C:\tools\sh.exe",
])
@pytest.mark.parametrize("command,refused", [
    ('"litetui --model gpt-5.5"', True),
    ("'claude -p \"review the codex adapter\"'", False),
])
def test_posix_shell_wrappers_judge_the_program_not_the_prompt(shell, command, refused):
    why = cli._spawn_floor_refusal(None, f"{shell} -c {command}")
    if refused:
        assert why and why.startswith("SPAWN REFUSED")
    else:
        assert why is None


@pytest.mark.parametrize("name", [None, "T0085-Probe-Test"])
def test_placeholder_does_not_block_strict_owned_registration(launch, tmp_path, name):
    from liteharness.strict_registration import validate_available_name

    def registration_preflight():
        validate_available_name(tmp_path, name or "T0085-Probe-Test")

    code, calls, verified, elapsed = launch(name=name, before_presence=registration_preflight)
    assert code == 0 and verified[0][0] == "real-seat"
    assert not (tmp_path / "agents" / f'{calls[0]["agent_id"]}.json').exists()
    assert (tmp_path / "agents" / "real-seat.json").exists()
