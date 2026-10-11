"""T0484: the closed-app job runner must name a model and effort when it starts `claude`.

`claude -p ...` launched with no --model boots the model in the user's settings.json at the
configured effort, which is how a nightly job ran on the most expensive pair. T0471 gave
`liteharness spawn` a configured default (spawn_default_model / spawn_default_effort, or the
LITEHARNESS_SPAWN_MODEL / LITEHARNESS_SPAWN_EFFORT variables); the tick runner reads the same
resolver, lets the job's own model / effort win, and refuses a bad value instead of falling
back to an unpinned launch. `exec_argv` (the tests / ops override) is never touched.

The job's own model is passed to claude as written (the app runner does the same) after a plain
safety check; only the configured default goes through the strict spawn resolver. The default is
read only for a field the job leaves unset, and a config file that exists but cannot be parsed
is an error, never "nothing configured".
"""
import json
import sqlite3
import sys
import threading
import types
from datetime import datetime

import pytest

from liteharness import config, librarian_tick, occurrences

JOB_ID = "nightly-job"
NOW = datetime(2026, 8, 25, 12, 0)
SLOT = "2026-08-25T03:30"
BARE = ["claude", "-p", "/ls-librarian", "--permission-mode", "bypassPermissions"]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """A tick against tmp roots with subprocess.run recorded, never executed."""
    for var in ("LITEHARNESS_SPAWN_MODEL", "LITEHARNESS_SPAWN_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path / "harness")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "harness" / "config.json")
    launches: list[list[str]] = []
    exit_code = [0]

    def fake_run(argv, **_kw):
        launches.append(list(argv))
        return types.SimpleNamespace(returncode=exit_code[0])

    monkeypatch.setattr(librarian_tick.subprocess, "run", fake_run)

    schedules = tmp_path / "schedules.json"
    db = tmp_path / "occurrences.sqlite"

    def write_job(**action_extra):
        schedules.write_text(json.dumps({"jobs": [{
            "id": JOB_ID, "enabled": True,
            "schedule": {"type": "cron", "expression": "30 3 * * *"},
            "action": {"type": "prompt", "prompt": "/ls-librarian",
                       "workdir": str(tmp_path), **action_extra},
        }]}), encoding="utf-8")

    def write_config(**values):
        config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.CONFIG_PATH.write_text(json.dumps(values), encoding="utf-8")

    def write_config_text(text):
        config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.CONFIG_PATH.write_text(text, encoding="utf-8")

    def tick(**kw):
        return librarian_tick.run_tick(
            JOB_ID, db_path=db, schedules_path=schedules, now=NOW,
            heartbeat_seconds=0.2, ttl_seconds=5.0, **kw)

    def ledger():
        conn = occurrences.open_ledger(db)
        try:
            return occurrences.status(conn, JOB_ID, SLOT)
        finally:
            conn.close()

    write_job()
    return types.SimpleNamespace(
        launches=launches, write_job=write_job, write_config=write_config,
        write_config_text=write_config_text, tick=tick, ledger=ledger, exit_code=exit_code)


def test_configured_model_and_effort_are_appended(rig):
    rig.write_config(spawn_default_model="claude-sonnet-5-5[1m]", spawn_default_effort="high")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "claude-sonnet-5-5[1m]", "--effort", "high"]]


def test_an_alias_is_resolved_the_way_spawn_resolves_it(rig):
    rig.write_config(spawn_default_model="sonnet")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "claude-sonnet-5-5"]]


def test_the_jobs_own_model_and_effort_beat_the_default(rig):
    rig.write_config(spawn_default_model="claude-sonnet-5-5", spawn_default_effort="high")
    rig.write_job(model="claude-haiku-4-5-20251001", effort="low")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "claude-haiku-4-5-20251001", "--effort", "low"]]


