"""Pattern query recall — one absent word must not erase four present ones.

🔴 THE DEFECT (T106). `_fts5_phrase_query` space-joined its quoted tokens, and
space-separated FTS5 terms are an IMPLICIT AND. So a five-word query whose first
four words matched a pattern perfectly returned NOTHING because the fifth did
not appear. Every seat querying collective memory in a natural sentence was
getting silence and reading it as "no such pattern".

⚠️ THE QUOTING ITSELF IS NOT THE BUG AND IS NOT REMOVED. Its docstring names the
hazard it exists for: FTS5 operators (-, OR, AND, NOT, NEAR, column:, parens,
quotes) turn a literal user string into a syntax error or a wrong match.
Measured on this box before touching anything:

    'cat -dog'      -> OperationalError: no such column: dog
    '"unbalanced'   -> OperationalError: unterminated string
    'a AND NOT b'   -> OperationalError: fts5: syntax error near "NOT"

Deleting the quotes to fix recall re-arms a bug someone already fixed, so the
injection control below is as load-bearing as the recall test.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from liteharness import cli


def _record(root: Path, agent: str, desc: str) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_record_pattern(
            outcome="success",
            agent_id=agent,
            task_desc=desc,
            project=str(root),
        )
    return buf.getvalue().strip().rsplit(" ", 1)[-1]


def _query(root: Path, query: str, top: int = 10) -> list[dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_query_patterns(top=top, fmt="json", query=query, project=str(root))
    return json.loads(buf.getvalue())


class PatternQueryRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".liteharness").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_one_absent_word_does_not_erase_the_match(self) -> None:
        """🔴 THE CANONICAL FAILURE. Four words match; the fifth does not."""
        _record(self.root, "agent-a", "electron worktree junction removal hazard")

        hits = _query(self.root, "electron worktree junction removal zzzabsentword")

        self.assertEqual(
            len(hits),
            1,
            "a query whose other four tokens matched perfectly returned nothing "
            "because one token was absent — implicit AND",
        )

    def test_a_query_of_only_absent_words_still_returns_nothing(self) -> None:
        """The control for the test above.

        Without it, "one absent word still matches" would pass against an
        implementation that returns everything for every query — which is not
        recall, it is the search doing nothing at all.
        """
        _record(self.root, "agent-a", "electron worktree junction removal hazard")

        self.assertEqual(_query(self.root, "zzzabsentword qqqmissing"), [])

    def test_more_matching_words_ranks_first(self) -> None:
        """Recall must not cost precision ORDERING.

        bm25 already sorts a row matching more query tokens above one matching
        fewer, which is why OR-ing the tokens is safe: the near-miss appears,
        below the full match, instead of the full match appearing alone.
        """
        _record(self.root, "agent-a", "worktree junction removal destroyed the target")
        _record(self.root, "agent-b", "junction only, nothing else in common")

        hits = _query(self.root, "worktree junction removal destroyed")

        self.assertEqual(len(hits), 2, "both rows share at least one token")
        self.assertIn("worktree", hits[0]["description"])

    def test_an_fts5_operator_in_a_user_string_cannot_break_the_query(self) -> None:
        """🔴 THE PROPERTY THAT MUST NOT REGRESS.

        Each of these raises OperationalError when handed to FTS5 unquoted.
        None may raise here, and none may be interpreted as an operator.
        """
        _record(self.root, "agent-a", "cat and dog and bird")

        for hostile in (
            "cat -dog",
            'cat "unbalanced',
            "cat AND NOT dog",
            "NEAR(cat dog)",
            "description:cat",
            "cat OR dog",
            "(cat",
        ):
            with self.subTest(query=hostile):
                # The assertion is that this does not raise. A syntax error
                # here is the injection bug coming back.
                hits = _query(self.root, hostile)
                self.assertIsInstance(hits, list)

    def test_a_negation_operator_is_read_as_a_word_not_an_operator(self) -> None:
        """`-dog` must not EXCLUDE dog; it is a literal the user typed.

        Not-raising is the weaker half. If `-dog` were parsed as an operator the
        query would silently return the wrong set, which no exception reports.
        """
        _record(self.root, "agent-a", "cat and dog together")
        _record(self.root, "agent-b", "cat alone")

        hits = _query(self.root, "cat -dog")

        # The row containing BOTH words must still be present. Under operator
        # parsing it would be the one row excluded.
        self.assertTrue(
            any("dog" in hit["description"] for hit in hits),
            "'-dog' was parsed as an exclusion instead of a literal token",
        )

    def test_an_empty_query_is_still_empty(self) -> None:
        _record(self.root, "agent-a", "anything at all")
        self.assertEqual(_query(self.root, "   "), [])


if __name__ == "__main__":
    unittest.main()
