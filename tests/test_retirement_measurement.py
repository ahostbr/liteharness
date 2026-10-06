"""Targeted psutil boundary mocked, never enumerate or signal live processes."""
from types import SimpleNamespace

import psutil
import pytest

from liteharness import retirement as r


@pytest.mark.parametrize("change", ["none", "birth", "parent", "gone", "zombie"])
def test_fresh_process_object_rechecks_cached_birth(monkeypatch, change):
    calls = []
    def process(pid):
        calls.append(pid)
        second = len(calls) == 2
        return SimpleNamespace(create_time=lambda: 2.001 if second and change == "birth" else 2,
            ppid=lambda: 11 if second and change == "parent" else 10,
            is_running=lambda: not(second and change == "gone"),
            status=lambda: psutil.STATUS_ZOMBIE if second and change == "zombie" else "running")
    monkeypatch.setattr(psutil, "Process", process)
    monkeypatch.setattr(psutil, "process_iter", lambda *a: pytest.fail("global census forbidden"))
    if change == "none":
        assert r.measure_process(20) == r.ProcessIdentity(20, 10, 2000)
    else:
        with pytest.raises(r.RetirementRefused, match="changed_during_measurement"):
            r.measure_process(20)
    assert calls == [20, 20]


def test_access_denied_is_neither_identity_nor_absence(monkeypatch):
    from liteharness.retirement_leader import process_absent
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", denied)
    with pytest.raises(r.RetirementRefused, match="measurement_unconfirmed"):
        r.measure_process(20)
    assert process_absent(20) is False


def test_only_native_no_such_process_is_positive_absence(monkeypatch):
    from liteharness.retirement_leader import process_absent
    def absent(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", absent)
    assert process_absent(20) is True
