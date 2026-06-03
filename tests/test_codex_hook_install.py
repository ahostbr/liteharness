import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liteharness import cli


class CodexHookInstallTests(unittest.TestCase):
    def test_install_codex_hooks_replaces_legacy_liteharness_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            target = codex_dir / "hooks.json"
            target.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python -m liteharness.hooks register",
                                            "timeout": 5,
                                        },
                                        {
                                            "type": "command",
                                            "command": "python -m liteharness.hooks check",
                                            "timeout": 5,
                                        },
                                    ],
                                }
                            ],
                            "UserPromptSubmit": [
                                {
                                    "matcher": "",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python -m liteharness.hooks check",
                                            "timeout": 5,
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            config_src = root / "codex_hooks.json"
            config_src.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python -m liteharness.codex_hooks session-start-register",
                                            "timeout": 5,
                                        },
                                        {
                                            "type": "command",
                                            "command": "python -m liteharness.codex_hooks session-start-check",
                                            "timeout": 5,
                                        },
                                    ],
                                }
                            ],
                            "PostToolUse": [
                                {
                                    "matcher": "",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python -m liteharness.codex_hooks post-tool-use-check",
                                            "timeout": 5,
                                        }
                                    ],
                                }
                            ],
                            "UserPromptSubmit": [
                                {
                                    "matcher": "",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python -m liteharness.codex_hooks user-prompt-submit-check",
                                            "timeout": 5,
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            with mock.patch("liteharness.cli.Path.home", return_value=root):
                with mock.patch("liteharness.cli._get_hook_config_path", return_value=config_src):
                    self.assertTrue(cli._install_codex_hooks())

            installed = json.loads(target.read_text(encoding="utf-8"))
            session_start_commands = [
                hook["command"]
                for matcher in installed["hooks"]["SessionStart"]
                for hook in matcher.get("hooks", [])
            ]
            self.assertEqual(
                session_start_commands,
                [
                    "python -m liteharness.codex_hooks session-start-register",
                    "python -m liteharness.codex_hooks session-start-check",
                ],
            )
            self.assertEqual(
                installed["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"],
                "python -m liteharness.codex_hooks user-prompt-submit-check",
            )
            self.assertEqual(
                installed["hooks"]["PostToolUse"][0]["hooks"][0]["command"],
                "python -m liteharness.codex_hooks post-tool-use-check",
            )


if __name__ == "__main__":
    unittest.main()
