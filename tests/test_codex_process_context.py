"""T926: the Codex registration's process-ancestry walk runs in-process.

It was one PowerShell/WMI query per ancestor. Measured 2.8-49 s on one chain
(load-dependent), and a PowerShell killed at its 5 s timeout lived ~20 s more
while its WMI call returned, blocking subprocess.run's untimed post-kill
communicate(). That was the hang in
test_codex_stdout_delivery::test_delivery_duplicate_refusal_and_restart.
"""
import os
import subprocess

import psutil
import pytest

from liteharness.cli_scripts.codex import manual_liteharness as m

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the walk runs on Windows only")


def test_the_walk_starts_no_process(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("detect_process_context started a process")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    rows = m.detect_process_context()["ancestry"]
    assert rows[0]["pid"] == os.getpid()
    assert rows[0]["parent"] == os.getppid()


class _Fake:
    def __init__(self, pid, name, cmd="", parent=None):
        self.pid, self._name, self._cmd, self._parent = pid, name, cmd, parent

    def name(self): return self._name
    def cmdline(self):
        if self._cmd is None:
            raise psutil.AccessDenied(self.pid)
        return self._cmd.split()
    def ppid(self): return self._parent.pid if self._parent else 0
    def parent(self): return self._parent


def test_codex_and_windows_terminal_are_found_up_the_chain(monkeypatch):
    wt = _Fake(30, "WindowsTerminal.exe", cmd=None)  # a protected cmdline is skipped, not fatal
    codex = _Fake(20, "node.exe", "node C:/x/@openai/codex/bin/codex.js", wt)
    me = _Fake(10, "python.exe", "python -m manual", codex)
    monkeypatch.setattr(psutil, "Process", lambda *a: me)
    ctx = m.detect_process_context()
    assert ctx["codex_pid"] == 20 and ctx["wt_process_pid"] == 30
    assert [r["pid"] for r in ctx["ancestry"]] == [10, 20, 30]


class _RawOSError(_Fake):
    """psutil 7 re-raises an unrecognised Windows error as a bare OSError
    (_pswindows.py:657), not as a psutil.Error."""
    def __init__(self, *a, where="parent", **k):
        super().__init__(*a, **k)
        self._where = where

    def parent(self):
        if self._where == "parent":
            raise OSError(22, "unrecognised windows error")
        return super().parent()

    def cmdline(self):
        if self._where == "cmdline":
            raise OSError(22, "unrecognised windows error")
        return super().cmdline()


@pytest.mark.parametrize("where", ["parent", "cmdline"])
def test_a_raw_oserror_from_psutil_never_escapes_registration(monkeypatch, where):
    # Knuth T926 B-1. The WMI base turned every failure into {}; a bare
    # OSError here would abort `start` instead.
    me = _RawOSError(10, "python.exe", "python -m manual", _Fake(20, "codex.exe"), where=where)
    monkeypatch.setattr(psutil, "Process", lambda *a: me)
    ctx = m.detect_process_context()
    assert ctx["ancestry"][0]["pid"] == 10
