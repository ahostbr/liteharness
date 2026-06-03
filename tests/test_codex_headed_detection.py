"""Unit tests for Codex headed-mode UIAutomation detection + log backoff."""

from __future__ import annotations

import importlib
import json
import sys
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


def _import_notify():
    """Import the codex notify module fresh under a stable name."""
    module_name = "liteharness.cli_scripts.codex.liteharness_notify"
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


def _import_watcher_module():
    """Import the inbox watcher script as a module without running main()."""
    module_name = "_test_codex_inbox_watcher"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = Path(__file__).resolve().parents[1] / (
        "liteharness/cli_scripts/codex/liteharness_inbox_watcher.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IsTerminalPaneTests(unittest.TestCase):
    """`_is_terminal_pane` must accept all known TermControl class names."""

    def setUp(self) -> None:
        self.notify = _import_notify()

    def test_legacy_bare_termcontrol(self) -> None:
        self.assertTrue(
            self.notify._is_terminal_pane({"class_name": "TermControl"})
        )

    def test_full_qualified_termcontrol(self) -> None:
        # Modern Windows Terminal exposes the fully-qualified class name.
        self.assertTrue(
            self.notify._is_terminal_pane(
                {"class_name": "Microsoft.Terminal.TerminalControl.TermControl"}
            )
        )

    def test_has_text_pattern_fallback(self) -> None:
        # Detection scout sets has_text_pattern when the class matches
        # *TermControl* even if Python-side matching missed something new.
        self.assertTrue(
            self.notify._is_terminal_pane(
                {"class_name": "Some.Future.Variant", "has_text_pattern": True}
            )
        )

    def test_non_terminal_pane_rejected(self) -> None:
        self.assertFalse(
            self.notify._is_terminal_pane(
                {"class_name": "TabsContainer", "has_text_pattern": False}
            )
        )

    def test_missing_class_name_rejected(self) -> None:
        self.assertFalse(self.notify._is_terminal_pane({}))


class CaptureFocusedTargetTests(unittest.TestCase):
    """`capture_focused_target` should fall back to a non-focused term pane."""

    def setUp(self) -> None:
        self.notify = _import_notify()
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.target_path = Path(self.tmpdir.name) / "target.json"

        # Patch module-level paths and env so the function actually runs.
        self._patches = [
            mock.patch.object(self.notify, "STATE_ROOT", Path(self.tmpdir.name)),
            mock.patch.object(self.notify, "target_path", return_value=self.target_path),
            mock.patch.dict(
                "os.environ",
                {"WT_SESSION": "test-session"},
                clear=False,
            ),
        ]
        for patcher in self._patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        # LITESUITE_BRIDGE_TOKEN must be unset so the function does not bail.
        if "LITESUITE_BRIDGE_TOKEN" in self.notify.os.environ:
            self.addCleanup(
                self.notify.os.environ.__setitem__,
                "LITESUITE_BRIDGE_TOKEN",
                self.notify.os.environ["LITESUITE_BRIDGE_TOKEN"],
            )
            del self.notify.os.environ["LITESUITE_BRIDGE_TOKEN"]

    def test_focused_full_qualified_class_is_captured(self) -> None:
        windows = [
            {
                "handle": 11,
                "title": "Codex",
                "panes": [
                    {
                        "id": 0,
                        "title": "codex",
                        "class_name": "Microsoft.Terminal.TerminalControl.TermControl",
                        "focused": True,
                    }
                ],
            }
        ]
        with mock.patch.object(
            self.notify.terminal_automation, "list_panes", return_value=windows
        ):
            self.notify.capture_focused_target("codex-test")
        payload = json.loads(self.target_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["window_handle"], 11)
        self.assertEqual(payload["pane_id"], 0)
        self.assertEqual(payload["agent_id"], "codex-test")
        self.assertEqual(payload["captured_via"], "focused")

    def test_unfocused_terminal_used_as_fallback(self) -> None:
        # No focused TermControl, but an unfocused Codex terminal is visible —
        # the previous code required focus and therefore captured nothing.
        windows = [
            {
                "handle": 22,
                "title": "Windows Terminal",
                "panes": [
                    {
                        "id": 5,
                        "title": "Codex",
                        "class_name": "Microsoft.Terminal.TerminalControl.TermControl",
                        "focused": False,
                    }
                ],
            }
        ]
        with mock.patch.object(
            self.notify.terminal_automation, "list_panes", return_value=windows
        ):
            self.notify.capture_focused_target("codex-test")
        payload = json.loads(self.target_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["window_handle"], 22)
        self.assertEqual(payload["pane_id"], 5)
        self.assertEqual(payload["captured_via"], "fallback")

    def test_no_terminal_panes_writes_nothing(self) -> None:
        with mock.patch.object(
            self.notify.terminal_automation, "list_panes", return_value=[]
        ):
            self.notify.capture_focused_target("codex-test")
        self.assertFalse(self.target_path.exists())


class CodexAgentIdentityTests(unittest.TestCase):
    """Codex monitor scripts must support current UUID-style LiteHarness IDs."""

    def setUp(self) -> None:
        self.notify = _import_notify()
        self.watcher = _import_watcher_module()

    def test_notify_accepts_uuid_agent_id_from_env(self) -> None:
        agent_id = "019de095-299b-7130-bf08-d126dd2c4593"
        with mock.patch.dict("os.environ", {"LITEHARNESS_AGENT_ID": agent_id}, clear=False):
            self.assertEqual(self.notify.read_agent_id(), agent_id)

    def test_watcher_accepts_uuid_agent_id_from_env(self) -> None:
        agent_id = "019de095-299b-7130-bf08-d126dd2c4593"
        with mock.patch.dict("os.environ", {"LITEHARNESS_AGENT_ID": agent_id}, clear=False):
            self.assertEqual(self.watcher.read_agent_id(), agent_id)

    def test_notify_state_paths_are_agent_scoped(self) -> None:
        one = self.notify.state_path("019de095-299b-7130-bf08-d126dd2c4593", "target.json")
        two = self.notify.state_path("49254e88-ed75-497d-97e5-31a6cbccaaa6", "target.json")
        self.assertIn("019de095-299b-7130-bf08-d126dd2c4593", one.name)
        self.assertIn("49254e88-ed75-497d-97e5-31a6cbccaaa6", two.name)
        self.assertNotEqual(one, two)


class NoTargetBackoffTests(unittest.TestCase):
    """`_log_no_target` should suppress repeats with exponential backoff."""

    def setUp(self) -> None:
        self.watcher = _import_watcher_module()
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.log_path = Path(self.tmpdir.name) / "watcher.log"

        # Reset state for a clean test.
        self.watcher._no_target_state.update(
            {"next_log_at": 0.0, "delay": self.watcher.NO_TARGET_BACKOFF_BASE, "suppressed": 0}
        )
        self._patches = [
            mock.patch.object(self.watcher, "STATE_ROOT", Path(self.tmpdir.name)),
            mock.patch.object(self.watcher, "log_path", return_value=self.log_path),
        ]
        for patcher in self._patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _line_count(self) -> int:
        if not self.log_path.exists():
            return 0
        return sum(1 for line in self.log_path.read_text(encoding="utf-8").splitlines() if line)

    def test_first_call_writes_immediately(self) -> None:
        self.watcher._log_no_target("no headed target")
        self.assertEqual(self._line_count(), 1)

    def test_repeats_are_suppressed_until_backoff_window_elapses(self) -> None:
        with mock.patch.object(self.watcher.time, "time", return_value=1000.0):
            self.watcher._log_no_target("no headed target")  # writes
            self.watcher._log_no_target("no headed target")  # suppressed
            self.watcher._log_no_target("no headed target")  # suppressed
        self.assertEqual(self._line_count(), 1)
        # Once the window elapses, the next call writes a single rolled-up line.
        with mock.patch.object(self.watcher.time, "time", return_value=1010.0):
            self.watcher._log_no_target("no headed target")
        self.assertEqual(self._line_count(), 2)

    def test_delay_doubles_up_to_max(self) -> None:
        # Drive the backoff to the cap by repeatedly logging past each window.
        clock = [0.0]
        with mock.patch.object(self.watcher.time, "time", side_effect=lambda: clock[0]):
            for _ in range(20):
                self.watcher._log_no_target("no headed target")
                clock[0] += 1000.0  # always past the next_log_at
        self.assertEqual(
            self.watcher._no_target_state["delay"],
            self.watcher.NO_TARGET_BACKOFF_MAX,
        )

    def test_reset_drops_delay_and_clears_suppressed_counter(self) -> None:
        with mock.patch.object(self.watcher.time, "time", return_value=2000.0):
            self.watcher._log_no_target("no headed target")
            self.watcher._log_no_target("no headed target")  # suppressed
        self.watcher._reset_no_target_backoff()
        self.assertEqual(
            self.watcher._no_target_state["delay"],
            self.watcher.NO_TARGET_BACKOFF_BASE,
        )
        self.assertEqual(self.watcher._no_target_state["suppressed"], 0)


class LogRotationTests(unittest.TestCase):
    """`append_log` should rotate when the log exceeds LOG_MAX_BYTES."""

    def setUp(self) -> None:
        self.watcher = _import_watcher_module()
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.log_path = Path(self.tmpdir.name) / "watcher.log"
        self._patches = [
            mock.patch.object(self.watcher, "STATE_ROOT", Path(self.tmpdir.name)),
            mock.patch.object(self.watcher, "log_path", return_value=self.log_path),
            mock.patch.object(self.watcher, "LOG_MAX_BYTES", 256),
        ]
        for patcher in self._patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_rotation_creates_backup_and_truncates_active_log(self) -> None:
        self.log_path.write_text("x" * 300, encoding="utf-8")
        self.watcher.append_log("after rotation")
        backup = self.log_path.with_suffix(self.log_path.suffix + ".1")
        self.assertTrue(backup.exists())
        self.assertEqual(backup.stat().st_size, 300)
        self.assertIn("after rotation", self.log_path.read_text(encoding="utf-8"))

    def test_no_rotation_under_threshold(self) -> None:
        self.log_path.write_text("y" * 100, encoding="utf-8")
        self.watcher.append_log("small line")
        backup = self.log_path.with_suffix(self.log_path.suffix + ".1")
        self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
