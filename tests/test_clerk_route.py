"""T0678 WS1: routing must be observable in the recipient's isolated maildir."""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from liteharness import config, inbox
from liteharness.clerk_route import (load_clerk_config, live_orchestrators, resolve_orchestrator,
                                     route_recipient, routing_config, _agent_record_live)

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


def orchestrator(home, agent="sentinel", **overrides):
    """A LIVE orchestrator row, owned by this process's parent: alive, and not the clerk rows' pid
    (two rows on one pid supersede each other)."""
    return presence(home, agent, **{"role": "orchestrator", "tier": "orchestrator",
                                    "session_pid": os.getppid(), **overrides})


@pytest.fixture
def sleeper():
    """One more live process of our own, for a second orchestrator row."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield child.pid
    child.kill()
    child.wait(timeout=10)


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
    orchestrator(home)  # live in every case, so each case's own reason decides the route
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

def receipts(home):
    """Every delivered message, by body: where it actually landed."""
    files = (home / ".liteharness/inbox/new").glob("*.json")
    return {m["body"]: m for m in (json.loads(f.read_text(encoding="utf-8")) for f in files)}


def test_orchestrator_restart_keeps_routing_with_the_config_untouched(home):
    """The orchestrator gets a new id on every restart. Nobody edits clerk.json; routing must follow."""
    configure(home)
    presence(home)
    orchestrator(home)
    stored = (home / ".liteharness" / "clerk.json").read_bytes()
    inbox.send("worker", "sentinel", "before the restart")

    orchestrator(home, exited_at="gone")  # the old session ends ...
    orchestrator(home, "sentinel-2")      # ... and the same seat registers under a new id
    assert resolve_orchestrator(load_clerk_config()) == "sentinel-2"
    inbox.send("worker", "sentinel-2", "after the restart")

    seen = receipts(home)
    assert (seen["before the restart"]["to"], seen["before the restart"]["original_to"]) == ("clerk", "sentinel")
    assert (seen["after the restart"]["to"], seen["after the restart"]["original_to"]) == ("clerk", "sentinel-2")
    assert (home / ".liteharness" / "clerk.json").read_bytes() == stored
    assert len(inbox.poll("clerk", include_broadcast=False)) == 2
    assert inbox.poll("sentinel-2", include_broadcast=False) == []


@pytest.mark.parametrize("registry", ["exited", "empty"])
def test_no_live_orchestrator_fails_open(home, registry):
    """With nobody to report to, the clerk takes nothing: mail lands exactly where it was addressed."""
    configure(home)
    presence(home)
    if registry == "exited":
        orchestrator(home, exited_at="gone")
    assert resolve_orchestrator(load_clerk_config()) is None
    assert routing_config("sentinel")["orchestrator_id"] is None
    inbox.send("worker", "sentinel", "to the stale id")
    inbox.send("worker", "someone-else", "to a peer")
    seen = receipts(home)
    assert seen["to the stale id"]["to"] == "sentinel"
    assert "original_to" not in seen["to the stale id"]
    assert seen["to a peer"]["to"] == "someone-else"
    assert inbox.poll("clerk", include_broadcast=False) == []


@pytest.mark.parametrize("recent", ["first", "second"])
def test_two_live_orchestrators_pick_the_most_recent_heartbeat_and_say_so(home, sleeper, caplog, recent):
    configure(home)
    presence(home)
    now = datetime.now(timezone.utc)
    older, newer = (now - timedelta(minutes=5)).isoformat(), now.isoformat()
    orchestrator(home, "first", last_seen=newer if recent == "first" else older)
    orchestrator(home, "second", session_pid=sleeper, last_seen=newer if recent == "second" else older)
    other = "second" if recent == "first" else "first"
    cfg = load_clerk_config()
    assert live_orchestrators(cfg) == [recent, other]
    with caplog.at_level(logging.WARNING, logger="liteharness.clerk_route"):
        assert resolve_orchestrator(cfg) == recent
        assert resolve_orchestrator(cfg) == recent  # same answer every time
    logged = [r for r in caplog.records if r.name == "liteharness.clerk_route"]
    assert len(logged) == 2
    text = logged[0].getMessage()
    assert "2 live orchestrators" in text and "first" in text and "second" in text
    assert text.endswith(f"using {recent}, the most recent heartbeat")
    # Mail addressed to EITHER live orchestrator is screened for the one it was addressed to.
    inbox.send("worker", "first", "for first")
    inbox.send("worker", "second", "for second")
    seen = receipts(home)
    assert (seen["for first"]["to"], seen["for first"]["original_to"]) == ("clerk", "first")
    assert (seen["for second"]["to"], seen["for second"]["original_to"]) == ("clerk", "second")


def test_one_live_orchestrator_is_not_logged(home, caplog):
    configure(home)
    orchestrator(home)
    with caplog.at_level(logging.WARNING, logger="liteharness.clerk_route"):
        assert resolve_orchestrator(load_clerk_config()) == "sentinel"
    assert [r for r in caplog.records if r.name == "liteharness.clerk_route"] == []


STALE = {"enabled": True, "orchestrator_id": "old-id", "clerk_id": "clerk",
         "direct_senders": ["my-voice-source"]}


@pytest.mark.parametrize("live_role", ["orchestrator", "builder"])
def test_a_config_written_with_a_stale_id_routes_to_the_live_orchestrator(home, live_role):
    """The file an earlier version wrote, never edited: its id names a session that ended.

    `builder` is the measured case: a resumed orchestrator can register with another role while it
    keeps its name, so the name on the stored id's old row is what finds it."""
    configure(home, STALE)
    stored = (home / ".liteharness" / "clerk.json").read_bytes()
    presence(home)
    orchestrator(home, "old-id", name="Lead", exited_at="gone")
    orchestrator(home, "new-id", name="Lead", role=live_role, tier="worker")
    presence(home, "bystander", name="Other", role="builder", session_pid=None)

    assert load_clerk_config() == STALE
    assert resolve_orchestrator(load_clerk_config()) == "new-id"
    inbox.send("worker", "new-id", "for the live one")
    inbox.send("worker", "old-id", "for the dead id")
    inbox.send("my-voice-source", "new-id", "from a direct sender")
    seen = receipts(home)
    assert (seen["for the live one"]["to"], seen["for the live one"]["original_to"]) == ("clerk", "new-id")
    assert seen["for the dead id"]["to"] == "old-id" and "original_to" not in seen["for the dead id"]
    assert seen["from a direct sender"]["to"] == "new-id"
    assert (home / ".liteharness" / "clerk.json").read_bytes() == stored


def test_the_clerk_is_never_taken_for_the_orchestrator(home):
    """Even holding the orchestrator's name or the stored id, the clerk's own seat is excluded."""
    configure(home, {**CFG, "orchestrator_id": "clerk", "orchestrator_name": "Lead"})
    presence(home, name="Lead", role="orchestrator")
    assert resolve_orchestrator(load_clerk_config()) is None
    inbox.send("worker", "clerk", "to the clerk itself")
    assert "original_to" not in receipts(home)["to the clerk itself"]


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
    orchestrator(home)  # live, so the malformed row is the only reason the mail goes direct
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
