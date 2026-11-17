"""LiteSuite's GlobalInbox writes seat-messages as {payload: {text}} with an epoch-ms `ts`
and no top-level `body` or `timestamp` (the image editor's "Send to agent", 2026-11-17).
The hook's "message(s) received" block and `cli inbox` / `cli list` showed them blank."""
import json

import pytest

from liteharness import cli, config, hooks, inbox

# The exact shape of a live seat-message (inbox/done/737521df-...-1794925088663.json; user path generic).
SEAT_MESSAGE = {
    "id": "737521df-c6d9-4f32-9f7d-b6b13ffd1419-1794925088663",
    "from": "orchestrator-chat",
    "to": "318842aa-a477-450c-8e4d-60f255358916",
    "type": "seat-message",
    "payload": {
        "text": "Snip revision 0 (2160×1095)\nD:/seat-attachments/paste-1794925088655-1a78bc30-150f-4313-93fd-1157dcb38cea.png\n\n",
        "reply_to": "orchestrator-chat",
    },
    "ts": 1794925088663,
}
ME = SEAT_MESSAGE["to"]


@pytest.fixture
def maildir(tmp_path, monkeypatch):
    dirs = {name: tmp_path / name for name in ("new", "cur", "done", "tmp")}
    for d in dirs.values():
        d.mkdir()
    for attr, name in (("INBOX_NEW", "new"), ("INBOX_CUR", "cur"), ("INBOX_DONE", "done"), ("INBOX_TMP", "tmp")):
        monkeypatch.setattr(inbox, attr, dirs[name])
    (dirs["new"] / (SEAT_MESSAGE["id"] + ".json")).write_text(json.dumps(SEAT_MESSAGE), encoding="utf-8")
    monkeypatch.setattr(config, "get_agent_id", lambda: ME)
    return dirs


def test_helpers_read_the_globalinbox_shape():
    assert inbox.message_body(SEAT_MESSAGE).startswith("Snip revision 0")
    assert inbox.message_time(SEAT_MESSAGE) == "2026-11-17T14:18:08.663000+00:00"


def test_helpers_keep_the_cli_shape():
    msg = {"body": "plain", "timestamp": "2026-11-17T09:00:00+00:00", "payload": {"text": "ignored"}, "ts": 1}
    assert inbox.message_body(msg) == "plain"
    assert inbox.message_time(msg) == "2026-11-17T09:00:00+00:00"
    assert inbox.message_body({}) == "" and inbox.message_time({}) == ""
    assert inbox.message_time({"ts": True}) == ""


def test_cli_inbox_shows_body_and_time(maildir, capsys):
    cli.cmd_inbox(count=10, agent=ME)
    out = capsys.readouterr().out
    assert "Snip revision 0" in out
    assert "2026-11-17T14:18:08" in out


def test_hook_check_block_shows_body(maildir, monkeypatch, capsys):
    import liteharness.mod_inbox as mod_inbox
    monkeypatch.setattr(config, "merge_presence_fields", lambda *a, **k: None)
    monkeypatch.setattr(mod_inbox, "native_owner_active", lambda _id: False)
    monkeypatch.setattr(mod_inbox, "native_recovery_pending", lambda _id: False)
    for name in ("_a_live_watcher_is_attached",):
        monkeypatch.setattr(hooks, name, lambda _id: False)
    monkeypatch.setattr(hooks, "_should_check", lambda: True)
    for name in ("_mark_checked", "_refresh_presence_model", "_maybe_cleanup"):
        monkeypatch.setattr(hooks, name, lambda: None)
    hooks.check_inbox()
    out = capsys.readouterr().out
    assert "1 message(s) received" in out
    assert "Snip revision 0" in out
