import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from liteharness import codex_hooks


class CodexHooksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.error_log_path = Path(self.temp_dir.name) / "codex_hooks_error.log"
        self.original_error_log_path = codex_hooks.ERROR_LOG_PATH
        self.original_read_stdin = codex_hooks.hooks._read_hook_stdin
        self.original_apply_context = codex_hooks.hooks._apply_hook_context
        self.original_register_presence = codex_hooks.hooks.register_presence
        self.original_check_inbox = codex_hooks.hooks.check_inbox

        codex_hooks.ERROR_LOG_PATH = self.error_log_path
        self.applied_inputs: list[dict] = []
        self.hook_input = {
            "session_id": "codex-session",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "test prompt",
        }
        codex_hooks.hooks._read_hook_stdin = lambda: dict(self.hook_input)
        codex_hooks.hooks._apply_hook_context = lambda data: self.applied_inputs.append(data)

    def tearDown(self) -> None:
        codex_hooks.ERROR_LOG_PATH = self.original_error_log_path
        codex_hooks.hooks._read_hook_stdin = self.original_read_stdin
        codex_hooks.hooks._apply_hook_context = self.original_apply_context
        codex_hooks.hooks.register_presence = self.original_register_presence
        codex_hooks.hooks.check_inbox = self.original_check_inbox
        self.temp_dir.cleanup()

    def _run_main(self, *args: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = codex_hooks.main(list(args))
        return exit_code, stdout.getvalue()

    def test_session_start_register_wraps_plain_text_as_valid_json(self) -> None:
        codex_hooks.hooks.register_presence = lambda: print("[LITEHARNESS] registered")

        exit_code, output = self._run_main("session-start-register")

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("[LITEHARNESS] registered", payload["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.applied_inputs, [self.hook_input])

    def test_user_prompt_submit_check_wraps_inbox_text_as_valid_json(self) -> None:
        codex_hooks.hooks.check_inbox = lambda: print("[LITEHARNESS] 1 message(s) received")

        exit_code, output = self._run_main("user-prompt-submit-check")

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("1 message(s) received", payload["hookSpecificOutput"]["additionalContext"])

    def test_post_tool_use_check_emits_nothing_when_no_context_exists(self) -> None:
        codex_hooks.hooks.check_inbox = lambda: None

        exit_code, output = self._run_main("post-tool-use-check")

        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "")

    def test_adapter_emits_valid_warning_json_on_failure(self) -> None:
        def boom() -> None:
            raise RuntimeError("boom")

        codex_hooks.hooks.register_presence = boom

        exit_code, output = self._run_main("session-start-register")

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertIn("LiteHarness Codex hook adapter failed", payload["systemMessage"])
        self.assertTrue(self.error_log_path.exists())
        self.assertIn("RuntimeError('boom')", self.error_log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
