"""T0472: a hand-started Codex session's presence row is as complete as a registered one.

`manual_liteharness start` wrote agents/<id>.json with no registered_at, name, cwd,
registration_source or role. A registry reader that orders same-session rows by
registered_at could neither order nor ignore it. The fix is on the writer: no reader
should need a special case for this row.
"""
import json
from datetime import datetime

import pytest

from liteharness import cli, config, naming, roles
from liteharness.cli_scripts.codex import manual_liteharness as manual

AGENT = "codex-alpha0001"

# The fields every reader of the registry may rely on. Named once; the test below holds a
# row written by `liteharness register` to the same list, so it cannot drift from register.
# `cwd` is not here: `register` never writes it (only the SessionStart hook does), so it is
# asserted for the started row on its own.
REGISTRY_REQUIRED = (
    "agent_id", "name", "cli", "model", "role", "tier",
    "registered_at", "started_at", "agent_last_seen", "last_seen", "registration_source",
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(manual, "detect_process_context", lambda: {})
    for key in ("LITEHARNESS_ROLE", "LITEHARNESS_TIER", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
        monkeypatch.delenv(key, raising=False)
    workdir = tmp_path / "widgets"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return tmp_path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def missing(row):
    return [key for key in REGISTRY_REQUIRED if not row.get(key)]


def test_a_started_row_carries_the_registry_fields(root):
    row = read(manual.write_presence(AGENT))
    assert missing(row) == []
    assert datetime.fromisoformat(row["registered_at"]).tzinfo is not None
    assert row["name"] == naming.get_name(AGENT)
    assert row["cwd"] == str(root / "widgets")
    assert row["registration_source"] == "startup"
    assert row["cli"] == manual.CODEX_MANUAL_CLI


def test_the_name_comes_from_the_naming_module(root):
    naming.set_override(AGENT, "Alpha")
    assert read(manual.write_presence(AGENT))["name"] == "Alpha"


def test_a_hand_started_session_is_never_an_orchestrator(root, monkeypatch):
    monkeypatch.setenv("LITEHARNESS_ROLE", "orchestrator")  # inherited from the launching shell
    monkeypatch.setenv("LITEHARNESS_TIER", "orchestrator")
    row = read(manual.write_presence(AGENT))
    assert roles.row_role(row, "") == roles.DEFAULT_ROLE != "orchestrator"
    assert row["role"] == roles.DEFAULT_ROLE and row["tier"] == roles.LEGACY_TIER[roles.DEFAULT_ROLE]


def test_a_second_start_keeps_registered_at_and_the_watcher_fields(root):
    path = manual.write_presence(AGENT)
    first = read(path)
    config.merge_presence_fields(path, {
        "watcher_last_seen": "2026-01-01T00:00:00+00:00", "watcher_pid": 4242, "watcher_ppid": 4141})
    manual.write_presence(AGENT)
    again = read(path)
    assert again["registered_at"] == first["registered_at"]
    assert again["started_at"] == first["started_at"]
    assert (again["watcher_last_seen"], again["watcher_pid"], again["watcher_ppid"]) == (
        "2026-01-01T00:00:00+00:00", 4242, 4141)


def test_an_old_shape_row_is_completed_by_the_next_start(root):
    path = root / "agents" / f"{AGENT}.json"
    path.parent.mkdir()
    path.write_text(json.dumps({
        "agent_id": AGENT, "model": "unknown", "project": "x",
        "started_at": "2026-01-01T00:00:00+00:00", "agent_last_seen": "2026-01-01T00:00:00+00:00",
        "pid": 1, "cli": manual.CODEX_MANUAL_CLI, "surface": "terminal"}))
    row = read(manual.write_presence(AGENT))
    assert missing(row) == []
    assert row["started_at"] == "2026-01-01T00:00:00+00:00"


def test_an_explicit_role_and_registration_source_are_kept(root):
    path = manual.write_presence(AGENT)
    config.merge_presence_fields(path, {"role": "scout", "tier": "thinker", "registration_source": "takeover"})
    row = read(manual.write_presence(AGENT))
    assert (row["role"], row["tier"], row["registration_source"]) == ("scout", "thinker", "takeover")


def test_the_heartbeat_touches_only_the_agent_clock(root):
    path = manual.write_presence(AGENT)
    before = read(path)
    manual.update_presence_heartbeat(AGENT)
    after = read(path)
    changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    assert changed <= {"agent_last_seen", "last_seen"}


def test_a_registered_row_and_a_started_row_pass_the_same_check(root, capsys):
    cli.cmd_register("registered-alpha", cli="claude-code", model="test-model")
    capsys.readouterr()
    registered = read(root / "agents" / "registered-alpha.json")
    assert missing(registered) == []
    assert missing(read(manual.write_presence(AGENT))) == []
