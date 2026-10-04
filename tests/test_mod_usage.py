import json
import os

import pytest

from liteharness import mod_usage as m


def payload(sid="seat", sampled=1_000_000, last=900_000, active=True):
    return {"schemaVersion": 1, "source": "claude-code-mod", "sessionId": sid,
            "sampledAt": sampled, "active": active, "usageAvailable": True,
            "context": {"tokens": 123, "window": 200_000, "percent": 0},
            "cache": {"estimated": True, "basis": "successful-main-turn-step-start",
                      "ttlMs": m.TTL_MS, "lastRequestAt": last,
                      "expiresAt": None if last is None else last + m.TTL_MS}}


def save(root, data, name=None):
    path = root / ((name or data["sessionId"]) + ".json")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_live_countdown_is_recomputed_and_extra_content_is_not_returned(tmp_path):
    data = payload()
    data["transcript"] = "must not leak"
    save(tmp_path, data)
    result = m.read_session("seat", root=tmp_path, now_ms=1_010_000)
    assert result["status"] == "ok"
    assert result["metrics"]["cache"]["remainingMs"] == 3_490_000
    assert result["metrics"]["context"]["percent"] == 0
    assert "must not leak" not in json.dumps(result)


def test_unknown_cold_stale_and_ended_are_distinct(tmp_path):
    save(tmp_path, payload(last=None))
    assert m.read_session("seat", root=tmp_path, now_ms=1_000_000)["metrics"]["cache"]["state"] == "unknown"
    save(tmp_path, payload())
    result = m.read_session("seat", root=tmp_path, now_ms=5_000_000)
    assert result["status"] == "stale"
    assert result["metrics"]["cache"]["state"] == "cold"
    save(tmp_path, payload(active=False))
    assert m.read_session("seat", root=tmp_path, now_ms=1_000_000)["status"] == "ended"


@pytest.mark.parametrize("change", [
    {"sessionId": "other"}, {"schemaVersion": 2}, {"sampledAt": True},
    {"sampledAt": 9_000_000}, {"context": {"percent": float("nan")}},
    {"context": {"percent": 101}}, {"cache": {"estimated": False}},
    {"active": "yes"},
])
def test_corrupt_contract_is_unavailable(tmp_path, change):
    data = payload()
    data.update(change)
    save(tmp_path, data, "seat")
    assert m.read_session("seat", root=tmp_path, now_ms=1_000_000)["status"] == "unavailable"


def test_partial_oversized_missing_and_unsafe_identity(tmp_path):
    assert m.read_session("seat", root=tmp_path)["status"] == "missing"
    path = tmp_path / "seat.json"
    for text in ('{"schemaVersion":', "x" * (m.MAX_BYTES + 1)):
        path.write_text(text)
        assert m.read_session("seat", root=tmp_path)["status"] == "unavailable"
    for sid in ("../secret", "", "a/b", "a.b", "x" * 129):
        with pytest.raises(ValueError):
            m.read_session(sid, root=tmp_path)


def test_all_reads_newest_first_is_bounded_and_exact_session_bypasses_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MAX_READS", 2)
    for sid, stamp in (("old", 998_000), ("new", 1_000_000), ("middle", 999_000)):
        path = save(tmp_path, payload(sid=sid, sampled=stamp))
        os.utime(path, ns=(stamp * 1_000_000, stamp * 1_000_000))
    result = m.read_all(root=tmp_path, now_ms=1_000_000)
    assert result["truncated"] is True
    assert result["candidates"] == 3
    assert [r["sessionId"] for r in result["sessions"]] == ["new", "middle"]
    assert m.read_session("old", root=tmp_path, now_ms=1_000_000)["status"] == "ok"


def test_discovery_filter_does_not_delete_old_files(tmp_path):
    path = save(tmp_path, payload())
    os.utime(path, ns=(1, 1))
    assert m.read_all(root=tmp_path, now_ms=m.DISCOVERABLE_MS + 1_000_000)["sessions"] == []
    assert path.exists()


def test_cli_json_output_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(m, "usage_root", lambda: tmp_path)
    assert m.main(["--session", "absent"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "missing"

@pytest.mark.parametrize("reader", ["session", "all"])
@pytest.mark.parametrize("field", ["sampledAt", "percent", "lastRequestAt"])
def test_huge_integer_in_real_snapshot_never_crashes_readers(tmp_path, field, reader):
    data = payload()
    if field == "sampledAt":
        data[field] = 10**400
    elif field == "percent":
        data["context"][field] = 10**400
    else:
        data["cache"][field] = 10**400
    path = save(tmp_path, data)
    os.utime(path, ns=(1_000_000_000_000, 1_000_000_000_000))
    assert m.read_session("seat", root=tmp_path, now_ms=1_000_000)["status"] == "unavailable"
    assert m.read_all(root=tmp_path, now_ms=1_000_000)["sessions"][0]["status"] == "unavailable"
