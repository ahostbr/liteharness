"""Bounded live ancestry evidence; no shell commands or Windows dependencies.

Unknown/inaccessible process evidence invalidates the entire observation. A newer
parent PID terminates the chain before that reused PID, never proving an edge to
it. The initial PID's identity must still be bound to its record by the caller.
"""
from __future__ import annotations

import ctypes
import math
import sys
from typing import NamedTuple


class ProcessIdentity(NamedTuple):
    pid: int
    started_at: float  # Unix seconds, matching psutil.create_time().


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _ProcessBasicInformation(ctypes.Structure):
    _fields_ = [("reserved1", ctypes.c_void_p), ("peb", ctypes.c_void_p),
                ("reserved2", ctypes.c_void_p * 2), ("pid", ctypes.c_size_t),
                ("parent_pid", ctypes.c_size_t)]


def _windows_ancestry(pid: int, stop_at: int | None) -> list[ProcessIdentity]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(_FileTime)] * 4
    kernel.GetProcessTimes.restype = ctypes.c_int
    native.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.POINTER(ctypes.c_uint32)]
    native.NtQueryInformationProcess.restype = ctypes.c_int32
    handles = []
    chain: list[ProcessIdentity] = []
    try:
        for _ in range(7):  # owner + at most six parents; never inspect an eighth
            if not 0 < pid <= 0xFFFFFFFF or any(item.pid == pid for item in chain):
                return []
            handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
            if not handle:
                return []
            handles.append(handle)
            if kernel.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT = still running
                return []
            info = _ProcessBasicInformation()
            size = ctypes.c_uint32()
            if (native.NtQueryInformationProcess(handle, 0, ctypes.byref(info),
                                                ctypes.sizeof(info), ctypes.byref(size)) != 0
                    or size.value != ctypes.sizeof(info) or info.pid != pid):
                return []
            created, exited, kernel_time, user_time = (_FileTime() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return []
            birth = ((created.high << 32) | created.low) / 10_000_000 - 11_644_473_600
            if not math.isfinite(birth) or birth <= 0:
                return []
            if chain and birth > chain[-1].started_at:
                break  # parent PID was reused after the child was born
            chain.append(ProcessIdentity(pid, birth))
            if pid == stop_at or not info.parent_pid:
                break
            pid = info.parent_pid
        if stop_at is not None and (not chain or chain[-1].pid != stop_at):
            return []
        # Retain handles until the whole walk is validated: an exited/recycled
        # intermediate process cannot silently turn this into partial authority.
        if any(kernel.WaitForSingleObject(handle, 0) != 258 for handle in handles):
            return []
        return chain
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


def _posix_ancestry(pid: int, stop_at: int | None) -> list[ProcessIdentity]:
    import psutil

    process = psutil.Process(pid)
    processes = []
    chain: list[ProcessIdentity] = []
    for depth in range(7):
        if process.pid <= 0 or any(item.pid == process.pid for item in chain):
            return []
        birth = process.create_time()
        if not math.isfinite(birth) or birth <= 0:
            return []
        processes.append(process)
        if chain and birth > chain[-1].started_at:
            break
        chain.append(ProcessIdentity(process.pid, birth))
        if process.pid == stop_at or depth == 6:
            break
        process = process.parent()
        if process is None:
            break
    if stop_at is not None and (not chain or chain[-1].pid != stop_at):
        return []
    if any(not p.is_running() or p.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
           for p in processes):
        return []
    return chain


def process_ancestry(pid: int | str | None, *, stop_at: int | None = None) -> list[ProcessIdentity]:
    """Child-first live identities, at most seven. Uncertain evidence returns [].

    With stop_at, prove only the path through that PID, inclusive. The target
    must be reached within the bound; its own parents are outside this proof.
    """
    try:
        if stop_at is not None and (type(stop_at) is not int or stop_at <= 0):
            return []
        if type(pid) is str:
            if not pid.isascii() or not pid.isdecimal():
                return []
            pid = int(pid)
        if type(pid) is not int or pid <= 0:
            return []
        if sys.platform == "win32":
            return _windows_ancestry(pid, stop_at)
        return _posix_ancestry(pid, stop_at)
    except Exception:  # noqa: BLE001 - all unknown inspection failures must refuse
        # OS/import/inspection failures never grant process ancestry authority.
        return []


def ancestor_pids(pid: int | str | None) -> list[int]:
    """Compatibility contract: owner PID followed by up to six live parents."""
    return [process.pid for process in process_ancestry(pid)]
