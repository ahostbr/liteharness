"""T660 — tell a watching orchestrator that a seat stopped, and hand it the
material to judge WHY.

🔴 A SEAT THAT STOPS LOOKS EXACTLY LIKE A SEAT THAT IS WAITING. Both are silent.
The orchestrator finds out by noticing, and on 2026-09-11 only Ryan did: OpenBolt
stopped at a prompt after message `dec63aaa`, and the work sat until a human
happened to look at the pane. There is no signal to miss, which is why nobody
missed it.

⬜ THE HOOK NEVER JUDGES. It reports that a stop happened and attaches what a
judgement would need — the seat's last words, whether its last act was to send a
message and to whom, how long since anyone wrote to it, and the branch. "Clean
stop that needs a nudge" and "waiting between inbox messages" are the same event
from the hook's vantage point; they differ only in context the orchestrator has
and the hook does not. Ryan's framing, and the reason there is no verdict field.

⚠️ AND NO DEDUPE SUPPRESSION, DELIBERATELY. The obvious optimisation is to stop
re-reporting a seat that already reported. It is wrong here: a seat that stops
five times in a row IS the signal, and a suppressor would hide exactly the case
worth acting on. Volume is the orchestrator's problem to judge, not the hook's to
pre-empt.

⚠️ ctx % IS ABSENT ON PURPOSE. The card asks for it "if readable"; it is not. The
percentage lives in Claude Code's own statusline UI, not in any file this hook
can read (`grep -rn statusline liteharness/*.py` for a cache finds nothing). An
estimate would be a number nobody could act on, so the field is omitted rather
than invented.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from . import config, inbox

#: `{enabled: bool, watch: {<seat-id>: [<orchestrator-id>, ...]}}`
CONFIG_PATH = config.HARNESS_ROOT / "stop-forward.json"

#: How much of the seat's last message travels. Enough to see what it was doing,
#: short enough that a chatty seat cannot flood an orchestrator's inbox.
TAIL_CHARS = 800

#: Transcript files are append-only JSONL and can reach tens of megabytes. Only
#: the end is ever interesting, so only the end is read.
TAIL_BYTES = 256_000


def load() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False, "watch": {}}
    if not isinstance(data, dict):
        return {"enabled": False, "watch": {}}
    data.setdefault("enabled", False)
    watch = data.get("watch")
    data["watch"] = watch if isinstance(watch, dict) else {}
    return data


def save(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def set_enabled(on: bool) -> dict[str, Any]:
    data = load()
    data["enabled"] = bool(on)
    save(data)
    return data


def add_watch(seat_id: str, orchestrator_id: str) -> dict[str, Any]:
    data = load()
    watchers = data["watch"].setdefault(seat_id, [])
    if orchestrator_id not in watchers:
        watchers.append(orchestrator_id)
    save(data)
    return data


def remove_watch(seat_id: str, orchestrator_id: Optional[str] = None) -> dict[str, Any]:
    data = load()
    if orchestrator_id is None:
        data["watch"].pop(seat_id, None)
    elif seat_id in data["watch"]:
        data["watch"][seat_id] = [w for w in data["watch"][seat_id] if w != orchestrator_id]
        if not data["watch"][seat_id]:
            data["watch"].pop(seat_id)
    save(data)
    return data


#: One id per seat — the last turn whose stop was forwarded.
SEEN_PATH = config.HARNESS_ROOT / "stop-forward-seen.json"


def _turn_id(transcript_path: str) -> Optional[str]:
    """The API message id of the transcript's last assistant turn.

    Imported from `hooks` rather than reimplemented: it is the same question,
    and two copies of a dedupe key are two chances to disagree about what
    counts as the same event.
    """
    if not transcript_path:
        return None
    try:
        from .hooks import _last_assistant_event_id

        return _last_assistant_event_id(transcript_path)
    except Exception:
        return None


def _already_forwarded(seat_id: str, event_id: str) -> bool:
    try:
        seen = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(seen, dict) and seen.get(seat_id) == event_id


def _record_forwarded(seat_id: str, event_id: str) -> None:
    try:
        seen = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError):
        seen = {}
    seen[seat_id] = event_id
    try:
        SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(seen), encoding="utf-8")
        os.replace(tmp, SEEN_PATH)
    except OSError:
        pass


def _transcript_tail(path: str) -> list[dict[str, Any]]:
    """The last few JSONL records, read from the END of the file.

    A transcript is append-only and large; reading it whole to look at its last
    entry would make a Stop hook cost more the longer a seat has been working,
    which is backwards.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()  # discard the partial record the seek landed in
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _text_of(message: Any) -> str:
    """The plain text of an assistant message, whatever shape it arrived in."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def last_assistant_text(records: list[dict[str, Any]]) -> str:
    for rec in reversed(records):
        if rec.get("type") != "assistant":
            continue
        text = _text_of(rec.get("message")).strip()
        if text:
            return text[:TAIL_CHARS]
    return ""


def last_inbox_send(
    records: list[dict[str, Any]], seat_id: Optional[str] = None
) -> Optional[str]:
    """Who the seat's last tool call messaged, or None if it was not a send.

    🔴 THE DISCRIMINATOR THE ORCHESTRATOR ACTUALLY WANTS. A seat that stopped
    straight after reporting in is waiting for an answer; a seat that stopped
    after editing a file has simply gone quiet. Both are a Stop event.
    """
    for rec in reversed(records):
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not uses:
            continue
        # The LAST tool call in the LAST turn that made one.
        call = uses[-1]
        blob = json.dumps(call.get("input") or {})
        name = str(call.get("name", ""))
        # 🔴 THE TOOL, NOT THE TEXT. Scanning the blob alone read a `Write` of a
        # file that merely MENTIONS `liteharness.cli send` as a send — measured
        # over 115 real send-shaped calls in a live transcript, one of them a
        # test fixture being written. A call is a send because of what it IS.
        if name not in ("Bash", "BashOutput") and "inbox" not in name.lower():
            return None
        if "liteharness.cli send" in blob or "inbox" in name.lower():
            return _recipient_of(blob, seat_id)
        return None
    return None


def _looks_like_id(token: str) -> bool:
    return len(token) == 36 and token.count("-") == 4


def _recipient_of(blob: str, seat_id: Optional[str]) -> str:
    """Who a send was addressed TO.

    🔴 THE FIRST VERSION TOOK THE FIRST UUID IT SAW AND REPORTED THE SENDER.
    Live on 2026-09-11 it told the orchestrator "inbox send -> c30dbfa8…", which
    is the seat's OWN id, for a message actually sent to 2dc57f3e. Every arm
    passed: the fixtures were built from a clean `send <target> hi` command, and
    the real call carries `--from <seat>` and a `--body-file` path that itself
    contains the seat id. A positional scan cannot tell an argument's ROLE from
    its shape.

    ⬜ SO IT READS THE POSITION THE CLI DEFINES: the token after `send`. The
    seat's own id is refused outright as a second guard — whatever the parse
    finds, "you sent a message to yourself" is never the fact being reported.
    """
    tokens = blob.replace('"', " ").replace(",", " ").replace("\\", " ").split()
    for i, token in enumerate(tokens):
        if token != "send":
            continue
        for candidate in tokens[i + 1 : i + 3]:
            if _looks_like_id(candidate) and candidate != seat_id:
                return candidate
    # No positional match — fall back to any id that is not the seat's own.
    for token in tokens:
        if _looks_like_id(token) and token != seat_id:
            return token
    return "unknown"


def seconds_since_inbox_received(seat_id: str) -> Optional[int]:
    """How long since anyone wrote to this seat, or None if nothing ever did."""
    newest = 0.0
    for folder in (inbox.INBOX_NEW, inbox.INBOX_CUR, inbox.INBOX_DONE):
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            if path.suffix != ".json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("to") != seat_id:
                continue
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    if newest <= 0:
        return None
    return max(0, int(time.time() - newest))


def seat_name(seat_id: str) -> str:
    """The seat's human name, or the first 8 of its id.

    Two places hold it and the override wins, which is the order
    `_evict_agent_records` (cli.py:1049) moves them in: `names/<id>` is written
    by a `--takeover`, `agents/<id>.json` by the ordinary registration.
    """
    root = config.get_root()
    try:
        override = (root / "names" / seat_id).read_text(encoding="utf-8").strip()
        if override:
            return override
    except OSError:
        pass
    try:
        rec = json.loads((root / "agents" / f"{seat_id}.json").read_text(encoding="utf-8"))
        name = rec.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, ValueError):
        pass
    return seat_id[:8]


def current_branch(cwd: Optional[str] = None) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = out.stdout.strip()
    return name or None


def compose_body(
    *,
    name: str,
    seat_id: str,
    last_text: str,
    sent_to: Optional[str],
    since_received: Optional[int],
    branch: Optional[str],
) -> str:
    """The message an orchestrator reads. Material only — never a verdict."""
    lines = [f"[STOP] {name} ({seat_id[:8]}) stopped", ""]
    if sent_to:
        lines.append(f"last tool call: inbox send -> {sent_to}")
    else:
        lines.append("last tool call: not an inbox send")
    lines.append(
        "since last inbox received: "
        + ("never" if since_received is None else f"{since_received}s")
    )
    if branch:
        lines.append(f"branch: {branch}")
    lines.append("")
    lines.append("last said:")
    lines.append(last_text or "(no assistant text in the transcript tail)")
    return "\n".join(lines)


def forward_stop(hook_input: dict[str, Any]) -> Optional[str]:
    """Called from the obs Stop branch. Returns the message id, or None.

    Every early return is a REASON not to send, and they are ordered cheapest
    first so a disabled flag costs one file read.
    """
    data = load()
    if not data.get("enabled"):
        return None

    seat_id = config.get_agent_id()
    watchers = data["watch"].get(seat_id) or []
    if not watchers:
        return None

    # A Stop hook that is itself running inside a stop-hook continuation would
    # report a stop that the previous report caused.
    if hook_input.get("stop_hook_active"):
        return None

    # 🔴 THE SAME STOP FIRES THIS HOOK TWICE ON THIS BOX. settings.json's Stop
    # and the plugin's hooks.json Stop both run `obs Stop`, so one stop produced
    # two byte-identical messages seconds apart (98a1beb9 / 063fc934, live
    # 2026-09-11). That is NOT the consecutive-stops case the card deliberately
    # keeps: it is one event delivered twice.
    #
    # ⬜ KEYED ON THE TURN, NOT THE CALL — the rule `_last_assistant_event_id`
    # (hooks.py:2166) already states for this exact duplicate: both registrations
    # read the SAME transcript entry and compute the same id, while a genuine
    # second stop is a different turn with its own id. A minted uuid would differ
    # per run and dedupe nothing; a content hash could not tell a duplicate
    # delivery from a seat saying the same thing twice.
    transcript_path = hook_input.get("transcript_path") or ""
    event_id = _turn_id(transcript_path)
    if event_id and _already_forwarded(seat_id, event_id):
        return None

    records = _transcript_tail(transcript_path)
    body = compose_body(
        name=seat_name(seat_id),
        seat_id=seat_id,
        last_text=last_assistant_text(records),
        sent_to=last_inbox_send(records, seat_id),
        since_received=seconds_since_inbox_received(seat_id),
        branch=current_branch(hook_input.get("cwd")),
    )

    # Recorded BEFORE sending: a crash mid-send must not licence a second
    # report of the same turn.
    if event_id:
        _record_forwarded(seat_id, event_id)

    last_id = None
    for watcher in watchers:
        # A seat watching itself would report its own stop and then stop again
        # reporting it.
        if watcher == seat_id:
            continue
        try:
            last_id = inbox.send(seat_id, watcher, body, msg_type="PROGRESS")
        except Exception as exc:  # a hook must never take the seat down with it
            print(f"[stop-forward] could not notify {watcher[:8]}: {exc}")
    return last_id
