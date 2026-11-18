"""T1337: a tree kill must never reach an older process that inherited a reused pid."""
import subprocess
import sys
import time

import psutil

from liteharness.proctree import Proc, kill_true_tree, true_descendants


def test_older_child_through_reused_pid_is_not_a_descendant():
    # The 2026-11-17 shape: seat 4232 -> conhost 27484 (new), while the PTY daemon
    # 16976 (started two days earlier) still names 27484 as its parent, and the
    # daemon's own seat 500 hangs under it.
    procs = [
        Proc(4232, 44704, 1000.0),
        Proc(35376, 4232, 1005.0),
        Proc(27484, 35376, 1005.5),
        Proc(16976, 27484, 10.0),   # older than its "parent": pid reuse
        Proc(500, 16976, 20.0),     # a seat under the daemon
    ]
    assert true_descendants(4232, procs) == [35376, 27484]


def test_ordinary_tree_is_complete_and_breadth_first():
    procs = [Proc(1, 0, 1.0), Proc(2, 1, 2.0), Proc(3, 1, 2.0), Proc(4, 2, 3.0)]
    assert true_descendants(1, procs) == [2, 3, 4]


def test_unknown_root_and_self_parent_are_safe():
    assert true_descendants(99, [Proc(1, 0, 1.0)]) == []
    assert true_descendants(0, [Proc(0, 0, 0.0), Proc(4, 0, 1.0)]) == [4]


def test_kill_true_tree_kills_a_real_child_and_grandchild():
    code = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        "time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not psutil.Process(parent.pid).children():
            time.sleep(0.1)
        grandchild = psutil.Process(parent.pid).children()[0].pid
        assert kill_true_tree(parent.pid) is True
        time.sleep(0.5)
        assert not psutil.pid_exists(parent.pid) or psutil.Process(parent.pid).status() == psutil.STATUS_ZOMBIE
        assert not psutil.pid_exists(grandchild)
    finally:
        if parent.poll() is None:
            parent.kill()
