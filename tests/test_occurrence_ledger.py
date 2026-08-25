"""The occurrence ledger's four load-bearing properties (each seen RED first
against ledger-less semantics — the red harness patches acquire to the
naive always-win shape and reruns THIS suite; both runs in the commit body):

  SIMULTANEOUS ACQUIRE   — two contenders race one (job_id, slot); exactly one wins
  STALE TAKEOVER         — a dead owner lapses; exactly ONE of two contenders
                           takes over, atomically (no observe-then-race window)
  LONG-RUN RENEWAL       — a run outliving the base TTL keeps its claim via heartbeat
  COMPLETED NEVER RERUNS — a completed occurrence refuses every later claim

Plus the tick-level consequence: two racing librarian-ticks produce exactly
one spawned process. The spawned command is injected (a marker script), so the
test measures the COORDINATION, not claude.
"""

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from liteharness import occurrences as occ
from liteharness.librarian_tick import run_tick, slot_for_daily_cron


class LedgerProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "occurrences.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _acquire_racing(self, n: int, ttl: float = 300.0) -> list[dict]:
        """n threads, separate connections, same slot, distinct owners."""
        results: list[dict] = [None] * n  # type: ignore[list-item]
        barrier = threading.Barrier(n)

        def contend(i: int) -> None:
            conn = occ.open_ledger(self.db)
            try:
                barrier.wait()
                results[i] = occ.acquire(
                    conn, "job-1", "2026-08-25T03:30", f"owner-{i}", ttl,
                    db_path=self.db,
                )
            finally:
                conn.close()

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_simultaneous_acquire_exactly_one_winner(self) -> None:
        results = self._acquire_racing(4)
        winners = [r for r in results if r["won"]]
        self.assertEqual(len(winners), 1, f"{len(winners)} winners: {results}")

    def test_stale_takeover_is_atomic_and_single(self) -> None:
        conn = occ.open_ledger(self.db)
        first = occ.acquire(conn, "job-1", "2026-08-25T03:30", "dead-owner", 0.05,
                            db_path=self.db)
        self.assertTrue(first["won"])
        conn.close()
        time.sleep(0.2)  # the owner's claim lapses; no heartbeat renews it

        results = self._acquire_racing(2)
        winners = [r for r in results if r["won"]]
        self.assertEqual(len(winners), 1, f"takeover raced: {results}")
        self.assertEqual(winners[0]["reason"], "stale-takeover")

    def test_long_run_renewal_keeps_the_claim(self) -> None:
        conn = occ.open_ledger(self.db)
        got = occ.acquire(conn, "job-1", "2026-08-25T03:30", "slow-runner", 0.3,
                          db_path=self.db)
        self.assertTrue(got["won"])

        # The run outlives its base TTL; heartbeats extend it each cycle.
        contender_won = False
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            time.sleep(0.1)
            self.assertTrue(
                occ.heartbeat(conn, "job-1", "2026-08-25T03:30", "slow-runner", 0.3)["renewed"]
            )
            c2 = occ.open_ledger(self.db)
            try:
                if occ.acquire(c2, "job-1", "2026-08-25T03:30", "usurper", 0.3,
                               db_path=self.db)["won"]:
                    contender_won = True
                    break
            finally:
                c2.close()
        conn.close()
        self.assertFalse(contender_won, "a heartbeating run was usurped mid-run")

    def test_completed_never_reruns(self) -> None:
        conn = occ.open_ledger(self.db)
        occ.acquire(conn, "job-1", "2026-08-25T03:30", "winner", 0.05, db_path=self.db)
        occ.complete(conn, "job-1", "2026-08-25T03:30", "winner", "success",
                     db_path=self.db)
        conn.close()
        time.sleep(0.2)  # even long past expiry...

        for attempt in range(3):
            c = occ.open_ledger(self.db)
            try:
                got = occ.acquire(c, "job-1", "2026-08-25T03:30",
                                  f"late-{attempt}", 300.0, db_path=self.db)
            finally:
                c.close()
            self.assertFalse(got["won"], "a completed occurrence was re-acquired")
            self.assertEqual(got["reason"], "completed")


