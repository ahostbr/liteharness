"""T660 — the Stop hook forwards material, and only when it was asked to.

Every arm drives the REAL `forward_stop` with the config and maildir relocated
into a tmp dir, because the guards being tested are the ones that decide whether
a message is sent at all — a mocked sender would prove the call shape and nothing
about the decision.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from liteharness import config, inbox, stop_forward  # noqa: E402

SEAT = "11111111-2222-3333-4444-555555555555"
ORCH = "99999999-8888-7777-6666-555555555555"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Config, maildir and agent records all under one throwaway root."""
    root = tmp_path / ".liteharness"
    (root / "agents").mkdir(parents=True)
    monkeypatch.setattr(config, "HARNESS_ROOT", root, raising=False)
    monkeypatch.setattr(config, "get_root", lambda: root, raising=False)
    monkeypatch.setattr(stop_forward, "CONFIG_PATH", root / "stop-forward.json", raising=False)
    # 🔴 BOTH paths, not just the config one. SEEN_PATH is computed at IMPORT
    # time from the real HARNESS_ROOT, so patching the constant alone left the
    # dedupe file pointing at ~/.liteharness — one arm's write leaked into the
    # next and the genuine-second-stop control failed for a reason that had
    # nothing to do with the code under test. A test that writes to the real
    # home is a test that can corrupt the box it is meant to measure.
    monkeypatch.setattr(stop_forward, "SEEN_PATH", root / "stop-forward-seen.json", raising=False)
    monkeypatch.setattr(config, "get_agent_id", lambda: SEAT, raising=False)
    for name, folder in (
        ("INBOX_NEW", root / "inbox" / "new"),
        ("INBOX_CUR", root / "inbox" / "cur"),
        ("INBOX_DONE", root / "inbox" / "done"),
        ("INBOX_TMP", root / "inbox" / "tmp"),
    ):
        folder.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(inbox, name, folder, raising=False)
    monkeypatch.setattr(inbox, "INBOX_ROOT", root / "inbox", raising=False)
    return root


def transcript(tmp_path: Path, records: list[dict]) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(path)


def said(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def sent_to(target: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": f"python -m liteharness.cli send {target} hi"},
                }
            ]
        },
    }


def delivered(root: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in (root / "inbox" / "new").iterdir()]


def test_the_flag_off_forwards_nothing(home, tmp_path):
    stop_forward.add_watch(SEAT, ORCH)
    stop_forward.set_enabled(False)

    assert stop_forward.forward_stop({"transcript_path": transcript(tmp_path, [said("hi")])}) is None
    assert delivered(home) == []


def test_an_unwatched_seat_forwards_nothing(home, tmp_path):
    stop_forward.set_enabled(True)  # enabled, but nobody is watching THIS seat

    assert stop_forward.forward_stop({"transcript_path": transcript(tmp_path, [said("hi")])}) is None
    assert delivered(home) == []


def test_a_seat_never_reports_its_own_stop_to_itself(home, tmp_path):
    """A seat watching itself would report a stop and then stop reporting it."""
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, SEAT)

    stop_forward.forward_stop({"transcript_path": transcript(tmp_path, [said("hi")])})
    assert delivered(home) == []


def test_a_stop_inside_a_stop_hook_continuation_is_not_reported(home, tmp_path):
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)

    stop_forward.forward_stop(
        {"transcript_path": transcript(tmp_path, [said("hi")]), "stop_hook_active": True}
    )
    assert delivered(home) == []


def test_the_material_is_all_present_and_carries_no_verdict(home, tmp_path):
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)
    inbox.send(ORCH, SEAT, "an earlier message to this seat")

    path = transcript(tmp_path, [sent_to(ORCH), said("I finished the thing and stopped.")])
    assert stop_forward.forward_stop({"transcript_path": path}) is not None

    notes = [m for m in delivered(home) if m["to"] == ORCH]
    assert len(notes) == 1
    body = notes[0]["body"]
    assert notes[0]["type"] == "PROGRESS"
    assert body.startswith(f"[STOP] {SEAT[:8]} ({SEAT[:8]}) stopped")
    assert f"last tool call: inbox send -> {ORCH}" in body
    assert "since last inbox received:" in body
    assert "I finished the thing and stopped." in body
    # 🔴 THE POINT OF THE CARD: material, never a judgement. A hook that said
    # "needs a nudge" would be guessing with strictly less context than the
    # orchestrator reading it.
    for verdict in ("needs a nudge", "idle", "stuck", "waiting for"):
        assert verdict not in body.lower()


def test_a_last_call_that_was_not_a_send_says_so(home, tmp_path):
    """The discriminator between 'reported in and is waiting' and 'went quiet'."""
    edit = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file": "x.ts"}}]},
    }
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)

    stop_forward.forward_stop({"transcript_path": transcript(tmp_path, [edit, said("done")])})
    body = [m for m in delivered(home) if m["to"] == ORCH][0]["body"]
    assert "last tool call: not an inbox send" in body


def test_never_received_reads_as_never_not_as_zero(home, tmp_path):
    """`0s` would say someone just wrote to it — the opposite of the truth."""
    assert stop_forward.seconds_since_inbox_received(SEAT) is None

    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)
    stop_forward.forward_stop({"transcript_path": transcript(tmp_path, [said("hi")])})

    body = [m for m in delivered(home) if m["to"] == ORCH][0]["body"]
    assert "since last inbox received: never" in body


def test_the_transcript_is_read_from_the_end_and_bounded(home, tmp_path):
    """A Stop hook must not cost more the longer the seat has worked."""
    filler = [said("x" * 2_000) for _ in range(400)]
    path = transcript(tmp_path, [*filler, said("the last thing I said")])
    assert Path(path).stat().st_size > stop_forward.TAIL_BYTES

    started = time.time()
    records = stop_forward._transcript_tail(path)
    elapsed = time.time() - started

    assert stop_forward.last_assistant_text(records) == "the last thing I said"
    assert len(records) < 400, "the whole file was parsed, not its tail"
    assert elapsed < 2.0


