"""T364 — a message to an id with NO presence file must not be deleted on arrival.

THE DEFECT, measured 2026-09-05. `cli send --force` exists precisely so you can write to
an id the registry does not know: the error that offers it says so in its own text —
*"Pass --force to send to an id the registry does not know."* But "addressed to an id with
no `agents/<id>.json`" is exactly the condition `_purge_orphaned_messages` calls ORPHANED,
so the sweep collects the class `--force` creates. Message `806ac83b` was accepted at 11:1x
("Sent message 806ac83b"), swept at 12:3x inside "Cleaned up 10 expired/orphaned
message(s)", with no log line naming it. Neither end was told for an hour and forty
minutes, and it only surfaced because the recipient went looking.

    --force GETS THE MESSAGE ACCEPTED. IT DOES NOT GET IT KEPT.

T350's `_recipient_is_live` closes the RACE — a record momentarily absent when `known_ids`
was photographed. It cannot close this one, and not by oversight: it returns True only when
a record EXISTS, and `--force` is the case where the record was NEVER there. The documented
hazard, the fix written for it, and this incident are all about the same `unlink`, and the
fix's own criterion exempts the biggest instance.

WHAT IS PINNED HERE:
  1. fresh mail to a record-less id SURVIVES, so a recipient that registers later still
     gets it (the 806ac83b reproduction — this is the test that must be RED before the fix);
  2. the grace is BOUNDED by the message's own TTL, so this is not "the sweep stops
     deleting" — an orphan past its TTL still goes;
  3. `broadcast` stays exempt at every age;
  4. every removal the sweep DOES perform is logged with id/from/to, because the whole
     defect is that a deletion nobody can notice is worse than a refusal anyone can retry.

⚠️ THE CONTROLS ARE NOT DECORATION. Tests 1 and 3 pass together only if the sweep both
spares and deletes; either alone is satisfiable by a sweep that does nothing, or by one
that deletes everything.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from liteharness import config, hooks, inbox

RECORDLESS = "806ac83b-0000-0000-0000-000000000000"


class OrphanSweepSparesForcedMailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_root = config.HARNESS_ROOT
        config.HARNESS_ROOT = self.root
        (self.root / "agents").mkdir(parents=True)
        self.new = self.root / "inbox" / "new"
        self.new.mkdir(parents=True)
        self._patch = mock.patch.object(inbox, "INBOX_NEW", self.new)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        config.HARNESS_ROOT = self.original_root
        self.temp_dir.cleanup()

    def _message(
        self,
        name: str,
        to: str,
        *,
        age_minutes: float = 0.0,
        ttl_minutes: int | None = inbox.DEFAULT_TTL_MINUTES,
        timestamp: str | None = "",
    ) -> Path:
        """Write one message. `age_minutes` ages BOTH the envelope and the file.

        Both, deliberately: the implementation may read either, and a fixture that aged
        only one would let a passing test hide which source is actually consulted.
        """
        sent_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        envelope: dict[str, object] = {
            "id": name,
            "to": to,
            "from": "11679396-e0cf-47fa-9b50-58582d967a1a",
            "body": "hi",
        }
        if timestamp != "":
            envelope["timestamp"] = timestamp if timestamp is not None else None
        else:
            envelope["timestamp"] = sent_at.isoformat()
        if ttl_minutes is not None:
            envelope["ttl_minutes"] = ttl_minutes
        path = self.new / f"{name}.json"
        path.write_text(json.dumps(envelope), encoding="utf-8")
        stamp = sent_at.timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def _register(self, agent_id: str) -> None:
        (self.root / "agents" / f"{agent_id}.json").write_text("{}", encoding="utf-8")

    def _swept_log(self) -> list[dict]:
        path = self.root / "swept-messages.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ------------------------------------------------------------------ 1. the defect

    def test_fresh_mail_to_a_recordless_recipient_survives_the_sweep(self) -> None:
        """806ac83b's exact fate. RED before the fix."""
        msg = self._message("forced", RECORDLESS)
        removed = hooks._purge_orphaned_messages()
        self.assertTrue(
            msg.exists(),
            "a --force send to an id with no presence file was deleted on the next sweep "
            "— the sender was told 'Sent message' and nothing ever contradicted it",
        )
        self.assertEqual(0, removed)

    def test_it_survives_long_enough_for_the_recipient_to_register(self) -> None:
        """The grace is only worth anything if it spans a real registration."""
        msg = self._message("waiting", RECORDLESS)
        hooks._purge_orphaned_messages()
        self._register(RECORDLESS)
        hooks._purge_orphaned_messages()
        self.assertTrue(msg.exists(), "mail died in the window it exists to survive")

    # ------------------------------------------------------- 2. the grace is BOUNDED

    def test_CONTROL_an_orphan_past_its_ttl_is_still_purged(self) -> None:
        """Without this, test 1 passes against a sweep that has stopped deleting."""
        msg = self._message("stale-orphan", RECORDLESS, age_minutes=inbox.DEFAULT_TTL_MINUTES + 5)
        removed = hooks._purge_orphaned_messages()
        self.assertFalse(msg.exists(), "the sweep no longer purges genuine orphans")
        self.assertEqual(1, removed)

    def test_the_envelopes_own_ttl_is_honoured_not_a_hardcoded_hour(self) -> None:
        short = self._message("short-ttl", RECORDLESS, age_minutes=10, ttl_minutes=5)
        long = self._message("long-ttl", RECORDLESS, age_minutes=10, ttl_minutes=120)
        hooks._purge_orphaned_messages()
        self.assertFalse(short.exists(), "a 5-minute TTL was ignored in favour of the default")
        self.assertTrue(long.exists(), "a 120-minute TTL was cut short at the default hour")

    # ------------------------------------------------------------ 3. broadcast exempt

    def test_broadcast_is_never_purged_even_long_past_its_ttl(self) -> None:
        msg = self._message("announcement", "broadcast", age_minutes=inbox.DEFAULT_TTL_MINUTES * 10)
        hooks._purge_orphaned_messages()
        self.assertTrue(msg.exists(), "a fleet broadcast was swept as an orphan")

    # --------------------------------------------------------------- 4. the log line

    def test_every_removal_names_the_id_the_sender_and_the_recipient(self) -> None:
        self._message("stale-orphan", RECORDLESS, age_minutes=inbox.DEFAULT_TTL_MINUTES + 5)
        hooks._purge_orphaned_messages()
        rows = self._swept_log()
        self.assertEqual(1, len(rows), "a deletion happened with nothing written down")
        row = rows[0]
        self.assertEqual("stale-orphan", row.get("id"))
        self.assertEqual(RECORDLESS, row.get("to"))
        self.assertEqual("11679396-e0cf-47fa-9b50-58582d967a1a", row.get("from"))

    def test_CONTROL_nothing_is_logged_when_nothing_is_removed(self) -> None:
        """A log that fires on every sweep would bury the removals it exists to surface."""
        self._message("forced", RECORDLESS)
        hooks._purge_orphaned_messages()
        self.assertEqual([], self._swept_log())

    # ------------------------------------------------- 5. unreadable age fails SAFE

    def test_a_message_with_no_usable_timestamp_falls_back_to_file_age(self) -> None:
        """An envelope written by something other than inbox.send still ages out.

        Falling back to mtime rather than sparing it forever: an anomalous file that can
        never be collected is an accumulation bug, and the file's own age is a fact even
        when its envelope is not.
        """
        fresh = self._message("no-ts-fresh", RECORDLESS, timestamp=None)
        old = self._message(
            "no-ts-old", RECORDLESS, age_minutes=inbox.DEFAULT_TTL_MINUTES + 5, timestamp=None
        )
        hooks._purge_orphaned_messages()
        self.assertTrue(fresh.exists(), "a young message was deleted because its envelope was odd")
        self.assertFalse(old.exists(), "a message with no timestamp could never be collected")

    def test_a_message_with_a_CORRUPT_timestamp_is_treated_the_same_way(self) -> None:
        fresh = self._message("bad-ts-fresh", RECORDLESS, timestamp="not-a-date")
        old = self._message(
            "bad-ts-old", RECORDLESS, age_minutes=inbox.DEFAULT_TTL_MINUTES + 5, timestamp="not-a-date"
        )
        hooks._purge_orphaned_messages()
        self.assertTrue(fresh.exists())
        self.assertFalse(old.exists())

    # ------------------------------------------------------- 6. T350 must not regress

    def test_CONTROL_mail_for_a_registered_agent_still_survives(self) -> None:
        agent_id = "11111111-2222-3333-4444-555555555555"
        self._register(agent_id)
        msg = self._message("keep", agent_id, age_minutes=inbox.DEFAULT_TTL_MINUTES * 10)
        hooks._purge_orphaned_messages()
        self.assertTrue(
            msg.exists(),
            "T350 regressed: mail for a LIVE registered agent was deleted for being old — "
            "age is a licence to collect an ORPHAN, never a reason to delete deliverable mail",
        )


if __name__ == "__main__":
    unittest.main()
