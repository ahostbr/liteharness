"""A seat's ROLE must survive its session ending, the way its name does.

The sequence that lost it (measured 2026-11-12 on a real orchestrator):

    register --tier orchestrator   ->  agents/<id>.json says orchestrator
    session ends                   ->  the row is archived to retired-agents/ and deleted
    resume, same id                ->  registration finds no row and writes the DEFAULT role

The name came back (it has its own store, `names/`). The role did not, so the resumed
orchestrator was a builder to everything that looks for the orchestrator by role.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from liteharness import cli, clerk_route, config, hooks, seat_lifecycle

SEAT = "55555555-5555-4555-8555-555555555555"
AFTER_CLEAR = "66666666-6666-4666-8666-666666666666"
OTHER = "77777777-7777-4777-8777-777777777777"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: os.getpid())
    monkeypatch.setattr(seat_lifecycle, "log_path", lambda: tmp_path / "seat-lifecycle.jsonl")
    (tmp_path / "agents").mkdir()
    (tmp_path / "names").mkdir()
    return tmp_path


def session_env(ident: str, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(
        ("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI", "LITECODE"))}
    env.update(LITEHARNESS_CLI="claude-code", LITEHARNESS_MODEL="test-model",
               CLAUDE_CODE_SESSION_ID=ident, **extra)
    return env


def start_session(ident: str, source: str, **extra) -> None:
    """The real SessionStart register hook for `ident`."""
    with mock.patch.dict(os.environ, session_env(ident, **extra), clear=True):
        hooks._apply_hook_context({"session_id": ident, "source": source, "hook_event_name": "SessionStart",
                                   "transcript_path": str(Path("transcripts") / f"{ident}.jsonl")})
        hooks.register_presence()


def end_session(root: Path, ident: str) -> None:
    """The real SessionEnd hook, then the measured outcome: the row is gone and only the archive is left.
    (`deregister` keeps the row of a seat that owns a conversation, marked offline; that row still
    carries its role, so the deleted row is the case that needs proving.)"""
    with mock.patch.dict(os.environ, session_env(ident), clear=True):
        hooks.deregister()
    assert (root / "retired-agents" / f"{ident}.json").is_file(), "the session end archived nothing"
    (root / "agents" / f"{ident}.json").unlink(missing_ok=True)


def row(root: Path, ident: str) -> dict:
    return json.loads((root / "agents" / f"{ident}.json").read_text(encoding="utf-8"))


def become(ident: str, role: str) -> None:
    cli.cmd_register(ident, cli="claude-code", model="test-model", tier=role, session_pid=os.getpid())


def test_a_resumed_orchestrator_is_still_the_orchestrator_and_is_found_by_role(root, capsys):
    start_session(SEAT, "startup")
    assert row(root, SEAT)["role"] == "builder", "positive control: a fresh seat starts with the default role"
    become(SEAT, "orchestrator")
    end_session(root, SEAT)
    assert json.loads((root / "retired-agents" / f"{SEAT}.json").read_text(encoding="utf-8"))["role"] == "orchestrator"

    start_session(SEAT, "resume")

    resumed = row(root, SEAT)
    assert (resumed["role"], resumed["tier"]) == ("orchestrator", "orchestrator"), (
        f"the resumed orchestrator came back as {resumed['role']!r}")
    # Found by ROLE alone: this configuration carries no stored id and no name to fall back on.
    assert clerk_route.resolve_orchestrator({"clerk_id": "some-clerk"}) == SEAT
    assert clerk_route.is_live_orchestrator(SEAT, {"clerk_id": "some-clerk"})
    assert "role orchestrator" in capsys.readouterr().out, "the seat was told another role than its row says"


def test_it_survives_a_second_end_and_resume(root):
    """The row rebuilt at the first resume is what the second session end archives."""
    start_session(SEAT, "startup")
    become(SEAT, "orchestrator")
    for _ in range(2):
        end_session(root, SEAT)
        start_session(SEAT, "resume")
    assert row(root, SEAT)["role"] == "orchestrator"


@pytest.mark.parametrize("role", ["builder", "owner", "critic"])
def test_every_role_comes_back_as_itself_not_as_the_orchestrator(root, role):
    """CONTROL: the fix restores what the seat WAS. It promotes nobody."""
    start_session(SEAT, "startup")
    become(SEAT, role)
    end_session(root, SEAT)
    start_session(SEAT, "resume")
    assert row(root, SEAT)["role"] == role


def test_a_seat_never_seen_before_gets_the_default_role(root):
    """CONTROL: no row, no archive, a process that registered nobody else."""
    start_session(OTHER, "resume")
    assert row(root, OTHER)["role"] == "builder"


def test_the_role_the_spawner_set_outranks_the_archive(root):
    start_session(SEAT, "startup")
    become(SEAT, "orchestrator")
    end_session(root, SEAT)
    start_session(SEAT, "resume", LITEHARNESS_ROLE="critic")
    assert row(root, SEAT)["role"] == "critic"


def test_an_archive_written_before_roles_had_their_own_key_is_still_read(root):
    """Archives on disk today carry only the older `tier` word."""
    (root / "retired-agents").mkdir()
    (root / "retired-agents" / f"{SEAT}.json").write_text(json.dumps(
        {"agent_id": SEAT, "name": "Lead", "tier": "orchestrator", "cli": "claude-code"}), encoding="utf-8")
    start_session(SEAT, "resume")
    assert row(root, SEAT)["role"] == "orchestrator"


def test_an_archive_filed_under_another_id_is_not_believed(root):
    (root / "retired-agents").mkdir()
    (root / "retired-agents" / f"{SEAT}.json").write_text(json.dumps(
        {"agent_id": OTHER, "role": "orchestrator", "cli": "claude-code"}), encoding="utf-8")
    start_session(SEAT, "resume")
    assert row(root, SEAT)["role"] == "builder"


def test_a_clear_in_the_orchestrators_terminal_keeps_the_role_under_the_new_id(root):
    """/clear ends the session and starts another in the SAME process, under a new session id."""
    start_session(SEAT, "startup")
    become(SEAT, "orchestrator")
    end_session(root, SEAT)

    start_session(AFTER_CLEAR, "clear")

    assert row(root, AFTER_CLEAR)["role"] == "orchestrator"
    assert clerk_route.resolve_orchestrator({"clerk_id": "some-clerk"}) == AFTER_CLEAR


def test_a_new_id_does_not_inherit_from_another_process_that_had_the_same_pid(root):
    """CONTROL, one variable apart from the test above: the archived row is of an EARLIER process
    that happened to have this pid (a reused pid), so it says nothing about this terminal."""
    start_session(SEAT, "startup")
    become(SEAT, "orchestrator")
    end_session(root, SEAT)
    archive = root / "retired-agents" / f"{SEAT}.json"
    saved = json.loads(archive.read_text(encoding="utf-8"))
    assert saved["session_pid"] == os.getpid() and saved["session_process_started_at"]
    saved["session_process_started_at"] -= 3_600_000
    archive.write_text(json.dumps(saved), encoding="utf-8")

    start_session(AFTER_CLEAR, "clear")

    assert row(root, AFTER_CLEAR)["role"] == "builder"


def test_register_without_a_role_restores_the_same_ids_role_and_no_one_elses(root, capsys):
    start_session(SEAT, "startup")
    become(SEAT, "orchestrator")
    end_session(root, SEAT)

    # `liteharness register` with no --tier, for the id whose session ended: it was the orchestrator.
    cli.cmd_register(SEAT, cli="claude-code", model="test-model", session_pid=os.getpid())
    assert row(root, SEAT)["role"] == "orchestrator"
    # The same command naming ANOTHER id, run from inside that seat, must not hand over its role.
    with mock.patch.dict(os.environ, session_env(SEAT), clear=True):
        cli.cmd_register(OTHER, cli="claude-code", model="test-model")
    assert row(root, OTHER)["role"] == "builder"
