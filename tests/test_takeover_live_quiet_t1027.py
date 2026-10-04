"""T1027 — `register --takeover` must never evict a LIVE holder, however quiet.

MEASURED (2026-09-26, throwaway registry, this repo's main): a holder "OpenBolt"
whose session_pid was ALIVE but whose last_seen was 700 s old was evicted by
`register --takeover` as a "dead ghost", because `_agent_record_live` used a
600 s freshness bar while `is_name_taken` uses 43200 s. The same record was
"taken" to a plain register and "dead" to --takeover. Every caller that
re-registers with --takeover (Claude seats after /clear, spawn, LiteTUI) could
evict a quiet seat — the user's own included.

Run:  python -m pytest tests/test_takeover_live_quiet_t1027.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liteharness import cli, config, naming  # noqa: E402


def _ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_root", lambda: tmp_path)
    monkeypatch.setenv("LITEHARNESS_NO_ANNOUNCE", "1")
    (tmp_path / "agents").mkdir()
    (tmp_path / "names").mkdir()
    return tmp_path


@pytest.fixture
def live_pid():
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield sleeper.pid
    sleeper.kill()


@pytest.fixture
def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _holder(root: Path, agent_id: str, pid: int, quiet_s: int, registered_s: int = 0) -> None:
    """`registered_s` ago, `quiet_s` since the last beat. The sleeper pid was
    started moments ago, so a record registered BEFORE it is a reused pid to
    hooks._record_belongs_to_process; the quiet arms register "now" and only
    backdate last_seen, which is the field the freshness bar reads."""
    (root / "agents" / f"{agent_id}.json").write_text(json.dumps({
        "agent_id": agent_id, "name": "OpenBolt", "session_pid": pid,
        "last_seen": _ago(quiet_s), "registered_at": _ago(registered_s)}), encoding="utf-8")
    naming.set_override(agent_id, "OpenBolt")


def test_the_probe_scenario_a_LIVE_quiet_holder_is_KEPT(root, live_pid, capsys):
    """Was: evicted to .ghost_evicted_<date>/ and the claimant took the name."""
    _holder(root, "avery", live_pid, 700)
    cli.cmd_register("claimant", cli="litetui", name="OpenBolt", takeover=True, session_pid=live_pid)

    assert (root / "agents" / "avery.json").exists(), "a live pid was evicted as a ghost"
    assert naming.get_name("avery") == "OpenBolt"
    assert naming.get_name("claimant") != "OpenBolt"
    assert "refusing takeover" in capsys.readouterr().out


def test_a_DEAD_pid_holder_is_still_evicted(root, dead_pid, live_pid):
    _holder(root, "corpse", dead_pid, 30)
    cli.cmd_register("claimant", cli="litetui", name="OpenBolt", takeover=True, session_pid=live_pid)
    assert naming.get_name("claimant") == "OpenBolt"


def test_both_checks_now_agree_on_one_record(root, live_pid):
    """The defect was two bars. One record, one verdict."""
    _holder(root, "quiet", live_pid, 700)
    assert naming.is_name_taken("OpenBolt", exclude_id="other") == "quiet"
    assert cli._agent_record_live("quiet") is True


def test_past_the_name_holding_bar_a_live_pid_is_not_proof(root, live_pid):
    """Bounded only because a pid can be reused after its owner died."""
    _holder(root, "ancient", live_pid, naming.NAME_HELD_SECONDS + 60)
    assert cli._agent_record_live("ancient") is False
    assert naming.is_name_taken("OpenBolt", exclude_id="other") is None


def test_a_REUSED_pid_does_not_hold_a_dead_seats_name(root, live_pid):
    """Dijkstra L1: the record was written BEFORE its session_pid's process
    started, so the pid was reused after the holder died. Under the 12 h bar it
    would otherwise squat the name, and a crashed LiteTUI would come back as
    OpenBolt-2 instead of reclaiming OpenBolt."""
    _holder(root, "crashed", live_pid, quiet_s=30, registered_s=3600)
    assert naming.is_name_taken("OpenBolt", exclude_id="other") is None
    assert cli._agent_record_live("crashed") is False
    cli.cmd_register("relaunch", cli="litetui", name="OpenBolt", session_pid=live_pid)
    assert naming.get_name("relaunch") == "OpenBolt"


def test_register_records_the_resolved_backend(root):
    cli.cmd_register("seat", cli="litetui", model="o4-mini", backend="codex")
    row = json.loads((root / "agents" / "seat.json").read_text(encoding="utf-8"))
    assert row["backend"] == "codex"


def test_register_argv_parses_backend(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["liteharness", "register", "--agent-id", "seat",
                                      "--backend", "codex", "--thinking-level", "high"])
    cli.main()
    row = json.loads((root / "agents" / "seat.json").read_text(encoding="utf-8"))
    assert (row["backend"], row["thinking_level"]) == ("codex", "high")
