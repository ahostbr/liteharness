"""A refusal must name the STORE it searched, not just the id it could not find (T365).

🔴 THE ERROR BLAMED THE TARGET WHILE THE FAULT WAS THE SCOPE. `verify-pattern` and
`revoke-pattern` default their project to `os.getcwd()`. A seat standing in the wrong
directory — the common case, since the pattern id it was handed came from another
project's store — was told:

    [verify-pattern] '2e705dca-…' resolves to 0 patterns — attestation refused
    (fail closed). task_ids are not attestation targets; take the Pattern-id from
    query-patterns output.

Measured 2026-09-05 with two temp stores. Every word of that is true and none of it is
the problem: the id IS a pattern_id, and it IS in a store — just not the one the command
looked in. The advice actively misleads, because re-running `query-patterns` yields the
same id and the same refusal.
    A MESSAGE THAT NAMES ONLY THE TARGET SENDS THE READER TO AUDIT THE ONE THING THAT
    WAS ALREADY CORRECT.

⭐ THE OVERRIDE WAS NEVER MISSING. `--project` already exists on both commands and
already appears in both usage strings; verifying across stores with it works today
(measured). So this is not a new flag — it is the refusal saying which store it read and
where that path came from, so the reader can see the mismatch instead of deducing it.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from liteharness import cli


def _seed_store(root: Path, task: str) -> None:
    """A real store with one unrelated pattern, so the file-missing branch cannot fire."""
    (root / ".liteharness").mkdir(parents=True, exist_ok=True)
    cli.cmd_record_pattern(
        outcome="success",
        task_desc=task,
        agent_id="t365-fixture",
        project=str(root),
    )


class RefusalNamesTheStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _seed_store(self.root, "an unrelated pattern that lives here")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _refusal(self, fn, **kw) -> str:
        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(SystemExit):
            fn(**kw)
        return buf.getvalue()

    def test_verify_refusal_names_the_store_it_searched(self) -> None:
        msg = self._refusal(
            cli.cmd_verify_pattern,
            pattern_id="an-id-that-lives-in-another-project",
            level="human",
            actor="SilverBolt",
            evidence_ref="inbox:test",
            project=str(self.root),
        )
        self.assertIn(
            str(self.root / ".liteharness" / "patterns.jsonl"),
            msg,
            "the refusal never says which store it read, so a wrong-directory seat "
            "cannot tell scope from absence",
        )

    def test_verify_refusal_says_the_scope_came_from_the_flag(self) -> None:
        msg = self._refusal(
            cli.cmd_verify_pattern,
            pattern_id="absent",
            level="human",
            actor="SilverBolt",
            evidence_ref="inbox:test",
            project=str(self.root),
        )
        self.assertIn("--project", msg, "the reader cannot tell how the store was chosen")

    def test_verify_refusal_says_the_scope_came_from_the_cwd(self) -> None:
        """The case that produced the card: no --project, so the store is the cwd's."""
        import os

        here = os.getcwd()
        try:
            os.chdir(self.root)
            msg = self._refusal(
                cli.cmd_verify_pattern,
                pattern_id="absent",
                level="human",
                actor="SilverBolt",
                evidence_ref="inbox:test",
            )
        finally:
            os.chdir(here)
        self.assertIn("cwd", msg, "a cwd-derived scope must say so — that IS the defect")

    def test_revoke_refusal_names_the_store_it_searched(self) -> None:
        """
        Revoke has the same shape and one extra edge: it never checks that the
        attestations file exists at all, so a wrong directory and a genuinely wrong
        attestation id produce the SAME sentence. Naming the path separates them.
        """
        msg = self._refusal(
            cli.cmd_revoke_pattern,
            pattern_id="whatever",
            reason="because",
            prior_attestation_id="no-such-attestation",
            actor="SilverBolt",
            project=str(self.root),
        )
        self.assertIn(
            str(self.root / ".liteharness" / "pattern-attestations.jsonl"),
            msg,
            "revoke refuses without ever saying which attestation log it read",
        )

    def test_the_refusals_still_fail_closed(self) -> None:
        """
        CONTROL — naming the store must not turn a refusal into a pass. The whole
        design is fail-closed; a friendlier message that started attesting anyway
        would be a far worse defect than the one being fixed.
        """
        for fn, kw in (
            (
                cli.cmd_verify_pattern,
                dict(
                    pattern_id="absent",
                    level="human",
                    actor="SilverBolt",
                    evidence_ref="x",
                    project=str(self.root),
                ),
            ),
            (
                cli.cmd_revoke_pattern,
                dict(
                    pattern_id="absent",
                    reason="r",
                    prior_attestation_id="a",
                    actor="SilverBolt",
                    project=str(self.root),
                ),
            ),
        ):
            with self.subTest(fn=fn.__name__):
                buf = io.StringIO()
                with redirect_stderr(buf), self.assertRaises(SystemExit) as ctx:
                    fn(**kw)
                self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
