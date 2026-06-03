#!/usr/bin/env python3
"""Watch LiteHarness inbox messages and inject them into a Kilo CLI pane."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INTERVAL = 2.0
HARNESS_ROOT = Path.home() / ".liteharness"


def slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Log:
    def __init__(self, root: Path, agent: str) -> None:
        self.path = root / "logs" / f"kilo_watcher_{slug(agent)}.log"

    def write(self, message: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    data["_path"] = str(path)
    return data


def ensure_maildir(path: Path) -> None:
    for name in ("new", "cur", "done", "tmp"):
        (path / name).mkdir(parents=True, exist_ok=True)


def inbox_roots(root: Path, agent: str) -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []

    global_root = root / "inbox"
    ensure_maildir(global_root)
    roots.append((global_root, "global"))

    mailbox_root = root / "mailboxes" / agent
    ensure_maildir(mailbox_root)
    roots.append((mailbox_root, "mailbox"))

    return roots


def addressed_to(message: dict[str, Any], agent: str, mode: str) -> bool:
    if mode == "mailbox":
        to = str(message.get("to") or "")
        return not to or to == agent or to == "broadcast"
    to = str(message.get("to") or "")
    return to == agent or to == "broadcast"


def poll_messages(root: Path, agent: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for inbox, mode in inbox_roots(root, agent):
        new_dir = inbox / "new"
        for path in sorted(new_dir.glob("*.json")):
            message = load_json(path)
            if not message:
                continue
            if addressed_to(message, agent, mode):
                message["_maildir"] = str(inbox)
                messages.append(message)
    return messages


def move_message(message: dict[str, Any], directory: str) -> Path | None:
    src = Path(str(message.get("_path") or ""))
    maildir = Path(str(message.get("_maildir") or src.parent.parent))
    if not src.exists():
        return None
    dst_dir = maildir / directory
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    try:
        os.replace(str(src), str(dst))
    except OSError:
        return None
    message["_path"] = str(dst)
    return dst


def claim_message(message: dict[str, Any]) -> bool:
    return move_message(message, "cur") is not None


def return_to_new(message: dict[str, Any]) -> bool:
    return move_message(message, "new") is not None


def complete_message(message: dict[str, Any], result: str) -> bool:
    annotate_message(message, result)
    return move_message(message, "done") is not None


def annotate_message(message: dict[str, Any], result: str) -> None:
    path = Path(str(message.get("_path") or ""))
    if not path.exists():
        return
    current = load_json(path) or {}
    current["result"] = result
    current["delivered_by"] = "kilo_watcher"
    current["delivered_at"] = now_iso()
    for key in ("_path", "_maildir"):
        current.pop(key, None)
    try:
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    except OSError:
        return


def import_terminal(log: Log):
    try:
        from liteharness import terminal_automation
    except Exception as exc:
        log.write(f"liteharness terminal_automation unavailable: {exc!r}")
        return None
    return terminal_automation


def normalize_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def find_target(args: argparse.Namespace, agent: str, log: Log) -> tuple[int, int] | None:
    if args.window_handle is not None and args.pane_id is not None:
        return (args.window_handle, args.pane_id)

    terminal = import_terminal(log)
    if not terminal:
        return None

    markers = [agent, *args.marker]
    markers = [marker for marker in markers if marker]
    if markers:
        try:
            match = terminal.find_pane_by_buffer_markers(markers)
        except Exception as exc:
            log.write(f"buffer marker target lookup failed: {exc!r}")
            match = None
        if isinstance(match, dict):
            handle = match.get("window_handle")
            pane = match.get("pane_id")
            if isinstance(handle, int) and isinstance(pane, int):
                return (handle, pane)

    try:
        windows = terminal.list_panes()
    except Exception as exc:
        log.write(f"Windows Terminal pane list failed: {exc!r}")
        return None

    cwd = Path.cwd().name.lower()
    best: tuple[int, int, int] | None = None
    for window in normalize_items(windows):
        handle = window.get("handle")
        if not isinstance(handle, int):
            continue
        text = " ".join(
            [
                str(window.get("title") or ""),
                " ".join(str(shell.get("cmdline") or "") for shell in normalize_items(window.get("shells"))),
            ],
        ).lower()
        window_score = 0
        for token, score in (("kilo", 8), ("opencode", 5), (cwd, 4)):
            if token and token in text:
                window_score += score
        for pane in normalize_items(window.get("panes")):
            pane_id = pane.get("id")
            if not isinstance(pane_id, int):
                continue
            pane_text = str(pane.get("title") or "").lower()
            score = window_score
            if "kilo" in pane_text:
                score += 8
            if pane.get("focused"):
                score += 3
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, handle, pane_id)

    if not best:
        return None
    return (best[1], best[2])


def format_prompt(message: dict[str, Any], agent: str) -> str:
    sender = str(message.get("from") or "unknown")
    msg_type = str(message.get("type") or "notification")
    body = str(message.get("body") or "").strip()
    project = message.get("project")
    thread = message.get("thread_id")
    lines = [
        "[LiteHarness inbox message auto-injected into Kilo]",
        f"From: {sender}",
        f"Type: {msg_type}",
    ]
    if project:
        lines.append(f"Project: {project}")
    if thread:
        lines.append(f"Thread: {thread}")
    lines.extend(
        [
            "Body:",
            body,
            "",
            "If a reply is needed, send it through LiteHarness inbox:",
            f"python -m liteharness.cli send {sender} \"your reply\" --from {agent}",
        ],
    )
    return "\n".join(lines)


def try_pty(agent: str, prompt: str, message: dict[str, Any], log: Log) -> bool:
    terminal = import_terminal(log)
    if not terminal:
        return False
    try:
        return bool(
            terminal.try_pty_inject(
                agent,
                prompt,
                log_fn=log.write,
                message_id=str(message.get("id") or ""),
                sender=str(message.get("from") or ""),
            ),
        )
    except Exception as exc:
        log.write(f"PTY injection failed: {exc!r}")
        return False


def try_terminal(args: argparse.Namespace, agent: str, prompt: str, log: Log) -> bool:
    target = find_target(args, agent, log)
    if not target:
        log.write("no Kilo terminal target found")
        return False

    terminal = import_terminal(log)
    if not terminal:
        return False
    try:
        return bool(terminal.send_input(target[0], target[1], prompt, auto_enter=True))
    except Exception as exc:
        log.write(f"terminal injection failed for {target[0]}:{target[1]}: {exc!r}")
        return False


def deliver(message: dict[str, Any], args: argparse.Namespace, agent: str, log: Log) -> bool:
    prompt = format_prompt(message, agent)
    if try_pty(agent, prompt, message, log):
        return True
    return try_terminal(args, agent, prompt, log)


def process_once(args: argparse.Namespace, log: Log) -> int:
    delivered = 0
    for message in poll_messages(args.root, args.agent_id):
        if str(message.get("from") or "") == args.agent_id:
            continue
        if not claim_message(message):
            continue

        if deliver(message, args, args.agent_id, log):
            result = "auto-injected-into-kilo"
            if args.complete:
                complete_message(message, result)
            else:
                annotate_message(message, result)
            delivered += 1
            log.write(f"delivered message {message.get('id') or Path(str(message.get('_path'))).name}")
            continue

        if return_to_new(message):
            log.write(f"delivery failed; returned message {message.get('id') or ''} to new")
        else:
            log.write(f"delivery failed; could not return message {message.get('id') or ''} to new")
    return delivered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", default=os.environ.get("LITEHARNESS_AGENT_ID", "").strip())
    parser.add_argument("--root", type=Path, default=HARNESS_ROOT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--complete", action="store_true", help="Move delivered messages from cur/ to done/.")
    parser.add_argument("--window-handle", type=int)
    parser.add_argument("--pane-id", type=int)
    parser.add_argument("--marker", action="append", default=[], help="Extra terminal buffer marker for target lookup.")
    return parser.parse_args()


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if not args.agent_id:
        print("Missing --agent-id or LITEHARNESS_AGENT_ID.", file=sys.stderr)
        return 2

    log = Log(args.root, args.agent_id)
    log.write(f"watching LiteHarness inbox for Kilo agent {args.agent_id}")

    while True:
        try:
            delivered = process_once(args, log)
            if delivered:
                print(f"[kilo_watcher] delivered {delivered} LiteHarness message(s).", flush=True)
        except KeyboardInterrupt:
            log.write("watcher stopped by keyboard interrupt")
            return 130
        except Exception as exc:
            log.write(f"watch loop failed: {exc!r}")

        if args.once:
            return 0
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
