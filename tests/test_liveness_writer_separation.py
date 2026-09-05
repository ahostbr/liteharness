"""T130 reproductions: a proxy heartbeat cannot attest to its owner's activity."""
import json
import os
from datetime import datetime, timedelta, timezone

import psutil
import pytest

from liteharness import cli, config, hooks


@pytest.fixture
def presence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "get_agent_id", lambda: "janitor")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    # An orphan watcher has no live owning CLI ancestor to discover.
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: None)
    path = tmp_path / "agents" / "victim.json"
    path.parent.mkdir()
    dead = next(pid for pid in range(999_000, 990_000, -7) if not psutil.pid_exists(pid))

    def seed(owner, *, watcher_age=0):
        now = datetime.now(timezone.utc)
        agent_stamp = (now - timedelta(hours=2)).isoformat()
        watcher_stamp = (now - timedelta(seconds=watcher_age)).isoformat()
        path.write_text(json.dumps({
            "agent_id": "victim", "cli": "claude-code", "model": "test", "tier": "worker",
            "session_pid": owner, "watcher_pid": os.getpid(),
            "last_seen": watcher_stamp, "agent_last_seen": agent_stamp,
            "watcher_last_seen": watcher_stamp, "registered_at": agent_stamp,
        }))
        return path
    return seed, path, dead


def test_live_watcher_with_dead_owner_must_not_hold_mail(presence):
    seed, path, dead = presence
    seed(dead)
    hooks.update_heartbeat("victim", is_watcher=True)
    assert not cli._agent_record_live("victim")
    assert not hooks._a_live_watcher_is_attached("victim")


def test_orphan_watcher_must_not_recreate_purged_owner(presence):
    seed, path, dead = presence
    seed(dead)
    # Simulate registration cached while the owner was still alive. The missing
    # record path must reject this cache after that owner has died.
    hooks._LAST_PRESENCE.update(json.loads(path.read_text()))
    hooks.update_heartbeat("victim", is_watcher=True)
    assert hooks._purge_stale_agents() == 1
    assert not path.exists()
    hooks.update_heartbeat("victim", is_watcher=True)
    assert not path.exists()


def test_agent_heartbeat_cannot_refresh_a_stale_watcher(presence):
    seed, path, dead = presence
    seed(os.getpid(), watcher_age=hooks.WATCHER_FRESH_SECONDS + 30)
    assert not hooks._a_live_watcher_is_attached("victim")
    hooks.update_heartbeat("victim", is_watcher=False)
    assert not hooks._a_live_watcher_is_attached("victim")


def test_live_idle_owner_is_not_purged(presence):
    seed, path, dead = presence
    seed(os.getpid(), watcher_age=hooks.STALE_AGENT_SECONDS + 30)
    assert hooks._purge_stale_agents() == 0
    assert path.exists()


def test_each_writer_stamps_only_its_own_clock(presence):
    seed, path, dead = presence
    seed(os.getpid())
    initial = json.loads(path.read_text())
    hooks.update_heartbeat("victim", is_watcher=True)
    watched = json.loads(path.read_text())
    assert watched["agent_last_seen"] == initial["agent_last_seen"]
    assert watched["watcher_last_seen"] != initial["watcher_last_seen"]
    hooks.update_heartbeat("victim", is_watcher=False)
    acted = json.loads(path.read_text())
    assert acted["watcher_last_seen"] == watched["watcher_last_seen"]
    assert acted["agent_last_seen"] != initial["agent_last_seen"]
    assert acted["last_seen"] == acted["agent_last_seen"]


def test_live_owner_can_recover_missing_presence(presence):
    seed, path, dead = presence
    seed(os.getpid())
    old_agent_clock = json.loads(path.read_text())["agent_last_seen"]
    hooks.update_heartbeat("victim", is_watcher=True)
    path.unlink()
    hooks.update_heartbeat("victim", is_watcher=True)
    restored = json.loads(path.read_text())
    assert restored["session_pid"] == os.getpid()
    assert restored["agent_last_seen"] == old_agent_clock
    assert restored["watcher_last_seen"]


def test_legacy_clock_is_unknown_and_logged(presence):
    seed, path, dead = presence
    seed(os.getpid())
    row = json.loads(path.read_text())
    del row["watcher_last_seen"]
    path.write_text(json.dumps(row))
    assert not hooks._a_live_watcher_is_attached("victim")
    log = path.parent.parent / "identity-log.jsonl"
    event = json.loads(log.read_text().splitlines()[-1])
    assert event["reason"] == "missing-watcher-last-seen"
    assert event["decision"] == "hook-delivers"


def test_status_reader_does_not_use_agent_clock(presence, capsys):
    from liteharness.cli_scripts.codex import manual_liteharness as manual
    seed, path, dead = presence
    seed(os.getpid())
    row = json.loads(path.read_text())
    stamp = row.pop("watcher_last_seen")
    path.write_text(json.dumps(row))
    monitors = path.parent.parent / "codex_sessions" / "monitors"
    monitors.mkdir(parents=True)
    (monitors / "victim.json").write_text(json.dumps({
        "agent_id": "victim", "pid": os.getpid(), "delivery": "desktop-turn",
    }))
    status = manual.codex_monitor_status("victim")
    assert status["watcher_updated_at"] is None
    assert not status["watcher_freshness_verified"]
    manual.print_codex_monitor_status("victim")
    assert "freshness unverified" in capsys.readouterr().out
    row["watcher_last_seen"] = stamp
    path.write_text(json.dumps(row))
    status = manual.codex_monitor_status("victim")
    assert status["watcher_updated_at"] == datetime.fromisoformat(stamp).timestamp()
    assert status["watcher_freshness_verified"]


def test_compatibility_clock_is_the_later_actual_instant():
    row = {"agent_last_seen": "2026-09-05T12:00:00+01:00"}
    config.stamp_activity(row, "2026-09-05T11:30:00+00:00", is_watcher=True)
    assert row["last_seen"] == row["watcher_last_seen"]


def test_native_lock_does_not_bypass_legacy_freshness(presence, monkeypatch, capsys):
    from liteharness import inbox
    from liteharness.cli_scripts.codex import liteharness_watcher_supervisor as supervisor
    seed, path, dead = presence
    seed(os.getpid())
    row = json.loads(path.read_text())
    del row["watcher_last_seen"]
    path.write_text(json.dumps(row))
    root = path.parent.parent
    monkeypatch.setattr(config, "get_agent_id", lambda: "victim")
    monkeypatch.setattr(hooks, "_is_codex_hook_runtime", lambda: True)
    monkeypatch.setattr(supervisor, "desktop_owner_active", lambda *args: True)
    monkeypatch.setattr(hooks, "_should_check", lambda: True)
    monkeypatch.setattr(hooks, "_mark_checked", lambda: None)
    monkeypatch.setattr(hooks, "_refresh_presence_model", lambda: None)
    monkeypatch.setattr(hooks, "_maybe_cleanup", lambda: None)
    for name in ("NEW", "CUR", "DONE", "TMP"):
        folder = root / "inbox" / name.lower()
        folder.mkdir(parents=True)
        monkeypatch.setattr(inbox, f"INBOX_{name}", folder)
    message = inbox.INBOX_NEW / "probe.json"
    message.write_text(json.dumps({
        "id": "probe", "from": "sender", "to": "victim", "body": "legacy-probe",
    }))
    hooks.check_inbox()
    assert "legacy-probe" in capsys.readouterr().out
    assert not message.exists()
    assert (inbox.INBOX_DONE / "probe.json").exists()
