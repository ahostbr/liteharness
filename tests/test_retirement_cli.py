"""Producer uses fixture identity/file/mail only, no live bridge/process actions."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from liteharness import inbox, retirement as r, retirement_cli as c, retirement_handoff as h


@pytest.fixture
def producer(tmp_path, monkeypatch):
    home = tmp_path / "own"
    home.mkdir()
    handoff = home / "handoff.md"
    handoff.write_text("Next: verify task proof. No work running.\n", encoding="utf-8")
    for field in ("NEW", "CUR", "DONE", "TMP"):
        directory = tmp_path / "mail" / field.lower()
        directory.mkdir(parents=True)
        monkeypatch.setattr(inbox, "INBOX_" + field, directory)
    monkeypatch.setattr(inbox, "INBOX_ROOT", tmp_path / "mail")
    for key in ("LITEHARNESS_AGENT_ID", "LITESUITE_AGENT_ID"):
        monkeypatch.delenv(key, raising=False)
    identity = r.SeatIdentity("agent", "seat", 20, 2000,
        r.ProcessIdentity(10, 0, 1000), 5000, "registry", "leader")
    registry = {"agent": {"agent_id": "agent", "cwd": str(home), "cli": "litetui"},
                "leader": {"agent_id": "leader"}}
    monkeypatch.setattr(c, "read_registry", lambda: registry)
    monkeypatch.setattr(c, "bind_caller", lambda *a, **kw: identity)
    monkeypatch.setattr(c, "owned_handoff_paths", lambda *a: ((home,), handoff))
    calls = []

    def bridge(method, path):
        calls.append((method, path))
        assert (method, path) == ("GET", "/pty/list")
        return {"sessions": []}
    return home, handoff, registry, identity, calls, bridge


def test_producer_real_persisted_receipt_links_existing_handoff(producer):
    home, handoff, _, _, calls, bridge = producer
    before = handoff.read_bytes()
    result = c.ack_idle(None, bridge)
    assert result["ok"] is True
    assert result["handoff"] == {"path": str(handoff), "size": len(before),
                                  "sha256": hashlib.sha256(before).hexdigest()}
    assert calls == [("GET", "/pty/list")] * 2
    receipt, = c.persisted_receipts()
    assert receipt["id"] == result["receiptId"]
    assert (receipt["from"], receipt["to"]) == ("agent", "leader")
    body = json.loads(receipt["body"])
    assert body["handoffWritten"] is True and body["handoff"] == result["handoff"]
    assert handoff.read_bytes() == before and list(home.iterdir()) == [handoff]


def test_no_default_requires_flag_before_ack(producer, monkeypatch):
    home, _, _, _, _, bridge = producer
    monkeypatch.setattr(c, "owned_handoff_paths", lambda *a: ((home,), None))
    with pytest.raises(r.RetirementRefused, match="handoff_required"):
        c.ack_idle(None, bridge)
    assert not c.persisted_receipts()


@pytest.mark.parametrize("mutation", ["registry", "handoff", "env"])
def test_changes_across_get_boundary_refuse_without_ack(producer, monkeypatch, mutation):
    _, handoff, _, identity, _, bridge = producer
    calls = 0
    def second_get(method, path):
        nonlocal calls
        calls += 1
        if calls == 2:
            if mutation == "registry":
                monkeypatch.setattr(c, "bind_caller", lambda *a, **kw: r.SeatIdentity(
                    "agent", "replacement", 20, 2000, identity.root, 5000, "changed", "leader"))
            elif mutation == "handoff":
                handoff.write_text("Changed handoff", encoding="utf-8")
            else:
                monkeypatch.setenv("LITEHARNESS_AGENT_ID", "agent")
                monkeypatch.setenv("LITESUITE_AGENT_ID", "other")
        return bridge(method, path)
    with pytest.raises(r.RetirementRefused):
        c.ack_idle(None, second_get)
    assert not c.persisted_receipts()


@pytest.mark.parametrize("mode", ["empty", "whitespace", "stale", "future", "outside", "directory", "oversize"])
def test_handoff_readonly_evidence_guards(tmp_path, monkeypatch, mode):
    path = tmp_path / "handoff.md"
    path.write_text("Saved next steps", encoding="utf-8")
    roots = (tmp_path,)
    if mode == "empty":
        path.write_text("")
    elif mode == "whitespace":
        path.write_text(" \n ")
    elif mode == "stale":
        os.utime(path, (0, 0))
    elif mode == "future":
        os.utime(path, (time.time() + 100, time.time() + 100))
    elif mode == "outside":
        other = tmp_path / "other"
        other.mkdir()
        roots = (other,)
    elif mode == "directory":
        path = tmp_path
    elif mode == "oversize":
        # Stub the threshold, NEVER allocate large or sparse fixtures.
        monkeypatch.setattr(h, "HANDOFF_MAX_BYTES", 1)
    before = tuple(tmp_path.rglob("*"))
    with pytest.raises(r.RetirementRefused):
        h.verify_handoff(path, roots)
    assert tuple(tmp_path.rglob("*")) == before


def test_exact_30minute_boundary_is_inclusive(tmp_path):
    path = tmp_path / "handoff.md"
    path.write_text("boundary")
    os.utime(path, (10000, 10000))
    assert h.verify_handoff(path, (tmp_path,), now=11800).size == 8
    with pytest.raises(r.RetirementRefused, match="30_minutes"):
        h.verify_handoff(path, (tmp_path,), now=11800.001)


def test_claude_requires_committed_handoff_without_writing(producer, monkeypatch):
    _, handoff, registry, _, _, bridge = producer
    registry["agent"]["cli"] = "claude-code"
    seen = []
    def committed(evidence, cwd):
        seen.append((evidence.path, str(cwd)))
        raise r.RetirementRefused("committed_handoff_unconfirmed")
    monkeypatch.setattr(c, "verify_committed_handoff", committed)
    with pytest.raises(r.RetirementRefused, match="committed"):
        c.ack_idle(str(handoff), bridge)
    assert seen and not c.persisted_receipts()


def test_changed_leader_registration_before_ack_refuses(producer, monkeypatch):
    _, _, registry, _, _, bridge = producer
    calls = 0
    def changed(method, path):
        nonlocal calls
        calls += 1
        if calls == 2:
            registry.pop("leader")
        return bridge(method, path)
    with pytest.raises(r.RetirementRefused, match="leader_that_spawned_it_changed"):
        c.ack_idle(None, changed)
    assert not c.persisted_receipts()


def test_ack_cli_rejects_other_agent_or_force_flags(producer):
    *_, bridge = producer
    for argv in (["--agent-id", "other"], ["--force"], ["--from", "other"]):
        with pytest.raises(SystemExit) as raised:
            c.command_ack_idle(argv, bridge)
        assert raised.value.code == 2
    assert not c.persisted_receipts()
