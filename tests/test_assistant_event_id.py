"""_last_assistant_event_id — the id must be DERIVED, not minted.

The defect these arms defend against (LiteSuite T132, 2026-08-31): the Stop
hook ran TWICE ~40ms apart, so Orchestrator Chat rendered one message twice.
The fix is an id-keyed dedupe downstream, and it is worth EXACTLY NOTHING
unless the two runs compute the SAME id for the same turn. `test_stable_across_calls`
is therefore the load-bearing arm — a uuid4 implementation passes every other
test in this file and fails that one.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liteharness.hooks import _last_assistant_event_id


def _write(tmp_path, entries):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(p)


def test_returns_last_assistant_message_id(tmp_path):
    path = _write(tmp_path, [
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "a1", "message": {"id": "msg_first"}},
        {"type": "user", "uuid": "u2"},
        {"type": "assistant", "uuid": "a2", "message": {"id": "msg_last"}},
    ])
    assert _last_assistant_event_id(path) == "msg_last"


def test_stable_across_calls(tmp_path):
    """THE ARM THAT MATTERS. Two hook runs, one turn, one id.

    A minted uuid4 would return a different value per call and the downstream
    dedupe would pass both posts through while looking correct.
    """
    path = _write(tmp_path, [
        {"type": "assistant", "uuid": "a1", "message": {"id": "msg_x"}},
    ])
    assert len({_last_assistant_event_id(path) for _ in range(5)}) == 1


def test_distinguishes_a_genuine_repeat_from_a_duplicate(tmp_path):
    """Identical TEXT in two turns must NOT collapse — that is a real repeat.

    This is why the id is the turn's identity and not a hash of `content`:
    Sentinel says "Quiet hold." twice and both belong on screen.
    """
    path = _write(tmp_path, [
        {"type": "assistant", "uuid": "a1", "message": {"id": "msg_1", "text": "Quiet hold."}},
    ])
    first = _last_assistant_event_id(path)
    path2 = _write(tmp_path, [
        {"type": "assistant", "uuid": "a1", "message": {"id": "msg_1", "text": "Quiet hold."}},
        {"type": "assistant", "uuid": "a2", "message": {"id": "msg_2", "text": "Quiet hold."}},
    ])
    assert first != _last_assistant_event_id(path2)


def test_falls_back_to_entry_uuid_when_message_id_absent(tmp_path):
    path = _write(tmp_path, [{"type": "assistant", "uuid": "a-only", "message": {}}])
    assert _last_assistant_event_id(path) == "a-only"


def test_absent_and_unreadable_yield_none(tmp_path):
    assert _last_assistant_event_id(None) is None
    assert _last_assistant_event_id("") is None
    assert _last_assistant_event_id(str(tmp_path / "does-not-exist.jsonl")) is None


def test_no_assistant_entry_yields_none(tmp_path):
    path = _write(tmp_path, [{"type": "user", "uuid": "u1"}])
    assert _last_assistant_event_id(path) is None


def test_survives_a_corrupt_trailing_line(tmp_path):
    """A half-written last line must not hide the turn before it."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps({"type": "assistant", "uuid": "a1", "message": {"id": "msg_ok"}})
        + "\n{\"type\": \"assis",
        encoding="utf-8",
    )
    assert _last_assistant_event_id(str(p)) == "msg_ok"


def test_reads_only_the_tail_of_a_large_transcript(tmp_path):
    """Bounded read: the answer must come from the END, not a full scan.

    Padding well past the 256KB window proves the seek is real — a full-file
    reader would also pass the assertion, so the arm additionally requires the
    EARLY entry to be invisible.
    """
    filler = [{"type": "user", "uuid": "u%d" % i, "pad": "x" * 512} for i in range(2000)]
    entries = [{"type": "assistant", "uuid": "old", "message": {"id": "msg_TOO_EARLY"}}]
    entries += filler
    entries += [{"type": "assistant", "uuid": "new", "message": {"id": "msg_recent"}}]
    path = _write(tmp_path, entries)
    assert os.path.getsize(path) > 262144
    got = _last_assistant_event_id(path)
    assert got == "msg_recent"
    assert got != "msg_TOO_EARLY"
