"""Read numeric Claude mod snapshots for an external the orchestrator tick.

No transcript inspection, plugin-store assumptions, live session calls, or writes.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import re
import time
from pathlib import Path

SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MAX_BYTES = 65_536
MAX_READS = 256
FRESH_MS = 60_000
DISCOVERABLE_MS = 86_400_000
TTL_MS = 3_600_000


def _number(value, maximum=2**53 - 1):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= maximum and math.isfinite(value))


def usage_root() -> Path:
    return Path.home() / ".liteharness" / "mods" / "usage"


def read_session(session_id: str, *, root: Path | None = None, now_ms=None) -> dict:
    """Return an explicit missing/invalid/stale record, never fake a zero reading."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        raise ValueError("Invalid Claude session ID")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    path = (usage_root() if root is None else root) / (session_id + ".json")
    result = {"sessionId": session_id, "status": "missing", "metrics": None}
    try:
        if path.is_symlink():
            raise ValueError("Snapshot must not be a symbolic link")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Oversized snapshot")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schemaVersion") != 1 or data.get("source") != "claude-code-mod":
            raise ValueError("Unsupported snapshot")
        if data.get("sessionId") != session_id:
            raise ValueError("Mismatched session identity")
        sampled = data.get("sampledAt")
        if not _number(sampled) or sampled > now:
            raise ValueError("Invalid sample clock")
        if not isinstance(data.get("active"), bool) or not isinstance(data.get("usageAvailable"), bool):
            raise ValueError("Invalid availability")
        context = data.get("context")
        cache = data.get("cache")
        if not isinstance(context, dict) or not isinstance(cache, dict):
            raise ValueError("Missing metrics")
        clean_context = {}
        for key in ("tokens", "window", "percent"):
            value = context.get(key)
            if value is not None and not _number(value, 100 if key == "percent" else 2**53 - 1):
                raise ValueError("Invalid context number")
            clean_context[key] = value
        if cache.get("estimated") is not True or cache.get("ttlMs") != TTL_MS or cache.get("basis") != "successful-main-turn-step-start":
            raise ValueError("Invalid estimate contract")
        last = cache.get("lastRequestAt")
        if last is not None and (not _number(last) or last > sampled):
            raise ValueError("Invalid request clock")
        expiry = None if last is None else last + TTL_MS
        if cache.get("expiresAt") != expiry:
            raise ValueError("Mismatched expiry")
        remaining = None if expiry is None else max(0, expiry - now)
        age = now - sampled
        status = "ended" if not data["active"] else "stale" if age > FRESH_MS else "ok"
        # Whitelist on output too: arbitrary extra fields never leak into a tick.
        result.update(status=status, ageMs=age, metrics={
            "schemaVersion": 1, "source": "claude-code-mod", "sessionId": session_id,
            "sampledAt": sampled, "active": data["active"],
            "usageAvailable": data["usageAvailable"], "context": clean_context,
            "cache": {"estimated": True, "basis": cache["basis"], "ttlMs": TTL_MS,
                      "lastRequestAt": last, "expiresAt": expiry, "remainingMs": remaining,
                      "state": "unknown" if last is None else "warm" if remaining else "cold"},
        })
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        result["status"] = "unavailable"
    return result


def read_all(*, root: Path | None = None, now_ms=None) -> dict:
    """Stat candidates, read newest <=256. Discoverability is not disk pruning."""
    root = usage_root() if root is None else root
    now = int(time.time() * 1000) if now_ms is None else now_ms
    selected = []
    candidates = 0
    try:
        for path in root.iterdir():
            if path.suffix != ".json" or not SESSION_ID.fullmatch(path.stem) or path.is_symlink():
                continue
            try:
                stamp = path.stat().st_mtime_ns
                if stamp / 1_000_000 < now - DISCOVERABLE_MS:
                    continue
            except OSError:
                continue
            candidates += 1
            item = (stamp, path.stem)
            if len(selected) < MAX_READS:
                heapq.heappush(selected, item)
            elif item > selected[0]:
                heapq.heapreplace(selected, item)
    except FileNotFoundError:
        return {"sessions": [], "truncated": False, "candidates": 0}
    except OSError:
        return {"sessions": [], "truncated": True, "candidates": candidates, "status": "unavailable"}
    sessions = [read_session(sid, root=root, now_ms=now) for _, sid in sorted(selected, reverse=True)]
    sessions = [r for r in sessions if r.get("ageMs", 0) <= DISCOVERABLE_MS]
    return {"sessions": sessions, "truncated": candidates > MAX_READS, "candidates": candidates}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = read_session(args.session) if args.session else read_all()
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