def test_the_quoted_text_is_capped(home, tmp_path):
    long = "y" * (stop_forward.TAIL_CHARS * 3)
    text = stop_forward.last_assistant_text([said(long)])
    assert len(text) == stop_forward.TAIL_CHARS


def test_removing_the_last_watcher_drops_the_seat(home):
    """CONTROL — the list must not fill with seats mapped to empty lists."""
    stop_forward.add_watch(SEAT, ORCH)
    assert SEAT in stop_forward.load()["watch"]

    stop_forward.remove_watch(SEAT, ORCH)
    assert SEAT not in stop_forward.load()["watch"]


def test_turning_the_flag_off_keeps_the_watch_list(home):
    """CONTROL — the flag and the list are separate state, per the card's order."""
    stop_forward.add_watch(SEAT, ORCH)
    stop_forward.set_enabled(True)
    stop_forward.set_enabled(False)

    data = stop_forward.load()
    assert data["enabled"] is False
    assert data["watch"][SEAT] == [ORCH]


# ── the two defects the tmp-dir arms could not see (live 2026-09-11) ─────────


def real_send(target: str, seat: str) -> dict:
    """The command shape a seat ACTUALLY sends, not the tidy one I first wrote.

    🔴 THE HEREDOC IS THE PART THAT BROKE IT, and my first attempt at this
    fixture did not have one — so a mutation reverting the fix to "first uuid
    wins" still passed, which is how I learned the reproduction was wrong before
    the commit rather than after. A report is written to a FILE first, and that
    body quotes the seat's own id in its trailers and its prose, so the seat id
    appears EARLIER in the command string than the send's target does. "The
    first uuid" then answers with the sender, every time.
    """
    body = (
        "cat > /tmp/report.txt <<'EOF'@"
        "T660 DONE. Agent-ID: {seat}@"
        "scratchpad: /tmp/claude/{seat}/x.txt@"
        "EOF@"
        "python -m liteharness.cli send {target} --body-file /tmp/report.txt --from {seat}"
    ).replace("@", "\n").format(seat=seat, target=target)
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Bash", "input": {"command": body}}]
        },
    }


def test_the_recipient_is_the_target_not_the_sender(home, tmp_path):
    """Live it reported the seat's OWN id for a message sent to the orchestrator."""
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)

    path = transcript(tmp_path, [real_send(ORCH, SEAT), said("sent it")])
    stop_forward.forward_stop({"transcript_path": path})

    body = [m for m in delivered(home) if m["to"] == ORCH][0]["body"]
    assert f"last tool call: inbox send -> {ORCH}" in body
    assert f"inbox send -> {SEAT}" not in body, "reported the sender as the recipient"


def test_the_seats_own_id_is_never_the_recipient(home):
    """Second guard: whatever the parse finds, 'sent to myself' is never the fact."""
    blob = f'{{"command": "liteharness.cli send --from {SEAT} /tmp/{SEAT}/x"}}'
    assert stop_forward._recipient_of(blob, SEAT) == "unknown"


def test_one_stop_forwards_once_even_though_the_hook_runs_twice(home, tmp_path):
    """settings.json Stop AND the plugin's hooks.json Stop both run `obs Stop`.

    Live this produced two byte-identical messages seconds apart. One stop is one
    event however many registrations observe it.
    """
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)
    records = [
        {"type": "assistant", "message": {"id": "msg_turn_one", "content": [
            {"type": "text", "text": "done"}]}},
    ]
    path = transcript(tmp_path, records)

    stop_forward.forward_stop({"transcript_path": path})
    stop_forward.forward_stop({"transcript_path": path})

    assert len([m for m in delivered(home) if m["to"] == ORCH]) == 1


def test_a_GENUINE_second_stop_still_forwards(home, tmp_path):
    """🔴 THE CONTROL, and the half Ryan explicitly wants kept.

    A seat that stops five times in a row IS the signal. A dedupe that keyed on
    content, or that simply suppressed a repeat, would pass the arm above and
    silently delete the case worth acting on.
    """
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)

    first = transcript(tmp_path, [{"type": "assistant", "message": {
        "id": "msg_turn_one", "content": [{"type": "text", "text": "same words"}]}}])
    stop_forward.forward_stop({"transcript_path": first})

    second = tmp_path / "t2.jsonl"
    second.write_text(json.dumps({"type": "assistant", "message": {
        "id": "msg_turn_two", "content": [{"type": "text", "text": "same words"}]}}),
        encoding="utf-8")
    stop_forward.forward_stop({"transcript_path": str(second)})

    assert len([m for m in delivered(home) if m["to"] == ORCH]) == 2


def test_a_Write_that_merely_mentions_the_send_command_is_not_a_send(home, tmp_path):
    """🔴 MEASURED, NOT IMAGINED. Scanning the blob for the command STRING read a
    Write of a test fixture as a send — 1 of 115 real send-shaped calls in a live
    transcript. A call is a send because of the TOOL it is, not because its text
    mentions one."""
    write = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Write", "input": {
            "file_path": "tests/x.py",
            "content": f"cmd = 'python -m liteharness.cli send {ORCH} hi'",
        }}]},
    }
    stop_forward.set_enabled(True)
    stop_forward.add_watch(SEAT, ORCH)

    stop_forward.forward_stop({"transcript_path": transcript(tmp_path, [write, said("wrote it")])})
    body = [m for m in delivered(home) if m["to"] == ORCH][0]["body"]
    assert "last tool call: not an inbox send" in body
