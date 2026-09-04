"""Codex inbox contract after retirement of automatic headed injection.

The former tests asserted focus capture/backoff for a path that could not deliver
in desktop tool sessions. Explicit terminal/desktop automation APIs are separate;
these tests assert the inbox entrypoints never call them.
"""
import importlib
import os
import subprocess
import sys
from unittest import mock

def test_notify_cannot_spawn_or_claim():
    notify = importlib.import_module("liteharness.cli_scripts.codex.liteharness_notify")
    with mock.patch("subprocess.Popen", side_effect=AssertionError("spawned")), mock.patch(
        "liteharness.inbox.claim", side_effect=AssertionError("claimed")
    ):
        assert notify.main() == 0

def test_legacy_watcher_uses_same_consumer():
    from liteharness.cli_scripts.codex import liteharness_inbox_watcher as alias
    from liteharness.cli_scripts.codex import liteharness_watcher_supervisor as source
    assert alias.main is source.main

def test_null_stdout_is_refused(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "liteharness.cli_scripts.codex.liteharness_watcher_supervisor",
         "--root", str(tmp_path), "--agent-id", "test-stdout"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "unattached" in result.stderr
    assert not (tmp_path / "codex_sessions").exists()

def test_identity_must_not_fall_back_to_another_session(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if k not in ("LITEHARNESS_AGENT_ID", "CODEX_THREAD_ID")}
    result = subprocess.run(
        [sys.executable, "-m", "liteharness.cli_scripts.codex.liteharness_watcher_supervisor",
         "--root", str(tmp_path)], env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "explicit valid" in result.stderr
