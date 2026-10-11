"""librarian_tick — the closed-app runner behind `liteharness librarian-tick`.

Invoked by a Windows scheduled task (`schtasks /TR "<python> -m liteharness.cli
librarian-tick --job-id <id>"`) while LiteSuite may or may not be running. The
occurrence LEDGER is the only arbiter: whichever runner (this one, or the app
scheduler) claims (job_id, slot) first runs the librarian; the other prints who
owns it and exits 0. Bridge/app liveness decides nothing.

The slot is the fire's SCHEDULED timestamp derived from the job's own cron
expression — never wall-clock — so both runners compute the SAME key for the
same nightly fire. v1 supports the daily form only (`M H * * *`), which is the
honest mirror of the schtasks registration (daily-only, refused otherwise in
the UI). A non-daily expression is a stated refusal, not a guess.

Missed-while-asleep (accepted v1): schtasks does not wake the machine, so a
slept-through 03:30 SKIPS to the next night. The report shows last-run age, so
a silent week is visible.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import config, occurrences

SCHEDULES_PATH = Path.home() / ".litesuite" / "agent" / "config" / "schedules.json"

# Heartbeat cadence and claim TTL. TTL >> cadence so one missed beat (busy
# disk, suspended process) does not forfeit a live run; a DEAD runner lapses
# after TTL and the atomic stale takeover hands the slot to a contender.
HEARTBEAT_SECONDS = 60.0
TTL_SECONDS = 300.0

_DAILY_CRON_RE = re.compile(r"^\s*(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*\s*$")


def load_job(job_id: str, schedules_path: Path = SCHEDULES_PATH) -> dict | None:
    """READ-ONLY view of the app's job store. This module never writes it —
    hand-editing schedules.json while the app runs corrupts its backups."""
    if not schedules_path.exists():
        return None
    try:
        data = json.loads(schedules_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for job in data.get("jobs", []):
        if job.get("id") == job_id:
            return job
    return None


def slot_for_daily_cron(expression: str, now: datetime) -> str | None:
    """The most recent scheduled fire <= now, as a LOCAL-naive ISO minute.

    Local because the app scheduler evaluates cron in local time; naive and
    minute-precision because both runners must derive the IDENTICAL string
    for the identical fire (the ledger key is a string).
    Returns None for non-daily-form expressions (v1 refusal).
    """
    m = _DAILY_CRON_RE.match(expression or "")
    if not m:
        return None
    minute, hour = int(m.group(1)), int(m.group(2))
    if minute > 59 or hour > 23:
        return None
    fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fire > now:
        fire -= timedelta(days=1)
    return fire.strftime("%Y-%m-%dT%H:%M")


# What the job's own model may look like. The app runner passes `action.model` to claude as
# written, so the tick does too. An allowlist, because claude may be a .cmd shim on Windows,
# where cmd.exe reads & | ; ^ % and friends as command syntax even inside an argv list.
# Covers aliases (sonnet[1m], opusplan), full ids and provider-prefixed ids.
_SAFE_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]*")


def _own_text(action: dict, key: str) -> str | None:
    value = action.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SystemExit(f"job {key} must be a string, got {value!r}.")
    return value.strip() or None


def _failure_text(exc: BaseException) -> str:
    """What a refusal or a failed start says, never empty: a deliberate SystemExit keeps its
    message as written; anything else carries its type in front, since "boom" alone does not
    say what raised it."""
    if isinstance(exc, SystemExit):
        text = "" if exc.code is None else str(exc.code).strip()
        return text or "refused without a message"
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def _require_readable_config() -> None:
    """A config file that exists but cannot be parsed is an error here, not "nothing
    configured": `config.load()` reads it as {}, which would launch claude unpinned."""
    path = config.CONFIG_PATH
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"{path} exists but cannot be read ({exc.strerror or type(exc).__name__}); "
                         "spawn_default_model / spawn_default_effort cannot be resolved.")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SystemExit(f"{path} exists but cannot be read as JSON ({exc}); "
                         "spawn_default_model / spawn_default_effort cannot be resolved.")
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must hold a JSON object (spawn_default_model lives there), "
                         f"got {type(data).__name__}.")


def _model_flags(action: dict) -> tuple[list[str], str | None]:
    """(`--model` / `--effort` flags, note) for the headless claude.

    The job's own model is passed as written after a plain safety check (the app runner does
    the same); the configured spawn default (T0471) goes through the strict resolver
    `liteharness spawn` uses, and is read only for a field the job leaves unset.
    `action["effort"]` is a tick-only extension, validated against the spawn efforts.
    The note is set when no model is pinned anywhere.

    Raises SystemExit on a bad value; the caller refuses the tick rather than start claude
    unpinned."""
    from .cli import _SPAWN_EFFORTS, _spawn_defaults, _validate_model_id

    model, effort = _own_text(action, "model"), _own_text(action, "effort")
    default_model = default_effort = None
    if model is None or effort is None:
        _require_readable_config()
        default_model, default_effort = _spawn_defaults()

    flags: list[str] = []
    note = None
    if model is not None:
        if not _SAFE_MODEL_RE.fullmatch(model):
            raise SystemExit(f"job model {model!r} must start with a letter or digit and use "
                             "only letters, digits and . _ : / @ + [ ] -")
        flags += ["--model", model]
    elif default_model:
        flags += ["--model", _validate_model_id(default_model)]
    else:
        note = ("no model on the job and no spawn_default_model in the harness config: "
                "claude will boot the model in its own settings.json")
    effort = effort or default_effort
    if effort:
        if effort not in _SPAWN_EFFORTS:
            raise SystemExit(f"Unknown effort: {effort!r}. Use one of: {', '.join(_SPAWN_EFFORTS)}.")
        flags += ["--effort", effort]
    return flags, note


