import json
import sqlite3
from datetime import datetime, timezone

import pytest

from liteharness.mod_fleet import read_snapshot


def presence(root, name, **fields):
    root.mkdir(exist_ok=True)
    row = {"agent_id": name, "name": name,
           "last_seen": datetime.fromtimestamp(1000, timezone.utc).isoformat(), **fields}
    (root / (name + ".json")).write_text(json.dumps(row), encoding="utf-8")


def board(path, count=1):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE tasks(id, title, status, assignee, tier, priority)")
        con.executemany("INSERT INTO tasks VALUES(?, 'task', 'building', 'seat', 2, 3)",
                        [(f"T{i:04}",) for i in range(count)])
        con.execute("INSERT INTO tasks VALUES('done', 'finished', 'done', '', 2, 3)")


def test_discovery_liveness_and_board_are_read_only(tmp_path):
    agents = tmp_path / "agents"
    presence(agents, "live", session_pid=1, tier="worker", model="model")
    presence(agents, "dead", session_pid=2)
    presence(agents, "unknown-owner")
    presence(agents, "exited", exited_at="yes")
    presence(agents, "stale", last_seen=datetime.fromtimestamp(0, timezone.utc).isoformat())
    (agents / "broken.json").write_text("{", encoding="utf-8")
    db = tmp_path / "tasks.db"
    board(db)
    before = db.read_bytes()
    result = read_snapshot(agents, db, now=1001, pid_alive=lambda pid: pid == 1)
    assert {row["name"] for row in result["agents"]} == {"live", "unknown-owner"}
    assert result["tasks"] == [{"id": "T0000", "title": "task", "status": "building",
                                 "assignee": "seat", "tier": "2"}]
    assert result["warnings"] == ["Unreadable agent presence"]
    assert db.read_bytes() == before


def test_missing_board_does_not_create_database(tmp_path):
    db = tmp_path / "absent.db"
    result = read_snapshot(tmp_path / "agents", db, now=1000)
    assert result["warnings"] == ["Agent registry unavailable", "Task board unavailable"]
    assert not db.exists()


def test_counts_and_bounded_output(tmp_path):
    agents = tmp_path / "agents"
    for i in range(103):
        presence(agents, f"seat{i:03}")
    db = tmp_path / "tasks.db"
    board(db, count=102)
    result = read_snapshot(agents, db, now=1000)
    assert result["agentCount"] == 103
    assert len(result["agents"]) == 100
    assert result["taskCount"] == 102
    assert len(result["tasks"]) == 100


@pytest.mark.parametrize("newest_name,older_name", [("a-new", "z-old"), ("z-new", "a-old")])
def test_same_owner_dedupes_before_count_and_discloses_superseded(tmp_path, newest_name, older_name):
    agents = tmp_path / "agents"
    presence(agents, older_name, session_pid=42, registered_at="2026-10-02T10:00:00Z")
    presence(agents, newest_name, session_pid=42, registered_at="2026-10-02T10:00:13Z")
    presence(agents, "ownerless-one")
    presence(agents, "ownerless-two")
    db = tmp_path / "tasks.db"
    board(db)
    result = read_snapshot(agents, db, now=1001, pid_alive=lambda pid: pid == 42)
    assert result["agentCount"] == 3
    assert {row["name"] for row in result["agents"]} == {newest_name, "ownerless-one", "ownerless-two"}
    assert result["supersededCount"] == 1
    assert result["warnings"] == ["1 superseded presence row(s) excluded"]
