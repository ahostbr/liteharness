"""No module may define the same top-level name twice.

WHY THIS EXISTS. On 2026-08-12 `ad33424` merged a second liteharness tree into
this one and appended a whole second copy of the memory-nudge subsystem to the
tail of `hooks.py`: `_turn_count_file_for`, `_bump_turn_counter`,
`_resolve_memory_index_path` and `memory_nudge`, all defined twice at top level.
Python keeps the LAST definition, so the tail copy silently won for a week.

Three days later `6736b4e` — whose subject line is literally "one definition for
the memory nudge" — improved the FIRST copy and did not delete the second. The
commit asserted the property in its own title and did not achieve it, and nothing
could tell anyone, because a duplicate definition is not a syntax error, not a
warning, and not a behaviour change until the two copies drift.

Measured 2026-08-19 before the cleanup: three of the four pairs were
behaviourally IDENTICAL under AST comparison with docstrings stripped, and the
fourth differed only in style. So this was never a live bug — it was a loaded
gun. That is precisely why it needed a test rather than a fix: the damage is
entirely in the future, at the moment someone edits one copy.

The failure mode is a TREE MERGE, which is how it arrived and how it will come
back. Import-time shadowing is invisible to every other gate we run.
"""

import ast
import collections
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "liteharness"


def _duplicate_top_level_names(source: str) -> dict[str, int]:
    """Top-level def/class/async-def names defined more than once."""
    tree = ast.parse(source)
    counts: collections.Counter = collections.Counter()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name] += 1
    return {name: n for name, n in counts.items() if n > 1}


class NoDuplicateDefinitionsTests(unittest.TestCase):
    def test_no_module_defines_a_top_level_name_twice(self) -> None:
        offenders = {}
        scanned = 0
        for path in sorted(PACKAGE.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                dups = _duplicate_top_level_names(source)
            except SyntaxError:
                continue
            scanned += 1
            if dups:
                offenders[str(path.relative_to(PACKAGE))] = dups

        # POSITIVE CONTROL: a pass is only meaningful if the scan actually ran.
        # An empty file list would satisfy the assertion below vacuously, which
        # is the exact way a green gate can measure nothing at all.
        self.assertGreater(scanned, 5, "scanned too few modules — the gate is not looking at anything")

        self.assertEqual(
            offenders,
            {},
            "top-level names defined more than once (the later one silently wins): " + repr(offenders),
        )

    def test_the_detector_can_actually_fail(self) -> None:
        """Proven able to fail — a gate that cannot go red has never been armed."""
        clean = "def a():\n    pass\n\n\ndef b():\n    pass\n"
        self.assertEqual(_duplicate_top_level_names(clean), {})

        poisoned = clean + "\n\ndef a():\n    pass\n"
        self.assertEqual(_duplicate_top_level_names(poisoned), {"a": 2})

        # and it must not be fooled by a nested redefinition, which is legal and
        # not what this gate is about
        nested = "def outer():\n    def a():\n        pass\n\n    def a():\n        pass\n"
        self.assertEqual(_duplicate_top_level_names(nested), {})


if __name__ == "__main__":
    unittest.main()
