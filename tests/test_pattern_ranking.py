"""Tests for relevance-first pattern retrieval and loader hygiene.

Two FTS5 indexes over the same JSONL ranked in opposite directions: the TS path
(intelligence.ts) is BM25-first, while this CLI path ordered by timestamp — a
query's best match lost to whatever was recorded last. These tests pin the
Python path to relevance-first ranking (recency only as tiebreak).

The ghost-row test pins the loader's positive validation: a non-pattern line
(e.g. a stray attestation event) must never become a pattern row. Attestations
live in a SEPARATE file by design, but the loader must not trust that — an
old-style mixed write would otherwise surface as an empty-description row with
the newest timestamp, topping every bare recency query.

BOTH tests were run against the unmodified source first and seen RED
(recency put the incidental match first; the synthetic event line became a
row). The red runs are pasted in the commit body.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from liteharness import cli


def _write_jsonl(root: Path, entries: list[dict]) -> None:
    path = root / ".liteharness" / "patterns.jsonl"
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )


def _query(root: Path, query: str | None, top: int = 10) -> list[dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_query_patterns(top=top, fmt="json", query=query, project=str(root))
    return json.loads(buf.getvalue())


class PatternRankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".liteharness").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_relevance_beats_recency(self) -> None:
        """An old entry dense in the query's terms must outrank a new entry
        that merely brushes each term once in a long unrelated write-up."""
        _write_jsonl(
            self.root,
            [
                {
                    "task_id": "t-old-dense",
                    "session": "a1",
                    "outcome": "failure",
                    "complexity": "medium",
                    "description": (
                        "quiet box isolation of the flaky suite: run the flaky "
                        "suite alone in a quiet box; isolation separates suite "
                        "flakiness from load"
                    ),
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
                {
                    "task_id": "t-new-incidental",
                    "session": "a2",
                    "outcome": "success",
                    "complexity": "medium",
                    "description": (
                        "Reworked the release pipeline configuration end to end. "
                        "A quiet deprecation warning appeared during packaging and "
                        "was traced to the box art asset loader. Considered "
                        "isolation of the e2e stage but deferred it. One flaky "
                        "screenshot comparison was retried. The full regression "
                        "suite passed after the cache was warmed. Also renamed "
                        "twelve build targets, updated the changelog template, "
                        "and rotated the signing certificate for the installer."
                    ),
                    "timestamp": "2026-08-20T00:00:00+00:00",
                },
            ],
        )

        got = _query(self.root, "quiet box isolation flaky suite")
        self.assertEqual(len(got), 2, "both entries match every term; both must return")
        self.assertEqual(
            got[0]["task_id"],
            "t-old-dense",
            "recency outranked relevance: the incidental match came first",
        )

    def test_full_scan_without_query_stays_recency_first(self) -> None:
        """With no query there is no relevance signal — recency is correct."""
        _write_jsonl(
            self.root,
            [
                {
                    "task_id": "t-older",
                    "session": "a1",
                    "outcome": "success",
                    "complexity": "medium",
                    "description": "first recorded fact",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
                {
                    "task_id": "t-newer",
                    "session": "a2",
                    "outcome": "success",
                    "complexity": "medium",
                    "description": "second recorded fact",
                    "timestamp": "2026-08-20T00:00:00+00:00",
                },
            ],
        )

        got = _query(self.root, None)
        self.assertEqual([e["task_id"] for e in got], ["t-newer", "t-older"])

    def test_ghost_row_event_line_never_becomes_a_pattern(self) -> None:
        """A non-pattern line in patterns.jsonl must be surfaced, never inserted.

        The loader previously inserted EVERY JSON line as a pattern row, so an
        event line became a ghost: empty description, real-looking shape, newest
        timestamp — topping every bare recency query.
        """
        _write_jsonl(
            self.root,
            [
                {
                    "task_id": "t-real",
                    "session": "a1",
                    "outcome": "success",
                    "complexity": "medium",
                    "description": "the one real pattern",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
                {
                    "type": "verification",
                    "pattern_id": "0f0e0d0c-0b0a-4a09-8807-060504030201",
                    "level": "human",
                    "actor": "ryan",
                    "timestamp": "2026-08-24T00:00:00+00:00",
                },
            ],
        )

        got = _query(self.root, None)
        self.assertEqual(
            len(got), 1, f"non-pattern line leaked into retrieval: {got!r}"
        )
        self.assertEqual(got[0]["task_id"], "t-real")


if __name__ == "__main__":
    unittest.main()
