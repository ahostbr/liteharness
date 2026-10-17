"""Harmless real-process wake locks/owned sessions and interleaved read exports.

Children only import this checkout and acquire local scratch locks. No model,
CLI seat, real store, or real bridge is executed.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from liteharness import ask_seat

ENTRY = {"agent_id": "11111111-1111-4111-8111-111111111111", "name": "Fixture"}
REPO = Path(ask_seat.__file__).resolve().parents[1]


def _launch(directory, *, delayed=False, owned=False, gated=False):
    # Use the actual installed interpreter, not a Windows venv redirector whose
    # Popen pid may belong to a wrapper rather than the lock-holding process.
    executable = sys._base_executable if sys.platform == "win32" else sys.executable
    code = f'''
import os, sys
from pathlib import Path
sys.path.insert(0, {str(REPO)!r})
from liteharness import ask_seat
assert Path(ask_seat.__file__).resolve() == Path({str(REPO / 'liteharness/ask_seat.py')!r})
ask_seat.ask_dir = lambda: Path({str(directory)!r})
def forbidden(*a, **kw):
    raise AssertionError("model boundary forbidden")
ask_seat.subprocess.run = forbidden
original_open = os.open
if {delayed!r}:
    def scheduled_open(path, flags, *a, **kw):
        # Fires for both the old generation implementation and persistent inode.
        if Path(path).name in ({ENTRY['agent_id'] + '.lock.1'!r}, {ENTRY['agent_id'] + '.lock'!r}):
            print("PAUSED", flush=True)
            assert sys.stdin.readline().strip() == "continue"
        return original_open(path, flags, *a, **kw)
    os.open = scheduled_open
if {gated!r}:
    print("READY", flush=True)
    assert sys.stdin.readline().strip() == "start"
try:
    if {owned!r}:
        from liteharness.agent_store import AgentStore
        from liteharness.agent_ownership import AgentSession
        lock = AgentSession.acquire_existing(AgentStore({str(directory)!r}), agent_id={ENTRY['agent_id']!r})
    else:
        lock = ask_seat._WakeLock({ENTRY!r})
        lock.__enter__()
except ask_seat.AskRefused:
    print("REFUSED", flush=True)
else:
    print("HELD " + str(os.getpid()), flush=True)
    assert sys.stdin.readline().strip() == "release"
    lock.__exit__()
    print("RELEASED", flush=True)
'''
    return subprocess.Popen([executable, "-B", "-u", "-c", code], cwd=directory,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _line(process):
    with ThreadPoolExecutor() as pool:
        future = pool.submit(process.stdout.readline)
        try:
            return future.result(timeout=15).strip()
        except BaseException:
            process.kill()  # unblock reader before joining its thread
            raise


def _tell(process, message):
    process.stdin.write(message + "\n")
    process.stdin.flush()


def _finish(process, *, release=False):
    if release:
        _tell(process, "release")
        assert _line(process) == "RELEASED"
    output, errors = process.communicate(timeout=15)
    assert (process.returncode, output, errors) == (0, "", "")


def _cleanup(children):
    for process in children:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)


@pytest.mark.parametrize("trial", range(5))
def test_delayed_contender_cannot_pass_a_released_and_reacquired_holder(tmp_path, trial):
    # Exact ABA history: B has chosen its path but not opened it; A acquires and
    # releases; C acquires and stays held; only then does B's actual open run.
    # Seed the old dead generation so this regression also fails on e7b56a1.
    (tmp_path / (ENTRY["agent_id"] + ".lock.0")).write_text("99999999")
    children = []
    try:
        b = _launch(tmp_path, delayed=True)
        children.append(b)
        assert _line(b) == "PAUSED"
        a = _launch(tmp_path)
        children.append(a)
        assert _line(a) == f"HELD {a.pid}"
        _finish(a, release=True)
        c = _launch(tmp_path)
        children.append(c)
        assert _line(c) == f"HELD {c.pid}"
        stable = tmp_path / (ENTRY["agent_id"] + ".lock")
        inode = stable.stat().st_ino if stable.exists() else None
        _tell(b, "continue")
        assert _line(b) == "REFUSED"  # old implementation instead reports HELD
        _finish(b)
        assert c.poll() is None
        assert stable.stat().st_ino == inode
        # A further contender must still see C's ownership after B's refusal.
        d = _launch(tmp_path)
        children.append(d)
        assert _line(d) == "REFUSED"
        _finish(d)
        _finish(c, release=True)
        assert stable.stat().st_ino == inode
    finally:
        _cleanup(children)


@pytest.mark.parametrize("trial", range(5))
def test_two_real_contenders_for_an_abandoned_lock_have_one_winner(tmp_path, trial):
    path = tmp_path / (ENTRY["agent_id"] + ".lock")
    path.write_bytes(b"\0")  # An abandoned persistent file is not ownership.
    inode = path.stat().st_ino
    children = []
    try:
        for _ in range(2):
            children.append(_launch(tmp_path, gated=True))
        assert [_line(p) for p in children] == ["READY", "READY"]
        for process in children:
            _tell(process, "start")
        outcomes = [_line(p) for p in children]
        assert sum(row.startswith("HELD ") for row in outcomes) == 1
        assert outcomes.count("REFUSED") == 1
        assert path.stat().st_ino == inode
        for process, row in zip(children, outcomes):
            if row.startswith("HELD "):
                assert row == f"HELD {process.pid}"
                _finish(process, release=True)
            else:
                _finish(process)
        assert path.stat().st_ino == inode
    finally:
        _cleanup(children)


@pytest.mark.parametrize("trial", range(3))
def test_real_holder_contention_crash_reclaim_and_repeated_reuse(tmp_path, monkeypatch, trial):
    monkeypatch.setattr(ask_seat, "ask_dir", lambda: tmp_path)
    children = []
    try:
        holder = _launch(tmp_path)
        children.append(holder)
        assert _line(holder) == f"HELD {holder.pid}"
        contender = _launch(tmp_path)
        children.append(contender)
        assert _line(contender) == "REFUSED"
        _finish(contender)
        with pytest.raises(ask_seat.AskRefused):
            with ask_seat._WakeLock(ENTRY):
                pytest.fail("holder is live")
        stable = tmp_path / (ENTRY["agent_id"] + ".lock")
        inode = stable.stat().st_ino
        holder.kill()
        holder.communicate(timeout=15)
        assert holder.returncode != 0
        for _ in range(20):
            with ask_seat._WakeLock(ENTRY):
                assert stable.stat().st_ino == inode
            assert stable.stat().st_ino == inode
        successor = _launch(tmp_path)
        children.append(successor)
        assert _line(successor) == f"HELD {successor.pid}"
        _finish(successor, release=True)
    finally:
        _cleanup(children)


def test_eight_thread_contention_has_one_holder_and_no_retirement_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(ask_seat, "ask_dir", lambda: tmp_path)
    for _ in range(25):
        barrier = threading.Barrier(8)
        release = threading.Event()
        ready = threading.Condition()
        results = []

        def contend():
            barrier.wait(timeout=10)
            try:
                with ask_seat._WakeLock(ENTRY):
                    with ready:
                        results.append("held")
                        ready.notify_all()
                    assert release.wait(10)
            except ask_seat.AskRefused:
                with ready:
                    results.append("refused")
                    ready.notify_all()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(contend) for _ in range(8)]
            try:
                with ready:
                    assert ready.wait_for(lambda: len(results) == 8, timeout=10)
                assert sorted(results) == ["held"] + ["refused"] * 7
            finally:
                release.set()
            for future in futures:
                future.result(timeout=10)


def test_concurrent_exports_cannot_read_or_remove_another_invocations_output(tmp_path, monkeypatch):
    monkeypatch.setattr(ask_seat, "ask_dir", lambda: tmp_path)
    path = tmp_path / "convo" / "convo.jsonl"
    first_written, second_written, first_done = (threading.Event() for _ in range(3))
    outputs = []
    serial = threading.Lock()

    def export(convo, output):
        assert not output.exists()
        with serial:
            number = len(outputs)
            outputs.append(output)
        output.write_text(f"## Assistant\n\nanswer-{number}\n", encoding="utf-8")
        if number == 0:
            first_written.set()
            assert second_written.wait(10)
        else:
            second_written.set()
            assert first_done.wait(10)
            assert output.is_file()  # R1's cleanup cannot remove R2's resource

    def first():
        try:
            return ask_seat.read_litetui(path)
        finally:
            first_done.set()
    monkeypatch.setattr(ask_seat, "_export_conversation", export)
    with ThreadPoolExecutor(max_workers=2) as pool:
        r1 = pool.submit(first)
        assert first_written.wait(10)
        r2 = pool.submit(ask_seat.read_litetui, path)
        assert [m.text for m in r1.result(timeout=15)] == ["answer-0"]
        assert [m.text for m in r2.result(timeout=15)] == ["answer-1"]
    assert len(set(outputs)) == 2
    assert all(not p.exists() for p in outputs)
    assert list((tmp_path / "export").iterdir()) == []


@pytest.mark.parametrize("registry", ["missing", "stale"])
def test_owned_session_cross_process_readonly_probe(tmp_path, monkeypatch, registry):
    from liteharness import agent_names, cli, config, owned_launch
    home = tmp_path / ".agents" / ENTRY["name"]
    (home / "conversations").mkdir(parents=True)
    (home / "settings.json").write_text(json.dumps({
        "schema_version": 1, **ENTRY,
        "execution": {"backend": "codex", "model": "fixture", "thinking_level": "high"}}))
    harness = tmp_path / "harness"
    (harness / "agents").mkdir(parents=True)
    monkeypatch.setattr(config, "get_root", lambda: harness)
    monkeypatch.setattr(owned_launch, "data_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bridge_request", lambda *a, **kw: {"sessions": []})
    agent_names.record_name(ENTRY["name"], ENTRY["agent_id"],
                            "22222222-2222-4222-8222-222222222222", None, backend="codex")
    if registry == "stale":
        (harness / "agents" / (ENTRY["agent_id"] + ".json")).write_text(json.dumps({
            **ENTRY, "session_pid": 1, "exited_at": "2026-01-01", "cli": "litetui"}))
    children = []
    try:
        holder = _launch(tmp_path, owned=True)
        children.append(holder)
        assert _line(holder) == f"HELD {holder.pid}"
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in home.rglob("*") if p.is_file()}
        with pytest.raises(ask_seat.AskRefused, match="live: message Fixture by inbox instead"):
            ask_seat.resolve_seat(ENTRY["name"])
        assert before == {p: (p.stat().st_size, p.stat().st_mtime_ns)
                          for p in home.rglob("*") if p.is_file()}
        _finish(holder, release=True)
        assert ask_seat.resolve_seat(ENTRY["name"])["agent_id"] == ENTRY["agent_id"]
        assert before == {p: (p.stat().st_size, p.stat().st_mtime_ns)
                          for p in home.rglob("*") if p.is_file()}
    finally:
        _cleanup(children)
