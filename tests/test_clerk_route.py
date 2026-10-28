"""T0678 WS1: routing must be observable in the recipient's isolated maildir."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from liteharness import config, inbox
from liteharness.clerk_route import load_clerk_config, route_recipient, _agent_record_live

CFG = {"enabled": True, "orchestrator_id": "sentinel", "clerk_id": "clerk",
       "direct_senders": ["orchestrator-chat", "ryan-literec", "Theater"]}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    root = tmp_path / ".liteharness"
    root.mkdir()
    monkeypatch.setattr(config, "HARNESS_ROOT", root)
    for folder in ("new", "cur", "done", "tmp"):
        monkeypatch.setattr(inbox, "INBOX_" + folder.upper(), root / "inbox" / folder)
    return tmp_path


def configure(home, cfg=CFG):
    (home / ".liteharness" / "clerk.json").write_text(json.dumps(cfg), encoding="utf-8")


def presence(home, agent="clerk", **overrides):
    now = datetime.now(timezone.utc).isoformat()
    record = {"agent_id": agent, "session_pid": os.getpid(), "last_seen": now, "registered_at": now}
    record.update(overrides)
    path = home / ".liteharness" / "agents" / (agent + ".json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


@pytest.mark.parametrize("frm,to,direct,forwarded,cfg,live,expected", [
    ("worker", "sentinel", False, None, None, True, ("sentinel", None)),
    ("worker", "sentinel", False, None, CFG, True, ("clerk", "sentinel")),
    ("worker", "sentinel", False, None, CFG, False, ("sentinel", None)),
    ("clerk", "sentinel", False, None, CFG, True, ("sentinel", None)),
    ("worker", "sentinel", True, None, CFG, True, ("sentinel", None)),
    ("Theater", "sentinel", False, None, CFG, True, ("sentinel", None)),
    ("worker", "other", False, None, CFG, True, ("other", None)),
    ("worker", "sentinel", False, "origin", CFG, True, ("sentinel", None)),
    ("worker", "sentinel", False, None, {**CFG, "enabled": False}, True, ("sentinel", None)),
])
def test_pure_route(frm, to, direct, forwarded, cfg, live, expected):
    assert route_recipient(frm, to, direct, forwarded, cfg, lambda _: live) == expected


@pytest.mark.parametrize("raw", [None, "{broken", "[]", "null", "true", '{}',
    '{"enabled": "false"}', json.dumps({**CFG, "clerk_id": "../bad"}),
    json.dumps({**CFG, "direct_senders": "Theater"})])
def test_missing_or_invalid_config_is_off(home, raw):
    if raw is not None:
        (home / ".liteharness" / "clerk.json").write_text(raw, encoding="utf-8")
    assert load_clerk_config() is None


def test_valid_config(home):
    configure(home)
    assert load_clerk_config() == CFG


@pytest.mark.parametrize("state", ["live", "dead", "missing", "exited", "superseded", "stale", "no-owner", "reused"])
def test_presence_semantics(home, state):
    if state != "missing":
        overrides = {}
        if state == "dead":
            # Create and wait for OUR process; no live agent is killed or signalled.
            child = subprocess.Popen([sys.executable, "-c", "pass"])
            child.wait(timeout=10)
            overrides["session_pid"] = child.pid
        if state == "exited": overrides["exited_at"] = "exited"
        if state == "stale": overrides["last_seen"] = "2000-01-01T00:00:00+00:00"
        if state == "no-owner": overrides["session_pid"] = None
        if state == "reused": overrides["registered_at"] = "2000-01-01T00:00:00+00:00"
        presence(home, **overrides)
        if state == "superseded":
            presence(home, "successor", registered_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat())
    assert _agent_record_live("clerk") is (state == "live")


@pytest.mark.parametrize("case,expected", [
    ("live", "clerk"), ("dead", "sentinel"), ("absent", "sentinel"),
    ("disabled", "sentinel"), ("direct", "sentinel"), ("orchestrator-chat", "sentinel"),
    ("ryan-literec", "sentinel"), ("Theater", "sentinel"), ("clerk", "sentinel"),
    ("forward", "sentinel"), ("superseded", "sentinel"),
])
def test_cli_recipient_maildir(home, case, expected):
    if case != "absent": configure(home, {**CFG, "enabled": case != "disabled"})
    if case == "dead":
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)
        presence(home, session_pid=child.pid)
    else:
        presence(home)
    if case == "superseded":
        presence(home, "successor", registered_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat())
    sender = case if case in (*CFG["direct_senders"], "clerk") else "worker"
    body = "Original body\nUnicode: café — unchanged"
    if case == "forward":
        inbox.send(sender, "sentinel", body, forwarded_for="original-worker")
    else:
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONPATH": str(ROOT)}
        result = subprocess.run([sys.executable, "-m", "liteharness.cli", "send", "sentinel", body,
            "--from", sender, "--force", *(["--direct"] if case == "direct" else [])],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    files = list((home / ".liteharness/inbox/new").glob("*.json"))
    assert len(files) == 1
    receipt = json.loads(files[0].read_text(encoding="utf-8"))
    assert receipt["to"] == expected
    assert receipt["from"] == sender
    assert receipt["body"] == body
    assert receipt.get("original_to") == ("sentinel" if expected == "clerk" else None)
    assert receipt.get("direct", False) is (case == "direct")
    assert receipt.get("forwarded_for") == ("original-worker" if case == "forward" else None)
    assert len(inbox.poll(expected, include_broadcast=False)) == 1
    assert inbox.poll("sentinel" if expected == "clerk" else "clerk", include_broadcast=False) == []

@pytest.mark.parametrize("age,expected", [(43200, True), (43200.001, False)])
def test_name_holder_age_boundary(home, monkeypatch, age, expected):
    from liteharness import clerk_route
    now = datetime.now(timezone.utc).replace(microsecond=0)
    presence(home, last_seen=(now - timedelta(seconds=age)).isoformat())
    monkeypatch.setattr(clerk_route.time, "time", lambda: now.timestamp())
    assert _agent_record_live("clerk") is expected


@pytest.mark.parametrize("offset,expected", [
    (0, True), (-0.001, True), (-0.020, True), (-0.020001, False),
    (-1, False), (None, True),
])
def test_process_start_boundary_and_unknown_leniency(home, monkeypatch, offset, expected):
    import psutil
    started = datetime.now(timezone.utc).replace(microsecond=0)
    presence(home, registered_at=(started + timedelta(seconds=offset or 0)).isoformat())
    def create_time(_self):
        if offset is None:
            raise psutil.AccessDenied()
        return started.timestamp()
    monkeypatch.setattr(psutil.Process, "create_time", create_time)
    assert _agent_record_live("clerk") is expected

@pytest.mark.parametrize("identity", ["clerk", "unrelated"])
@pytest.mark.parametrize("malformed", [[], None])
def test_malformed_presence_still_delivers_direct(home, identity, malformed):
    configure(home)
    presence(home)
    (home / ".liteharness/agents" / f"{identity}.json").write_text(json.dumps(malformed), encoding="utf-8")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, "-m", "liteharness.cli", "send", "sentinel", "never lose this body",
        "--from", "worker", "--force"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    files = list((home / ".liteharness/inbox/new").glob("*.json"))
    assert len(files) == 1, result.stderr
    receipt = json.loads(files[0].read_text(encoding="utf-8"))
    assert receipt["to"] == "sentinel"
    assert receipt["body"] == "never lose this body"
    assert "original_to" not in receipt
    assert result.returncode == 0, result.stderr