def test_the_environment_beats_the_config_file(rig, monkeypatch):
    rig.write_config(spawn_default_model="claude-sonnet-5-5", spawn_default_effort="high")
    monkeypatch.setenv("LITEHARNESS_SPAWN_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("LITEHARNESS_SPAWN_EFFORT", "medium")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "claude-haiku-4-5-20251001", "--effort", "medium"]]


def test_nothing_configured_leaves_the_argv_exactly_as_it_was(rig):
    assert rig.tick() == 0
    assert rig.launches == [BARE]


def test_exec_argv_is_never_given_the_flags(rig):
    rig.write_config(spawn_default_model="claude-sonnet-5-5", spawn_default_effort="high")
    rig.write_job(model="claude-haiku-4-5-20251001", effort="low")
    assert rig.tick(exec_argv=["marker", "--x"]) == 0
    assert rig.launches == [["marker", "--x"]]


def test_exec_argv_ignores_a_bad_default_too(rig):
    rig.write_config(spawn_default_model="not a model", spawn_default_effort="loud")
    assert rig.tick(exec_argv=["marker"]) == 0
    assert rig.launches == [["marker"]]


@pytest.mark.parametrize("config_values, job_action", [
    ({"spawn_default_model": "not a model"}, {}),
    ({"spawn_default_effort": "loud"}, {}),
    ({"spawn_default_model": 5}, {}),
    ({"spawn_default_model": "claude-sonnet-5-5"}, {"model": "not a model"}),
    ({"spawn_default_model": "claude-sonnet-5-5"}, {"effort": "loud"}),
    ({"spawn_default_model": "claude-sonnet-5-5"}, {"model": 7}),
])
def test_a_bad_value_fails_loudly_and_starts_nothing(rig, capsys, config_values, job_action):
    rig.write_config(**config_values)
    rig.write_job(**job_action)
    assert rig.tick() == 1
    assert rig.launches == [], "claude was started with a bad model or effort"
    assert "spawn-failed" in capsys.readouterr().err
    row = rig.ledger()
    assert row["completed_at"] and row["outcome"].startswith("spawn-failed"), row


def test_a_failed_tick_does_not_rerun_the_slot(rig):
    rig.write_config(spawn_default_model="not a model")
    assert rig.tick() == 1
    rig.write_config(spawn_default_model="claude-sonnet-5-5")
    assert rig.tick() == 0  # the slot is history now, like any other spawn-failed
    assert rig.launches == []


# ── R1: a config file that exists but cannot be parsed is an error, a missing one is not ─────

@pytest.mark.parametrize("text, words", [
    ("{not json", ("cannot be read as JSON",)),
    ("", ("cannot be read as JSON",)),
    ("﻿{}", ("cannot be read as JSON",)),  # a notepad-style BOM: config.load() reads it as {}
    ("[1, 2]", ("must hold a JSON object",)),
    ('"sonnet"', ("must hold a JSON object",)),
])
def test_an_unusable_config_file_is_refused_naming_the_file_and_key(rig, capsys, text, words):
    rig.write_config_text(text)
    assert rig.tick() == 1
    assert rig.launches == [], "claude was started although the default could not be read"
    err = capsys.readouterr().err
    assert str(config.CONFIG_PATH) in err and "spawn_default_model" in err, err
    assert all(w in err for w in words), err
    row = rig.ledger()
    assert row["completed_at"] and row["outcome"].startswith("spawn-failed"), row


def test_a_config_path_that_cannot_be_read_is_refused_naming_the_file_and_key(rig, capsys):
    config.CONFIG_PATH.mkdir(parents=True)  # a directory where the file should be
    assert rig.tick() == 1
    assert rig.launches == []
    err = capsys.readouterr().err
    assert str(config.CONFIG_PATH) in err and "spawn_default_model" in err, err
    assert "cannot be read" in err and "Traceback" not in err, err
    assert "as JSON" not in err, "an OS read error was reported as a JSON problem: " + err
    row = rig.ledger()
    assert row["completed_at"] and row["outcome"].startswith("spawn-failed"), row


def test_a_missing_config_file_is_the_nothing_configured_case(rig):
    assert not config.CONFIG_PATH.exists()
    assert rig.tick() == 0
    assert rig.launches == [BARE]


# ── R2: the job's own model is passed as written; only the default is resolved ────────────────

@pytest.mark.parametrize("model", [
    "sonnet[1m]", "opusplan", "default", "sonnet", "claude-opus-5-5[1m]",
    "claude-haiku-4-5-20251001", "vendor.claude-x-1:0", "org/model@v1+x_y",
])
def test_the_jobs_own_model_is_passed_verbatim(rig, model):
    rig.write_config(spawn_default_model="claude-haiku-4-5-20251001")
    rig.write_job(model=model)
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", model]]


# claude may be a .cmd shim on Windows, where cmd.exe reads these as command syntax.
SHELL_METACHARS = list("&|;\"'`$%^<>()!*?{}~#\\ \t") + ["\x00", "\x1f", "\x7f", "é", " "]


@pytest.mark.parametrize("model", [
    "two words", "bad\nline", "--dangerously-skip-permissions", "-m", ".hidden", "[1m]",
    *[f"a{ch}echo{ch}X" for ch in SHELL_METACHARS],
    *[f"model{ch}" for ch in SHELL_METACHARS if not ch.isspace()],
])
def test_the_jobs_own_model_must_pass_the_allowlist(rig, capsys, model):
    rig.write_job(model=model)
    assert rig.tick() == 1
    assert rig.launches == [], "claude was started with a model outside the allowlist"
    err = capsys.readouterr().err
    assert "spawn-failed" in err and len(err.strip().splitlines()) == 1, err
    assert rig.ledger()["outcome"].startswith("spawn-failed")


def test_the_default_model_still_goes_through_the_strict_resolver(rig):
    rig.write_config(spawn_default_model="opusplan")  # fine as a job value, not as the default
    assert rig.tick() == 1
    assert rig.launches == []


def test_the_jobs_effort_is_still_checked_against_the_known_levels(rig):
    rig.write_config(spawn_default_model="claude-sonnet-5-5")
    rig.write_job(effort="loud")
    assert rig.tick() == 1
    assert rig.launches == []


# ── R3: the unpinned launch is announced on stderr and in the ledger; blanks fall through ────

def test_an_unpinned_launch_says_so_on_stderr_and_in_the_ledger(rig, capsys):
    assert rig.tick() == 0
    captured = capsys.readouterr()
    assert "Note: no model" in captured.err and "Note:" not in captured.out
    row = rig.ledger()
    assert row["outcome"].startswith("success") and "no model" in row["outcome"], row
    assert "\n" not in row["outcome"]


def test_a_pinned_launch_records_a_plain_success(rig, capsys):
    rig.write_config(spawn_default_model="claude-sonnet-5-5")
    assert rig.tick() == 0
    assert "no model" not in capsys.readouterr().err
    assert rig.ledger()["outcome"] == "success"


def test_a_failing_unpinned_run_is_not_recorded_as_a_success(rig):
    rig.exit_code[0] = 1
    assert rig.tick() == 1
    assert rig.ledger()["outcome"] == "exit-1 (unpinned)"


@pytest.mark.parametrize("blank",["", "   ", "\t"])
def test_a_blank_job_model_or_effort_falls_through_to_the_default(rig, blank):
    rig.write_config(spawn_default_model="claude-sonnet-5-5", spawn_default_effort="high")
    rig.write_job(model=blank, effort=blank)
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "claude-sonnet-5-5", "--effort", "high"]]


