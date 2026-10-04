"""One fake peer per CLI verb: no real registry, bridge, seat or model calls."""
import json

import pytest
from liteharness import cli, litetui_client as client

SEAT = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("argv,command", [
    (["status"], {"type": "gui.state"}),
    (["load", "weights", "--ctx", "8192"], {"type": "gui.models.load", "slug": "weights", "ctx": 8192}),
    (["unload", "weights"], {"type": "gui.models.unload", "slug": "weights"}),
    (["model", "haiku"], {"type": "gui.models.select", "slug": "haiku"}),
    (["thinking", "high"], {"type": "gui.thinking.set", "level": "high"}),
    (["context", "4096"], {"type": "gui.context.set", "context": 4096}),
    (["backend", "claude"], {"type": "gui.backend.set", "name": "claude"}),
    (["engine", "start"], {"type": "gui.engine.start"}),
    (["engine", "stop"], {"type": "gui.engine.stop"}),
    (["engine", "status"], {"type": "gui.engine.status"}),
    (["compact"], {"type": "gui.conversations.compact"}),
    (["reconnect"], {"type": "gui.models.reconnect"}),
    (["send", "literal text"], {"type": "gui.prompt.submit", "message": "literal text"}),
    (["read"], {"type": "gui.conversations.read"}),
])
def test_one_fake_peer_arm_per_verb(monkeypatch, capsys, argv, command):
    calls = []
    def peer(agent, payload, **kw):
        calls.append((agent, payload, kw))
        return {"fake": True}
    monkeypatch.setattr(client, "request", peer)
    client.main(["--agent-id", SEAT, *argv])
    assert calls == [(SEAT, command, {"timeout": 30})]
    assert json.loads(capsys.readouterr().out) == {"fake": True}


def test_spawn_delegates_existing_owner(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: calls.append(kw))
    client.main(["spawn", "--pty", "--cwd", str(tmp_path), "--backend", "claude", "--model", "haiku",
                 "--thinking-level", "default", "--name", "Test", "--tier", "worker", "--spawned-by", SEAT])
    assert len(calls) == 1 and calls[0]["spawn_cli"] == "litetui" and calls[0]["pty_mode"]


def test_resume_reparent_delegates_without_registry_rewrite(monkeypatch):
    from liteharness import resume_seat
    monkeypatch.setattr(resume_seat, "lookup", lambda *args: pytest.fail("wrapper must not pre-resolve native resume"))
    calls = []
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: calls.append(kw))
    client.main(["spawn", "--resume", SEAT, "--kill-old", "--spawned-by", SEAT])
    assert calls[0]["resume_agent_id"] == SEAT and calls[0]["kill_old"] and calls[0]["spawned_by"] == SEAT
    assert calls[0]["split_mode"] and not calls[0]["pty_mode"]


def test_shared_prelaunch_haiku_high_refuses_before_bridge(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(client, "_parent_has_endpoint", lambda *a: True)
    monkeypatch.setattr(client, "request", lambda *a, **k: {"thinking": {"levels": ["default"]}})
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: pytest.fail("No launch before supported-level check"))
    with pytest.raises(ValueError, match="does not support"):
        client.main(["spawn", "--pty", "--cwd", str(tmp_path), "--backend", "claude",
                     "--model", "haiku", "--thinking-level", "high", "--spawned-by", SEAT])


def test_unknown_capability_and_old_parent_fail_loud(monkeypatch):
    monkeypatch.setattr(client, "_parent_has_endpoint", lambda *a: True)
    def absent(*args, **kwargs):
        raise ValueError("No live authenticated LiteTUI endpoint")
    monkeypatch.setattr(client, "request", absent)
    with pytest.raises(ValueError, match="No live authenticated"):
        client.validate_spawn(SEAT, "codex", "unknown", "high")


def test_help_lists_all_verbs(capsys):
    with pytest.raises(SystemExit) as exc:
        client.main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    for verb in ("spawn", "status", "load", "unload", "model", "thinking", "context", "backend", "engine", "compact", "reconnect", "send", "read"):
        assert verb in help_text


def test_invalid_context_never_connects(monkeypatch):
    monkeypatch.setattr(client, "request", lambda *a, **k: pytest.fail("invalid ctx must be local refusal"))
    with pytest.raises(SystemExit):
        client.main(["--agent-id", SEAT, "load", "weights", "--ctx", "0"])


def test_named_resume_delegates_without_raw_id_lookup(monkeypatch):
    from liteharness import resume_seat
    monkeypatch.setattr(resume_seat, "lookup", lambda *a: pytest.fail("name resolution belongs to resume owner"))
    calls = []
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: calls.append(kw))
    client.main(["spawn", "--resume", "NamedSeat"])
    assert calls[0]["resume_agent_id"] == "NamedSeat"
    assert calls[0]["split_mode"] and not calls[0]["kill_old"]


def test_top_level_cli_routes_same_client(monkeypatch, capsys):
    import sys
    calls = []
    monkeypatch.setattr(client, "request", lambda *a, **k: calls.append((a, k)) or {"fake": True})
    monkeypatch.setattr(sys, "argv", ["liteharness", "litetui", "--agent-id", SEAT, "status"])
    cli.main()
    assert calls[0][0] == (SEAT, {"type": "gui.state"})
    assert json.loads(capsys.readouterr().out) == {"fake": True}
