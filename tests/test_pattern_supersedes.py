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
from contextlib import redirect_stderr, redirect_stdout
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


class BareStringSupersedesTests(unittest.TestCase):
    """A string where a list belongs must NOT be iterated character-wise.

    🔴 THE LIVE INCIDENT THIS PINS. LiteSuite/.liteharness/patterns.jsonl line 28
    stored supersedes as 47 single characters — list("ac965cc1-...-1787681416").
    The caller passed a bare string; `",".join(a_string)` produced
    "a,c,9,6,..." and the CLI's comma split turned that into one entry per
    character. Nothing raised. The supersession therefore named 47 ids that do
    not exist and retired NOTHING, so the corrected verdict and the wrong one
    both stayed live in query results.

    ⭐ The failure is silent in BOTH directions: no error on write, and on read
    a supersession that matches nothing is indistinguishable from a pattern that
    never superseded anything. That is why this needs an instrument.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".liteharness").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _entries(self) -> list[dict]:
        return [
            json.loads(ln)
            for ln in (self.root / ".liteharness" / "patterns.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.strip()
        ]

    def test_a_bare_string_is_stored_as_one_element(self) -> None:
        stale = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        _record(self.root, "a2", "Northwind retainer is 9500 per month", supersedes=stale)

        rec = self._entries()[-1]
        self.assertEqual(
            rec["supersedes"], [stale],
            "a bare string was exploded into characters instead of wrapped",
        )

    def test_a_bare_string_actually_retires_the_pattern(self) -> None:
        """Storage shape is only half of it — the edge must still apply.

        Asserting the stored list alone would pass on a fix that wrapped the
        string but broke retrieval.
        """
        stale = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        _record(self.root, "a2", "Northwind retainer is 9500 per month", supersedes=stale)

        ids = {p["task_id"] for p in _query(self.root, "Northwind retainer")}
        self.assertNotIn(stale, ids, "bare-string supersession did not retire anything")

    def test_a_list_of_strings_still_works(self) -> None:
        """Control: the coercion must not disturb the documented shape."""
        stale = _record(self.root, "a1", "Northwind retainer is 4000 per month")
        _record(self.root, "a2", "Northwind retainer is 9500 per month", supersedes=[stale])
        self.assertEqual(self._entries()[-1]["supersedes"], [stale])

    def test_a_nonsense_supersedes_refuses_loudly(self) -> None:
        """Neither str nor list-of-str: refuse rather than store something odd.

        Silently coercing an int or a dict is how the next caller learns nothing
        from being wrong.
        """
        for bad in (17, {"id": "x"}, [1, 2], [None]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError, SystemExit)):
                    _record(self.root, "a3", "whatever", supersedes=bad)


if __name__ == "__main__":
    unittest.main()


class SupersedesIsAReferenceTests(unittest.TestCase):
    """T878 — the id must name a record, not merely look like one.

    `_coerce_id_list` checks the SHAPE, and its docstring already records what a
    shape-valid array that names nothing does: 47 one-character ids that
    "retired NOTHING, while reading back as a perfectly well-formed list". That
    fix closed the ROUTE it had seen (a string iterated character-wise) and
    never added the PROPERTY it needed, so the same outcome stayed reachable —
    on 2026-09-18 a seat that could not find the real id typed a plausible UUID
    and it was written as a COMPLETED retirement.

        A SHAPE FIX FOR A REFERENCE BUG CLOSES THE ROUTE, NOT THE HOLE.
        A DEAD POINTER IS NOT A CORRUPT FILE. IT IS A WELL-FORMED LIE.

    ⬜ The paired arms in LiteSuite's `patterns-supersedes.test.ts` spawn THIS
    CLI, so the rule is covered from both sides; these exist so it survives
    without that repo present.
    """

    FABRICATED = "a7ad9c4e-0000-0000-0000-000000000000"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".liteharness").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _rows(self) -> list[dict]:
        path = self.root / ".liteharness" / "patterns.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_an_id_no_record_carries_is_refused(self) -> None:
        _record(self.root, "seat", "the record a correction retires")
        before = len(self._rows())
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            _record(self.root, "seat", "the correction", supersedes=[self.FABRICATED])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn(self.FABRICATED, err.getvalue())
        # Refused BEFORE the append: a rejected write leaves no row behind.
        self.assertEqual(len(self._rows()), before)

    def test_a_valid_task_id_is_still_accepted(self) -> None:
        """🔴 THE ARM THAT MATTERS SECOND. The cheap wrong fix is a check strict
        enough to refuse everything, and a store that accepts no retirements
        looks exactly like a store with no retirements to make."""
        tid = _record(self.root, "seat", "the retired record")
        _record(self.root, "seat", "the correction", supersedes=[tid])
        self.assertEqual(self._rows()[-1]["supersedes"], [tid])

    def test_the_other_handle_resolves_too(self) -> None:
        """Historical arrays name task_ids, new ones name pattern_ids, and
        `_pattern_fts5_query` tests both. A check that knows one handle would
        refuse every historical retirement."""
        _record(self.root, "seat", "the retired record")
        pid = self._rows()[-1]["pattern_id"]
        _record(self.root, "seat", "the correction", supersedes=[pid])
        self.assertEqual(self._rows()[-1]["supersedes"], [pid])

    def test_one_bad_id_among_several_refuses_the_whole_write(self) -> None:
        tid = _record(self.root, "seat", "the retired record")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            _record(self.root, "seat", "x", supersedes=[tid, self.FABRICATED])
        self.assertEqual(len(self._rows()), 1)

    def test_a_row_the_validating_reader_would_drop_is_still_a_target(self) -> None:
        """⚠️ THE CHECK READS RAW LINES ON PURPOSE, and this is not hypothetical:
        measured 2026-09-18 on the live C:/Projects store, 21 of 502 rows carry
        PROSE in `outcome` (713-2253 chars) and are rejected by the validating
        reader. Those rows are real records. A reference check built on a
        validating view would refuse every retirement naming one of them.
        """
        path = self.root / ".liteharness" / "patterns.jsonl"
        path.write_text(json.dumps({
            "task_id": "seat-1789700000",
            "pattern_id": "11111111-2222-3333-4444-555555555555",
            "session": "seat", "agent_id": "seat",
            "outcome": "ROOT CAUSE (measured ...): prose where the enum belongs",
            "complexity": "medium", "description": "a row no schema accepts",
            "verified": "unverified", "timestamp": "2026-09-18T00:00:00+00:00",
        }) + "\n", encoding="utf-8")
        _record(self.root, "seat", "the correction",
                supersedes=["11111111-2222-3333-4444-555555555555"])
        self.assertEqual(self._rows()[-1]["supersedes"],
                         ["11111111-2222-3333-4444-555555555555"])
