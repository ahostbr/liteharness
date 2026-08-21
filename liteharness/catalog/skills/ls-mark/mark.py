"""Ask the human to mark a spot on their screen. Blocks; prints the handoff.

Launches marker_overlay.ps1 (shipped beside this file) in interactive mode:
a draggable ring with send/cancel buttons. On send, the overlay captures the
marker's monitor WITH THE RING STILL IN THE SHOT and writes an atomic JSON
handoff. This script polls for it and prints exactly one line: the JSON, or
CANCELLED, or TIMEOUT.

The JSON is read with utf-8-sig — Windows PowerShell writes it with a BOM,
which plain utf-8 json parsing rejects (caught in a live drill, not by
inspection).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

OVERLAY = Path(__file__).resolve().parent / "marker_overlay.ps1"


def main() -> int:
    ap = argparse.ArgumentParser(description="Human screen-marker: blocks until send/cancel/timeout.")
    ap.add_argument("--timeout", type=int, default=180, help="seconds to wait (default 180)")
    ap.add_argument("--color", default="cyan", help="ring color, name or #RRGGBB")
    args = ap.parse_args()

    if not OVERLAY.exists():
        print(f"ERROR: overlay script missing at {OVERLAY}")
        return 2

    handoff = Path(tempfile.mkdtemp(prefix="ls_mark_")) / "mark.json"
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-File", str(OVERLAY),
        "-Interactive", "-HandoffFile", str(handoff),
        "-Color", args.color,
    ]
    creation = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(cmd, creationflags=creation,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"ERROR: could not launch overlay: {e}")
        return 2

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if handoff.exists():
            try:
                data = json.loads(handoff.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as e:
                print(f"ERROR: unreadable handoff: {e}")
                return 2
            if data.get("cancelled"):
                print("CANCELLED")
                return 1
            print(json.dumps(data))
            return 0
        time.sleep(0.3)
    # Nobody answered: take the ring DOWN. An overlay that outlives its
    # question is UI litter the human has to clean up by hand.
    try:
        proc.terminate()
    except OSError:
        pass
    print(f"TIMEOUT after {args.timeout}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
