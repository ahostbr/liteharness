"""Audit a configured clerk against digest AND actual maildir receipts (stdlib only)."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# Approved keyword set, plus the explicit never-list nouns not covered by it.
NEVER = re.compile(r"MR|merge|integrat|GATE|PROPOSE|approv|GO\b|delete|remove|worktree remove|"
                   r"lease|restart|build|Play|download|push|deploy|card|spawn|"
                   r"money|editor|cleanup|new work|new seat|model load|F:", re.IGNORECASE)
IDENTITY = re.compile(r"^Clerk \(not [^\r\n)]+\):")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _time(value) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _body(msg: dict) -> str:
    body = msg.get("body", msg.get("payload", {}).get("text"))
    if not isinstance(body, str):
        raise ValueError(f"Message {msg.get('id')} has no auditable textual body")
    return body


def audit(root: Path, since: str) -> dict[str, int]:
    cutoff = _time(since)
    cfg = json.loads((root / "clerk.json").read_text(encoding="utf-8"))
    clerk, orchestrator = cfg["clerk_id"], cfg["orchestrator_id"]
    if not all(isinstance(v, str) and SAFE_ID.fullmatch(v) for v in (clerk, orchestrator)) or clerk == orchestrator:
        raise ValueError("Audit requires separate safe clerk/orchestrator ids")
    messages = {}
    recent = []
    for folder in ("new", "cur", "done"):
        for path in (root / "inbox" / folder).glob("*.json"):
            msg = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(msg, dict):
                raise ValueError(f"Malformed maildir record: {path.name}")
            if msg.get("from") != clerk and msg.get("to") not in (clerk, orchestrator):
                continue
            key = msg["id"]
            if key in messages and messages[key] != msg:
                raise ValueError(f"Conflicting maildir records for {key}")
            messages[key] = msg
    recent = [m for m in messages.values() if _time(m.get("timestamp", m.get("ts"))) >= cutoff]
    digest = {}
    path = root / "clerk" / f"digest-{orchestrator}.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if _time(row["ts"]) >= cutoff:
                digest[row["msg_id"]] = row
    violations = 0
    # Every clerk-sent message, not just forwards or messages to the orchestrator.
    outbound = [m for m in recent if m.get("from") == clerk]
    violations += sum(not IDENTITY.match(_body(m)) for m in outbound)
    originals = {m["id"]: m for m in recent if m.get("to") == clerk and m.get("from") != clerk}
    # A digest can reference older mail processed during this window.
    for msg_id in digest:
        if msg_id in messages:
            originals[msg_id] = messages[msg_id]
        else:
            violations += 1  # Retention erased proof: never call an unprovable day clean.
    for msg_id, msg in originals.items():
        text = _body(msg)
        row = digest.get(msg_id, {})
        action = row.get("action")
        never = bool(NEVER.search(text) or msg.get("from") in cfg.get("direct_senders", []))
        if never and action != "forwarded":
            violations += 1
        elif action == "forwarded":
            # A claimed forwarded action is insufficient: the new mail must exist,
            # preserve sender provenance and retain the original text exactly.
            receipts = [m for m in messages.values() if m.get("from") == clerk
                        and m.get("to") == orchestrator and m.get("reply_to") == msg_id
                        and m.get("forwarded_for") == msg.get("from")]
            def exact(m):
                parts = _body(m).split("\n", 2)
                return (len(parts) == 3 and IDENTITY.match(parts[0])
                        and parts[1].startswith("[FWD from ")
                        and parts[1].endswith(f" · msg {msg_id}]") and parts[2] == text)
            if not any(exact(m) for m in receipts):
                violations += 1
        elif action and action not in ("acked", "answered", "relayed", "digested"):
            violations += 1
    return {
        "reached_sentinel": sum(m.get("to") == orchestrator for m in recent),
        "handled": sum(r.get("action") in ("acked", "answered", "relayed", "digested") for r in digest.values()),
        "forwarded": sum(r.get("action") == "forwarded" for r in digest.values()),
        "never_list_violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO timestamp, UTC if no offset")
    args = parser.parse_args()
    try:
        counts = audit(Path.home() / ".liteharness", args.since)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"Audit could not establish receipts: {exc}\n")
    print(" ".join(f"{name}={value}" for name, value in counts.items()))
    return 1 if counts["never_list_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
