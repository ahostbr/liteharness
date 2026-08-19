"""The two help texts for `register` must name the same flags.

There are two: the top-level `liteharness --help` listing, and register's own
"Usage:" line printed when --agent-id is missing. They drift, and the friendlier
one is the one people read.

Found 2026-08-19 by OpenBolt, hours after --session-pid was added: it read the
top-level help to learn the register flags and was told --session-pid did not
exist. --takeover and --canvas-session had been missing for longer.

Compares the FLAG SETS rather than the strings, so wrapping and ordering stay
free to differ.
"""
import inspect
import re
import unittest

from liteharness import cli

_FLAG = re.compile(r"--[a-z][a-z0-9-]*")


class HelpTextsAgreeTests(unittest.TestCase):
    def test_register_flags_match_between_the_two_help_texts(self):
        src = inspect.getsource(cli.main)

        usage = [l for l in src.splitlines()
                 if "Usage: liteharness register" in l and "print(" in l]
        self.assertEqual(len(usage), 1, "expected exactly one register usage line")
        own = set(_FLAG.findall(usage[0]))

        # The top-level block: the `register ...` line plus its continuations.
        lines = src.splitlines()
        start = next(i for i, l in enumerate(lines) if '"  register --agent-id ID' in l)
        block = []
        for l in lines[start:]:
            if "print(" not in l:
                break
            if "Update agent presence" in l:
                break
            block.append(l)
        listing = set(_FLAG.findall(" ".join(block)))

        self.assertEqual(
            own - listing, set(),
            "flags in register's own usage but MISSING from `liteharness --help`",
        )
        self.assertEqual(
            listing - own, set(),
            "flags in `liteharness --help` but not in register's own usage",
        )


if __name__ == "__main__":
    unittest.main()
