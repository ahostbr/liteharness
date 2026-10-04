"""Opt-in Claude mod maildir adapter; accepted delivery is not task completion.

Uses the existing inbox claim/complete operations. A bounded, process-identity
lease keeps legacy Claude consumers off native-owned mail. No LiteTUI ownership.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

import psutil

from . import config, inbox

LEASE_SECONDS = 20
MAX_MESSAGES = 8
MAX_BODY = 24000
MAX_BATCH = 48000
MAX_FILE = 256000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_RECEIPT = re.compile(r"[A-Za-z0-9_.-]+\.json\Z")


def valid_id(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _path(agent_id: str) -> Path:
    if not valid_id(agent_id):
        raise ValueError("Invalid agent identity")
    return config.get_root() / "mods" / "inbox" / (agent_id + ".json")


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _owner(agent_id: str) -> tuple[int, float]:
    row = _read(config.get_root() / "agents" / (agent_id + ".json"))
    if row.get("agent_id") != agent_id or row.get("cli") != "claude-code" or row.get("exited_at"):
        raise ValueError("Native inbox requires a registered live Claude seat")
    pid, started = row.get("session_pid"), row.get("session_process_started_at")
    if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or
            not isinstance(started, (int, float)) or isinstance(started, bool) or
            not math.isfinite(started)):
        raise ValueError("Claude process identity unavailable")
    process = psutil.Process(pid)
    if abs(process.create_time() * 1000 - started) > 1:
        raise ValueError("Claude process identity changed")
    return pid, started


def _prove_caller(pid: int) -> None:
    if pid not in {p.pid for p in psutil.Process().parents()}:
        raise ValueError("Adapter is not a child of the registered Claude process")


def native_owner_active(agent_id: str) -> bool:
    """Only a fresh lease on the same registered Claude process may suppress mail."""
    try:
        row = _read(_path(agent_id))
        now = time.time()
        if not (row.get("schemaVersion") == 1 and row.get("agentId") == agent_id and
                valid_id(row.get("sessionId")) and valid_id(row.get("owner")) and
                0 <= now - row["renewedAt"] < LEASE_SECONDS and
                now < row["expiresAt"] <= row["renewedAt"] + LEASE_SECONDS):
            return False
        pid, started = _owner(agent_id)
        return row.get("pid") == pid and row.get("processStartedAt") == started
    except (ValueError, TypeError, KeyError, OSError, psutil.Error):
        return False


def native_recovery_pending(agent_id: str) -> bool:
    """A stale native receipt in cur must not be stranded behind a new-only watcher."""
    if native_owner_active(agent_id):
        return False
    try:
        state = _read(_path(agent_id))
        if state.get("schemaVersion") != 1 or state.get("agentId") != agent_id:
            return False
        return any(isinstance(name, str) and _RECEIPT.fullmatch(name) and
                   _message(inbox.INBOX_CUR / name, agent_id) is not None
                   for name in state.get("claims", []))
    except (OSError, TypeError, ValueError):
        return False


@contextmanager
def _locked(path: Path):
    # A busy or crashed adapter fails visibly, never guesses ownership. This
    # short-lived OS lock is released even if Python terminates unexpectedly.
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.with_suffix(".lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def _length(text: str) -> int:
    """Match JavaScript string.length (UTF-16 units), including lone surrogates."""
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _message(path: Path, agent_id: str) -> dict | None:
    try:
        if path.is_symlink() or not _RECEIPT.fullmatch(path.name) or path.stat().st_size > MAX_FILE:
            return None
    except OSError:
        return None
    msg = _read(path)
    payload = msg.get("payload")
    body = msg.get("body")
    if not isinstance(body, str) and isinstance(payload, dict):
        body = payload.get("text", payload.get("body"))
    if (not valid_id(msg.get("id")) or not valid_id(msg.get("from")) or
            msg.get("from") == agent_id or msg.get("to") not in (agent_id, "broadcast") or
            not isinstance(body, str) or _length(body) > MAX_BODY):
        return None
    return {"id": msg["id"], "from": msg["from"], "to": msg["to"], "body": body,
            "type": str(msg.get("type", "notification"))[:128],
            "priority": "urgent" if msg.get("priority") == "urgent" else "normal",
            "receipt": path.name}


def operate(action: str, agent_id: str, session_id: str, owner: str,
            receipts: list[str] | None = None) -> dict:
    if not all(valid_id(value) for value in (agent_id, session_id, owner)):
        raise ValueError("Invalid inbox identity")
    pid, started = _owner(agent_id)
    _prove_caller(pid)
    path = _path(agent_id)
    with _locked(path):
        state = _read(path)
        active = native_owner_active(agent_id)
        same = state.get("owner") == owner and state.get("sessionId") == session_id
        if active and not same:
            raise ValueError("Native inbox already has a live owner")
        claims = state.get("claims", [])
        claims = [name for name in claims if isinstance(name, str) and _RECEIPT.fullmatch(name)]
        result = {"schemaVersion": 1, "agentId": agent_id, "sessionId": session_id,
                  "messages": [], "blocked": 0}
        if action in ("ack", "release"):
            if not same:
                raise ValueError("Inbox receipt owner changed")
            if action == "ack":
                if not active:
                    raise ValueError("Inbox receipt owner expired")
                for name in receipts or []:
                    if name not in claims:
                        raise ValueError("Receipt not owned by this inbox")
                for name in receipts or []:
                    message = _message(inbox.INBOX_CUR / name, agent_id)
                    if message is None or not inbox.complete({"_path": name}):
                        raise ValueError("Inbox acknowledgement failed")
                    claims.remove(name)
            state.update(claims=claims, expiresAt=0 if action == "release" else state["expiresAt"])
            config.atomic_write_json(path, state)
            return result
        if action != "poll":
            raise ValueError("Unknown inbox operation")
        now = time.time()
        state = {"schemaVersion": 1, "agentId": agent_id, "sessionId": session_id,
                 "owner": owner, "pid": pid, "processStartedAt": started,
                 "renewedAt": now, "expiresAt": now + LEASE_SECONDS, "claims": claims}
        # Publish ownership BEFORE claiming. Crash windows leave mail in new/cur,
        # where the stale legacy fallback can recover it, never in done/.
        config.atomic_write_json(path, state)
        inbox.ensure_dirs()
        total = 0
        ids = set()
        for name in list(claims):
            candidate = inbox.INBOX_CUR / name
            if not candidate.exists():
                claims.remove(name)
                continue
            message = _message(candidate, agent_id)
            if message is None:
                result["blocked"] += 1
                continue
            if message["id"] in ids:
                result["blocked"] += 1
                continue
            if len(result["messages"]) >= MAX_MESSAGES or total + _length(message["body"]) > MAX_BATCH:
                break
            result["messages"].append(message)
            ids.add(message["id"])
            total += _length(message["body"])
        for candidate in sorted(inbox.INBOX_NEW.glob("*.json")):
            if len(result["messages"]) >= MAX_MESSAGES:
                break
            message = _message(candidate, agent_id)
            if message is None:
                # Only report blocked mail for this recipient, not other seats.
                if _read(candidate).get("to") in (agent_id, "broadcast"):
                    result["blocked"] += 1
                continue
            if message["id"] in ids:
                result["blocked"] += 1
                continue
            if total + _length(message["body"]) > MAX_BATCH:
                continue
            # Persist the receipt BEFORE rename: a crash between claim and
            # ledger publication must not strand cur mail behind a renewed lease.
            claims.append(candidate.name)
            state["claims"] = claims
            config.atomic_write_json(path, state)
            if inbox.claim({"_path": str(candidate)}):
                result["messages"].append(message)
                ids.add(message["id"])
                total += _length(message["body"])
        state["claims"] = claims
        if result["blocked"]:
            # Unsupported mail must not indefinitely suppress its legacy reader.
            # The mod pauses native dispatch until a human retries/reloads it.
            state["expiresAt"] = 0
            result["messages"] = []
        config.atomic_write_json(path, state)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("poll", "ack", "release"))
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--receipt", action="append", default=[])
    args = parser.parse_args()
    try:
        print(json.dumps(operate(args.action, args.agent, args.session, args.owner, args.receipt)))
        return 0
    except (ValueError, OSError, psutil.Error) as exc:
        print(json.dumps({"error": type(exc).__name__, "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
