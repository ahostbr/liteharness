"""T0222 regressions: vanished owner evidence and cleanup-destroyed resume identity."""
import json
import time
from datetime import datetime, timezone

import pytest

from liteharness import config, hooks, resume_seat, seat_lifecycle as lifecycle


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_root", lambda: tmp_path / "harness")
    monkeypatch.setattr(config, "get_agent_id", lambda: "janitor")
    monkeypatch.setattr(lifecycle, "log_path", lambda: tmp_path / "seat-lifecycle.jsonl")
    return tmp_path


def test_dead_owner_cleanup_preserves_resume_identity_without_roster_resurrection(isolated, monkeypatch):
    root = config.get_root()
    path = root / "agents" / "victim.json"
    path.parent.mkdir(parents=True)
    row = {"agent_id": "victim", "name": "Smoke", "tier": "worker", "model": "test",
           "backend": "codex", "thinking_level": "high", "session_pid": 123,
           "last_seen": datetime.now(timezone.utc).isoformat(),
           "rpc_nonce": "test-private-nonce", "rpc_token_path": "C:/private/token-file"}
    path.write_text(json.dumps(row))
    monkeypatch.setattr(hooks, "_pid_alive", lambda _: False)
    convo = isolated / "data" / ".convos" / "conversation"
    convo.mkdir(parents=True)
    (convo / "settings.json").write_text(json.dumps({"seat_id": "victim"}))
    (convo / "convo.jsonl").write_text('{"type":"meta"}\n')
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(isolated / "data"))
    assert hooks._purge_stale_agents() == 1
    # T0236: victim owns a conversation (seat_id), so the sweep marks it offline, never deletes.
    assert json.loads(path.read_text())["status"] == "offline"
    path.unlink()  # now simulate the record being absent: retired identity alone must resolve
    _, _, saved = resume_seat.lookup("victim", None)
    assert saved["name"] == "Smoke"
    assert "rpc_nonce" not in saved and "rpc_token_path" not in saved
    assert saved["retirement_reason"] == "stale-agent-sweep:owner-process-gone"
    assert not path.exists()  # lookup cannot resurrect a ghost
    rows = lifecycle.records()
    assert [r["event"] for r in rows] == ["registry_delete_requested", "registry_marked_offline"]
    assert rows[0]["origin"]["actor_id"] == "janitor"
    assert rows[1]["request_record_id"] == rows[0]["record_id"]
    assert lifecycle.latest_for(saved, rows)["record_id"] == rows[1]["record_id"]


def test_delete_fails_closed_when_evidence_cannot_be_written(isolated, monkeypatch):
    path = config.get_root() / "agents" / "victim.json"
    path.parent.mkdir(parents=True)
    row = {"agent_id": "victim"}
    path.write_text(json.dumps(row))
    monkeypatch.setattr(lifecycle, "append", lambda _: None)
    assert not lifecycle.preserve_before_delete(path, row, "test")
    assert path.exists()


def test_changed_registration_wins_over_stale_cleanup(isolated):
    path = config.get_root() / "agents" / "victim.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"agent_id": "victim", "registered_at": "new"}))
    assert not lifecycle.preserve_before_delete(path, {"agent_id": "victim"}, "test")
    assert path.exists()


