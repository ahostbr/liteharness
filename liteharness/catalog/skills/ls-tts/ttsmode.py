#!/usr/bin/env python
"""Read or set TTS *mode* — the standing "speak every response" preference.

This is a SEPARATE concern from speaking one message. speak.py never consults
this flag and this script never speaks. Keeping them apart is deliberate: one
control that means both "say this now" and "say everything from now on" is one
string covering several states, and that is a defect waiting to be found.

    python ttsmode.py status      # -> "on" or "off" (exit 0 = on, 1 = off)
    python ttsmode.py on
    python ttsmode.py off
    python ttsmode.py toggle

Presence of the flag file is the state, which keeps every existing reader
working. The file also carries JSON describing who set it and when, so a stale
mode is diagnosable rather than mysterious.

Writes are atomic — temp file plus os.replace — because more than one process
can set this and a half-written flag read by a third is a silent wrong answer.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "liteharness"
FLAG_PATH = STATE_DIR / "tts_mode_enabled"

USAGE = "usage: ttsmode.py [status|on|off|toggle]"


def is_on() -> bool:
    return FLAG_PATH.exists()


def describe() -> str:
    """Human-readable state, including provenance when we have it."""
    if not is_on():
        return "off"
    try:
        meta = json.loads(FLAG_PATH.read_text(encoding="utf-8"))
        who = meta.get("set_by") or "unknown"
        when = meta.get("set_at_local") or "unknown time"
        return "on (set by %s at %s)" % (who, when)
    except Exception:
        # Zero-byte or legacy flag: presence is still authoritative.
        return "on"


def turn_on(who: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "state": "on",
        "set_by": who,
        "set_at_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "set_at_epoch": int(time.time()),
    }, indent=2)

    # Atomic: a reader sees either the old file or the complete new one.
    fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".tts_mode_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(FLAG_PATH))
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def turn_off() -> None:
    FLAG_PATH.unlink(missing_ok=True)      # unlink is already atomic


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    who = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("USERNAME") or "unknown"

    if action == "status":
        print(describe())
        return 0 if is_on() else 1

    if action == "toggle":
        action = "off" if is_on() else "on"

    if action == "on":
        turn_on(who)
        print("on")
        return 0

    if action == "off":
        turn_off()
        print("off")
        return 0

    sys.stderr.write(USAGE + "\n")
    return 64


if __name__ == "__main__":
    sys.exit(main())
