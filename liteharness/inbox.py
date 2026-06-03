"""
LiteHarness Inbox — Maildir-based inter-agent messaging.

Global inbox at ~/.liteharness/inbox/{new,cur,done}.
Atomic write via tmp + os.replace(). Atomic claim via os.replace().
One JSON file per message. TTL-based auto-cleanup.

Compatible with LiteSuite's GlobalInbox (TypeScript, fs.rename).
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

INBOX_ROOT = Path.home() / ".liteharness" / "inbox"
INBOX_NEW = INBOX_ROOT / "new"
INBOX_CUR = INBOX_ROOT / "cur"
INBOX_DONE = INBOX_ROOT / "done"
INBOX_TMP = INBOX_ROOT / "tmp"

DEFAULT_TTL_MINUTES = 60  # 1 hour


def ensure_dirs() -> None:
    """Create inbox directories if they don't exist."""
    for d in (INBOX_NEW, INBOX_CUR, INBOX_DONE, INBOX_TMP):
        d.mkdir(parents=True, exist_ok=True)


def make_filename(priority: str = "normal") -> str:
    """Generate a message filename: <timestamp>__<priority>__<uuid>.json"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:12]
    return f"{ts}__{priority}__{uid}.json"


def send(
    from_agent: str,
    to_agent: str,
    body: str,
    *,
    thread_id: Optional[str] = None,
    reply_to: Optional[str] = None,
    priority: str = "normal",
    msg_type: str = "notification",
    project: Optional[str] = None,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    cli: str = "unknown",
    model: str = "unknown",
    surface: Optional[str] = None,
    originator: Optional[str] = None,
) -> str:
    """
    Send a message to another agent's inbox.

    Atomic write: write to tmp/, then os.replace() into new/.
    Returns the message ID.
    """
    ensure_dirs()

    msg_id = str(uuid.uuid4())
    filename = make_filename(priority)

    message = {
        "id": msg_id,
        "from": from_agent,
        "to": to_agent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id,
        "reply_to": reply_to,
        "priority": priority,
        "type": msg_type,
        "body": body,
        "project": project,
        "ttl_minutes": ttl_minutes,
        "cli": cli,
        "model": model,
    }
    if surface:
        message["surface"] = surface
    if originator:
        message["originator"] = originator

    tmp_path = INBOX_TMP / filename
    new_path = INBOX_NEW / filename

    # Atomic write: tmp then rename
    tmp_path.write_text(json.dumps(message, indent=2), encoding="utf-8")
    os.replace(str(tmp_path), str(new_path))

    return msg_id


def poll(
    agent_id: str,
    *,
    project: Optional[str] = None,
    include_broadcast: bool = True,
) -> list[dict]:
    """
    Check inbox for messages addressed to this agent.

    Reads new/ directory, filters by to_agent.
    Does NOT claim — just peeks. Use claim() to take ownership.
    """
    ensure_dirs()
    messages = []

    for f in sorted(INBOX_NEW.iterdir()):
        if not f.suffix == ".json":
            continue
        try:
            msg = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        to = msg.get("to", "")
        is_match = to == agent_id
        if is_match or (include_broadcast and to == "broadcast"):
            # Filter by project if specified
            if project and msg.get("project") and msg["project"] != project:
                continue
            msg["_path"] = str(f)
            messages.append(msg)

    return messages


def claim(message: dict) -> bool:
    """
    Atomically claim a message: move from new/ to cur/.

    Returns True if claim succeeded (we won the race).
    Returns False if another agent already claimed it.
    """
    src = Path(message.get("_path", ""))
    if not src.exists():
        return False

    dst = INBOX_CUR / src.name
    try:
        os.replace(str(src), str(dst))
        return True
    except OSError:
        return False


def complete(message: dict, result: Optional[str] = None) -> bool:
    """
    Mark a claimed message as complete: move from cur/ to done/.

    Optionally attach a result to the message before moving.
    """
    src = INBOX_CUR / Path(message.get("_path", "")).name
    if not src.exists():
        return False

    if result:
        try:
            msg = json.loads(src.read_text(encoding="utf-8"))
            msg["result"] = result
            msg["completed_at"] = datetime.now(timezone.utc).isoformat()
            src.write_text(json.dumps(msg, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError):
            pass

    dst = INBOX_DONE / src.name
    try:
        os.replace(str(src), str(dst))
        return True
    except OSError:
        return False


def cleanup(max_age_minutes: Optional[int] = None) -> int:
    """
    Remove expired messages from all directories.

    Uses each message's ttl_minutes if max_age_minutes not specified.
    Returns count of removed messages.
    """
    ensure_dirs()
    removed = 0
    now = time.time()

    for directory in (INBOX_NEW, INBOX_CUR, INBOX_DONE):
        for f in directory.iterdir():
            if not f.suffix == ".json":
                continue
            try:
                msg = json.loads(f.read_text(encoding="utf-8"))
                ttl = max_age_minutes or msg.get("ttl_minutes", DEFAULT_TTL_MINUTES)
                ts = msg.get("timestamp", "")
                if ts:
                    msg_time = datetime.fromisoformat(ts).timestamp()
                    if now - msg_time > ttl * 60:
                        f.unlink()
                        removed += 1
            except (json.JSONDecodeError, OSError, ValueError):
                continue

    return removed


def read_and_claim(
    agent_id: str,
    *,
    project: Optional[str] = None,
) -> list[dict]:
    """
    Convenience: poll + claim all messages for this agent.

    Returns list of successfully claimed messages.
    """
    messages = poll(agent_id, project=project)
    claimed = []
    for msg in messages:
        if claim(msg):
            claimed.append(msg)
    return claimed
