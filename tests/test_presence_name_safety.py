"""Presence-name fallback stays contained, bounded, and subordinate to refusals."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from liteharness import announce, cli, config, naming, seat_lifecycle

AGENT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
RECORD_LIMIT = 16 * 1024
NAME_LIMIT = 128


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(seat_lifecycle, "log_path", lambda: tmp_path / "lifecycle.jsonl")
    monkeypatch.setattr(announce, "announce_registration", lambda *args, **kwargs: [])
    (tmp_path / "agents").mkdir()
    return tmp_path


def record(root, agent_id=AGENT, **fields):
    data = {"agent_id": agent_id, "name": "Alpha",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "registered_at": datetime.now(timezone.utc).isoformat()}
    data.update(fields)
    path = root / "agents" / f"{agent_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("takeover", [False, True])
@pytest.mark.parametrize("previous", ["exited", "stale"])
def test_returning_claimant_cannot_reclaim_a_refused_name(root, capsys, takeover, previous):
    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    record(root, last_seen=stale, **({"exited_at": stale} if previous == "exited" else {}))
    holder = record(root, OTHER, session_pid=os.getpid())
    holder_before = holder.read_bytes()
    assert not (root / "names" / AGENT).exists()

    cli.cmd_register(AGENT, cli="litetui", name="Alpha", takeover=takeover)

    row = json.loads((root / "agents" / f"{AGENT}.json").read_text(encoding="utf-8"))
    assert row["name"] == naming.generate_name(AGENT) != "Alpha"
    assert "exited_at" not in row
    assert naming.get_name(AGENT) == row["name"]
    assert holder.read_bytes() == holder_before
    assert naming.is_name_taken("Alpha", exclude_id=AGENT) == OTHER
    assert "Picking a different name" in capsys.readouterr().out


@pytest.mark.parametrize("agent_id", ["", ".", "..", "../other", "..\\other", "/other",
                                      "\\other", "Z:other", "Z:/other", "Z:\\other",
                                      "\\\\host\\share", "name:stream", "bad\x00id",
                                      "name/child", "name\\child", "name.", "name "])
def test_unsafe_ids_do_not_attempt_name_file_lookup(root, monkeypatch, agent_id):
    overrides = []
    monkeypatch.setattr(naming, "get_override", lambda ident: overrides.append(ident))
    assert naming.get_name(agent_id) == naming.generate_name(agent_id)
    assert overrides == []


@pytest.mark.parametrize("kind", ["parent", "backslash_parent", "absolute", "empty"])
def test_external_or_empty_id_record_cannot_supply_a_name(root, kind):
    (root / "other.json").write_text('{"name":"Alpha"}', encoding="utf-8")
    (root / "agents" / ".json").write_text('{"name":"Alpha"}', encoding="utf-8")
    agent_id = {"parent": "../other", "backslash_parent": "..\\other",
                "absolute": str(root / "other"), "empty": ""}[kind]
    assert naming.get_name(agent_id) == naming.generate_name(agent_id)


@pytest.mark.parametrize("agent_id", ["codex-alpha0001", "seat_2", "canvas-123-456"])
def test_valid_non_uuid_ids_still_use_presence(root, agent_id):
    record(root, agent_id)
    assert naming.get_name(agent_id) == "Alpha"


def test_directory_is_not_a_presence_record(root):
    (root / "agents" / f"{AGENT}.json").mkdir()
    assert naming.get_name(AGENT) == naming.generate_name(AGENT)


@pytest.mark.parametrize("external", [False, True])
def test_presence_symlinks_are_not_followed(root, external):
    target = (root if external else root / "agents") / "target.json"
    target.write_text('{"name":"Alpha"}', encoding="utf-8")
    link = root / "agents" / f"{AGENT}.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    assert naming.get_name(AGENT) == naming.generate_name(AGENT)


def test_oversized_record_is_not_a_name_source(root):
    record(root, padding="x" * RECORD_LIMIT)
    assert naming.get_name(AGENT) == naming.generate_name(AGENT)


def test_oversized_presence_name_is_not_accepted(root):
    record(root, name="a" * (NAME_LIMIT + 1))
    assert naming.get_name(AGENT) == naming.generate_name(AGENT)


def test_record_and_name_at_the_limits_are_accepted(root):
    raw = json.dumps({"name": "a" * NAME_LIMIT}).encode("utf-8")
    raw += b" " * (RECORD_LIMIT - len(raw))
    (root / "agents" / f"{AGENT}.json").write_bytes(raw)
    assert naming.get_name(AGENT) == "a" * NAME_LIMIT


def test_override_does_not_open_the_presence_file(root, monkeypatch):
    path = record(root)
    naming.set_override(AGENT, "Beta")
    opened = []
    real_path_open = Path.open
    real_os_open = os.open

    def path_open(candidate, *args, **kwargs):
        if candidate == path:
            opened.append(candidate)
        return real_path_open(candidate, *args, **kwargs)

    def os_open(candidate, *args, **kwargs):
        if Path(candidate) == path:
            opened.append(candidate)
        return real_os_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(os, "open", os_open)
    assert naming.get_name(AGENT) == "Beta"
    assert opened == []
