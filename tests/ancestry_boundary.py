"""Synthetic Win32 process observations, exercising the real ctypes walker."""
from types import SimpleNamespace

from liteharness import process_ancestry


class Function:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


def windows_processes(monkeypatch, rows, *, denied=(), failed_times=(), failed_query=(),
                      exited=(), exit_after_walk=()):
    """rows maps PID to (parent PID, Unix birth seconds); no walker is mocked."""
    closed, opened, waits = [], [], {}

    def open_process(rights, inherit, pid):
        assert rights == 0x101000 and not inherit
        opened.append(pid)
        return pid if pid in rows and pid not in denied else 0

    def query(handle, kind, info, size, returned):
        assert kind == 0
        if handle in failed_query:
            return -1
        info._obj.pid = handle
        info._obj.parent_pid = rows[handle][0]
        returned._obj.value = size
        return 0

    def times(handle, created, exited_time, kernel_time, user_time):
        if handle in failed_times:
            return 0
        ticks = round((rows[handle][1] + 11_644_473_600) * 10_000_000)
        created._obj.low = ticks & 0xFFFFFFFF
        created._obj.high = ticks >> 32
        return 1

    def wait(handle, timeout):
        assert timeout == 0
        waits[handle] = waits.get(handle, 0) + 1
        return 0 if handle in exited or (handle in exit_after_walk and waits[handle] > 1) else 258

    kernel = SimpleNamespace(OpenProcess=Function(open_process),
                             GetProcessTimes=Function(times),
                             WaitForSingleObject=Function(wait),
                             CloseHandle=Function(lambda handle: closed.append(handle) or 1))
    native = SimpleNamespace(NtQueryInformationProcess=Function(query))
    monkeypatch.setattr(process_ancestry.ctypes, 'WinDLL',
                        lambda name, **kwargs: {'kernel32': kernel, 'ntdll': native}[name], raising=False)
    monkeypatch.setattr(process_ancestry, 'sys', SimpleNamespace(platform='win32'))
    return SimpleNamespace(opened=opened, closed=closed)
