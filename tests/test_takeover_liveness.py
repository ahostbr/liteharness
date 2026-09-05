"""T141-P — two defects that both answered the wrong question confidently.

1. `register --takeover` refused to reclaim a name from a DEAD agent whose
   `session_pid` had been reused by the LIVE claimant (a `/clear` in the same
   terminal). The liveness probe was measuring the agent asking for the name.
   `discover` already reported that record as "superseded by a later registration
   on the same PID" — the two disagreed about one record in the same second.

2. `inbox --agent-id <id>` was silently ignored and answered about the CALLER,
   returning "No messages involving <you>" for a question never asked.

Both are the same failure shape: an instrument that cannot see the case still
returns an answer, and the answer looks exactly like a real one.

Run:  python -m pytest tests/test_takeover_liveness.py -q
      python tests/test_takeover_liveness.py          (no pytest needed)
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

from liteharness import cli, config  # noqa: E402


def _now(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _write_agent(root: Path, agent_id: str, **fields) -> None:
    (root / "agents").mkdir(parents=True, exist_ok=True)
    record = {
        "agent_id": agent_id,
        "last_seen": _now(),
        "registered_at": _now(),
        "session_pid": os.getpid(),  # a REAL live pid: that is the whole trap
        "name": agent_id,
    }
    record.update(fields)
    (root / "agents" / f"{agent_id}.json").write_text(json.dumps(record), encoding="utf-8")


class _Root:
    """Point config.get_root() at a temp dir for the duration of a test."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = config.get_root
        root = Path(self._tmp.name)
        config.get_root = lambda: root  # type: ignore[assignment]
        cli.config.get_root = config.get_root  # type: ignore[attr-defined]
        return root

    def __exit__(self, *exc) -> None:
        config.get_root = self._orig  # type: ignore[assignment]
        cli.config.get_root = self._orig  # type: ignore[attr-defined]
        self._tmp.cleanup()


# ── 1. takeover liveness ────────────────────────────────────────────────────

def test_ghost_sharing_a_live_pid_is_NOT_live():
    """THE REGRESSION. Predecessor and successor share one terminal's pid.

    Both rows carry a live `session_pid` (this test process). Freshness cannot
    separate them either — an orphaned watcher keeps the dead row's heartbeat
    warm. Only the later registration wins.
    """
    with _Root() as root:
        _write_agent(root, "ghost", registered_at=_now(-60), last_seen=_now())
        _write_agent(root, "successor", registered_at=_now(0))
        assert cli._agent_record_live("ghost") is False, (
            "the dead predecessor still reads as LIVE — the probe is measuring the claimant"
        )
        assert cli._agent_record_live("successor") is True, (
            "the live successor must stay live, or takeover would steal from a running agent"
        )


def test_a_lone_live_record_is_still_live():
    """CONTROL. Without this the fix could 'pass' by calling everything dead."""
    with _Root() as root:
        _write_agent(root, "solo")
        assert cli._agent_record_live("solo") is True


def test_two_records_on_DIFFERENT_pids_do_not_supersede_each_other():
    """The rule is per-pid. An unrelated agent must never retire someone's name."""
    with _Root() as root:
        _write_agent(root, "a", registered_at=_now(-60), session_pid=os.getpid())
        _write_agent(root, "b", registered_at=_now(0), session_pid=os.getpid() + 1)
        assert cli._agent_record_live("a") is True


def test_a_record_with_no_session_pid_is_never_grouped():
    """Mirrors _dedupe_by_session_pid: unknown owner is not a bucket to collapse."""
    with _Root() as root:
        _write_agent(root, "orphan", session_pid=None)
        _write_agent(root, "later", registered_at=_now(10))
        assert cli._superseded_by_later_registration(
            "orphan", {"session_pid": None, "registered_at": _now(-10)}
        ) is False


def test_an_exited_successor_does_not_supersede():
    """A row that has already declared `exited_at` cannot retire anyone."""
    with _Root() as root:
        _write_agent(root, "holder", registered_at=_now(-60))
        _write_agent(root, "dead_later", registered_at=_now(0), exited_at=_now(0))
        assert cli._agent_record_live("holder") is True


def test_stale_heartbeat_still_dead():
    """The pre-existing staleness rule must survive the change."""
    with _Root() as root:
        _write_agent(root, "stale", last_seen=_now(-(cli._NAME_LIVE_STALE_SECONDS + 60)))
        assert cli._agent_record_live("stale") is False


# ── 2. inbox flag handling ──────────────────────────────────────────────────

