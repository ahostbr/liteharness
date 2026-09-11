"""T660 — two Stop registrations are two PROCESSES, and only the kernel can pick.

🔴 THE FIX THAT WAS NOT ONE. The first dedupe kept `{seat: last_turn_id}` and did
read-then-write: load, compare, send, save. Its arms passed, because a single
process calling `forward_stop` twice really is deduped by it. Live, the stop still
arrived twice (1dff3f6d / e9247fab, 2026-09-11) — settings.json's Stop and the
plugin's hooks.json Stop run CONCURRENTLY, so both read "not yet recorded" before
either wrote.

⬜ NO ORDERING OF THE SAME THREE STEPS FIXES IT. Writing before sending closes a
crash window, not a race: there is no point in a load/compare/store sequence that
two processes cannot both pass. `os.open(O_CREAT | O_EXCL)` has no such point —
the kernel creates the file exactly once and the loser's FileExistsError IS the
answer.

⚠️ SO THIS ARM USES REAL PROCESSES. A threaded version would prove less: CPython's
GIL makes the interleaving that broke this rarer, and the two hooks are not
threads. It is the one thing the in-process arms structurally cannot say.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SEAT = "11111111-2222-3333-4444-555555555555"
ORCH = "99999999-8888-7777-6666-555555555555"


def _forward_in_child(root: str, transcript: str, ready, done) -> None:
    """Run one `forward_stop` in a fresh process rooted at `root`.

    Module level and importable, because Windows spawns rather than forks: a
    closure would not survive the trip.
    """
    from pathlib import Path as P

    from liteharness import config, inbox, stop_forward

    base = P(root)
    config.HARNESS_ROOT = base
    inbox.INBOX_ROOT = base / "inbox"
    inbox.INBOX_NEW = base / "inbox" / "new"
    inbox.INBOX_CUR = base / "inbox" / "cur"
    inbox.INBOX_DONE = base / "inbox" / "done"
    inbox.INBOX_TMP = base / "inbox" / "tmp"
    config.get_agent_id = lambda: SEAT

    # Both children wait on the same gate, so they contend for the claim instead
    # of arriving politely one after the other.
    ready.wait(timeout=10)
    try:
        stop_forward.forward_stop({"transcript_path": transcript})
    finally:
        done.put(1)


@pytest.mark.skipif(
    multiprocessing.get_start_method(allow_none=True) not in (None, "spawn", "fork"),
    reason="needs a usable multiprocessing start method",
)
def test_two_concurrent_hooks_send_exactly_one_message(tmp_path):
    root = tmp_path / ".liteharness"
    for sub in ("inbox/new", "inbox/cur", "inbox/done", "inbox/tmp", "agents"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "stop-forward.json").write_text(
        json.dumps({"enabled": True, "watch": {SEAT: [ORCH]}}), encoding="utf-8"
    )

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"id": "msg_one_turn", "content": [{"type": "text", "text": "done"}]},
            }
        ),
        encoding="utf-8",
    )

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    done: multiprocessing.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=_forward_in_child, args=(str(root), str(transcript), ready, done))
        for _ in range(2)
    ]
    for p in procs:
        p.start()
    ready.set()
    for p in procs:
        p.join(timeout=60)

    # 🔴 THE VALIDITY GATE. "Exactly one message" is also what you get when one
    # child dies on import and the other works — and the first run of this arm
    # finished in 0.26 s, far too fast for two spawned interpreters, which is
    # what made me check. Both children must have RUN for the count below to be
    # a statement about the claim rather than about a crash.
    completed = 0
    for _ in range(2):
        try:
            completed += done.get(timeout=30)
        except Exception:  # noqa: BLE001 — an empty queue is the failure itself
            break
    assert completed == 2, f"only {completed} of 2 child processes reached forward_stop"
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    sent = list((root / "inbox" / "new").glob("*.json"))
    bodies = [json.loads(p.read_text(encoding="utf-8")) for p in sent]
    notes = [b for b in bodies if b.get("to") == ORCH]

    assert len(notes) == 1, f"two concurrent hooks produced {len(notes)} messages"
