"""Tests for append-only pattern supersession.

Git-as-memory is append-only at every layer, which handles EVENTS correctly but
leaves STATE with no way to go stale: a pattern recorded months ago and the
pattern that corrects it describe the same subject in the same vocabulary, so
retrieval cannot rank one over the other and hands the caller a contradiction.

The fix is supersession-as-an-event: a correction NAMES the task_ids it retires.
Nothing is edited or deleted — retrieval just stops returning the retired record.

These tests pin that contract. Supersession filtering fails SILENTLY when broken
(no error, stale patterns simply reappear), so it needs an instrument.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from liteharness import cli


def _record(root: Path, agent: str, desc: str, supersedes=None) -> str:
    """Record a pattern; return its task_id."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_record_pattern(
            outcome="success",
            agent_id=agent,
            task_desc=desc,
            project=str(root),
            supersedes=supersedes,
        )
    # "Recorded pattern: success for <task_id>"
    return buf.getvalue().strip().rsplit(" ", 1)[-1]


def _query(root: Path, query: str, top: int = 10) -> list[dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_query_patterns(top=top, fmt="json", query=query, project=str(root))
    return json.loads(buf.getvalue())


class PatternSupersedesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".liteharness").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_query_exposes_task_id(self) -> None:
        """Callers can only supersede what query names.

        The sync previously read a non-existent `id` key off the JSONL (the field
        is `task_id`), so this column was empty on every row and no caller could
        reference a pattern at all.
        """
        tid = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        self.assertTrue(tid)

        got = _query(self.root, "Northwind retainer")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["task_id"], tid)
        self.assertEqual(got[0]["id"], tid, "legacy `id` key must stay populated")

    def test_superseded_pattern_is_not_returned(self) -> None:
        stale = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        _record(self.root, "a2", "Northwind retainer is 9500 per month", supersedes=[stale])

        got = _query(self.root, "Northwind retainer")
        ids = {p["task_id"] for p in got}
        self.assertNotIn(stale, ids, "retired pattern leaked back into retrieval")
        self.assertEqual(len(got), 1)
        self.assertIn("9500", got[0]["description"])

    def test_log_still_holds_the_retired_record(self) -> None:
        """Supersession must not delete — git-as-memory stays append-only."""
        stale = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        _record(self.root, "a2", "Northwind retainer is 9500 per month", supersedes=[stale])

        lines = [
            json.loads(ln)
            for ln in (self.root / ".liteharness" / "patterns.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.strip()
        ]
        self.assertEqual(len(lines), 2)
        self.assertIn(stale, {e["task_id"] for e in lines})

    def test_supersedes_edge_round_trips(self) -> None:
        stale = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        live = _record(
            self.root, "a2", "Northwind retainer is 9500 per month", supersedes=[stale]
        )

        got = _query(self.root, "Northwind retainer")
        row = next(p for p in got if p["task_id"] == live)
        self.assertEqual(row["supersedes"], [stale])

    def test_unsuperseded_patterns_are_unaffected(self) -> None:
        """A supersedes edge must retire only what it names."""
        stale = _record(self.root, "a1", "Harbor Point retainer is 4000 per month")
        other = _record(self.root, "a2", "Harbor Point delivered the onboarding flow")
        _record(
            self.root, "a3", "Harbor Point retainer is 9500 per month", supersedes=[stale]
        )

        ids = {p["task_id"] for p in _query(self.root, "Harbor Point")}
        self.assertIn(other, ids)
        self.assertNotIn(stale, ids)

    def test_stale_cache_is_rebuilt_when_schema_changes(self) -> None:
        """A patterns.db written by the pre-supersedes schema must not poison reads.

        The FTS5 table is a derived cache; a column-set mismatch is resolved by
        dropping it, never by migrating.
        """
        import sqlite3

        db_path = self.root / ".liteharness" / "patterns.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE VIRTUAL TABLE patterns USING fts5("
            "description, reason, lesson, id UNINDEXED, timestamp UNINDEXED, "
            "outcome UNINDEXED, complexity UNINDEXED, tokenize=\"unicode61\")"
        )
        conn.execute("CREATE TABLE patterns_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        stale = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        _record(self.root, "a2", "Northwind retainer is 9500 per month", supersedes=[stale])

        ids = {p["task_id"] for p in _query(self.root, "Northwind retainer")}
        self.assertNotIn(stale, ids)


if __name__ == "__main__":
    unittest.main()
