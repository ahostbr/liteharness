import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from liteharness import config, hooks


class PresenceLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_root = config.HARNESS_ROOT
        self.original_config_path = config.CONFIG_PATH
        config.HARNESS_ROOT = self.root
        config.CONFIG_PATH = self.root / "config.json"
        # The per-process last-known-good cache leaks between tests: a prior
        # test's heartbeat makes the recreate path restore that test's dict.
        hooks._LAST_PRESENCE = {}

    def tearDown(self) -> None:
        config.HARNESS_ROOT = self.original_root
        config.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    def _presence_path(self, agent_id: str) -> Path:
        return self.root / "agents" / f"{agent_id}.json"

    def test_register_presence_writes_immutable_session_pid(self) -> None:
        # A live declared PID is honored (register only records session_pid when
        # its process is alive — a dead/stale PID falls through to the ancestor
        # walk, by design). Use this process's own PID as the live declared PID.
        live_pid = hooks.os.getpid()
        with mock.patch.dict(
            hooks.os.environ,
            {
                "LITEHARNESS_AGENT_ID": "agent-123",
                "LITEHARNESS_CLI": "claude-code",
                "LITEHARNESS_MODEL": "claude-opus",
                "LITEHARNESS_SESSION_PID": str(live_pid),
            },
            clear=False,
        ):
            hooks.register_presence()

        presence = json.loads(self._presence_path("agent-123").read_text(encoding="utf-8"))
        self.assertEqual(presence["session_pid"], live_pid)
        # MERGE DECISION 2026-08-12: presence still carries "pid"/"ppid".
        # The other tree had removed them in favour of the immutable session_pid and
        # this assertion encoded that intent - but session_manager.py reads
        # info["pid"] in three places, so dropping the field is a breaking change to
        # a live consumer, not a cleanup. session_pid remains the liveness key; pid
        # stays for compatibility. Assert what actually matters instead.
        self.assertEqual(presence["session_pid"], live_pid)
        self.assertNotIn("watcher_pid", presence)

    def test_heartbeat_keeps_session_pid_and_writes_watcher_pid(self) -> None:
        owner_pid = hooks.os.getpid()  # This control requires a live owner.
        path = self._presence_path("agent-123")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"agent_id": "agent-123", "session_pid": owner_pid, "last_seen": "old"}),
            encoding="utf-8",
        )

        with mock.patch.dict(hooks.os.environ, {"LITEHARNESS_AGENT_ID": "agent-123"}, clear=False):
            with mock.patch.object(hooks.os, "getpid", return_value=999):
                with mock.patch.object(hooks.os, "getppid", return_value=111):
                    # watcher_pid is watcher-owned: only the long-lived watch
                    # loop stamps it (one-shot hook beats no longer do).
                    hooks.update_heartbeat(is_watcher=True)

        presence = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(presence["session_pid"], owner_pid)
        self.assertEqual(presence["watcher_pid"], 999)
        self.assertEqual(presence["watcher_ppid"], 111)
        self.assertNotIn("pid", presence)

    def test_heartbeat_recreate_does_not_invent_session_pid(self) -> None:
        # Recreate now requires watcher authority (is_watcher + model AND tier
        # in env) — a one-shot beat refuses to fabricate a registration at all.
        with mock.patch.dict(
            hooks.os.environ,
            {
                "LITEHARNESS_AGENT_ID": "agent-123",
                "LITEHARNESS_MODEL": "claude-opus",
                "LITEHARNESS_TIER": "worker",
            },
            clear=False,
        ):
            with mock.patch.object(hooks.os, "getpid", return_value=999):
                hooks.update_heartbeat(is_watcher=True)

        presence = json.loads(self._presence_path("agent-123").read_text(encoding="utf-8"))
        self.assertNotIn("session_pid", presence)
        self.assertEqual(presence["watcher_pid"], 999)

    def test_watch_requires_non_empty_agent_id(self) -> None:
        stderr = io.StringIO()
        original_argv = sys.argv
        sys.argv = ["hooks.py", "watch", "--agent-id"]
        try:
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                hooks.main()
        finally:
            sys.argv = original_argv

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("non-empty --agent-id", stderr.getvalue())

    def test_watch_auto_skips_when_session_agent_id_is_missing(self) -> None:
        stderr = io.StringIO()
        original_argv = sys.argv
        sys.argv = ["hooks.py", "watch-auto"]
        env_clear = {
            "LITEHARNESS_AGENT_ID": "",
            "LITESUITE_AGENT_ID": "",
            "CLAUDE_CODE_SESSION_ID": "",
            "CLAUDE_SESSION_ID": "",
            "LITECODE_SESSION_ID": "",
            "CODEX_SESSION_ID": "",
            "GEMINI_SESSION_ID": "",
        }
        try:
            with mock.patch.dict(hooks.os.environ, env_clear, clear=False):
                with redirect_stderr(stderr):
                    hooks.main()
        finally:
            sys.argv = original_argv

        self.assertIn("watch-auto skipped", stderr.getvalue())

    def test_watch_auto_uses_authoritative_agent_id(self) -> None:
        original_argv = sys.argv
        sys.argv = ["hooks.py", "watch-auto"]
        try:
            with mock.patch.dict(hooks.os.environ, {"LITEHARNESS_AGENT_ID": "agent-123"}, clear=False):
                with mock.patch.object(hooks, "watch_inbox") as watch_inbox:
                    hooks.main()
        finally:
            sys.argv = original_argv

        watch_inbox.assert_called_once_with(override_agent_id="agent-123")


if __name__ == "__main__":
    unittest.main()