def run_tick(
    job_id: str,
    exec_argv: list[str] | None = None,
    db_path: Path | str | None = None,
    schedules_path: Path = SCHEDULES_PATH,
    now: datetime | None = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ttl_seconds: float = TTL_SECONDS,
) -> int:
    """Contend, and run the librarian if we win. Returns the process exit code.

    `exec_argv` overrides the spawned command (tests inject a marker script);
    the default is the headless librarian invocation, whose exact flags are
    VERIFIED on a real closed-app fire before any schtasks registration.
    """
    job = load_job(job_id, schedules_path)
    if job is None:
        print(f"[librarian-tick] job '{job_id}' not found in {schedules_path}", file=sys.stderr)
        return 2

    # A stale OS task can outlive the job's enabled state (sync failed, or
    # the disable raced the fire). The tick re-checks at fire time so the
    # task registration is never the authority on whether a job runs.
    if not job.get("enabled", True):
        print(f"[librarian-tick] job '{job_id}' is disabled — nothing to do")
        return 0

    schedule = job.get("schedule") or {}
    if schedule.get("type") != "cron":
        print(f"[librarian-tick] job '{job_id}' has schedule type "
              f"'{schedule.get('type')}' — only cron is supported", file=sys.stderr)
        return 2
    slot = slot_for_daily_cron(schedule.get("expression", ""), now or datetime.now())
    if slot is None:
        print(f"[librarian-tick] expression '{schedule.get('expression')}' is not "
              "daily-form (M H * * *) — v1 supports daily only, refusing rather "
              "than guessing a slot", file=sys.stderr)
        return 2

    db = Path(db_path) if db_path else occurrences.DEFAULT_DB
    owner = f"tick-{uuid.uuid4()}"
    conn = occurrences.open_ledger(db)
    try:
        claim = occurrences.acquire(conn, job_id, slot, owner, ttl_seconds, db_path=db)
        if not claim["won"]:
            row = claim.get("row") or {}
            print(f"[librarian-tick] slot {slot} {claim['reason']}: "
                  f"owner={row.get('owner_token', '?')} "
                  f"completed_at={row.get('completed_at')} — nothing to do")
            return 0

        action = job.get("action") or {}
        workdir = action.get("workdir") or str(Path.home())
        note = None
        if exec_argv:
            argv = exec_argv
        else:
            try:
                flags, note = _model_flags(action)
            except (SystemExit, Exception) as exc:
                # A bad model or effort never falls back to an unpinned launch. The slot
                # completes as failed, the same as a spawn that could not start; the ledger
                # keeps the first line, stderr the whole message.
                message = _failure_text(exc)
                print(f"[librarian-tick] spawn-failed: {message} — claude was not started",
                      file=sys.stderr)
                occurrences.complete(conn, job_id, slot, owner,
                                     f"spawn-failed: {message.splitlines()[0]}", db_path=db)
                return 1
            if note:
                print(f"[librarian-tick] Note: {note}.", file=sys.stderr)
            argv = [
                "claude", "-p", action.get("prompt", "/ls-librarian"),
                "--permission-mode", "bypassPermissions", *flags,
            ]

        stop = threading.Event()

        def _beat() -> None:
            # One connection for the thread's lifetime, closed on stop — a
            # per-beat connection leaks a WAL handle every cycle (and on
            # Windows an open handle blocks directory cleanup).
            beat_conn = occurrences.open_ledger(db)
            try:
                while not stop.wait(heartbeat_seconds):
                    occurrences.heartbeat(beat_conn, job_id, slot, owner, ttl_seconds)
            finally:
                beat_conn.close()

        beater = threading.Thread(target=_beat, daemon=True)
        beater.start()
        try:
            proc = subprocess.run(argv, cwd=workdir)
            if proc.returncode == 0:
                outcome = f"success ({note})" if note else "success"
            else:
                outcome = f"exit-{proc.returncode}" + (" (unpinned)" if note else "")
        except OSError as exc:
            # FileNotFoundError, PermissionError, WinError 193: whatever the OS refuses.
            outcome = f"spawn-failed: {_failure_text(exc).splitlines()[0]}"
            proc = None
        finally:
            stop.set()
            # The beater holds a db connection; wait for it to close before
            # completing, or the handle outlives the tick (and blocks
            # directory cleanup on Windows).
            beater.join(timeout=heartbeat_seconds + 5)

        occurrences.complete(conn, job_id, slot, owner, outcome, db_path=db)
        print(f"[librarian-tick] slot {slot} completed: {outcome}")
        return 0 if (proc is not None and proc.returncode == 0) else 1
    finally:
        conn.close()
