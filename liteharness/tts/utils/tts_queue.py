"""
TTS Queue Manager for LiteHarness

Provides file-based locking for managing concurrent TTS announcements.
Prevents overlapping audio when multiple hooks fire simultaneously.

Functions:
    acquire_tts_lock(agent_id, timeout) - Acquire exclusive TTS lock
    release_tts_lock(agent_id) - Release the TTS lock
    is_tts_locked() - Check if TTS is currently locked
    cleanup_stale_locks(max_age_seconds) - Remove stale locks
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Lock file in ~/.liteharness/ global state dir (portable, no hardcoded project paths)
_LOCK_DIR = Path.home() / ".liteharness" / "tts_queue"
_LOCK_FILE = _LOCK_DIR / "tts.lock"

# TTS should never hold a lock longer than this (seconds).
# Covers: smart summary (~10s) + audio gen (~5s) + playback (~60s) + buffer.
_MAX_LOCK_AGE = 120

# Global file handle for the lock
_lock_file_handle: Optional[object] = None


def _ensure_lock_dir() -> None:
    """Ensure the lock directory exists."""
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is still running. Safe on both Windows and Unix."""
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def _get_lock_age() -> Optional[float]:
    """Get the age of the current lock in seconds, or None if no lock."""
    if not _LOCK_FILE.exists():
        return None
    try:
        info = _read_lock_info()
        if info and "timestamp" in info:
            lock_time = datetime.fromisoformat(info["timestamp"])
            return (datetime.now() - lock_time).total_seconds()
        return time.time() - _LOCK_FILE.stat().st_mtime
    except Exception:
        return None


def _force_remove_lock() -> bool:
    """Force-remove the lock file. Returns True if removed."""
    try:
        _LOCK_FILE.unlink()
        return True
    except OSError:
        try:
            os.replace(str(_LOCK_FILE), str(_LOCK_FILE) + ".dead")
            Path(str(_LOCK_FILE) + ".dead").unlink(missing_ok=True)
            return True
        except OSError:
            return False


def _write_lock_info(agent_id: str) -> None:
    """Write lock metadata to the lock file."""
    lock_info = {
        "agent_id": agent_id,
        "timestamp": datetime.now().isoformat(),
        "pid": os.getpid(),
    }
    with open(_LOCK_FILE, "w") as f:
        json.dump(lock_info, f)


def _read_lock_info() -> Optional[dict]:
    """Read lock metadata from the lock file."""
    if not _LOCK_FILE.exists():
        return None
    try:
        with open(_LOCK_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return None
            return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return None


def acquire_tts_lock(agent_id: str, timeout: int = 30) -> bool:
    """
    Acquire exclusive TTS lock using file-based locking.

    Args:
        agent_id: Identifier for the agent acquiring the lock
        timeout: Maximum seconds to wait for lock (default 30)

    Returns:
        True if lock acquired, False if timeout reached
    """
    global _lock_file_handle

    _ensure_lock_dir()
    _cleanup_stale_lock_if_needed()

    start_time = time.time()
    retry_interval = 0.1
    max_retry_interval = 1.0

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            return False

        try:
            if sys.platform == "win32":
                lock_file_tmp = str(_LOCK_FILE) + f".{os.getpid()}"
                try:
                    fd = os.open(lock_file_tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                    lock_info = json.dumps(
                        {
                            "agent_id": agent_id,
                            "timestamp": datetime.now().isoformat(),
                            "pid": os.getpid(),
                        }
                    ).encode()
                    os.write(fd, lock_info)
                    os.close(fd)
                    try:
                        os.rename(lock_file_tmp, str(_LOCK_FILE))
                        _lock_file_handle = str(_LOCK_FILE)
                        return True
                    except OSError:
                        try:
                            info = _read_lock_info()
                            holder_pid = info.get("pid") if info else None
                            lock_age = _get_lock_age()
                            if lock_age is not None and lock_age > _MAX_LOCK_AGE:
                                os.replace(lock_file_tmp, str(_LOCK_FILE))
                                _lock_file_handle = str(_LOCK_FILE)
                                return True
                            if holder_pid and not _is_pid_alive(holder_pid):
                                os.replace(lock_file_tmp, str(_LOCK_FILE))
                                _lock_file_handle = str(_LOCK_FILE)
                                return True
                            os.unlink(lock_file_tmp)
                        except Exception:
                            try:
                                os.unlink(lock_file_tmp)
                            except OSError:
                                pass
                except OSError:
                    pass
            else:
                import fcntl

                fd = os.open(str(_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    _lock_file_handle = fd
                    _write_lock_info(agent_id)
                    return True
                except (OSError, BlockingIOError):
                    os.close(fd)
        except OSError:
            pass

        time.sleep(retry_interval)
        retry_interval = min(retry_interval * 1.5, max_retry_interval)


def release_tts_lock(agent_id: str) -> None:
    """Release the TTS lock."""
    global _lock_file_handle

    if _lock_file_handle is None:
        return

    try:
        if sys.platform == "win32":
            try:
                os.unlink(_lock_file_handle)
            except OSError:
                pass
        else:
            import fcntl

            fcntl.flock(_lock_file_handle, fcntl.LOCK_UN)
            os.close(_lock_file_handle)
    except OSError:
        pass
    finally:
        _lock_file_handle = None


def is_tts_locked() -> bool:
    """Check if TTS is currently locked by another process."""
    _ensure_lock_dir()

    if not _LOCK_FILE.exists():
        return False

    lock_age = _get_lock_age()
    if lock_age is not None and lock_age > _MAX_LOCK_AGE:
        _force_remove_lock()
        return False

    try:
        if sys.platform == "win32":
            info = _read_lock_info()
            holder_pid = info.get("pid") if info else None
            if holder_pid and _is_pid_alive(holder_pid):
                return True
            _force_remove_lock()
            return False
        else:
            import fcntl

            fd = os.open(str(_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                return False
            except (OSError, BlockingIOError):
                os.close(fd)
                return True
    except OSError:
        return False


def _cleanup_stale_lock_if_needed() -> None:
    """Quick check: if lock exists and is stale (dead PID or too old), remove it."""
    if not _LOCK_FILE.exists():
        return

    lock_age = _get_lock_age()
    if lock_age is not None and lock_age > _MAX_LOCK_AGE:
        _force_remove_lock()
        return

    info = _read_lock_info()
    if info and "pid" in info:
        if not _is_pid_alive(info["pid"]):
            _force_remove_lock()


def cleanup_stale_locks(max_age_seconds: int = 60) -> None:
    """Remove locks older than max age."""
    if not _LOCK_FILE.exists():
        return

    try:
        lock_age = _get_lock_age()
        if lock_age is None:
            return
        if lock_age > max_age_seconds:
            info = _read_lock_info()
            if info and "pid" in info:
                if _is_pid_alive(info["pid"]):
                    return
            _force_remove_lock()
    except OSError:
        pass


def get_lock_info() -> Optional[dict]:
    """Get information about the current lock holder."""
    return _read_lock_info()