def test_a_failure_is_one_line_in_the_ledger_and_whole_on_stderr(rig, capsys):
    rig.write_config(spawn_default_model="not a model")  # the resolver's refusal runs to several lines
    assert rig.tick() == 1
    assert "Accepted aliases" in capsys.readouterr().err
    outcome = rig.ledger()["outcome"]
    assert outcome.startswith("spawn-failed") and "\n" not in outcome, outcome


# ── N1: the default is read only for a field the job leaves unset ────────────────────────────

def test_a_fully_pinned_job_runs_when_the_default_is_bad(rig):
    rig.write_config(spawn_default_model="not a model", spawn_default_effort="loud")
    rig.write_job(model="sonnet[1m]", effort="low")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "sonnet[1m]", "--effort", "low"]]


def test_a_fully_pinned_job_runs_when_the_config_file_is_unusable(rig):
    rig.write_config_text("{not json")
    rig.write_job(model="sonnet[1m]", effort="low")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "sonnet[1m]", "--effort", "low"]]


def test_a_half_pinned_job_takes_the_other_field_from_the_default(rig):
    rig.write_config(spawn_default_model="claude-sonnet-5-5", spawn_default_effort="high")
    rig.write_job(effort="low")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "claude-sonnet-5-5", "--effort", "low"]]


