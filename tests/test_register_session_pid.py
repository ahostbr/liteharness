"""`register --session-pid` — the owning process, so liveness is a fact not a guess.

WHY THIS EXISTS. Two mechanisms read `presence.session_pid` and BOTH treat a
falsy one as "not live":

  * `_agent_record_live`, which is what stops `--takeover` stealing a name from a
    running agent; and
  * the janitor's dead-owner purge, which is what stops dead rows accumulating.

`liteharness.hooks` has always recorded it. `liteharness.cli register` never did.
So every agent that registers through the CLI rather than through a Claude Code
hook was permanently indistinguishable from a corpse to both.

Measured on the live roster 2026-08-19: SIX rows for one LiteTUI seat, four of
them dead and unreapable; and two live probes in which the second took the name
from the first, because the first read as a ghost.

The tests that matter here are the ones that would have caught the ORIGINAL bug:
absence of the field, and a dead pid. A test that only checks the happy path
passes just as well against code that writes nothing.
"""
import json
import os
import unittest
from unittest import mock

from liteharness import cli, config


class RegisterSessionPidTests(unittest.TestCase):
    def setUp(self):
        self._tmp = mock.patch.object(config, "get_root")
        root = self.enterContext(mock.patch.object(config, "get_root")) if hasattr(
            self, "enterContext") else None
        # Python 3.10 compatibility: fall back to manual patching.
        if root is None:
            self._patcher = mock.patch.object(config, "get_root")
            root = self._patcher.start()
            self.addCleanup(self._patcher.stop)
        import tempfile
        from pathlib import Path
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        root.return_value = Path(self._dir.name)
        self.root = Path(self._dir.name)

    def presence(self, agent_id="a-1"):
        p = self.root / "agents" / f"{agent_id}.json"
        return json.loads(p.read_text(encoding="utf-8"))

    # ── the fix ────────────────────────────────────────────────────────────
    def test_records_the_callers_live_pid(self):
        cli.cmd_register("a-1", cli="litetui", session_pid=os.getpid())
        self.assertEqual(self.presence()["session_pid"], os.getpid())

    # ── the original bug: the field was simply never written ───────────────
    def test_without_the_flag_nothing_is_recorded(self):
        """Absence must stay absent, not become a guess.

        This is the pre-fix behaviour and it is deliberately preserved: a caller
        that cannot name its owning process must not have one inferred for it.
        os.getppid() would be wrong for anything launched behind a wrapper shell,
        and recording a transient parent trades an accumulation bug for a
        disappearance bug -- the agent reaped while it is still running.
        """
        cli.cmd_register("a-1", cli="litetui")
        self.assertIsNone(self.presence().get("session_pid"))

    # ── a dead pid is worse than no pid ────────────────────────────────────
    def test_a_dead_pid_is_refused_not_recorded(self):
        """Writing a dead pid marks the row reapable the instant it is written."""
        with mock.patch("liteharness.hooks._pid_alive", return_value=False):
            cli.cmd_register("a-1", cli="litetui", session_pid=999_999_999)
        self.assertIsNone(self.presence().get("session_pid"))

    def test_the_refusal_is_announced(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with mock.patch("liteharness.hooks._pid_alive", return_value=False):
            with redirect_stdout(buf):
                cli.cmd_register("a-1", cli="litetui", session_pid=999_999_999)
        self.assertIn("not a live process", buf.getvalue())

    # ── it actually changes what the liveness check concludes ──────────────
    def test_a_registered_pid_makes_the_agent_read_as_LIVE(self):
        """The whole point. Before the fix this returned False for every live
        CLI-registered agent, which is what let --takeover steal a held name."""
        cli.cmd_register("a-1", cli="litetui", session_pid=os.getpid())
        self.assertTrue(cli._agent_record_live("a-1"))

    def test_without_a_pid_a_LIVE_agent_still_reads_as_a_ghost(self):
        """The bug, pinned. Same fresh registration, no pid -> not live.

        Kept as a test rather than a comment so nobody 'simplifies'
        _agent_record_live on the assumption that a fresh heartbeat is enough.
        """
        cli.cmd_register("a-1", cli="litetui")
        self.assertFalse(cli._agent_record_live("a-1"))

    # ── argv ───────────────────────────────────────────────────────────────
    def test_argv_rejects_a_non_integer(self):
        import sys
        argv = ["liteharness", "register", "--agent-id", "a-1",
                "--session-pid", "not-a-number"]
        with mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_usage_line_documents_the_flag(self):
        import inspect
        src = inspect.getsource(cli.main)
        self.assertIn("--session-pid PID", src)


if __name__ == "__main__":
    unittest.main()
