"""discover must collapse /resume double-registrations.

`/resume` boots with a throwaway session id, SessionStart registers it, then the
resume adopts the real id and registers again — two rows, one process. Measured
2026-08-19: pid 61112 (GrimShard 14:56:31 -> Sentinel 14:56:45) and pid 269264
(LongRivet 14:56:08 -> OpenBolt 14:56:20), ~13s apart.

The pre-existing liveness filter cannot catch this. Both rows carry the SAME
LIVE pid, so `_pid_alive` is correctly True for each — a check that asks "is
this dead?" can never collapse two rows that are both alive. That is the whole
reason this needs its own pass.
"""

from liteharness.cli import _dedupe_by_session_pid


def _a(agent_id, pid, registered_at, name=None):
    return {"agent_id": agent_id, "session_pid": pid, "registered_at": registered_at,
            "name": name or agent_id}


def test_two_rows_one_pid_collapse_to_the_later_registration():
    rows = [
        _a("grimshard", 61112, "2026-08-19T14:56:31"),
        _a("sentinel", 61112, "2026-08-19T14:56:45"),
    ]
    kept, superseded = _dedupe_by_session_pid(rows)
    assert len(kept) == 1, "one process must produce one row"
    assert kept[0]["agent_id"] == "sentinel", "the ADOPTED (later) id wins, not the throwaway"
    assert [s["agent_id"] for s in superseded] == ["grimshard"]


def test_order_of_input_does_not_change_the_winner():
    """The later registration wins even when it is seen first."""
    rows = [
        _a("sentinel", 61112, "2026-08-19T14:56:45"),
        _a("grimshard", 61112, "2026-08-19T14:56:31"),
    ]
    kept, superseded = _dedupe_by_session_pid(rows)
    assert kept[0]["agent_id"] == "sentinel"
    assert [s["agent_id"] for s in superseded] == ["grimshard"]


def test_distinct_pids_are_never_collapsed():
    rows = [_a("sentinel", 61112, "t1"), _a("openbolt", 269264, "t2")]
    kept, superseded = _dedupe_by_session_pid(rows)
    assert len(kept) == 2 and superseded == []


def test_rows_without_a_session_pid_are_never_grouped():
    """The opposite failure, and a worse one.

    Grouping on a falsy pid buckets every owner-less row together and the roster
    silently loses all but one of them. Measured on the live fleet the day the
    owner field landed: 11 of 15 presence files had no session_pid.
    """
    rows = [_a("a", None, "t1"), _a("b", None, "t2"), _a("c", 0, "t3")]
    kept, superseded = _dedupe_by_session_pid(rows)
    assert len(kept) == 3, "owner-less rows must all survive"
    assert superseded == []


def test_mixed_fleet_the_real_2026_08_19_shape():
    rows = [
        _a("grimshard", 61112, "2026-08-19T14:56:31"),
        _a("sentinel", 61112, "2026-08-19T14:56:45"),
        _a("longrivet", 269264, "2026-08-19T14:56:08"),
        _a("openbolt", 269264, "2026-08-19T14:56:20"),
        _a("silverbolt", 221020, "2026-08-19T15:01:00"),
        _a("silentchoke", None, "2026-08-19T15:00:00"),
    ]
    kept, superseded = _dedupe_by_session_pid(rows)
    ids = sorted(a["agent_id"] for a in kept)
    assert ids == ["openbolt", "sentinel", "silentchoke", "silverbolt"]
    assert sorted(s["agent_id"] for s in superseded) == ["grimshard", "longrivet"]


def test_superseded_is_reported_not_silently_dropped():
    """A dropped row nothing reports is how a roster gets quietly wrong again."""
    rows = [_a("throwaway", 999, "t1"), _a("real", 999, "t2")]
    _, superseded = _dedupe_by_session_pid(rows)
    assert superseded, "the caller must be able to name what it removed"


def test_missing_registered_at_does_not_crash():
    """Older presence files predate the field; absence must not raise."""
    rows = [{"agent_id": "x", "session_pid": 5}, {"agent_id": "y", "session_pid": 5}]
    kept, superseded = _dedupe_by_session_pid(rows)
    assert len(kept) == 1 and len(superseded) == 1
