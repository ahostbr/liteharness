import tempfile
import unittest
from pathlib import Path

from liteharness import session_manager


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_session_root = session_manager.SESSION_ROOT
        self.original_config_path = session_manager.CONFIG_PATH
        self.original_snapshots_dir = session_manager.SNAPSHOTS_DIR

        root = Path(self.temp_dir.name)
        session_manager.SESSION_ROOT = root
        session_manager.CONFIG_PATH = root / "config.json"
        session_manager.SNAPSHOTS_DIR = root / "snapshots"

    def tearDown(self) -> None:
        session_manager.SESSION_ROOT = self.original_session_root
        session_manager.CONFIG_PATH = self.original_config_path
        session_manager.SNAPSHOTS_DIR = self.original_snapshots_dir
        self.temp_dir.cleanup()

    def test_legacy_snapshot_normalizes_to_claude(self):
        snapshot = {
            "name": "legacy",
            "saved_at": "2026-04-24T00:00:00",
            "sessions": [{
                "session_id": "abc123",
                "cwd": "C:\\Projects",
                "name": "old",
                "flags": ["--model", "opus"],
                "terminal_pid": 42,
            }],
        }

        normalized = session_manager._normalize_snapshot(snapshot)

        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(normalized["default_layout"], "windows")
        self.assertEqual(normalized["sessions"][0]["cli"], "claude")
        self.assertEqual(normalized["sessions"][0]["launch_args"], ["--model", "opus"])
        self.assertEqual(normalized["sessions"][0]["terminal_group"], "42")

    def test_extract_launch_args_preserves_values_and_skips_resume(self):
        cmdline = [
            "claude.exe",
            "--model", "claude-opus-4-8",
            "--permission-mode", "bypassPermissions",
            "--resume", "session-id",
            "--not-safe", "ignored",
        ]

        args = session_manager._extract_launch_args(cmdline, "claude")

        self.assertEqual(args, [
            "--model", "claude-opus-4-8",
            "--permission-mode", "bypassPermissions",
        ])

    def test_legacy_value_flags_without_values_are_dropped(self):
        args = session_manager._sanitize_launch_args(
            ["--model", "--permission-mode", "--dangerously-skip-permissions"],
            "claude",
        )

        self.assertEqual(args, ["--dangerously-skip-permissions"])

    def test_agent_command_matrix(self):
        base = {
            "session_id": "sid",
            "cwd": "C:\\Projects",
            "name": "",
            "launch_args": ["--model", "m"],
        }

        self.assertEqual(
            session_manager._agent_command({**base, "cli": "claude"}),
            ["claude", "--resume", "sid", "--model", "m"],
        )
        self.assertEqual(
            session_manager._agent_command({**base, "cli": "codex"}),
            ["codex", "--model", "m", "resume", "sid"],
        )
        self.assertEqual(
            session_manager._agent_command({**base, "cli": "copilot"}),
            ["copilot", "--resume=sid", "--model", "m"],
        )

    def test_windows_layout_builds_one_command_per_session(self):
        sessions = [
            self._session("claude", "a", "one"),
            self._session("codex", "b", "two"),
        ]

        commands = session_manager.build_restore_commands(sessions, "windows")

        self.assertEqual(len(commands), 2)
        self.assertTrue(all(cmd[:3] == ["wt.exe", "-w", "new"] for cmd in commands))
        self.assertEqual(commands[0][3], "new-tab")
        self.assertEqual(commands[1][3], "new-tab")

    def test_tabs_layout_groups_by_terminal_group(self):
        sessions = [
            self._session("claude", "a", "one", group="left"),
            self._session("codex", "b", "two", group="left"),
            self._session("copilot", "c", "three", group="right"),
        ]

        commands = session_manager.build_restore_commands(sessions, "tabs")

        self.assertEqual(len(commands), 2)
        self.assertIn(";", commands[0])
        self.assertNotIn("split-pane", commands[0])

    def test_panes_layout_uses_split_pane(self):
        sessions = [
            self._session("claude", "a", "one"),
            self._session("codex", "b", "two"),
            self._session("copilot", "c", "three"),
        ]

        commands = session_manager.build_restore_commands(sessions, "panes")

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].count("split-pane"), 2)

    def _session(self, cli, session_id, name, group="g"):
        return {
            "cli": cli,
            "session_id": session_id,
            "cwd": "C:\\Projects",
            "name": name,
            "model": "",
            "launch_args": [],
            "terminal_pid": 1,
            "terminal_group": group,
        }


if __name__ == "__main__":
    unittest.main()
