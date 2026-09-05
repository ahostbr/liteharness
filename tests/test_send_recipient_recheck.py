"""T238 — a send refused a LIVE agent because one directory listing lied.

The registry is one JSON file per agent, and a watcher heartbeat rewrites a
record with tmp-write + `os.replace` (hooks.py:270-286). A `glob("*.json")` that
runs inside that window comes back missing exactly the record being rewritten.
REPRODUCED 2026-09-03 (Sentinel, `scratchpad/race_probe.py`, mechanism by
OpenBolt 6feaf389): 45,103 rewrites against 375,989 reads produced one listing
short of the rewritten record. One such listing is enough for `cmd_send` to
refuse a live id with "not registered ... Pass --force" — seen against a98678ea
at 14:4x, and the workaround it recommends is a paste into a terminal, which is
the thing that loses conversations.

    ABSENCE IS THE ONLY VERDICT THIS READER CAN GET WRONG, so it is the only
    one worth paying 50 ms to re-check.

⚠️ HOW THE RACE IS MADE DETERMINISTIC HERE, AND WHAT THAT COSTS. The real miss
rate is roughly one in 376,000 reads; a test that waits for a genuine one is a
test that is slow, flaky, or vacuous. So the gap is driven instead of waited
for: the record starts ABSENT (which is exactly what a missed listing looks
like to the reader) and the patched `time.sleep` — the retry's own gap — puts it
back, standing in for the `os.replace` landing. Everything else is real: a real
temp registry, the real `_known_agent_ids` doing a real `glob`, the real
`cmd_send`. What is NOT proven here is the rate or the mechanism of the miss;
that was measured by the probe this card cites, and it is not re-measured.

Run:  python -m pytest tests/test_send_recipient_recheck.py -q
      python tests/test_send_recipient_recheck.py       (no pytest needed)
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liteharness import cli, config, inbox  # noqa: E402

LIVE = "a98678ea-1111-4222-8333-444444444444"
OTHERS = [f"0000000{n}-0000-4000-8000-00000000000{n}" for n in range(1, 5)]


def _record(root: Path, agent_id: str) -> Path:
    return root / "agents" / f"{agent_id}.json"


def _write_agent(root: Path, agent_id: str) -> None:
    (root / "agents").mkdir(parents=True, exist_ok=True)
    _record(root, agent_id).write_text(
        json.dumps(
            {
                "agent_id": agent_id,
                "name": agent_id[:8],
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "session_pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )


class _Bench:
    """A temp registry, a temp maildir, and a sleep the test controls.

    `during_gap` runs inside the retry's own sleep — the only moment at which a
    rewrite could land — so the arms below differ ONLY in whether the record
    comes back, never in timing luck.
    """

    def __init__(self, during_gap=None) -> None:
        self._during_gap = during_gap
        self.slept: list[float] = []

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._orig_root = config.get_root
        config.get_root = lambda: self.root  # type: ignore[assignment]
        cli.config.get_root = config.get_root  # type: ignore[attr-defined]

        self._orig_inbox = (
            inbox.INBOX_ROOT,
            inbox.INBOX_NEW,
            inbox.INBOX_CUR,
            inbox.INBOX_DONE,
            inbox.INBOX_TMP,
        )
        inbox.INBOX_ROOT = self.root / "inbox"
        inbox.INBOX_NEW = inbox.INBOX_ROOT / "new"
        inbox.INBOX_CUR = inbox.INBOX_ROOT / "cur"
        inbox.INBOX_DONE = inbox.INBOX_ROOT / "done"
        inbox.INBOX_TMP = inbox.INBOX_ROOT / "tmp"
        inbox.ensure_dirs()

        self._orig_sleep = cli.time.sleep

        def _sleep(seconds: float) -> None:
            self.slept.append(seconds)
            if self._during_gap:
                self._during_gap(self.root)

        cli.time.sleep = _sleep  # type: ignore[assignment]

        for agent_id in OTHERS:
            _write_agent(self.root, agent_id)
        return self.root

    def __exit__(self, *exc) -> None:
        cli.time.sleep = self._orig_sleep  # type: ignore[assignment]
        (
            inbox.INBOX_ROOT,
            inbox.INBOX_NEW,
            inbox.INBOX_CUR,
            inbox.INBOX_DONE,
            inbox.INBOX_TMP,
        ) = self._orig_inbox
        config.get_root = self._orig_root  # type: ignore[assignment]
        cli.config.get_root = self._orig_root  # type: ignore[attr-defined]
        self._tmp.cleanup()


def _send(to: str, **kwargs) -> tuple[int, str, str]:
    """Run cmd_send, capturing its streams and its exit code."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            cli.cmd_send(to, "a briefing that must not be lost", from_id="sender", **kwargs)
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _delivered() -> int:
    return len(list(inbox.INBOX_NEW.glob("*"))) if inbox.INBOX_NEW.exists() else 0


# ── the deliverable ─────────────────────────────────────────────────────────

