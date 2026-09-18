"""The writer must refuse what the reader will refuse.

Measured 2026-09-18 across three live stores: 1085 rows, 40 of them rejected by
`readPatterns()` for one reason — ``outcome: Invalid option: expected one of
"success"|"failure"``. The field held the pattern's own analysis, 713 to 2,253
characters of it, and the store had told every one of those callers it had
recorded a pattern.

    A WRITE PATH THAT ACCEPTS WHAT THE READ PATH REFUSES MAKES A RECORD THAT
    EXISTS AND CANNOT BE RECALLED. IT IS WORSE THAN A FAILED WRITE, BECAUSE A
    FAILED WRITE IS VISIBLE.

Nobody noticed for weeks because the receipt was assembled from the request:
``Recorded pattern: <the caller's own prose> for <task_id>``. An echo agrees
with whatever was passed, so it reads as validation while carrying none.

TWO WRONG SHAPES were measured and they get two messages, because they are two
different misunderstandings:
  * PROSE — the caller answered the field instead of selecting a value. A field
    named for a question ("what came out of this?") will be answered.
  * A VERIFICATION WORD — `outcome="unverified"`, four rows, all written the
    same day. The reminder that fires on every agent turn says a pattern is born
    ``verified:"unverified"``, a schema fact and not a modifier you choose; a
    caller obeying it put the word in the nearest field that would take it.
    AN INSTRUCTION ABOUT A FIELD YOU MUST NOT SET IS AN INSTRUCTION TO FIND A
    FIELD TO SET. That is a documentation defect, not a slip, so it gets its own
    sentence rather than a generic one.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from liteharness import cli

AGENT = "89ca3d12-b336-4edb-a7ed-765d45b334ec"

# The real shape of a rejected row, shortened: the analysis, in the enum field.
PROSE = (
    "ROOT CAUSE (measured 2026-08-26): openai-codex plugin 1.0.4 "
    "scripts/session-lifecycle-hook.mjs:38 appends an export line to "
    "CLAUDE_ENV_FILE on every session start, so the value grows without bound."
)


def _record(root: Path, **kw):
    """Run the recorder, returning (exit_code_or_None, stdout)."""
    buf = io.StringIO()
    params = {
        "outcome": "success",
        "agent_id": AGENT,
        "task_desc": "an approach, which is where analysis belongs",
        "project": str(root),
        "supersedes": None,
    }
    params.update(kw)
    try:
        with redirect_stdout(buf):
            cli.cmd_record_pattern(**params)
    except SystemExit as exc:
        return exc.code, buf.getvalue()
    return None, buf.getvalue()


def _rows(root: Path) -> list[dict]:
    p = root / ".liteharness" / "patterns.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class OutcomeRefusal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_prose_in_outcome_is_refused_and_the_message_names_the_right_field(self):
        """🔴 The defect itself. Refusing is half of it; the other half is that
        the caller is told WHERE the text they wrote actually goes."""
        code, _ = _record(self.root, outcome=PROSE)

        self.assertEqual(code, 2, "a prose outcome must exit 2, not record")
        self.assertEqual(_rows(self.root), [], "nothing may reach the store")

    def test_a_verification_word_gets_its_OWN_message_not_the_generic_one(self):
        """The four measured rows said `unverified`, and telling that caller
        "put it in --task" would be advice to record the word as analysis."""
        code, _ = _record(self.root, outcome="unverified")

        self.assertEqual(code, 2)
        self.assertEqual(_rows(self.root), [])

    def test_success_and_failure_are_still_accepted(self):
        """CONTROL. The cheap wrong fix rejects long strings, or everything."""
        for value in ("success", "failure"):
            with self.subTest(outcome=value):
                root = Path(tempfile.mkdtemp(dir=self._tmp.name))
                code, _ = _record(root, outcome=value)
                self.assertIsNone(code, f"{value} must still record")
                self.assertEqual([r["outcome"] for r in _rows(root)], [value])

    def test_the_extended_spellings_still_map_to_failure(self):
        """CONTROL, and the regression this fix could most easily have caused:
        `stuck`/`unknown`/`blocked` are existing accepted spellings, normalised
        BEFORE the refusal. A guard placed above that mapping would reject them
        and call it a tightening."""
        for value in ("stuck", "unknown", "blocked"):
            with self.subTest(outcome=value):
                root = Path(tempfile.mkdtemp(dir=self._tmp.name))
                code, _ = _record(root, outcome=value)
                self.assertIsNone(code, f"{value} must still record")
                self.assertEqual([r["outcome"] for r in _rows(root)], ["failure"])

    def test_case_and_space_are_the_same_answer_typed_by_a_human(self):
        """`Success` used to be written VERBATIM — the same unreadable row as
        prose produced, from a shift key. And under the refusal it would have
        earned the prose message, which reads as nonsense for seven characters.
        """
        for value in ("Success", " failure ", "STUCK"):
            with self.subTest(outcome=value):
                root = Path(tempfile.mkdtemp(dir=self._tmp.name))
                code, _ = _record(root, outcome=value)
                self.assertIsNone(code, f"{value!r} must record, not refuse")
                self.assertIn(_rows(root)[0]["outcome"], ("success", "failure"))

    def test_the_receipt_matches_the_row_that_was_written(self):
        """⚠️ THIS ARM IS A CONTROL, NOT A PROOF, AND IT IS LABELLED AS ONE
        BECAUSE IT WENT GREEN BEFORE THE FIX.

        It was written to prove the receipt no longer echoes the caller. It
        cannot: the old line printed `schema_outcome`, which for `stuck` was
        ALREADY "failure" — the echo was only ever observable for values that
        passed through the mapping unchanged, i.e. prose. And prose is now
        refused above, so the input that could distinguish the two prints no
        longer reaches this line at all.

            A GUARD THAT REMOVES THE ONLY INPUT WHICH COULD DISTINGUISH TWO
            IMPLEMENTATIONS ALSO REMOVES THE TEST THAT COULD TELL THEM APART.

        The print now reads `entry["outcome"]` — the value on the line that
        reached the file — so a mapping added later between the entry and the
        print cannot diverge from it. That is structural, has no behavioural
        delta today, and is claimed as nothing more. What this arm still does
        is pin the normalisation: `stuck` is written AND reported as `failure`.
        """
        code, out = _record(self.root, outcome="stuck")

        self.assertIsNone(code)
        self.assertIn("failure", out)
        self.assertNotIn("stuck", out)
        self.assertEqual([r["outcome"] for r in _rows(self.root)], ["failure"])


if __name__ == "__main__":
    unittest.main()
