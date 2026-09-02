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

def _run_inbox(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "liteharness.cli", "inbox", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=90,
    )


def test_inbox_agent_id_is_accepted_not_ignored():
    """`--agent-id` must be honoured. Before: silently dropped, answered about the caller."""
    r = _run_inbox("1", "--agent-id", "00000000-dead-4dead-dead-000000000000")
    combined = r.stdout + r.stderr
    assert r.returncode == 0, f"--agent-id should be accepted; got rc={r.returncode}\n{combined}"
    assert "00000000-dead-4dead-dead-000000000000" in combined, (
        "the id asked about does not appear in the answer — the flag was ignored again"
    )


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