def test_a_job_that_pins_only_the_model_takes_the_effort_from_the_default(rig):
    rig.write_config(spawn_default_model="claude-sonnet-5-5", spawn_default_effort="high")
    rig.write_job(model="opusplan")
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", "opusplan", "--effort", "high"]]


# ── T0486 N1: the broad except around _model_flags catches Exception, never an interrupt ────────

def _raise(exc):
    def raiser(_action):
        raise exc
    return raiser


def test_an_unexpected_exception_from_model_flags_fails_the_slot_and_starts_nothing(
        rig, monkeypatch, capsys):
    monkeypatch.setattr(librarian_tick, "_model_flags", _raise(RuntimeError("boom")))
    assert rig.tick() == 1
    assert rig.launches == [], "claude was started although the model flags could not be built"
    err = capsys.readouterr().err
    assert "Traceback" not in err and "spawn-failed" in err, err
    row = rig.ledger()
    assert row["completed_at"] and row["outcome"].startswith("spawn-failed"), row


def test_a_failed_lazy_import_in_model_flags_fails_the_slot_and_starts_nothing(rig, monkeypatch):
    monkeypatch.setitem(sys.modules, "liteharness.cli", None)  # `from .cli import ...` now raises
    assert rig.tick() == 1
    assert rig.launches == []
    row = rig.ledger()
    assert row["completed_at"] and row["outcome"].startswith("spawn-failed"), row