def test_a_record_that_reappears_during_the_gap_is_NOT_refused():
    """🔴 THE DELIVERABLE. First listing misses it, second sees it, send proceeds."""
    with _Bench(during_gap=lambda root: _write_agent(root, LIVE)):
        code, out, err = _send(LIVE)
    assert code == 0, f"a live agent was refused: {err}"
    assert "NOTHING WAS SENT" not in err
    assert "Sent message" in out


def test_THE_ARM_CAN_FAIL_a_record_that_never_comes_back_is_still_refused():
    """🔴 THE CONTROL THAT MAKES THE ARM ABOVE MEAN SOMETHING.

    Same code path, same two reads, same 50 ms — the ONLY difference is that
    nothing lands in the gap. If this ever passes, the arm above is passing
    because the guard stopped working, not because the retry works.
    """
    with _Bench(during_gap=None):
        code, _out, err = _send(LIVE)
    assert code == 1, "an id that is genuinely absent must still be refused"
    assert "NOTHING WAS SENT" in err
    assert "--force" in err, "the refusal must keep naming its own remedy"


def test_the_single_read_is_what_used_to_refuse_it():
    """The pre-fix behaviour, stated as a measurement rather than as history.

    One read of a registry that is momentarily short of the record answers
    "not registered" — which is the whole defect, and is why the second read
    exists.
    """
    with _Bench():
        known, error = cli._known_agent_ids()
        assert error is None, f"the registry must be readable for this to mean anything: {error}"
        assert LIVE not in known, "the bench did not reproduce the missing record"
        assert len(known) == len(OTHERS), "the other agents must still be visible"


# ── the guard is not weakened ───────────────────────────────────────────────

def test_a_genuinely_unknown_id_is_refused_after_both_reads():
    """The typo guard this file's neighbours document twice must survive."""
    with _Bench(during_gap=lambda root: _write_agent(root, LIVE)):
        code, _out, err = _send("not-a-real-agent-id")
    assert code == 1
    assert "NOTHING WAS SENT" in err


def test_an_unreadable_registry_still_fails_OPEN_and_says_so():
    """A re-read must never turn "cannot verify" into a refusal.

    `_known_agent_ids` fails open on an unreadable registry on purpose: an empty
    set from a broken reader and a genuinely unknown id arrive identical, and
    refusing on the former would silence the fleet from one bad read.
    """
    with _Bench() as root:
        for agent_id in OTHERS:
            _record(root, agent_id).unlink()
        code, out, err = _send(LIVE)
    assert code == 0, "an unreadable registry must not block a send"
    assert "RECIPIENT NOT VERIFIED" in err
    assert "Sent message" in out


def test_force_still_skips_the_check_entirely():
    """--force is the documented escape and must not start paying for the check."""
    bench = _Bench()
    with bench:
        code, out, _err = _send(LIVE, force=True)
    assert code == 0
    assert "Sent message" in out
    assert bench.slept == [], "--force reached the recheck it is meant to skip"


def test_broadcast_is_an_address_that_owns_no_record():
    with _Bench():
        code, out, _err = _send("broadcast")
    assert code == 0
    assert "Sent message" in out


# ── cost ────────────────────────────────────────────────────────────────────

def test_a_recipient_found_on_the_first_read_never_waits():
    """⭐ EVERY SEND IN THE FLEET GOES THROUGH THIS PATH.

    Paying the retry on the common case would put 50 ms on every message for a
    one-in-376,000 miss. The delay is the price of a NEGATIVE, and only that.
    """
    bench = _Bench(during_gap=lambda root: _write_agent(root, LIVE))
    with bench as root:
        _write_agent(root, LIVE)  # present from the start
        code, out, _err = _send(LIVE)
    assert code == 0 and "Sent message" in out
    assert bench.slept == [], "the common case paid for the rare one"


def test_the_gap_is_slept_exactly_once_on_a_miss():
    """One re-read, not a loop: a retry that keeps trying is a hang with a name."""
    bench = _Bench(during_gap=lambda root: _write_agent(root, LIVE))
    with bench:
        code, _out, _err = _send(LIVE)
    assert code == 0
    assert bench.slept == [cli.RECIPIENT_RECHECK_DELAY_S], f"slept {bench.slept}"


def test_no_sleep_at_all_when_the_first_read_finds_it():
    bench = _Bench()
    with bench:
        _write_agent(bench.root, LIVE)
        code, _out, _err = _send(LIVE)
    assert code == 0
    assert bench.slept == [], f"a hit paid the retry delay: {bench.slept}"


# ── the message describes the read that decided ─────────────────────────────

def test_the_refusal_counts_the_second_listing_not_the_first():
    """A check must report the criterion it actually applied.

    The count and the did-you-mean list come from the read the refusal was made
    on. Quoting the first listing after deciding on the second would describe a
    registry nobody consulted.
    """
    extra = "99999999-9999-4999-8999-999999999999"
    bench = _Bench(during_gap=lambda root: _write_agent(root, extra))
    with bench:
        code, _out, err = _send(LIVE)
    assert code == 1
    assert f"{len(OTHERS) + 1} agent(s) known" in err, err


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
