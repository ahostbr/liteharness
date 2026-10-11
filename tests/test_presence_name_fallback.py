"""Name lookups share override > presence > deterministic fallback precedence."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from liteharness import cli, config, hooks, naming, terminal_automation

AGENT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
GENERATED = "SourCoil"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "agents").mkdir()
    return tmp_path


def presence(root, agent_id=AGENT, **fields):
    row = {"agent_id": agent_id, "name": "Alpha", "cli": "litetui",
           "registration_source": "takeover",
           "last_seen": datetime.now(timezone.utc).isoformat()}
    row.update(fields)
    path = root / "agents" / f"{agent_id}.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    return path


def test_presence_name_without_override(root):
    presence(root)
    assert naming.get_name(AGENT) == "Alpha"
    assert not (root / "names").exists()


def test_override_wins_without_refreshing_last_seen(root):
    presence(root)
    naming.set_override(AGENT, "Beta")
    path = root / "names" / AGENT
    os.utime(path, (1_000_000_000, 1_000_000_000))
    before = path.stat().st_mtime_ns
    assert naming.get_name(AGENT) == "Beta"
    assert path.stat().st_mtime_ns == before


def test_missing_record_keeps_exact_generated_name(root):
    assert naming.get_name(AGENT) == GENERATED


@pytest.mark.parametrize("raw", ["{broken", "[]", "null", '"Alpha"', "17"])
def test_corrupt_or_non_object_presence_falls_back(root, raw):
    (root / "agents" / f"{AGENT}.json").write_text(raw, encoding="utf-8")
    assert naming.get_name(AGENT) == GENERATED


@pytest.mark.parametrize("name", ["", " \t\n", None, 17, ["Alpha"]])
def test_blank_or_non_string_presence_name_falls_back(root, name):
    presence(root, name=name)
    assert naming.get_name(AGENT) == GENERATED


def test_missing_name_falls_back(root):
    (root / "agents" / f"{AGENT}.json").write_text("{}", encoding="utf-8")
    assert naming.get_name(AGENT) == GENERATED


def test_presence_name_is_trimmed(root):
    presence(root, name=" Alpha \t")
    assert naming.get_name(AGENT) == "Alpha"


@pytest.mark.parametrize("directory", ["agents", "names"])
def test_invalid_utf8_falls_back(root, directory):
    path = root / directory / (f"{AGENT}.json" if directory == "agents" else AGENT)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"\xff")
    assert naming.get_name(AGENT) == GENERATED


def test_unreadable_presence_falls_back(root, monkeypatch):
    path = presence(root)
    real_open = os.open

    def denied(candidate, *args, **kwargs):
        if Path(candidate) == path:
            raise PermissionError("record unavailable")
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    assert naming.get_name(AGENT) == GENERATED


def test_unavailable_root_falls_back(root, monkeypatch):
    def unavailable():
        raise RuntimeError("root unavailable")

    monkeypatch.setattr(config, "get_root", unavailable)
    assert naming.get_name(AGENT) == GENERATED


def test_lookup_reads_current_record_without_writing_or_caching(root):
    path = presence(root, name="Alpha", superseded_by=OTHER)
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert naming.get_name(AGENT) == "Alpha"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    presence(root, name="Beta")
    assert naming.get_name(AGENT) == "Beta"
    assert not (root / "names").exists()


def test_name_collision_lookup_agrees_with_presence(root):
    presence(root)
    assert naming.is_name_taken("alpha") == AGENT
    assert naming.is_name_taken("alpha", exclude_id=AGENT) is None
    naming.set_override(AGENT, "Beta")
    assert naming.is_name_taken("alpha") is None
    assert naming.is_name_taken("beta") == AGENT
    presence(root, exited_at=datetime.now(timezone.utc).isoformat())
    assert naming.is_name_taken("beta") is None


def test_discover_prints_litetui_presence_name(root, monkeypatch, capsys):
    presence(root, model="test-model")
    monkeypatch.setattr(terminal_automation, "list_panes", lambda: [])
    cli.cmd_discover()
    output = capsys.readouterr().out
    assert f"[active] Alpha ({AGENT})" in output
    assert "litetui/test-model" in output
    assert GENERATED not in output


def test_discover_superseded_summary_keeps_each_records_name(root, monkeypatch, capsys):
    presence(root, session_pid=12345, registered_at="2026-01-01T00:00:00+00:00")
    presence(root, OTHER, name="Beta", session_pid=12345,
             registered_at="2026-01-02T00:00:00+00:00")
    monkeypatch.setattr(hooks, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(terminal_automation, "list_panes", lambda: [])
    cli.cmd_discover()
    output = capsys.readouterr().out
    assert "Alpha/12345" in output
    assert f"[active] Beta ({OTHER})" in output
    assert f"[active] Alpha ({AGENT})" not in output
