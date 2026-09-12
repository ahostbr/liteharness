"""A seat that registers or takes a name tells every live orchestrator by inbox.

Ryan 2026-09-12: a Codex Desktop task worked in LiteSuite for an hour before the
orchestrator knew it existed. `discover` only answers when asked.

The arms that would have caught the original gap: a NEW presence file produces
a message to the orchestrator; a mere re-register of the same seat does not
(or every heartbeat would spam the orchestrator); a rename does; a dead or
exited orchestrator gets nothing; an orchestrator does not announce to itself.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from liteharness import cli, config, inbox


class AnnounceRegistrationTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        p = mock.patch.object(config, "get_root", return_value=self.root)
        p.start(); self.addCleanup(p.stop)
        ib = self.root / "inbox"
        for name, sub in (("INBOX_ROOT", ""), ("INBOX_NEW", "new"), ("INBOX_CUR", "cur"),
                          ("INBOX_DONE", "done"), ("INBOX_TMP", "tmp")):
            q = mock.patch.object(inbox, name, ib / sub if sub else ib)
            q.start(); self.addCleanup(q.stop)
        (self.root / "agents").mkdir(parents=True)

    def orchestrator(self, agent_id="orch-1", **extra):
        row = {"agent_id": agent_id, "tier": "orchestrator", "name": "Sentinel",
               "last_seen": datetime.now(timezone.utc).isoformat(),
               "session_pid": os.getpid()}
        row.update(extra)
        (self.root / "agents" / f"{agent_id}.json").write_text(json.dumps(row), encoding="utf-8")

    def messages(self):
        out = []
        for f in sorted(inbox.INBOX_NEW.glob("*.json")):
            out.append(json.loads(f.read_text(encoding="utf-8")))
        return out

    # ── the gap ────────────────────────────────────────────────────────────
    def test_new_seat_alerts_the_live_orchestrator(self):
        self.orchestrator()
        cli.cmd_register("seat-1", cli="codex-desktop", model="gpt-6-astra", name="SilentCrypt")
        msgs = self.messages()
        self.assertEqual(len(msgs), 1)
        m = msgs[0]
        self.assertEqual(m["to"], "orch-1")
        self.assertEqual(m["from"], "seat-1")
        self.assertIn("SilentCrypt", m["body"])
        self.assertIn("seat-1", m["body"])
        self.assertIn("codex-desktop/gpt-6-astra", m["body"])

    # ── the failure the fix must not introduce ─────────────────────────────
    def test_re_registering_the_same_seat_is_silent(self):
        self.orchestrator()
        cli.cmd_register("seat-1", cli="litetui", name="OpenBolt")
        cli.cmd_register("seat-1", cli="litetui", name="OpenBolt")
        cli.cmd_register("seat-1", cli="litetui")
        self.assertEqual(len(self.messages()), 1)

    def test_a_rename_is_announced(self):
        self.orchestrator()
        cli.cmd_register("seat-1", cli="litetui", name="LiteTUI")
        cli.cmd_register("seat-1", cli="litetui", name="OpenBolt")
        bodies = [m["body"] for m in self.messages()]
        self.assertEqual(len(bodies), 2)
        # Maildir filenames are not ordered within a second: match by content.
        self.assertTrue(any("took the name OpenBolt (was LiteTUI)" in b for b in bodies), bodies)

    def test_exited_or_dead_orchestrator_gets_nothing(self):
        self.orchestrator("orch-gone", exited_at=datetime.now(timezone.utc).isoformat())
        self.orchestrator("orch-dead", session_pid=2 ** 22 + 12345)
        cli.cmd_register("seat-1", cli="litetui", name="OpenBolt")
        self.assertEqual(self.messages(), [])

    def test_orchestrator_does_not_announce_to_itself(self):
        cli.cmd_register("orch-1", cli="claude-code", tier="orchestrator", name="Sentinel")
        self.assertEqual(self.messages(), [])

    def test_no_announce_env_keeps_test_children_quiet(self):
        self.orchestrator()
        with mock.patch.dict(os.environ, {"LITEHARNESS_NO_ANNOUNCE": "1"}):
            cli.cmd_register("seat-1", cli="litetui", name="JadePack")
        self.assertEqual(self.messages(), [])

    def test_two_live_orchestrators_both_hear_it(self):
        self.orchestrator("orch-1")
        self.orchestrator("orch-2")
        cli.cmd_register("seat-1", cli="litetui", name="OpenBolt")
        self.assertEqual(sorted(m["to"] for m in self.messages()), ["orch-1", "orch-2"])


if __name__ == "__main__":
    unittest.main()
