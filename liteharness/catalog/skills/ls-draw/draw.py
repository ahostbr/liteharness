"""ls-draw — screenshot a monitor, let the human draw on it, get the picture back.

THIN ON PURPOSE. Everything real lives in LiteSuite: the capture, the window, the pill, the
files. This POSTs the AgentBridge and prints ONE line, exactly as ls-mark does, so the skill
has no second implementation to drift from.

  python draw.py                     capture monitor 0, wait up to 600s
  python draw.py --mon 1             capture monitor 1
  python draw.py --mon all           capture every monitor, draw on monitor 0
  python draw.py --timeout 120 --note "circle the broken one"
  python draw.py --get <session>     re-read a finished session
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE_URL = os.environ.get("LITESUITE_BRIDGE_URL", "http://127.0.0.1:7423").rstrip("/")
TOKEN_FILE = Path.home() / ".litesuite" / "bridge-token"

# The bridge holds the connection open for the whole drawing session. The socket ceiling has
# to sit ABOVE the server's own timeout, or the client gives up on a session that is still
# live and the human's drawing is lost with nothing that names the cause.
CLIENT_SLACK_S = 30

DOWN = "LiteSuite is not running"


def token() -> str | None:
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or os.environ.get("LITESUITE_BRIDGE_TOKEN") or None


def call(method: str, path: str, body: dict | None, timeout: float) -> dict:
    tok = token()
    if not tok:
        print(DOWN)
        sys.exit(1)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    if data is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"ERROR: bridge returned {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        sys.exit(1)
    except (urllib.error.URLError, OSError):
        # A refused connection is the ordinary "app is closed" case. Say the sentence and
        # stop — do not retry, do not print a stack the caller has to interpret.
        print(DOWN)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Let the human draw on a screenshot.")
    ap.add_argument("--mon", default="0", help="monitor index or 'all' (default 0)")
    ap.add_argument("--timeout", type=float, default=600, help="seconds to wait (default 600)")
    ap.add_argument("--note", default=None, help="prompt shown in the pill's note field")
    ap.add_argument("--get", default=None, metavar="SESSION", help="re-read a finished session")
    args = ap.parse_args()

    if args.get:
        print(json.dumps(call("GET", f"/draw/{args.get}", None, 30)))
        return

    mon: object = "all" if str(args.mon).strip().lower() == "all" else int(args.mon)
    body: dict = {"mon": mon, "timeout": args.timeout}
    if args.note:
        body["note"] = args.note

    result = call("POST", "/draw", body, args.timeout + CLIENT_SLACK_S)

    # ls-mark's spelling: bare words a caller can branch on without parsing json. Neither is
    # an error — the human simply chose not to draw.
    outcome = result.get("outcome")
    if outcome in ("CANCELLED", "TIMEOUT"):
        print(outcome)
        return

    print(json.dumps(result))


if __name__ == "__main__":
    main()