def test_a_keyboard_interrupt_from_model_flags_is_not_swallowed(rig, monkeypatch):
    monkeypatch.setattr(librarian_tick, "_model_flags", _raise(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rig.tick()
    assert rig.launches == []


# ── T0486 N2: the allowlist's first character is a letter or digit, ASCII only ────────────────

@pytest.mark.parametrize("model", ["_x", "éa", "Éa", "٣a", ".x", "/x", "[x]", "@x", ":x", "+x", "-x"])
def test_a_model_may_not_start_with_anything_but_an_ascii_letter_or_digit(rig, model):
    rig.write_job(model=model)
    assert rig.tick() == 1
    assert rig.launches == []
    assert rig.ledger()["outcome"].startswith("spawn-failed")


@pytest.mark.parametrize("model", ["x", "9", "9a", "A", "a_b", "a.b"])
def test_a_model_starting_with_an_ascii_letter_or_digit_is_accepted_as_written(rig, model):
    rig.write_job(model=model)
    assert rig.tick() == 0
    assert rig.launches == [BARE + ["--model", model]]


# ── T0486 N3: the recorded failure says what failed, never an empty text or "None" ────────────

def test_an_unexpected_exception_is_recorded_with_its_type(rig, monkeypatch, capsys):
    monkeypatch.setattr(librarian_tick, "_model_flags", _raise(RuntimeError("boom")))
    assert rig.tick() == 1
    assert rig.ledger()["outcome"] == "spawn-failed: RuntimeError: boom"
    assert "spawn-failed: RuntimeError: boom" in capsys.readouterr().err


@pytest.mark.parametrize("exc", [RuntimeError(), RuntimeError(""), RuntimeError("  \n "), ValueError()])
def test_an_exception_without_a_message_is_recorded_as_its_type(rig, monkeypatch, exc):
    monkeypatch.setattr(librarian_tick, "_model_flags", _raise(exc))
    assert rig.tick() == 1
    assert rig.ledger()["outcome"] == f"spawn-failed: {type(exc).__name__}"


@pytest.mark.parametrize("exit_args", [(), (None,), ("",), ("  ",)])
def test_a_refusal_without_a_message_is_recorded_as_such(rig, monkeypatch, capsys, exit_args):
    monkeypatch.setattr(librarian_tick, "_model_flags", _raise(SystemExit(*exit_args)))
    assert rig.tick() == 1
    assert rig.ledger()["outcome"] == "spawn-failed: refused without a message"
    err = capsys.readouterr().err
    assert "spawn-failed: refused without a message" in err and "None" not in err, err


def test_a_multi_line_unexpected_exception_is_one_line_in_the_ledger_and_whole_on_stderr(
        rig, monkeypatch, capsys):
    monkeypatch.setattr(librarian_tick, "_model_flags", _raise(RuntimeError("first\nsecond")))
    assert rig.tick() == 1
    assert rig.ledger()["outcome"] == "spawn-failed: RuntimeError: first"
    assert "second" in capsys.readouterr().err


# ── T0486 N5: a non-zero exit says whether the run was pinned ─────────────────────────────────

def test_a_failing_pinned_run_records_a_plain_exit_code(rig):
    rig.write_config(spawn_default_model="claude-sonnet-5-5")
    rig.exit_code[0] = 3
    assert rig.tick() == 1
    assert rig.ledger()["outcome"] == "exit-3"


def test_a_failing_unpinned_run_records_the_exit_code_and_that_it_was_unpinned(rig, capsys):
    rig.exit_code[0] = 3
    assert rig.tick() == 1
    outcome = rig.ledger()["outcome"]
    assert outcome == "exit-3 (unpinned)" and "\n" not in outcome, outcome
    assert "completed: exit-3 (unpinned)" in capsys.readouterr().out


# ── T0486 N6: any OSError from starting claude completes the slot, with nothing left running ───

def _refuse_to_start(monkeypatch, exc):
    def fake_run(argv, **_kw):
        raise exc
    monkeypatch.setattr(librarian_tick.subprocess, "run", fake_run)


def _track_ledger_connections(monkeypatch):
    opened: list[sqlite3.Connection] = []
    real = occurrences.open_ledger

    def tracking(*a, **kw):
        conn = real(*a, **kw)
        opened.append(conn)
        return conn

    monkeypatch.setattr(occurrences, "open_ledger", tracking)
    return opened


@pytest.mark.parametrize("exc, text", [
    (PermissionError(13, "Permission denied"), "spawn-failed: PermissionError: "),
    (OSError(193, "%1 is not a valid Win32 application"), "spawn-failed: OSError: "),
    (FileNotFoundError(2, "No such file or directory"), "spawn-failed: FileNotFoundError: "),
    (OSError("first\nsecond"), "spawn-failed: OSError: first"),
])
def test_an_os_refusal_to_start_claude_completes_the_slot_on_one_line(
        rig, monkeypatch, capsys, exc, text):
    _refuse_to_start(monkeypatch, exc)
    assert rig.tick() == 1  # a Traceback would be an exception out of tick()
    row = rig.ledger()
    assert row["completed_at"] and row["outcome"].startswith(text), row
    assert "\n" not in row["outcome"] and str(exc.args[-1]).splitlines()[0] in row["outcome"], row
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err and f"completed: {row['outcome']}" in captured.out


def test_an_os_refusal_to_start_claude_leaves_no_thread_and_no_open_connection(rig, monkeypatch):
    opened = _track_ledger_connections(monkeypatch)
    before = set(threading.enumerate())
    _refuse_to_start(monkeypatch, PermissionError(13, "Permission denied"))
    assert rig.tick() == 1
    assert set(threading.enumerate()) <= before, "the heartbeat thread outlived the tick"
    assert len(opened) == 2, "expected the tick's connection and the heartbeat's"
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("select 1")


def test_a_slot_completed_by_an_os_refusal_is_not_rerun(rig, monkeypatch):
    _refuse_to_start(monkeypatch, PermissionError(13, "Permission denied"))
    assert rig.tick() == 1
    assert rig.tick() == 0  # the slot is history, like any other spawn-failed
