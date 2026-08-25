"""Tests for the `verified` field, pattern identity, and append-only attestations.

Doctrine (git-as-memory v2): every pattern is BORN `verified: "unverified"` —
record carries no level flag at all. Promotion is an APPEND-ONLY attestation in
a SEPARATE file (`.liteharness/pattern-attestations.jsonl`), one event per
state change, each level requiring its own evidence (human→evidence_ref,
judgement→delegation_ref, gauntlet→run_id). Demotion is a `revocation` event
requiring reason + prior_attestation_id. Nothing is ever edited in place.

Identity: new records get an immutable UUID4 `pattern_id`. Legacy rows get a
deterministic content-addressed id `legacy:<sha256(canonical)>` derived at read
time — no path, repo, or ordinal component, so the same committed JSONL yields
the same ids in every checkout. Exact duplicate records deliberately SHARE one
identity (indistinguishable statements of one fact); duplicate task_ids with
different content get different ids. Attestations target pattern_id ONLY and
FAIL CLOSED on zero matches — a task_id is never a fallback handle, because the
task_id namespace holds live collisions (unknown-<epoch> ids collide within a
second; LiteSuite's own file holds unknown-1783644326 x3).

This is provenance enforcement plus policy, NOT security: any local process
can append; the defense is that every state change is causal and attributable.
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from liteharness import cli

# Same-shape stand-in for a live specimen row: a record whose task_id the CLI
# minted itself (`unknown-<epoch>`) after ignoring the caller's intent, i.e.
# the colliding-namespace class that makes task_id unusable as an attestation
# target. The real row's private content cannot enter this public repo (the
# pre-commit PII gate refuses it); the VERBATIM specimen — with the Python-
# derived id as the cross-language conformance anchor — lives in the private
# repo's harvest tests. The derivation hashes the PARSED object, so surface
# formatting differences are immaterial.
SPECIMEN_LINE = (
    '{"task_id": "unknown-1787678826", "session": "cli", "outcome": "failure", '
    '"complexity": "medium", "description": "[verification] None\\n\\nGit diff summary:\\n'
    ' src/example/loader.ts                              |   93 +-\\n'
    ' src/example/loader.test.ts                         |   29 +-\\n'
    ' docs/example-notes.md                              |   18 +-\\n'
    ' 3 files changed, 97 insertions(+), 43 deletions(-)", '
    '"timestamp": "2026-08-25T17:27:06.968863+00:00", "reason": "Outcome was failure"}'
)


def _record(root: Path, agent: str, desc: str, supersedes=None) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_record_pattern(
            outcome="success",
            agent_id=agent,
            task_desc=desc,
            project=str(root),
            supersedes=supersedes,
        )
    return buf.getvalue().strip().rsplit(" ", 1)[-1]


def _query_json(root: Path, query: str | None, top: int = 10) -> list[dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_query_patterns(top=top, fmt="json", query=query, project=str(root))
    return json.loads(buf.getvalue())


def _query_text(root: Path, query: str | None, top: int = 10) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_query_patterns(top=top, fmt="text", query=query, project=str(root))
    return buf.getvalue()


def _jsonl_entries(root: Path) -> list[dict]:
    path = root / ".liteharness" / "patterns.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _attest_path(root: Path) -> Path:
    return root / ".liteharness" / "pattern-attestations.jsonl"


def _verify(root: Path, pattern_id: str, level: str, **kw) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_verify_pattern(
            pattern_id=pattern_id,
            level=level,
            actor=kw.pop("actor", "ryan"),
            project=str(root),
            **kw,
        )
    return buf.getvalue()


class PatternVerifiedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".liteharness").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- birth state ------------------------------------------------------

    def test_record_born_unverified_with_uuid(self) -> None:
        _record(self.root, "a1", "Northwind retainer is 4000 per month")

        entry = _jsonl_entries(self.root)[-1]
        self.assertEqual(entry["verified"], "unverified")
        pid = entry["pattern_id"]
        # UUID4 shape: 36 chars, hyphens at fixed positions, version nibble 4.
        self.assertEqual(len(pid), 36)
        self.assertEqual(pid[14], "4")

        rows = _query_json(self.root, "Northwind retainer")
        self.assertEqual(rows[0]["verified"], "unverified")
        self.assertEqual(rows[0]["pattern_id"], pid)

        text = _query_text(self.root, "Northwind retainer")
        self.assertIn("[UNVERIFIED]", text)
        self.assertIn(pid, text)

    def test_record_has_no_level_channel(self) -> None:
        """A level flag at record time must be an ERROR, not silently eaten —
        an ignored flag would let a recorder believe it self-promoted."""
        for flag in ("--verified", "--level"):
            proc = subprocess.run(
                [
                    sys.executable, "-m", "liteharness.cli", "record-pattern",
                    "--outcome", "success", "--task", "x",
                    flag, "human",
                    "--project", str(self.root),
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0, f"{flag} was accepted")
            self.assertIn(flag, proc.stderr)
        self.assertEqual(_jsonl_entries(self.root), [], "refused record still wrote")

    # -- promotion --------------------------------------------------------

    def test_verify_promotes_and_survives_warm_cache(self) -> None:
        _record(self.root, "a1", "Northwind retainer is 4000 per month")
        pid = _jsonl_entries(self.root)[-1]["pattern_id"]

        # Warm the FTS5 cache BEFORE the attestation: the attestation appends
        # to a file the old cache check never watched, so this order is the
        # regression that catches a stale-cache promotion.
        self.assertIn("[UNVERIFIED]", _query_text(self.root, "Northwind"))

        _verify(self.root, pid, "human", evidence_ref="conv:test-approval")

        events = [
            json.loads(ln)
            for ln in _attest_path(self.root).read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "verification")
        self.assertEqual(events[0]["level"], "human")
        self.assertEqual(events[0]["evidence_ref"], "conv:test-approval")

        text = _query_text(self.root, "Northwind")
        self.assertIn("[VERIFIED]", text)
        self.assertNotIn("[UNVERIFIED]", text)
        self.assertEqual(_query_json(self.root, "Northwind")[0]["verified"], "human")
        # patterns.jsonl itself is never edited — birth state stays on disk.
        self.assertEqual(_jsonl_entries(self.root)[-1]["verified"], "unverified")

    def test_each_level_requires_its_evidence(self) -> None:
        _record(self.root, "a1", "Northwind retainer is 4000 per month")
        pid = _jsonl_entries(self.root)[-1]["pattern_id"]

        for level in ("human", "judgement", "gauntlet"):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit, msg=f"{level} without evidence"):
                    _verify(self.root, pid, level)
        self.assertFalse(_attest_path(self.root).exists(), "refusal still appended")

        _verify(self.root, pid, "judgement", delegation_ref="conv:use-your-judgement")
        self.assertIn("[JUDGEMENT]", _query_text(self.root, "Northwind"))
        _verify(self.root, pid, "gauntlet", run_id="run-123")
        self.assertIn("[GAUNTLET]", _query_text(self.root, "Northwind"))

    def test_verify_fails_closed_on_unknown_target(self) -> None:
        _record(self.root, "a1", "Northwind retainer is 4000 per month")

        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit):
                _verify(self.root, "unknown-1787678826", "human", evidence_ref="x")
        self.assertIn("0 patterns", err.getvalue())
        self.assertFalse(_attest_path(self.root).exists())

    # -- revocation -------------------------------------------------------

    def test_revoke_requires_reason_and_prior(self) -> None:
        _record(self.root, "a1", "Northwind retainer is 4000 per month")
        pid = _jsonl_entries(self.root)[-1]["pattern_id"]
        _verify(self.root, pid, "human", evidence_ref="conv:test")
        attestation_id = json.loads(
            _attest_path(self.root).read_text(encoding="utf-8").splitlines()[0]
        )["attestation_id"]

        cases = [
            {"reason": "", "prior_attestation_id": attestation_id},
            {"reason": "was wrong", "prior_attestation_id": ""},
            {"reason": "was wrong", "prior_attestation_id": "not-a-real-attestation"},
        ]
        for kw in cases:
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit, msg=repr(kw)):
                    cli.cmd_revoke_pattern(
                        pattern_id=pid, actor="ryan", project=str(self.root), **kw
                    )
        self.assertIn("[VERIFIED]", _query_text(self.root, "Northwind"))

        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_revoke_pattern(
                pattern_id=pid,
                reason="approval was based on a stale run",
                prior_attestation_id=attestation_id,
                actor="ryan",
                project=str(self.root),
            )
        self.assertIn("[UNVERIFIED]", _query_text(self.root, "Northwind"))
        events = _attest_path(self.root).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 2, "revocation must append, never edit")

    # -- identity ---------------------------------------------------------

    def test_duplicate_task_ids_with_different_content_get_different_ids(self) -> None:
        path = self.root / ".liteharness" / "patterns.jsonl"
        rows = [
            {
                "task_id": "unknown-1700000000",
                "session": "cli",
                "outcome": "failure",
                "complexity": "medium",
                "description": "first fact recorded in this second",
                "timestamp": "2026-08-01T00:00:00+00:00",
            },
            {
                "task_id": "unknown-1700000000",
                "session": "cli",
                "outcome": "failure",
                "complexity": "medium",
                "description": "second, different fact recorded in the same second",
                "timestamp": "2026-08-01T00:00:00.5+00:00",
            },
        ]
        path.write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

        got = _query_json(self.root, "fact recorded second")
        self.assertEqual(len(got), 2)
        pids = {r["pattern_id"] for r in got}
        self.assertEqual(len(pids), 2, "different content must not share an identity")
        for pid in pids:
            self.assertTrue(pid.startswith("legacy:"), pid)

        target = next(
            r["pattern_id"] for r in got if "first fact" in r["description"]
        )
        _verify(self.root, target, "human", evidence_ref="conv:test")
        by_desc = {
            ("first" if "first fact" in r["description"] else "second"): r["verified"]
            for r in _query_json(self.root, "fact recorded second")
        }
        self.assertEqual(by_desc["first"], "human")
        self.assertEqual(by_desc["second"], "unverified",
                         "attesting one duplicate task_id promoted its sibling")

    def test_exact_duplicates_share_one_identity(self) -> None:
        row = {
            "task_id": "unknown-1700000001",
            "session": "cli",
            "outcome": "success",
            "complexity": "medium",
            "description": "the same statement of the same fact",
            "timestamp": "2026-08-01T00:00:00+00:00",
        }
        path = self.root / ".liteharness" / "patterns.jsonl"
        path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

        got = _query_json(self.root, "same statement same fact")
        self.assertEqual(len(got), 2)
        pids = {r["pattern_id"] for r in got}
        self.assertEqual(len(pids), 1, "identical records are one fact, one identity")

        _verify(self.root, pids.pop(), "human", evidence_ref="conv:test")
        self.assertTrue(
            all(r["verified"] == "human" for r in _query_json(self.root, "same statement")),
            "attesting the fact must reach every identical copy",
        )

    def test_specimen_class_task_id_refused_legacy_id_attests(self) -> None:
        entry = json.loads(SPECIMEN_LINE)
        pid1 = cli._pattern_content_id(entry)
        pid2 = cli._pattern_content_id(json.loads(SPECIMEN_LINE))
        self.assertEqual(pid1, pid2, "derivation must be deterministic")
        self.assertRegex(pid1, r"^legacy:[0-9a-f]{64}$")

        path = self.root / ".liteharness" / "patterns.jsonl"
        path.write_text(SPECIMEN_LINE + "\n", encoding="utf-8")

        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _verify(self.root, "unknown-1787678826", "human", evidence_ref="x")

        _verify(self.root, pid1, "human", evidence_ref="conv:specimen-test")
        rows = _query_json(self.root, "verification loader example")
        self.assertEqual(rows[0]["verified"], "human")
        self.assertEqual(rows[0]["pattern_id"], pid1)

    # -- event hygiene ----------------------------------------------------

    def test_unknown_event_type_changes_nothing(self) -> None:
        _record(self.root, "a1", "Northwind retainer is 4000 per month")
        pid = _jsonl_entries(self.root)[-1]["pattern_id"]
        _attest_path(self.root).write_text(
            json.dumps({"type": "zzz", "pattern_id": pid, "level": "human"}) + "\n",
            encoding="utf-8",
        )

        err = io.StringIO()
        with redirect_stderr(err):
            rows = _query_json(self.root, "Northwind retainer")
        self.assertEqual(rows[0]["verified"], "unverified",
                         "an unknown event type changed state")
        self.assertIn("unknown", err.getvalue().lower())

    def test_unresolved_attestation_is_surfaced_not_silent(self) -> None:
        _record(self.root, "a1", "Northwind retainer is 4000 per month")
        _attest_path(self.root).write_text(
            json.dumps({
                "type": "verification",
                "attestation_id": "a" * 36,
                "pattern_id": "legacy:" + "0" * 64,
                "level": "human",
                "actor": "ryan",
                "evidence_ref": "conv:x",
                "timestamp": "2026-08-25T00:00:00+00:00",
            }) + "\n",
            encoding="utf-8",
        )

        err = io.StringIO()
        with redirect_stderr(err):
            rows = _query_json(self.root, "Northwind retainer")
        self.assertEqual(rows[0]["verified"], "unverified")
        self.assertIn("unresolved", err.getvalue().lower())

    # -- supersession by pattern_id --------------------------------------

    def test_supersession_by_pattern_id(self) -> None:
        _record(self.root, "a1", "Harbor Point retainer is 4000 per month")
        stale_pid = _jsonl_entries(self.root)[-1]["pattern_id"]
        _record(
            self.root, "a2", "Harbor Point retainer is 9500 per month",
            supersedes=[stale_pid],
        )

        got = _query_json(self.root, "Harbor Point retainer")
        self.assertEqual(len(got), 1)
        self.assertIn("9500", got[0]["description"])


if __name__ == "__main__":
    unittest.main()
