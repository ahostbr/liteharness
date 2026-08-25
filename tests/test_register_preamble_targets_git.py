"""The register / PostCompact preamble must not send agents to a memory file.

This block is emitted by `register_presence` on EVERY register and EVERY
compaction, which makes it the highest-frequency instruction in the whole system
— higher than the every-other-turn nudge. git-as-memory v2 WS1 retargeted the
nudge; leaving this block pointing at MEMORY.md would have kept the old
instruction arriving on a schedule nobody chose, and the louder channel would
have quietly won.

Read against SOURCE rather than by invoking register_presence: the function
writes presence files and prints an identity block built from live config, so
calling it in a unit test asserts against the harness's state instead of the
text. What is being pinned here is the text itself.
"""

import inspect
import unittest

from liteharness import hooks


class RegisterPreambleTargetsGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = inspect.getsource(hooks.register_presence)

    def test_the_preamble_is_actually_in_this_function(self) -> None:
        # Control. Every assertion below is a substring check over `self.src`,
        # and all of them pass trivially if the block has merely MOVED to another
        # function. This anchors them to a known-present neighbour.
        self.assertIn("CHAIN OF COMMAND", self.src)
        self.assertGreater(len(self.src), 2000)

    def test_it_does_not_send_agents_to_a_memory_file(self) -> None:
        self.assertNotIn("MEMORY IS CAPPED", self.src)
        self.assertNotIn("MEMORY.md", self.src)

    def test_it_names_where_durable_knowledge_actually_goes(self) -> None:
        # The negative above is satisfied by deleting the block outright, which
        # would lose the doctrine instead of retargeting it. This is the half
        # that forbids that.
        self.assertIn("lst run pattern", self.src)
        self.assertIn("COMMIT BODY", self.src)
        self.assertIn("HANDOFF", self.src)
        self.assertIn('verified:"unverified"', self.src)


if __name__ == "__main__":
    unittest.main()
