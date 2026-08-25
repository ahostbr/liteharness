"""librarian_install — offer the librarian as a MECHANISM, not as a habit.

`liteharness librarian-install --mode app|os|print`

    app    write the librarian job into LiteSuite's own scheduler store
    os     register a Windows scheduled task that runs it with the app closed
    print  print both procedures and do nothing

WHY `print` IS THE NON-INTERACTIVE DEFAULT: a bootstrap that silently registers
OS tasks is exactly the surprise git-as-memory exists to remove. Nothing here
runs unless a person named a mode.

🔴 THE `app` MODE WRITES A FILE LITESUITE ALSO WRITES, so it needs an arbiter.
It cannot be port liveness: the AgentBridge can be down while SchedulerStorage is
very much alive, so a dead port would license the CLI to corrupt a live store.
The only thing allowed to decide is the occurrence ledger's well-known config
row, which the app holds for its lifetime and heartbeats.

⚠️ NEVER `complete()` THE CONFIG ROW. complete() is terminal — "a completed
occurrence never reruns", and acquire's stale takeover explicitly requires
completed_at IS NULL — so completing this row would burn the lock permanently for
the app as well as for this CLI.

`release()` is the correct exit: owner-matched, never-completed-only, deletes the
row so it is immediately re-acquirable. This module acquires for the duration of
the write and releases in a `finally`, so a CLI install does not leave the app
locked out for the rest of the TTL. The TTL remains the CRASH backstop — if this
process dies mid-write, the row lapses and the atomic stale takeover recovers it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import occurrences

#: The well-known ledger row that stands for "somebody owns schedules.json".
#: A real cron slot is an ISO minute; this sentinel cannot collide with one.
CONFIG_LOCK_JOB_ID = "__scheduler_config__"
CONFIG_LOCK_SLOT = "lock"

#: Short on purpose. The refusal message quotes it, and a user who closes
#: LiteSuite should not have to wait long to be allowed to install.
LOCK_TTL_SECONDS = 30.0

LIBRARIAN_JOB_ID = "liteharness-librarian"
TASK_NAME = r"LiteSuite\librarian"

#: Daily, and daily only — the honest mirror of what librarian_tick supports
#: (`M H * * *`). A non-daily expression is a stated refusal there, so offering
#: one here would install a job its own runner refuses to fire.
LIBRARIAN_CRON = "30 3 * * *"
_TASK_TIME = "03:30"

#: The engine's JobAction union (desktop types.ts, JobAction). An action.type
#: outside it loads anyway — the store raw-parses — computes nextRun off its
#: valid cron, passes a schedule-shape gate, and then executeJobAction silently
#: returns undefined: a success no-op every fire, with notifyOnError
#: structurally unable to trigger. The first dogfood install (2026-08-25)
#: shipped exactly that as {"type": "cli"}. Writes are gated on this set.
ENGINE_JOB_ACTION_TYPES = frozenset({"prompt", "script", "team"})

Emit = Callable[[str], None]
Runner = Callable[[list[str]], int]


def _default_runner(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, check=False).returncode
    except OSError:
        return 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms() -> int:
    """Epoch milliseconds — the store's timestamp idiom for job rows."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def schedules_path(home: Path) -> Path:
    return home / ".litesuite" / "agent" / "config" / "schedules.json"


def librarian_job() -> dict:
    """The job spec written into LiteSuite's store.

    Shaped to the store's existing job keys so the app's own UI can display and
    edit it like any other job — an entry the app cannot render is a job the
    user cannot turn off.
    """
    now = _now_ms()
    return {
        "id": LIBRARIAN_JOB_ID,
        "name": "Nightly Librarian",
        "description": (
            "Verify docs against code; promote verified claims from daily "
            "notes + human-verified patterns into arch docs. "
            "Installed by `liteharness librarian-install`."
        ),
        "enabled": True,
        "schedule": {"type": "cron", "expression": LIBRARIAN_CRON},
        # The APP path runs the skill as a PromptAction — `liteharness
        # librarian-tick` is the CLOSED-APP (schtasks, --mode os) runner, not
        # an action type the engine can execute.
        "action": {
            "type": "prompt",
            "prompt": "/ls-librarian",
            "workdir": "C:\\Projects",
            "permissionMode": "bypassPermissions",
            "timeoutMinutes": 45,
            "maxTurns": 80,
        },
        "tags": ["liteharness", "librarian"],
        "status": "idle",
        "history": [],
        "notifyOnStart": False,
        "notifyOnComplete": False,
        "notifyOnError": True,
        "createdAt": now,
        "updatedAt": now,
        "nextRun": None,
    }


def _print_procedures(emit: Emit) -> int:
    emit("The librarian promotes verified patterns into your architecture docs.")
    emit("Nothing is scheduled until you pick one of these:")
    emit("")
    emit("  liteharness librarian-install --mode app")
    emit("      Adds it to LiteSuite's own scheduler. Runs while the app is open.")
    emit("      Refused while LiteSuite holds the scheduler config.")
    emit("")
    emit("  liteharness librarian-install --mode os")
    emit(f"      Registers a Windows scheduled task ({TASK_NAME}) at {_TASK_TIME}.")
    emit("      Runs with the app closed. Remove it with --mode os --remove.")
    emit("")
    emit("Either is safe alongside the other: the occurrence ledger arbitrates,")
    emit("so the same nightly fire can never run twice.")
    return 0


