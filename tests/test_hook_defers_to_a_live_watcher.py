"""T372 — the per-turn hook must not archive mail a live watcher is there to render.

THE DEFECT. Two consumers drain one maildir per seat: the long-lived watcher
(`hooks watch --agent-id X`) and the per-turn `check_inbox`. The hook prints the message
for stdout injection and then archives it to done/ in one UNCONDITIONAL step
(hooks.py, "Move reported messages to done/ so they aren't re-reported"). Rendering is
the host's business — a swallowed stdout (the hook has a 5 s timeout) files a message as
delivered that nobody saw. Measured 2026-09-05: gate report `9aec3daa` was claimed by the
hook during an 18 s watcher pause, never rendered, and recovered by hand from done/.

    PRINTING IS NOT ACKNOWLEDGEMENT.

⚠️ AND THE NET WAS ALREADY THERE. `check_inbox` scans new/ AND cur/ precisely because
"messages in cur/ were claimed but the agent may not have seen them (hook timeout, output
swallowed, etc.)" — then the archive step files past that net into done/, which nothing
re-scans. The idea was present; the last step stepped over it.

⚠️ AND THE FIX WAS ALREADY THERE, SCOPED TO ONE RUNTIME. T370 added
`if _is_codex_hook_runtime() and desktop_owner_active(...): return`, whose comment states
the general rule — "Leave wake-owned mail to its attached consumer instead of archiving it
from a turn hook." Both conjuncts must hold, so every Claude Code seat still had the
defect while the file read as fixed. What follows generalises that principle; FullBit's
branch is untouched beside it.

THE RULE PINNED HERE: the hook defers when THIS id's presence row names a `watcher_pid`
that is alive AND whose `last_seen` is within WATCHER_FRESH_SECONDS. Deferral is SILENT —
the watcher renders, and sweep bookkeeping does not belong in a seat's context.

Every arm below asserts BOTH halves — where the file ended up AND whether stdout carried
the body — because either alone is satisfiable by the wrong behaviour: a hook that prints
without archiving passes a file-only assertion, and one that archives without printing
passes a stdout-only one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ME = "aaaaaaaa-1111-2222-3333-444444444444"
BODY = "THE-GATE-REPORT"


def _a_pid_that_is_not_running() -> int:
    """A pid nothing owns. Walks down from a high number rather than guessing one."""
    import psutil

    for candidate in range(999_000, 990_000, -7):
        if not psutil.pid_exists(candidate):
            return candidate
    raise AssertionError("could not find a free pid to stand in for a dead watcher")


def _seed(home: Path, *, to: str = ME, watcher_pid: int | None = None,
          watcher_age_s: float = 0.0) -> Path:
    """One message in new/, and a presence row describing this id's watcher (or none)."""
    root = home / ".liteharness"
    (root / "inbox" / "new").mkdir(parents=True, exist_ok=True)
    (root / "agents").mkdir(parents=True, exist_ok=True)
    row: dict[str, object] = {"agent_id": ME, "cli": "claude-code", "tier": "worker"}
    row["watcher_last_seen"] = (datetime.now(timezone.utc) - timedelta(seconds=watcher_age_s)).isoformat()
    row["last_seen"] = row["watcher_last_seen"]
    row["session_pid"] = os.getpid()
    if watcher_pid is not None:
        row["watcher_pid"] = watcher_pid
    (root / "agents" / f"{ME}.json").write_text(json.dumps(row), encoding="utf-8")
    msg = root / "inbox" / "new" / "m1.json"
    msg.write_text(
        json.dumps({"id": "m1", "from": "2cbc7137", "to": to,
                    "timestamp": datetime.now(timezone.utc).isoformat(), "body": BODY}),
        encoding="utf-8",
    )
    return msg


def _run_check(home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home),
           "LITEHARNESS_AGENT_ID": ME}
    # A Codex runtime would take FullBit's branch instead; make sure we are not one.
    for k in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        env.pop(k, None)
    return subprocess.run(
        [sys.executable, "-m", "liteharness.hooks", "check"],
        cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=90,
    )


