"""Codex-specific hook adapters that always emit valid Codex hook JSON."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from . import config, hooks


ERROR_LOG_PATH = config.HARNESS_ROOT / "codex_hooks_error.log"


COMMANDS: dict[str, tuple[str, str]] = {
    "session-start-register": ("SessionStart", "register_presence"),
    "session-start-check": ("SessionStart", "check_inbox"),
    "post-tool-use-check": ("PostToolUse", "check_inbox"),
    "user-prompt-submit-check": ("UserPromptSubmit", "check_inbox"),
}


def _capture_stdout(action_name: str) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        getattr(hooks, action_name)()
    return buffer.getvalue().strip()


def _emit_payload(
    *,
    event_name: str,
    additional_context: str | None = None,
    system_message: str | None = None,
) -> None:
    payload: dict[str, object] = {}
    if system_message:
        payload["systemMessage"] = system_message
    if additional_context:
        payload["hookSpecificOutput"] = {
            "hookEventName": event_name,
            "additionalContext": additional_context,
        }
    if payload:
        sys.stdout.write(json.dumps(payload))
        sys.stdout.write("\n")


def _log_error(command_name: str, error: Exception) -> None:
    config.ensure_root()
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {command_name}: {error!r}\n")


def _run(command_name: str, event_name: str, action_name: str) -> int:
    if command_name.endswith("-check"):
        return 0

    hook_input = hooks._read_hook_stdin()
    if hook_input:
        hooks._apply_hook_context(hook_input)

    try:
        captured_output = _capture_stdout(action_name)
    except Exception as error:
        _log_error(command_name, error)
        _emit_payload(
            event_name=event_name,
            system_message=f"LiteHarness Codex hook adapter failed during {command_name}: {error}",
        )
        return 0

    if captured_output:
        _emit_payload(event_name=event_name, additional_context=captured_output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)

    event_name, action_name = COMMANDS[args.command]
    return _run(args.command, event_name, action_name)


if __name__ == "__main__":
    raise SystemExit(main())