class ReleaseTests(unittest.TestCase):
    """release() ABANDONS an unfinished claim (the lifetime-lock pattern —
    complete() on a lock would burn the slot forever). Owner-matched and
    never-completed only; executed work must still complete()."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "occurrences.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_release_reopens_the_slot_immediately(self) -> None:
        conn = occ.open_ledger(self.db)
        occ.acquire(conn, "lock", "lifetime", "holder", 300.0, db_path=self.db)
        self.assertTrue(
            occ.release(conn, "lock", "lifetime", "holder", db_path=self.db)["released"]
        )
        # No TTL wait: the next contender claims at once.
        got = occ.acquire(conn, "lock", "lifetime", "next-holder", 300.0, db_path=self.db)
        conn.close()
        self.assertTrue(got["won"])
        self.assertEqual(got["reason"], "claimed")

    def test_release_refuses_non_owner_and_completed(self) -> None:
        conn = occ.open_ledger(self.db)
        occ.acquire(conn, "job-1", "2026-08-25T03:30", "winner", 300.0, db_path=self.db)
        self.assertFalse(
            occ.release(conn, "job-1", "2026-08-25T03:30", "impostor", db_path=self.db)["released"]
        )
        occ.complete(conn, "job-1", "2026-08-25T03:30", "winner", "success", db_path=self.db)
        self.assertFalse(
            occ.release(conn, "job-1", "2026-08-25T03:30", "winner", db_path=self.db)["released"],
            "a completed occurrence is history and must never be deleted",
        )
        got = occ.acquire(conn, "job-1", "2026-08-25T03:30", "late", 300.0, db_path=self.db)
        conn.close()
        self.assertFalse(got["won"], "completed-never-reruns must survive release attempts")


class TickTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.db = self.base / "occurrences.sqlite"
        self.marker = self.base / "spawns.log"
        self.schedules = self.base / "schedules.json"
        self.schedules.write_text(json.dumps({
            "settings": {"enabled": True},
            "jobs": [{
                "id": "librarian-job",
                "name": "Nightly Librarian",
                "enabled": True,
                "schedule": {"type": "cron", "expression": "30 3 * * *"},
                "action": {"type": "prompt", "prompt": "/ls-librarian",
                           "workdir": str(self.base)},
            }],
        }), encoding="utf-8")
        # The injected command: append one line to the marker, atomically-ish.
        self.exec_argv = [
            sys.executable, "-c",
            "import sys, pathlib; "
            f"p = pathlib.Path(r'{self.marker}'); "
            "f = open(p, 'a'); f.write('spawn\\n'); f.close()",
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _tick(self) -> int:
        return run_tick(
            "librarian-job",
            exec_argv=self.exec_argv,
            db_path=self.db,
            schedules_path=self.schedules,
            heartbeat_seconds=0.2,
            ttl_seconds=5.0,
        )

    def test_two_racing_ticks_spawn_exactly_once(self) -> None:
        # Exceptions are captured, and the barrier carries a timeout: a thread
        # that dies pre-barrier must surface its error, never deadlock the
        # sibling (a hang hides the true failure).
        outcomes: list[object] = [None, None]
        barrier = threading.Barrier(2, timeout=60)

        def go(i: int) -> None:
            try:
                barrier.wait()
                outcomes[i] = self._tick()
            except Exception as exc:  # noqa: BLE001 — the assert below reports it
                outcomes[i] = exc

        threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        spawns = self.marker.read_text(encoding="utf-8").count("spawn") if self.marker.exists() else 0
        self.assertEqual(
            sorted(repr(o) for o in outcomes), ["0", "0"],
            f"a tick errored instead of losing cleanly: {outcomes!r}",
        )
        self.assertEqual(spawns, 1, f"{spawns} spawns from 2 racing ticks ({outcomes!r})")

    def test_second_tick_after_completion_does_nothing(self) -> None:
        self.assertEqual(self._tick(), 0)
        self.assertEqual(self._tick(), 0)
        spawns = self.marker.read_text(encoding="utf-8").count("spawn")
        self.assertEqual(spawns, 1, "a completed slot was re-run by a later tick")

    def test_disabled_job_is_a_no_op(self) -> None:
        """A stale schtasks entry firing after a disable must do nothing —
        the task registration is never the authority on whether a job runs."""
        data = json.loads(self.schedules.read_text(encoding="utf-8"))
        data["jobs"][0]["enabled"] = False
        self.schedules.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self._tick(), 0)
        self.assertFalse(self.marker.exists(), "a disabled job was executed")

    def test_non_daily_cron_is_refused(self) -> None:
        data = json.loads(self.schedules.read_text(encoding="utf-8"))
        data["jobs"][0]["schedule"]["expression"] = "*/5 * * * *"
        self.schedules.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self._tick(), 2, "a non-daily cron must refuse, not guess")


class SlotDerivationTests(unittest.TestCase):
    def test_daily_slot_before_and_after_fire_time(self) -> None:
        after = datetime(2026, 8, 25, 12, 0)
        self.assertEqual(slot_for_daily_cron("30 3 * * *", after), "2026-08-25T03:30")
        before = datetime(2026, 8, 25, 2, 0)
        self.assertEqual(slot_for_daily_cron("30 3 * * *", before), "2026-08-24T03:30")
        exact = datetime(2026, 8, 25, 3, 30)
        self.assertEqual(slot_for_daily_cron("30 3 * * *", exact), "2026-08-25T03:30")

    def test_non_daily_forms_return_none(self) -> None:
        now = datetime(2026, 8, 25, 12, 0)
        for expr in ("*/5 * * * *", "30 3 * * 1", "30 3 1 * *", "not a cron", ""):
            self.assertIsNone(slot_for_daily_cron(expr, now), expr)


if __name__ == "__main__":
    unittest.main()