def _outcome(home: Path, msg: Path, proc: subprocess.CompletedProcess) -> tuple[bool, bool]:
    """(still queued in new/, stdout carried the body)."""
    return msg.exists(), BODY in (proc.stdout + proc.stderr)


# ------------------------------------------------------------------ i. the defect

def test_a_live_and_fresh_watcher_keeps_the_hook_off_the_mail():
    """RED before the fix: the hook claims it out from under the watcher."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        msg = _seed(home, watcher_pid=os.getpid(), watcher_age_s=0)
        queued, printed = _outcome(home, msg, _run_check(home))
        assert queued, (
            "the turn hook archived mail that a live, fresh watcher was there to render — "
            "printing is not acknowledgement, and done/ is never re-scanned"
        )
        assert not printed, (
            "deferral must be SILENT: the watcher renders, and the hook must not put a "
            "second copy into the turn"
        )


def test_the_deferral_is_silent_about_itself():
    """No 'skipped N messages' line either. Bookkeeping is not the seat's business."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        _seed(home, watcher_pid=os.getpid(), watcher_age_s=0)
        proc = _run_check(home)
        assert proc.stdout.strip() == "", f"the hook narrated its deferral: {proc.stdout!r}"


# --------------------------------------------------- ii-iv. when the hook MUST claim

def test_CONTROL_a_dead_watcher_pid_does_not_hold_the_mail():
    """T350's shape: deferring to a watcher that is gone means nobody delivers."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        msg = _seed(home, watcher_pid=_a_pid_that_is_not_running(), watcher_age_s=0)
        queued, printed = _outcome(home, msg, _run_check(home))
        assert not queued, "mail was left for a watcher whose process no longer exists"
        assert printed, "the hook went quiet without delivering — the message reached nobody"


def test_CONTROL_a_stale_watcher_last_seen_does_not_hold_the_mail():
    """Two failure modes, ONE test, and the docstring is where they are told apart.

    A pid can be alive and not be the watcher (RECYCLED by an unrelated process), and a
    watcher process can be alive with its consumer PAUSED — the 18 s window that lost
    9aec3daa. Neither is distinguishable from outside, and neither needs to be: both show
    up as a `last_seen` that has stopped advancing, so the freshness window covers both.
    """
    from liteharness import hooks

    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        msg = _seed(home, watcher_pid=os.getpid(),
                    watcher_age_s=hooks.WATCHER_FRESH_SECONDS + 30)
        queued, printed = _outcome(home, msg, _run_check(home))
        assert not queued, (
            "mail was held for a watcher that has stopped heartbeating — a bounded delay "
            "is the point, an unbounded one is the defect again with a different cause"
        )
        assert printed


def test_CONTROL_no_watcher_pid_at_all_does_not_hold_the_mail():
    """The seat has no attached consumer; the hook is the only delivery there is."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        msg = _seed(home, watcher_pid=None)
        queued, printed = _outcome(home, msg, _run_check(home))
        assert not queued
        assert printed


def test_CONTROL_no_presence_row_at_all_does_not_hold_the_mail():
    """An unregistered seat must not be worse off than an unwatched one."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        msg = _seed(home, watcher_pid=None)
        (home / ".liteharness" / "agents" / f"{ME}.json").unlink()
        queued, printed = _outcome(home, msg, _run_check(home))
        assert not queued
        assert printed


# ------------------------------------------------------------------- v. broadcast

def test_a_broadcast_follows_the_same_rule():
    """One rule, not two: a broadcast is mail, and the watcher renders it too."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        msg = _seed(home, to="broadcast", watcher_pid=os.getpid(), watcher_age_s=0)
        queued, printed = _outcome(home, msg, _run_check(home))
        assert queued, "a broadcast was archived out from under a live watcher"
        assert not printed


def test_CONTROL_a_broadcast_is_still_delivered_when_no_watcher_is_live():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        msg = _seed(home, to="broadcast", watcher_pid=_a_pid_that_is_not_running())
        queued, printed = _outcome(home, msg, _run_check(home))
        assert not queued
        assert printed


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