def _run_inbox(*args, home: Path | None = None, caller: str | None = None) -> subprocess.CompletedProcess:
    """Run the inbox CLI, optionally against a maildir of our own (T369).

    `home` redirects `Path.home()` in the CHILD via USERPROFILE/HOME, which is the only
    seam available here: `inbox.INBOX_ROOT` is computed at import from `Path.home()`, and
    this is a subprocess, so no in-process mock can reach it.
    """
    env = dict(os.environ)
    if home is not None:
        env["USERPROFILE"] = str(home)
        env["HOME"] = str(home)
    if caller is not None:
        env["LITEHARNESS_AGENT_ID"] = caller
    return subprocess.run(
        [sys.executable, "-m", "liteharness.cli", "inbox", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=90, env=env,
    )


DEAD_ID = "00000000-dead-4dead-dead-000000000000"
OTHER_ID = "11111111-2222-3333-4444-555555555555"
CALLER_ID = "99999999-9999-9999-9999-999999999999"


def _seed_maildir(home: Path) -> None:
    """One message for each id, plus a broadcast — the shape that exposed the coupling."""
    new = home / ".liteharness" / "inbox" / "new"
    new.mkdir(parents=True, exist_ok=True)
    for name, to, body in (
        ("for-dead", DEAD_ID, "ADDRESSED-TO-DEAD"),
        ("for-other", OTHER_ID, "ADDRESSED-TO-OTHER"),
        ("announcement", "broadcast", "FLEET-BROADCAST"),
    ):
        (new / f"{name}.json").write_text(
            json.dumps({"id": name, "from": "2cbc7137", "to": to,
                        "timestamp": _now(), "body": body}),
            encoding="utf-8",
        )


def test_inbox_agent_id_is_accepted_not_ignored():
    """`--agent-id` must be honoured. Before: silently dropped, answered about the caller.

    🔴 THIS TEST USED TO READ THE DEVELOPER'S REAL ~/.liteharness MAILDIR (T369). It asked
    for a dead id and asserted that id appeared in the output — which is only true of the
    EMPTY answer, "No messages involving <id>". `cmd_inbox` keeps any message whose `to` is
    `broadcast` for EVERY queried id, so the moment a real fleet notice was the newest
    message the answer became a listing whose header reads `<sender> -> broadcast`, the
    queried id appeared nowhere, and the test failed. Measured twice on 2026-09-05 against
    unmodified main, both times eating a live notice.

        A TEST THAT SHELLS OUT AGAINST SHARED MUTABLE STATE IS NOT FLAKY. It is coupled,
        and its result reports on the machine as much as on the code.

    The intent is unchanged and is what is pinned below: the flag must select WHOSE inbox
    is answered. That is now asserted by CONTRAST — the same maildir answered three
    different ways — rather than by hoping an id appears in prose, which was only ever a
    proxy for it and was satisfied by the empty case.
    """
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        _seed_maildir(home)

        dead = _run_inbox("10", "--agent-id", DEAD_ID, home=home, caller=CALLER_ID)
        combined = dead.stdout + dead.stderr
        assert dead.returncode == 0, f"--agent-id should be accepted; got rc={dead.returncode}\n{combined}"
        assert "ADDRESSED-TO-DEAD" in combined, (
            "the queried id's own mail is missing — the flag was ignored again"
        )
        assert "ADDRESSED-TO-OTHER" not in combined, (
            "another agent's directed mail was returned for this id"
        )

        other = _run_inbox("10", "--agent-id", OTHER_ID, home=home, caller=CALLER_ID)
        assert "ADDRESSED-TO-OTHER" in (other.stdout + other.stderr)
        assert "ADDRESSED-TO-DEAD" not in (other.stdout + other.stderr)

        # ⚠️ THE DISCRIMINATOR. Both runs above could pass against a CLI that answered
        # about the caller if the caller happened to be the id asked for. Asking as
        # nobody proves the flag — and only the flag — chose the answer.
        caller = _run_inbox("10", home=home, caller=CALLER_ID)
        assert "ADDRESSED-TO-DEAD" not in (caller.stdout + caller.stderr)
        assert "ADDRESSED-TO-OTHER" not in (caller.stdout + caller.stderr)


def test_CONTROL_a_broadcast_is_answered_for_every_id():
    """Without this, the assertions above pass against a filter that returns nothing.

    This is also the exact behaviour that made the old test environment-coupled, so it is
    pinned deliberately rather than left as an accident of the implementation.
    """
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        _seed_maildir(home)
        for who in (DEAD_ID, OTHER_ID):
            r = _run_inbox("10", "--agent-id", who, home=home, caller=CALLER_ID)
            assert "FLEET-BROADCAST" in (r.stdout + r.stderr), (
                f"a broadcast was withheld from {who} — the filter now drops fleet notices"
            )


def test_an_empty_maildir_names_the_id_it_was_asked_about():
    """The original assertion, kept — but now on the only input for which it is true."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / ".liteharness" / "inbox" / "new").mkdir(parents=True)
        r = _run_inbox("10", "--agent-id", DEAD_ID, home=home, caller=CALLER_ID)
        combined = r.stdout + r.stderr
        assert r.returncode == 0, combined
        assert DEAD_ID in combined, "the empty answer no longer says whose inbox was empty"
        assert CALLER_ID not in combined, "the empty answer named the CALLER — the original defect"


def test_inbox_rejects_an_unknown_flag_loudly():
    """The CLASS fix: the next wrong spelling must fail, not lie."""
    r = _run_inbox("--agnet-id", "x")
    assert r.returncode != 0, "an unknown flag must not be silently ignored"
    assert "unknown option" in (r.stdout + r.stderr).lower()


def test_inbox_flag_value_is_not_read_as_the_count():
    """A UUID must not be mistaken for N, and N must still work beside the flag."""
    r = _run_inbox("--agent-id", "00000000-dead-4dead-dead-000000000000")
    assert r.returncode == 0, r.stdout + r.stderr


def test_inbox_missing_value_fails_rather_than_defaulting():
    r = _run_inbox("--agent-id")
    assert r.returncode != 0
    assert "needs a value" in (r.stdout + r.stderr).lower()


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    failures = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
