import io
import json
import re
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

    def test_check_commands_are_no_ops_and_cannot_be_influenced(self) -> None:
        """"-check" commands do nothing, DELIBERATELY, and nothing can make them do otherwise.

        Replaces test_user_prompt_submit_check_wraps_inbox_text_as_valid_json, which asserted
        behaviour that had been intentionally removed: inbox delivery moved to the standalone
        watcher (cli_scripts/codex/liteharness_inbox_watcher.py), and _run returns before it
        reads stdin or captures output. The old test had been failing at HEAD, and the answer
        was in the OTHER tree — LiteSuite's copy carried the comment explaining the no-op that
        this tree did not.

        It also replaces test_post_tool_use_check_emits_nothing_when_no_context_exists, which
        passed VACUOUSLY: it stubbed check_inbox and asserted empty output, but the early
        return means the stub can never run, so the assertion held no matter what the stub did.
        A test whose subject is unreachable proves nothing about the subject.

        So the stub here is made LOUD. If any "-check" command ever regains a path to the
        action, this fails — which the quiet stub could not detect.
        """
        for command in ("session-start-check", "post-tool-use-check", "user-prompt-submit-check"):
            with self.subTest(command=command):
                codex_hooks.hooks.check_inbox = lambda: print("SHOULD NEVER BE EMITTED")
                exit_code, output = self._run_main(command)
                self.assertEqual(exit_code, 0)
                self.assertEqual(output, "")

    def test_check_commands_are_not_wired_into_the_shipped_config(self) -> None:
        """The no-op only saves anything if the config stops invoking it.

        Measured 2026-08-10: LiteSuite's copy had 0 "-check" entries and THIS tree — the one
        `import liteharness` resolves to — still had 3, so every Codex agent paid a Python
        process launch per SessionStart, per tool call and per prompt to reach an early return.
        The optimisation's comment lived in the tree that does not run; its cost lived here.
        """
        config = (
            Path(codex_hooks.__file__).resolve().parent / "hooks_configs" / "codex_hooks.json"
        )
        raw = config.read_text(encoding="utf-8")
        json.loads(raw)  # a malformed config is its own failure
        wired = re.findall(r"codex_hooks\s+([a-z0-9-]*-check)\b", raw)
        self.assertEqual(
            wired, [], f"no-op -check hooks are still wired and cost a subprocess each: {wired}"
        )

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