def test_owner_exit_has_bounded_redacted_output_and_no_fabricated_cause(isolated):
    recorder = lifecycle.ProcessLifecycle("seat", "pty", 123, time.time(),
        {"API_KEY": "known-private-value"})
    recorder.data("before\n" * 70 + "context: 72%\nTraceback (most recent call last):\n")
    recorder.data("API_KEY=known-private-value\nBearer bearer-secret\nError: crashed\n")
    recorder.exit(2)
    recorder.exit(3)
    rows = lifecycle.records(unexplained=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["actual_cause"] == "unknown"
    assert row["exit_code"] == 2 and row["signal"] is None
    assert row["last_context_percent"] == 72
    assert len(row["output_tail"]) <= 50
    assert "known-private-value" not in json.dumps(row)
    assert "bearer-secret" not in json.dumps(row)
    assert row["traceback"]
    assert lifecycle.latest_for({"agent_id": "seat", "run_id": row["run_id"]}, lifecycle.records())["record_id"] == row["record_id"]


def test_kill_request_is_linked_but_not_claimed_as_actual_cause(isolated):
    recorder = lifecycle.ProcessLifecycle("seat", "pty", 123, time.time(), {})
    recorder.request_kill({"source": "resume:kill-old", "actor_id": "leader"})
    recorder.exit(0)
    rows = lifecycle.records()
    assert [r["event"] for r in rows] == ["spawn", "kill_requested", "exit"]
    assert rows[-1]["kill_record_id"] == rows[1]["record_id"]
    assert rows[-1]["origin"]["actor_id"] == "leader"
    assert not lifecycle.records(unexplained=True)
    assert rows[-1]["actual_cause"] == "unknown"


def test_since_query_is_inclusive_and_corrupt_log_is_not_a_clean_result(isolated):
    record = lifecycle.append({"event": "exit", "unexplained": True})
    row = lifecycle.records()[0]
    assert lifecycle.records(since=row["timestamp"], unexplained=True)[0]["record_id"] == record
    with lifecycle.log_path().open("a") as fh:
        fh.write("partial\n")
    with pytest.raises(ValueError, match="invalid lifecycle line 2"):
        lifecycle.records()


def test_process_gone_query_flags_no_exit_but_not_access_denied_or_recorded_kill(isolated, monkeypatch):
    import psutil
    row = {"event": "register", "seat_id": "victim", "pid": 123,
           "process_started_at": 1790773000000,
           "record_id": "registration", "timestamp": "2026-09-30T13:00:00+00:00"}
    def missing(_):
        raise psutil.NoSuchProcess(123)
    monkeypatch.setattr(psutil, "Process", missing)
    result = lifecycle.unexplained_since([row], "2026-09-30T13:10:00Z")
    assert result[0]["event"] == "process_gone"
    assert result[0]["record_id"] == "registration"
    assert result[0]["exit_code"] is None
    kill = {**row, "event": "kill_requested", "timestamp": "2026-09-30T13:11:00+00:00"}
    assert not lifecycle.unexplained_since([row, kill], None)
    def denied(_):
        raise psutil.AccessDenied(123)
    monkeypatch.setattr(psutil, "Process", denied)
    assert not lifecycle.unexplained_since([row], None)


def test_reused_same_pid_same_seat_never_joins_other_creation_or_stale_exit(isolated, monkeypatch):
    import psutil
    row = {"event": "register", "seat_id": "seat", "pid": 123,
           "process_started_at": 1000000, "record_id": "new-registration",
           "timestamp": "2026-09-30T13:00:00+00:00"}
    # An older process may occupy the same PID; older-than-registration is not proof.
    monkeypatch.setattr(psutil, "Process", lambda _: type("Process", (), {"create_time": lambda self: 900})())
    old_exit = {**row, "process_started_at": 900000, "event": "exit", "unexplained": False,
                "record_id": "old-exit", "timestamp": "2026-09-30T13:01:00+00:00"}
    old_kill = {**old_exit, "event": "kill_requested"}
    result = lifecycle.unexplained_since([row, old_exit, old_kill], None)
    assert [r["record_id"] for r in result] == ["new-registration"]
    presence = {"agent_id": "seat", "session_pid": 123, "session_process_started_at": 1000000}
    assert lifecycle.latest_for(presence, [old_exit]) is None
    matched = {**old_exit, "process_started_at": 1000000, "record_id": "matching-exit"}
    assert lifecycle.latest_for(presence, [old_exit, matched])["record_id"] == "matching-exit"
    assert lifecycle.latest_for({"agent_id": "seat"}, [matched]) is None
    monkeypatch.setattr(psutil, "Process", lambda _: type("Process", (), {"create_time": lambda self: 1000})())
    assert not lifecycle.unexplained_since([row], None)


def test_legacy_creation_unknown_does_not_suppress_or_assert_live_identity(isolated, monkeypatch):
    import psutil
    row = {"event": "register", "seat_id": "seat", "pid": 123,
           "record_id": "unknown", "timestamp": "2026-09-30T13:00:00+00:00"}
    kill = {**row, "event": "kill_requested"}
    assert not lifecycle.same_process(row, kill)
    monkeypatch.setattr(psutil, "Process", lambda _: type("Process", (), {"create_time": lambda self: 1000})())
    assert not lifecycle.unexplained_since([row, kill], None)  # exists, but identity unproven
    def missing(_):
        raise psutil.NoSuchProcess(123)
    monkeypatch.setattr(psutil, "Process", missing)
    assert lifecycle.unexplained_since([row, kill], None)[0]["event"] == "process_gone"
    assert not lifecycle.same_process({**row, "run_id": "old"}, {**row, "run_id": "new"})


def test_owner_minted_run_links_root_and_registered_child_without_timestamp_guess(isolated, monkeypatch):
    import psutil
    run = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
    monkeypatch.setenv("LITEHARNESS_SEAT_RUN_ID", run)
    monkeypatch.setattr(psutil, "Process", lambda _: type("Process", (), {"create_time": lambda self: 1000})())
    presence = {"agent_id": "seat", "session_pid": 456, "registered_at": "2026-09-30T13:00:00Z"}
    lifecycle.capture_registration_identity(presence)
    child = lifecycle.identity(presence)
    assert child["process_started_at"] == 1000000
    assert child["run_id"] == run
    recorder = lifecycle.ProcessLifecycle("seat", "pty", 123, 1000.25,
                                          {"LITEHARNESS_SEAT_RUN_ID": run})
    recorder.exit(2)
    root = lifecycle.records()[-1]
    assert root["process_started_at"] is None
    assert root["observed_started_at"] == 1000250
    assert lifecycle.same_process(child, root)
    assert lifecycle.latest_for(presence, [root])["record_id"] == root["record_id"]