def _install_app(home: Path, emit: Emit, db_path: Path | None) -> int:
    path = schedules_path(home)
    if not path.exists():
        emit("No LiteSuite scheduler config found at:")
        emit(f"  {path}")
        emit("Install/run LiteSuite once, or use --mode os to schedule it without the app.")
        return 2

    db = Path(db_path) if db_path is not None else occurrences.DEFAULT_DB
    conn = occurrences.open_ledger(db)
    try:
        owner = f"librarian-install-{os.getpid()}"
        held = False
        claim = occurrences.acquire(
            conn,
            CONFIG_LOCK_JOB_ID,
            CONFIG_LOCK_SLOT,
            owner,
            LOCK_TTL_SECONDS,
            db_path=db,
        )
        if not claim.get("won"):
            emit("LiteSuite owns the scheduler config — it is running.")
            emit("Use the Autonomy tab to add the librarian, or close LiteSuite and re-run.")
            emit(
                f"(A clean shutdown releases the lock immediately. If it was killed, "
                f"wait ~{int(LOCK_TTL_SECONDS)}s for the claim to expire.)"
            )
            return 3

        held = True

        # Held for the write only. NEVER completed — see the module docstring.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            emit(f"Could not read the scheduler config: {exc}")
            return 4

        jobs = data.get("jobs", [])
        if any(j.get("id") == LIBRARIAN_JOB_ID for j in jobs):
            emit("The librarian job is already installed — nothing to do.")
            return 0

        job = librarian_job()
        if job["action"]["type"] not in ENGINE_JOB_ACTION_TYPES:
            # Writer-side gate: an action the engine cannot dispatch becomes a
            # nightly success no-op, invisible to every schedule-shape check.
            emit(
                f"Refusing to write: action type {job['action']['type']!r} is "
                f"not one the engine executes ({sorted(ENGINE_JOB_ACTION_TYPES)})."
            )
            return 7

        jobs.append(job)
        data["jobs"] = jobs

        # Backup BEFORE the replace. The store holds every schedule the user
        # has; a bad write here costs all of them, and the app's own backup
        # directory is the place it already looks.
        backup_dir = path.parent / "schedule_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = _now_iso().replace(":", "-").replace("+00-00", "Z")
        try:
            (backup_dir / f"{stamp}.json").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError as exc:
            emit(f"Refusing to write: could not create a backup first ({exc}).")
            return 5

        # Atomic replace: a torn write would leave the app with an unparseable
        # store and no scheduler at all.
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            emit(f"Could not write the scheduler config: {exc}")
            return 6

        emit(f"Librarian installed into LiteSuite's scheduler ({LIBRARIAN_CRON}).")
        emit("Open the Autonomy tab to see or disable it.")
        return 0
    finally:
        # Hand the lock back the moment the write is done. Without this the app
        # is locked out for the remainder of the TTL after a CLI install — a
        # self-inflicted outage for a write that took milliseconds.
        # release() is owner-matched, so this can never free somebody else's claim.
        if held:
            try:
                occurrences.release(
                    conn, CONFIG_LOCK_JOB_ID, CONFIG_LOCK_SLOT, owner, db_path=db
                )
            except Exception:
                # A failed release is not a failed install: the TTL still frees it.
                pass
        conn.close()


def _install_os(emit: Emit, runner: Runner, remove: bool) -> int:
    if remove:
        argv = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
        rc = runner(argv)
        if rc != 0:
            emit(f"schtasks failed (exit {rc}) — the task was not removed.")
            return rc
        emit(f"Removed the scheduled task {TASK_NAME}.")
        return 0

    command = f'"{sys.executable}" -m liteharness.cli librarian-tick --job-id {LIBRARIAN_JOB_ID}'
    argv = [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        command,
        "/SC",
        "DAILY",
        "/ST",
        _TASK_TIME,
        "/F",
    ]
    rc = runner(argv)
    if rc != 0:
        emit(f"schtasks failed (exit {rc}) — no task was registered.")
        return rc
    emit(f"Registered {TASK_NAME}, daily at {_TASK_TIME}.")
    emit("It runs with LiteSuite closed; the occurrence ledger prevents a double fire.")
    emit(f"Remove it with: liteharness librarian-install --mode os --remove")
    return 0


def install(
    mode: str,
    *,
    home: Path | None = None,
    emit: Emit | None = None,
    runner: Runner | None = None,
    db_path: Path | None = None,
    remove: bool = False,
) -> int:
    """Run one install mode. Returns a process exit code."""
    emit = emit or print
    home = Path(home) if home is not None else Path.home()

    if mode == "print":
        return _print_procedures(emit)
    if mode == "app":
        return _install_app(home, emit, db_path)
    if mode == "os":
        return _install_os(emit, runner or _default_runner, remove)

    emit(f"Unknown mode: {mode!r}. Expected one of: app, os, print.")
    return 2
