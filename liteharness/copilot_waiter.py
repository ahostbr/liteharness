"""
LiteHarness Copilot waiter — one-shot inbox delivery for background task alerts.

This module is additive and intentionally separate from hooks.py. It is designed
for CLIs like Copilot that can surface background task completion, but do not
inject a long-running watcher process into the active conversation.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from . import config, inbox
from .hooks import _create_watcher


def _is_target_message(message: dict, agent_id: str) -> bool:
    """Return True when the inbox message is addressed to this agent."""
    to = str(message.get("to", ""))
    if not to or message.get("from") == agent_id:
        return False
    return to == agent_id or to == "broadcast"


def _load_message(path: Path) -> Optional[dict]:
    """Read a JSON inbox message, returning None on invalid content."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _claim_message(path: Path, message: dict) -> Optional[dict]:
    """
    Claim a new message or adopt an already-claimed one from cur/.

    Returns a message dict with _path set for inbox.complete(), or None if a
    claim race was lost.
    """
    message["_path"] = str(path)

    if path.parent == inbox.INBOX_NEW:
        if not inbox.claim(message):
            return None
        claimed_path = inbox.INBOX_CUR / path.name
        message["_path"] = str(claimed_path)
        return message

    if path.parent == inbox.INBOX_CUR:
        return message

    return None


def _scan_for_message(agent_id: str) -> Optional[dict]:
    """Find and claim the next message for this agent."""
    inbox.ensure_dirs()

    for directory in (inbox.INBOX_NEW, inbox.INBOX_CUR):
        if not directory.exists():
            continue

        for path in sorted(directory.iterdir()):
            if path.suffix != ".json":
                continue

            message = _load_message(path)
            if message is None or not _is_target_message(message, agent_id):
                continue

            claimed = _claim_message(path, message)
            if claimed is not None:
                return claimed

    return None


def wait_for_next_message(agent_id: str, timeout: Optional[float] = None) -> Optional[dict]:
    """
    Block until the next matching inbox message arrives, then claim it.

    Returns the claimed message dict, or None if timeout expires.
    """
    watcher = _create_watcher(str(inbox.INBOX_ROOT))
    deadline = None if timeout is None else time.monotonic() + timeout

    while True:
        message = _scan_for_message(agent_id)
        if message is not None:
            return message

        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            watcher.wait(timeout=min(5.0, remaining))
        else:
            watcher.wait(timeout=5.0)


def format_message_summary(message: dict, agent_id: str) -> str:
    """Format a delivered message as human-readable stdout."""
    sender = message.get("from", "unknown")
    priority = message.get("priority", "normal")
    msg_type = message.get("type", "notification")
    body = str(message.get("body", ""))
    thread = message.get("thread_id")
    project = message.get("project")

    prefix = "[URGENT] " if priority == "urgent" else ""

    lines = [f"[LITEHARNESS] {prefix}Message from {sender} ({msg_type}):"]
    lines.append(body)
    lines.append("TO REPLY (use this exact command — do NOT reply to your own ID):")
    lines.append(f'python -m liteharness.cli send {sender} "your reply" --from {agent_id}')
    if thread:
        lines.append(f"Thread: {thread}")
    if project:
        lines.append(f"Project: {project}")
    return "\n".join(lines)


def run_once(agent_id: str, timeout: Optional[float] = None) -> int:
    """Wait once, print a summary, mark the message done, and exit."""
    message = wait_for_next_message(agent_id, timeout=timeout)
    if message is None:
        print(
            f"[LITEHARNESS] No message received for agent {agent_id} before timeout.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(format_message_summary(message, agent_id), flush=True)
    inbox.complete(message, result="Delivered by liteharness.copilot_waiter")
    return 0


def main() -> None:
    """CLI entry point for the one-shot waiter."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Block until the next LiteHarness inbox message arrives, then print and exit."
    )
    parser.add_argument("--agent-id", help="Full agent ID to watch. Defaults to config.get_agent_id().")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional timeout in seconds before exiting with code 1.",
    )
    args = parser.parse_args()

    agent_id = args.agent_id or config.get_agent_id()
    raise SystemExit(run_once(agent_id, timeout=args.timeout))


if __name__ == "__main__":
    main()
