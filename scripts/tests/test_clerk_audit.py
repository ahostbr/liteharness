"""Audit source + delivered receipt, never trust a digest's claimed action alone."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.clerk_audit import audit

STAMP = "2026-10-24T10:00:00+00:00"
SINCE = "2026-10-24T00:00:00+00:00"
SCRIPT = Path(__file__).resolve().parents[1] / "clerk_audit.py"


@pytest.fixture
def root(tmp_path):
    root = tmp_path / ".liteharness"
    for folder in ("inbox/new", "inbox/cur", "inbox/done", "clerk"):
        (root / folder).mkdir(parents=True)
    (root / "clerk.json").write_text(json.dumps({"clerk_id": "clerk", "orchestrator_id": "orch", "direct_senders": ["Theater"]}), encoding="utf-8")
    return root


def put(root, msg, folder="done"):
    (root / "inbox" / folder / (msg["id"] + ".json")).write_text(json.dumps(msg), encoding="utf-8")


def sample(root, body="MR merge request", action="forwarded", forwarded=True):
    msg = {"id": "original", "from": "worker", "to": "clerk", "timestamp": STAMP, "body": body}
    put(root, msg)
    row = {"ts": STAMP, "msg_id": "original", "from": "worker", "from_name": "Worker",
           "summary": body[:140], "action": action}
    (root / "clerk/digest-orch.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    if forwarded:
        put(root, {"id": "fwd", "from": "clerk", "to": "orch", "timestamp": STAMP,
                   "forwarded_for": "worker", "reply_to": "original",
                   "body": "Clerk (not Coordinator):\n[FWD from Worker · msg original]\n" + body}, "cur")


def test_good_forward_checks_actual_receiver_receipt(root):
    sample(root)
    assert audit(root, SINCE) == {"reached_sentinel": 1, "handled": 0, "forwarded": 1, "never_list_violations": 0}


@pytest.mark.parametrize("action", ["acked", "answered", "relayed", "digested"])
def test_never_list_handling_is_a_violation(root, action):
    sample(root, action=action, forwarded=False)
    assert audit(root, SINCE)["never_list_violations"] == 1


def test_claimed_forward_without_delivered_mail_is_a_violation(root):
    sample(root, forwarded=False)
    assert audit(root, SINCE)["never_list_violations"] == 1


def test_altered_forward_body_is_a_violation(root):
    sample(root)
    p = root / "inbox/cur/fwd.json"
    msg = json.loads(p.read_text())
    msg["body"] += " EDITED"
    p.write_text(json.dumps(msg))
    assert audit(root, SINCE)["never_list_violations"] == 1


def test_unidentified_clerk_message_is_a_violation(root):
    sample(root, body="routine progress", action="acked", forwarded=False)
    put(root, {"id": "ack", "from": "clerk", "to": "worker", "timestamp": STAMP, "body": "Noted. I am Coordinator."})
    assert audit(root, SINCE)["never_list_violations"] == 1


def test_routine_ack_is_allowed_and_counted(root):
    sample(root, body="routine progress", action="acked", forwarded=False)
    put(root, {"id": "ack", "from": "clerk", "to": "worker", "timestamp": STAMP, "body": "Clerk (not Coordinator): noted."})
    assert audit(root, SINCE) == {"reached_sentinel": 0, "handled": 1, "forwarded": 0, "never_list_violations": 0}


def test_missing_original_evidence_cannot_be_green(root):
    sample(root)
    (root / "inbox/done/original.json").unlink()
    assert audit(root, SINCE)["never_list_violations"] == 1


def test_ryan_source_is_never_handled_even_without_keyword(root):
    sample(root, body="hello", action="acked", forwarded=False)
    p = root / "inbox/done/original.json"
    msg = json.loads(p.read_text())
    msg["from"] = "Theater"
    p.write_text(json.dumps(msg))
    assert audit(root, SINCE)["never_list_violations"] == 1


def test_new_mail_without_digest_does_not_hide_never_list_item(root):
    put(root, {"id": "pending", "from": "worker", "to": "clerk", "timestamp": STAMP, "body": "lease restart"}, "new")
    assert audit(root, SINCE)["never_list_violations"] == 1


def test_later_forward_cannot_erase_earlier_never_list_handling(root):
    sample(root, action="forwarded", forwarded=True)
    path = root / "clerk/digest-orch.jsonl"
    forwarded = json.loads(path.read_text(encoding="utf-8"))
    answered = {**forwarded, "action": "answered"}
    path.write_text(json.dumps(answered) + "\n" + json.dumps(forwarded) + "\n", encoding="utf-8")
    # Both source and exact receiver receipt exist. A later good action cannot
    # erase the earlier unsafe decision from this append-only history.
    result = subprocess.run([sys.executable, str(SCRIPT), "--since", SINCE],
        env={**os.environ, "HOME": str(root.parent), "USERPROFILE": str(root.parent)},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "handled=1" in result.stdout
    assert "forwarded=1" in result.stdout
    assert "never_list_violations=1" in result.stdout


def test_since_excludes_old_mail(root):
    sample(root, action="acked", forwarded=False)
    assert audit(root, "2026-10-25T00:00:00+00:00")["never_list_violations"] == 0


@pytest.mark.parametrize("bad,exit_code", [(False, 0), (True, 1)])
def test_script_exit_code_negative_proof(root, bad, exit_code):
    sample(root, action="answered" if bad else "forwarded", forwarded=not bad)
    result = subprocess.run([sys.executable, str(SCRIPT), "--since", SINCE],
        env={**os.environ, "HOME": str(root.parent), "USERPROFILE": str(root.parent)},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == exit_code, result.stderr
    assert f"never_list_violations={int(bad)}" in result.stdout
    assert "reached_sentinel=" in result.stdout

@pytest.mark.parametrize("body", ["money", "new work", "editor", "cleanup", "F: job", "model load", "PROPOSE", "GO", "Play", "download", "spawn"])
def test_all_explicit_never_list_categories_are_visible(root, body):
    sample(root, body=body, action="answered", forwarded=False)
    assert audit(root, SINCE)["never_list_violations"] == 1
