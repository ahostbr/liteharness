"""occurrences — the SQLite occurrence ledger. THE ONLY ARBITER of job fires.

One database, `~/.liteharness/occurrences.sqlite`, WAL + busy_timeout, spoken
natively by BOTH runners (this module via Python stdlib sqlite3; the LiteSuite
scheduler via better-sqlite3). Bridge/app liveness is ADVISORY ONLY — logged,
never deciding.

PROTOCOL (this docstring is the shared contract; the Node side cites it and
the cross-language conformance suite drives both runners against one db):

  TABLE occurrences(
    job_id       TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,   -- the fire's SCHEDULED slot (from the cron), never wall-clock
    owner_token  TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    completed_at TEXT,
    outcome      TEXT,
    PRIMARY KEY (job_id, scheduled_at)
  )

  All timestamps are UTC ISO-8601 with a fixed 'YYYY-MM-DDTHH:MM:SS.ffffff+00:00'
  shape, so string comparison IS time comparison.

  ACQUIRE(job, slot, owner, ttl):
    1. INSERT the row (claims the slot). Success -> WON.
    2. On PK conflict, ONE conditional update:
         UPDATE ... SET owner_token/acquired_at/heartbeat_at/expires_at
         WHERE job_id=? AND scheduled_at=? AND expires_at < now
           AND completed_at IS NULL
       changes==1 -> WON by atomic stale takeover (the condition is evaluated
       under SQLite's write lock — two contenders cannot both observe expiry
       and both take over; there is no observe-then-race window).
       changes==0 -> LOST (alive owner, or the occurrence already completed).
  HEARTBEAT(job, slot, owner, ttl): extends expires_at, but ONLY
       WHERE owner_token=? AND completed_at IS NULL — a long run keeps its
       claim alive; a usurped or completed run cannot resurrect it.
  COMPLETE(job, slot, owner, outcome): sets completed_at+outcome,
       WHERE owner_token=? AND completed_at IS NULL.
       A COMPLETED OCCURRENCE NEVER RERUNS: no code path clears completed_at,
       and ACQUIRE's takeover explicitly requires completed_at IS NULL.

The ledger is RUNTIME COORDINATION, not truth/memory: it stays UNCOMMITTED.
Each acquire/complete also appends a line to `occurrence-receipts.jsonl`
beside the db — an append-only audit trail that IS allowed to be read.

A CLI exercise mode exists so the cross-language conformance suite can
interleave Python and Node operations against one database:

    python -m liteharness.occurrences <acquire|heartbeat|complete|status>
        --db PATH --job ID --slot ISO --owner TOKEN [--ttl SECONDS] [--outcome S]

Each subcommand prints a single JSON object and exits 0 (the JSON carries
`won`/`renewed`/`completed` booleans — outcomes, not transport).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".liteharness" / "occurrences.sqlite"
_RECEIPTS_NAME = "occurrence-receipts.jsonl"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS occurrences (
  job_id       TEXT NOT NULL,
  scheduled_at TEXT NOT NULL,
  owner_token  TEXT NOT NULL,
  acquired_at  TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  completed_at TEXT,
  outcome      TEXT,
  PRIMARY KEY (job_id, scheduled_at)
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plus(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def open_ledger(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None (autocommit) is load-bearing: Python's default
    # wraps writes in implicit BEGIN DEFERRED transactions, and a deferred
    # read->write lock upgrade returns SQLITE_BUSY IMMEDIATELY (the busy
    # handler is bypassed to prevent deadlock) — measured as
    # "database is locked" on a racing contender despite busy_timeout.
    # In autocommit, single-statement writes wait properly, and ACQUIRE's
    # multi-statement claim takes BEGIN IMMEDIATE explicitly.
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    # busy_timeout FIRST — everything after must wait, not fail.
    conn.execute("PRAGMA busy_timeout=30000")
    # The one-time rollback->WAL conversion of a FRESH db needs exclusivity
    # and can return SQLITE_BUSY outside the busy handler when two racing
    # opens both attempt it (measured: "database is locked" here on round 3
    # of a 2-thread race). The mode is persistent in the file, so a bounded
    # retry rides out the sibling's conversion and then reads 'wal' cheaply.
    for attempt in range(50):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 49:
                raise
            import time as _time
            _time.sleep(0.05)
    conn.execute(_SCHEMA)
    return conn


def _receipt(db_path: Path | str, event: dict) -> None:
    try:
        path = Path(db_path).parent / _RECEIPTS_NAME
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({**event, "at": _now()}) + "\n")
    except OSError:
        pass  # the receipt is audit, never a gate


def acquire(
    conn: sqlite3.Connection,
    job_id: str,
    scheduled_at: str,
    owner_token: str,
    ttl_seconds: float,
    db_path: Path | str = DEFAULT_DB,
) -> dict:
    """Claim (job_id, scheduled_at). Returns {won, reason, row}."""
    now = _now()
    expires = _plus(ttl_seconds)
    # One IMMEDIATE write transaction covers the whole claim: the write lock
    # is taken up front (honoring busy_timeout), so insert-else-takeover is a
    # single atomic step with no window between the two.
    conn.execute("BEGIN IMMEDIATE")
    try:
        via = None
        try:
            conn.execute(
                "INSERT INTO occurrences(job_id, scheduled_at, owner_token, acquired_at, heartbeat_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, scheduled_at, owner_token, now, now, expires),
            )
            via = "insert"
        except sqlite3.IntegrityError:
            cur = conn.execute(
                "UPDATE occurrences SET owner_token=?, acquired_at=?, heartbeat_at=?, expires_at=? "
                "WHERE job_id=? AND scheduled_at=? AND expires_at < ? AND completed_at IS NULL",
                (owner_token, now, now, expires, job_id, scheduled_at, now),
            )
            if cur.rowcount == 1:
                via = "stale-takeover"
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    if via is not None:
        _receipt(db_path, {"event": "acquire", "job_id": job_id,
                           "scheduled_at": scheduled_at, "owner_token": owner_token,
                           "via": via})
        reason = "claimed" if via == "insert" else via
        return {"won": True, "reason": reason, "row": status(conn, job_id, scheduled_at)}

    row = status(conn, job_id, scheduled_at)
    reason = "completed" if row and row.get("completed_at") else "owned"
    return {"won": False, "reason": reason, "row": row}


def heartbeat(
    conn: sqlite3.Connection,
    job_id: str,
    scheduled_at: str,
    owner_token: str,
    ttl_seconds: float,
) -> dict:
    """Renew the claim. Only the current owner of a live occurrence can."""
    now = _now()
    # Single statement in autocommit mode — atomic on its own.
    cur = conn.execute(
        "UPDATE occurrences SET heartbeat_at=?, expires_at=? "
        "WHERE job_id=? AND scheduled_at=? AND owner_token=? AND completed_at IS NULL",
        (now, _plus(ttl_seconds), job_id, scheduled_at, owner_token),
    )
    return {"renewed": cur.rowcount == 1}


def complete(
    conn: sqlite3.Connection,
    job_id: str,
    scheduled_at: str,
    owner_token: str,
    outcome: str,
    db_path: Path | str = DEFAULT_DB,
) -> dict:
    """Finish the occurrence. Nothing ever clears completed_at afterwards."""
    # Single statement in autocommit mode — atomic on its own.
    cur = conn.execute(
        "UPDATE occurrences SET completed_at=?, outcome=? "
        "WHERE job_id=? AND scheduled_at=? AND owner_token=? AND completed_at IS NULL",
        (_now(), outcome, job_id, scheduled_at, owner_token),
    )
    done = cur.rowcount == 1
    if done:
        _receipt(db_path, {"event": "complete", "job_id": job_id,
                           "scheduled_at": scheduled_at, "owner_token": owner_token,
                           "outcome": outcome})
    return {"completed": done}


def release(
    conn: sqlite3.Connection,
    job_id: str,
    scheduled_at: str,
    owner_token: str,
    db_path: Path | str = DEFAULT_DB,
) -> dict:
    """ABANDON an unfinished claim — the row is deleted and immediately
    re-acquirable. Owner-matched and never-completed only: a completed
    occurrence is history and stays forever.

    For LIFETIME LOCKS (e.g. the scheduler-config sentinel slot): complete()
    would BURN the slot permanently (takeover requires completed_at IS NULL
    and nothing clears it), so a lock holder releases on clean shutdown and
    lets TTL lapse cover crashes. For EXECUTED WORK this is the WRONG verb:
    work that ran must complete() — releasing after running re-opens the
    slot for a duplicate run.
    """
    cur = conn.execute(
        "DELETE FROM occurrences "
        "WHERE job_id=? AND scheduled_at=? AND owner_token=? AND completed_at IS NULL",
        (job_id, scheduled_at, owner_token),
    )
    released = cur.rowcount == 1
    if released:
        _receipt(db_path, {"event": "release", "job_id": job_id,
                           "scheduled_at": scheduled_at, "owner_token": owner_token})
    return {"released": released}


def status(conn: sqlite3.Connection, job_id: str, scheduled_at: str) -> dict | None:
    row = conn.execute(
        "SELECT job_id, scheduled_at, owner_token, acquired_at, heartbeat_at, "
        "expires_at, completed_at, outcome FROM occurrences "
        "WHERE job_id=? AND scheduled_at=?",
        (job_id, scheduled_at),
    ).fetchone()
    if row is None:
        return None
    keys = ["job_id", "scheduled_at", "owner_token", "acquired_at",
            "heartbeat_at", "expires_at", "completed_at", "outcome"]
    return dict(zip(keys, row))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="liteharness.occurrences")
    parser.add_argument("op", choices=["acquire", "heartbeat", "complete", "release", "status"])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--job", required=True)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--owner", default="")
    parser.add_argument("--ttl", type=float, default=300.0)
    parser.add_argument("--outcome", default="success")
    args = parser.parse_args(argv)

    conn = open_ledger(args.db)
    try:
        if args.op == "acquire":
            out = acquire(conn, args.job, args.slot, args.owner, args.ttl, db_path=args.db)
        elif args.op == "heartbeat":
            out = heartbeat(conn, args.job, args.slot, args.owner, args.ttl)
        elif args.op == "complete":
            out = complete(conn, args.job, args.slot, args.owner, args.outcome, db_path=args.db)
        elif args.op == "release":
            out = release(conn, args.job, args.slot, args.owner, db_path=args.db)
        else:
            out = {"row": status(conn, args.job, args.slot)}
    finally:
        conn.close()
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
