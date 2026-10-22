"""Shared ancestry contract: native boundaries, not fabricated walker results."""
import builtins
import os
import sys
from itertools import pairwise
from types import SimpleNamespace

import pytest
from ancestry_boundary import windows_processes

from liteharness import process_ancestry as ancestry
from liteharness import resume_seat


@pytest.mark.parametrize('pid', [None, False, True, 0, -1, 1.5, '1.5', '', ' 1', 'x', [], {}])
def test_invalid_pid_refuses_without_os_inspection(monkeypatch, pid):
    native = windows_processes(monkeypatch, {})
    assert ancestry.ancestor_pids(pid) == []
    assert native.opened == []


def test_windows_chain_numeric_owner_first_and_limited_to_six_parents(monkeypatch):
    rows = {pid: (pid - 1, 100 + pid) for pid in range(1, 10)}
    native = windows_processes(monkeypatch, rows)
    assert resume_seat._ancestor_pids('9') == [9, 8, 7, 6, 5, 4, 3]
    assert native.opened == [9, 8, 7, 6, 5, 4, 3]
    assert native.closed == list(reversed(native.opened))


@pytest.mark.parametrize('failure', ['denied', 'failed_times', 'failed_query', 'exited', 'exit_after_walk'])
def test_unknown_or_exited_intermediate_invalidates_whole_walk_and_closes_handles(monkeypatch, failure):
    native = windows_processes(monkeypatch, {3: (2, 300), 2: (1, 200), 1: (0, 100)},
                               **{failure: (2,)})
    assert ancestry.process_ancestry(3) == []
    expected = [pid for pid in native.opened if not (failure == 'denied' and pid == 2)]
    assert native.closed == list(reversed(expected))


def test_reused_parent_is_not_an_ancestor(monkeypatch):
    native = windows_processes(monkeypatch, {3: (2, 300), 2: (0, 400)})
    assert ancestry.process_ancestry(3) == [ancestry.ProcessIdentity(3, 300)]
    assert native.closed == [2, 3]


def test_cycle_unknown_pid_and_unavailable_native_api_refuse(monkeypatch):
    windows_processes(monkeypatch, {3: (3, 300)})
    assert ancestry.ancestor_pids(3) == []
    assert ancestry.ancestor_pids(999) == []
    assert ancestry.ancestor_pids(2**32) == []
    monkeypatch.setattr(ancestry.ctypes, 'WinDLL', lambda *a, **k: (_ for _ in ()).throw(OSError('denied')))
    assert ancestry.ancestor_pids(3) == []


def test_posix_boundary_keeps_birth_and_liveness_guards(monkeypatch):
    import psutil

    monkeypatch.setattr(ancestry, 'sys', SimpleNamespace(platform='linux'))
    rows = {3: (2, 300), 2: (1, 200), 1: (None, 100)}
    dead = set()

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return rows[self.pid][1]

        def parent(self):
            return Process(rows[self.pid][0]) if rows[self.pid][0] else None

        def is_running(self):
            return self.pid not in dead

        def status(self):
            return psutil.STATUS_RUNNING

    monkeypatch.setattr(psutil, 'Process', Process)
    assert ancestry.ancestor_pids(3) == [3, 2, 1]
    assert ancestry.process_ancestry(3, stop_at=2) == [
        ancestry.ProcessIdentity(3, 300), ancestry.ProcessIdentity(2, 200)]
    assert ancestry.process_ancestry(3, stop_at=999) == []
    rows[2] = (1, 400)
    assert ancestry.ancestor_pids(3) == [3]
    assert ancestry.process_ancestry(3, stop_at=2) == []
    dead.add(2)
    assert ancestry.ancestor_pids(3) == []
    monkeypatch.setattr(psutil, 'Process', lambda pid: (_ for _ in ()).throw(psutil.AccessDenied(pid)))
    assert ancestry.ancestor_pids(3) == []


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows process handles')
def test_current_process_native_ancestry_without_psutil(monkeypatch):
    original = builtins.__import__

    def no_psutil(name, *args, **kwargs):
        if name == 'psutil':
            raise ImportError('Windows proof must not need psutil')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', no_psutil)
    chain = ancestry.process_ancestry(os.getpid(), stop_at=os.getppid())
    assert chain and chain[0].pid == os.getpid()
    assert chain[1].pid == os.getppid()
    assert all(parent.started_at <= child.started_at for child, parent in pairwise(chain))
    assert len(chain) == 2


@pytest.mark.parametrize('target', [False, True, 0, -1, 1.5, '2', [], {}])
def test_invalid_target_refuses_before_inspection(monkeypatch, target):
    native = windows_processes(monkeypatch, {})
    assert ancestry.process_ancestry(3, stop_at=target) == []
    assert native.opened == []


def test_targeted_path_does_not_require_targets_inaccessible_parent(monkeypatch):
    native = windows_processes(monkeypatch, {3: (2, 300), 2: (1, 200)}, denied=(1,))
    assert ancestry.process_ancestry(3, stop_at=2) == [
        ancestry.ProcessIdentity(3, 300), ancestry.ProcessIdentity(2, 200)]
    assert native.opened == [3, 2] and native.closed == [2, 3]
    assert ancestry.process_ancestry(3) == []
    assert native.opened == [3, 2, 3, 2, 1]


@pytest.mark.parametrize('failure', ['denied', 'failed_times', 'failed_query', 'exited', 'exit_after_walk'])
def test_targeted_path_unknown_intermediate_refuses(monkeypatch, failure):
    native = windows_processes(monkeypatch, {3: (2, 300), 2: (1, 200), 1: (0, 100)},
                               **{failure: (2,)})
    assert ancestry.process_ancestry(3, stop_at=1) == []
    expected = [pid for pid in native.opened if not (failure == 'denied' and pid == 2)]
    assert native.closed == list(reversed(expected))


def test_target_must_be_reached_without_pid_reuse_and_inside_bound(monkeypatch):
    windows_processes(monkeypatch, {3: (2, 300), 2: (0, 400)})
    assert ancestry.process_ancestry(3, stop_at=2) == []
    windows_processes(monkeypatch, {3: (0, 300)})
    assert ancestry.process_ancestry(3, stop_at=2) == []
    native = windows_processes(monkeypatch, {pid: (pid - 1, 100 + pid) for pid in range(1, 10)})
    assert ancestry.process_ancestry(9, stop_at=2) == []
    assert native.opened == [9, 8, 7, 6, 5, 4, 3]
    assert native.closed == list(reversed(native.opened))
