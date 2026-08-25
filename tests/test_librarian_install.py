"""`liteharness librarian-install` — offer the librarian as a MECHANISM.

The three modes are deliberately unequal in risk, and the tests are weighted the
same way: `print` is inert, `os` touches the Windows task scheduler, and `app`
writes Ryan's live scheduler config. Everything that writes is exercised against
an injected home and an injected command runner, never the real ones.

🔴 THE LOCK IS THE POINT OF THE `app` MODE. LiteSuite's scheduler and this CLI
both write `schedules.json`. Port liveness cannot arbitrate that — the bridge can
be down while SchedulerStorage is live — so the occurrence ledger's well-known
config row is the only thing that may decide, and a held lock must REFUSE.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from liteharness import librarian_install as li
from liteharness import occurrences


def _schedules(home: Path) -> Path:
    return home / ".litesuite" / "agent" / "config" / "schedules.json"


def _seed_store(home: Path, jobs: list[dict] | None = None) -> Path:
    p = _schedules(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"version": 1, "jobs": jobs or [], "events": [], "settings": {}}, indent=2),
        encoding="utf-8",
    )
    return p


class PrintModeTests(unittest.TestCase):
    def test_print_exits_zero_and_shows_both_procedures(self) -> None:
        out: list[str] = []
        with TemporaryDirectory() as td:
            rc = li.install("print", home=Path(td), emit=out.append)
        self.assertEqual(rc, 0)
        text = "\n".join(out)
        self.assertIn("--mode app", text)
        self.assertIn("--mode os", text)

    def test_print_writes_nothing(self) -> None:
        with TemporaryDirectory() as td:
            home = Path(td)
            li.install("print", home=home, emit=lambda _m: None)
            # The no-surprises default: a mode that prints must not create the
            # config tree it is describing.
            self.assertFalse(_schedules(home).exists())
            self.assertFalse((home / ".litesuite").exists())


class AppModeTests(unittest.TestCase):
    def test_refuses_when_litesuite_was_never_installed(self) -> None:
        out: list[str] = []
        with TemporaryDirectory() as td:
            rc = li.install("app", home=Path(td), emit=out.append)
        self.assertNotEqual(rc, 0)
        self.assertIn("litesuite", "\n".join(out).lower())

    def test_refuses_while_the_app_holds_the_config_lock(self) -> None:
        out: list[str] = []
        with TemporaryDirectory() as td:
            home = Path(td)
            _seed_store(home)
            db = home / "occ.sqlite"
            conn = occurrences.open_ledger(db)
            # Stand in for the running app: claim the well-known config row.
            got = occurrences.acquire(
                conn,
                li.CONFIG_LOCK_JOB_ID,
                li.CONFIG_LOCK_SLOT,
                "the-app",
                li.LOCK_TTL_SECONDS,
                db_path=db,
            )
            self.assertTrue(got["won"], "test setup failed to take the lock")
            conn.close()

            rc = li.install("app", home=home, emit=out.append, db_path=db)

        self.assertNotEqual(rc, 0)
        text = "\n".join(out)
        self.assertIn("Autonomy", text)
        # The refusal must state how release actually happens, and BOTH paths
        # are real since release() landed: a clean shutdown frees it at once,
        # a killed app frees it when the claim expires. Naming only the first
        # would strand anyone whose app crashed.
        self.assertIn("expir", text.lower())
        self.assertIn("close litesuite", text.lower())

    def test_installs_the_job_when_the_lock_is_free(self) -> None:
        with TemporaryDirectory() as td:
            home = Path(td)
            path = _seed_store(home)
            db = home / "occ.sqlite"

            rc = li.install("app", home=home, emit=lambda _m: None, db_path=db)
            self.assertEqual(rc, 0)

            data = json.loads(path.read_text(encoding="utf-8"))
            ids = [j.get("id") for j in data["jobs"]]
            self.assertIn(li.LIBRARIAN_JOB_ID, ids)

            # A backup must exist BEFORE the replace, or a bad write costs the
            # user every schedule they had.
            backups = list((path.parent / "schedule_backups").glob("*.json"))
            self.assertTrue(backups, "no backup was written before replacing the store")

    def test_second_install_does_not_duplicate_the_job(self) -> None:
        with TemporaryDirectory() as td:
            home = Path(td)
            path = _seed_store(home)
            db = home / "occ.sqlite"
            li.install("app", home=home, emit=lambda _m: None, db_path=db)
            li.install("app", home=home, emit=lambda _m: None, db_path=db)
            data = json.loads(path.read_text(encoding="utf-8"))
            ids = [j.get("id") for j in data["jobs"]]
            self.assertEqual(ids.count(li.LIBRARIAN_JOB_ID), 1)

    def test_preserves_the_jobs_that_were_already_there(self) -> None:
        with TemporaryDirectory() as td:
            home = Path(td)
            existing = {"id": "dream-sweep", "name": "Dream + Doc Sweep", "enabled": True}
            path = _seed_store(home, [existing])
            db = home / "occ.sqlite"
            li.install("app", home=home, emit=lambda _m: None, db_path=db)
            data = json.loads(path.read_text(encoding="utf-8"))
            ids = [j.get("id") for j in data["jobs"]]
            # Control: installing must ADD, never replace the store.
            self.assertIn("dream-sweep", ids)
            self.assertIn(li.LIBRARIAN_JOB_ID, ids)

    def _written_job(self) -> dict:
        with TemporaryDirectory() as td:
            home = Path(td)
            path = _seed_store(home)
            db = home / "occ.sqlite"
            rc = li.install("app", home=home, emit=lambda _m: None, db_path=db)
            self.assertEqual(rc, 0)
            jobs = json.loads(path.read_text(encoding="utf-8"))["jobs"]
            return next(j for j in jobs if j.get("id") == li.LIBRARIAN_JOB_ID)

    def test_written_action_type_is_in_the_engines_union(self) -> None:
        # The engine's JobAction union is prompt|script|team (desktop
        # types.ts). An action.type outside it LOADS fine (storage raw-parses),
        # computes nextRun off its valid cron, passes a schedule-shape gate —
        # and executeJobAction silently returns undefined: a success no-op
        # every night, with notifyOnError structurally unable to fire. The
        # first dogfood install (2026-08-25) shipped exactly that as "cli".
        job = self._written_job()
        self.assertIn(job["action"]["type"], li.ENGINE_JOB_ACTION_TYPES)

    def test_written_job_is_t4_verbatim(self) -> None:
        job = self._written_job()
        self.assertEqual(job["name"], "Nightly Librarian")
        action = job["action"]
        self.assertEqual(action["type"], "prompt")
        self.assertEqual(action["prompt"], "/ls-librarian")
        self.assertEqual(action["workdir"], "C:\\Projects")
        self.assertEqual(action["permissionMode"], "bypassPermissions")
        self.assertEqual(action["timeoutMinutes"], 45)
        self.assertEqual(action["maxTurns"], 80)
        self.assertEqual(
            job["schedule"], {"type": "cron", "expression": "30 3 * * *"}
        )
        self.assertTrue(job["enabled"])
        # Store idiom: epoch millis like every app-written job — the store's
        # other rows carry ints, and a mixed-type column is a sort/display trap.
        self.assertIsInstance(job["createdAt"], int)
        self.assertIsInstance(job["updatedAt"], int)

    def test_releases_the_lock_so_the_app_is_not_locked_out(self) -> None:
        # release() landed after this module was drafted (oss 6961d7b). Before
        # it, a CLI install held the config row for the REST OF THE TTL — a
        # self-inflicted outage for a write that takes milliseconds.
        with TemporaryDirectory() as td:
            home = Path(td)
            _seed_store(home)
            db = home / "occ.sqlite"
            self.assertEqual(0, li.install("app", home=home, emit=lambda _m: None, db_path=db))

            conn = occurrences.open_ledger(db)
            try:
                # The proof that matters is not "the row looks free" but that a
                # DIFFERENT owner can actually take it immediately.
                got = occurrences.acquire(
                    conn,
                    li.CONFIG_LOCK_JOB_ID,
                    li.CONFIG_LOCK_SLOT,
                    "the-app-starting-up",
                    li.LOCK_TTL_SECONDS,
                    db_path=db,
                )
                self.assertTrue(
                    got["won"],
                    "the CLI kept the config lock after installing; the app is locked out",
                )
            finally:
                conn.close()

    def test_never_completes_the_lock_row(self) -> None:
        # 🔴 complete() is TERMINAL in the ledger — "a completed occurrence never
        # reruns". Completing the config row would burn the lock permanently for
        # BOTH the app and this CLI. So a successful install must leave the row
        # acquirable again.
        with TemporaryDirectory() as td:
            home = Path(td)
            _seed_store(home)
            db = home / "occ.sqlite"
            li.install("app", home=home, emit=lambda _m: None, db_path=db)

            conn = occurrences.open_ledger(db)
            row = occurrences.status(conn, li.CONFIG_LOCK_JOB_ID, li.CONFIG_LOCK_SLOT)
            conn.close()
            if row is not None:
                self.assertIsNone(
                    row["completed_at"],
                    "the config lock row was COMPLETED — it can never be acquired again",
                )


class OsModeTests(unittest.TestCase):
    def test_registers_a_daily_task_and_reports_the_command(self) -> None:
        calls: list[list[str]] = []

        def runner(argv: list[str]) -> int:
            calls.append(argv)
            return 0

        out: list[str] = []
        with TemporaryDirectory() as td:
            rc = li.install("os", home=Path(td), emit=out.append, runner=runner)

        self.assertEqual(rc, 0)
        self.assertTrue(calls, "schtasks was never invoked")
        argv = calls[0]
        self.assertEqual(argv[0], "schtasks")
        self.assertIn("/Create", argv)
        joined = " ".join(argv)
        self.assertIn(li.TASK_NAME, joined)
        self.assertIn("librarian-tick", joined)

    def test_uninstall_removes_the_task(self) -> None:
        calls: list[list[str]] = []

        def runner(argv: list[str]) -> int:
            calls.append(argv)
            return 0

        with TemporaryDirectory() as td:
            rc = li.install("os", home=Path(td), emit=lambda _m: None, runner=runner, remove=True)

        self.assertEqual(rc, 0)
        argv = calls[0]
        self.assertIn("/Delete", argv)
        self.assertIn(li.TASK_NAME, " ".join(argv))

    def test_a_failing_schtasks_is_reported_not_swallowed(self) -> None:
        out: list[str] = []
        with TemporaryDirectory() as td:
            rc = li.install("os", home=Path(td), emit=out.append, runner=lambda _a: 1)
        self.assertNotEqual(rc, 0, "schtasks failed and install still reported success")


class ModeValidationTests(unittest.TestCase):
    def test_an_unknown_mode_refuses_rather_than_defaulting(self) -> None:
        out: list[str] = []
        with TemporaryDirectory() as td:
            rc = li.install("banana", home=Path(td), emit=out.append)
        # An unknown mode that silently did the safest thing would hide a typo
        # in someone's install script forever.
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
