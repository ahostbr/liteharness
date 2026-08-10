"""Codex-specific hook adapters.

Output contract, stated accurately because the previous wording ("always emit valid Codex
hook JSON") was false on half the branches and a docstring is a separate claim that nothing
checks:

    "-check" commands                          -> emit NOTHING (deliberate no-op)
    action raises                              -> valid JSON (systemMessage)
    action succeeds with output                -> valid JSON (hookSpecificOutput)
    action succeeds with empty output          -> emit NOTHING

The guarantee that actually holds, and the one callers can rely on, is narrower and stronger
than "always JSON": this module never emits MALFORMED JSON. It either writes a valid payload
or writes nothing at all. Silence is a legitimate result here, not a failure.
"""

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
    # "-check" hooks are intentional no-ops — inbox delivery is handled by the standalone
    # watcher (cli_scripts/codex/liteharness_inbox_watcher.py). Their entries have been
    # removed from codex_hooks.json to avoid spawning a wasted subprocess on every tool
    # call and every prompt.
    #
    # 🔴 That removal had only ever happened in the LiteSuite copy of this package. In THIS
    # tree — the one `import liteharness` actually resolves to — all three were still wired
    # (session-start-check, post-tool-use-check, user-prompt-submit-check), so every Codex
    # agent really was paying a Python process launch per SessionStart, per tool call and per
    # prompt to reach this early return and do nothing. Measured 2026-08-10: LiteSuite 0
    # wired, oss 3 wired. The comment explaining the optimisation lived in the tree that does
    # not run; the cost lived in the tree that does. Third instance of that divergence in 24h.
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
