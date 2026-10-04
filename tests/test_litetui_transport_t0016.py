"""Fake sockets/presence prove local authentication and bounded reply correlation."""
import io
import json
import os
import socket

import pytest
from liteharness import cli, litetui_client as client

AID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def peer(monkeypatch, tmp_path):
    root = tmp_path / "harness"
    (root / "agents").mkdir(parents=True)
    token = tmp_path / "data" / "rpc" / AID / "nonce.json"
    token.parent.mkdir(parents=True)
    endpoint = {"agent_id": AID, "pid": 123, "nonce": "runtime-nonce", "host": "127.0.0.1", "port": 4101, "token_path": str(token)}
    token.write_text(json.dumps({"agent_id": AID, "pid": 123, "nonce": "runtime-nonce", "token": "f" * 64}))
    token.chmod(0o600)
    row = {"agent_id": AID, "cli": "litetui", "session_pid": 123, "litetui_rpc": endpoint}
    (root / "agents" / f"{AID}.json").write_text(json.dumps(row))
    monkeypatch.setattr(client.config, "get_root", lambda: root)
    monkeypatch.setattr(cli, "_live_owner_pid", lambda row: 123)
    monkeypatch.setattr(client.uuid, "uuid4", lambda: type("Id", (), {"hex": "correlation"})())
    return root, token, endpoint


class Socket:
    def __init__(self, frames):
        self.frames = frames
        self.sent = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, value): self.timeout = value
    def sendall(self, raw): self.sent.append(json.loads(raw))
    def makefile(self, mode):
        return io.BytesIO(b"".join((json.dumps(x) + "\n").encode() for x in self.frames))


def test_fake_socket_auth_and_correlated_jsonl(peer, monkeypatch):
    s = Socket([{"type": "authenticated", "agent_id": AID, "nonce": "runtime-nonce"},
                {"type": "response", "id": "correlation", "ok": True, "result": {"fake": True}}])
    monkeypatch.setattr(client.socket, "create_connection", lambda *a, **k: s)
    assert client.request(AID, {"type": "gui.state"}) == {"fake": True}
    assert s.sent == [{"agent_id": AID, "nonce": "runtime-nonce", "token": "f" * 64},
                      {"type": "gui.state", "id": "correlation"}]


@pytest.mark.parametrize("kind", ["auth", "correlation", "oversize"])
def test_invalid_peer_frames_rejected(peer, monkeypatch, kind):
    auth = {"type": "authenticated", "agent_id": AID, "nonce": "runtime-nonce"}
    result = {"type": "response", "id": "correlation", "ok": True}
    if kind == "auth": auth["nonce"] = "stale"
    if kind == "correlation": result["id"] = "other"
    if kind == "oversize": result["extra"] = "x" * client.MAX_FRAME
    s = Socket([auth, result])
    monkeypatch.setattr(client.socket, "create_connection", lambda *a, **k: s)
    with pytest.raises(ValueError):
        client.request(AID, {"type": "gui.state"})
    if kind == "auth": assert len(s.sent) == 1


@pytest.mark.parametrize("kind", ["remote", "pid", "token-identity", "dead"])
def test_stale_foreign_endpoint_never_connects(peer, monkeypatch, kind):
    root, token, endpoint = peer
    path = root / "agents" / f"{AID}.json"
    row = json.loads(path.read_text())
    if kind == "remote": row["litetui_rpc"]["host"] = "192.0.2.1"
    if kind == "pid": row["litetui_rpc"]["pid"] = 456
    if kind == "dead": monkeypatch.setattr(cli, "_live_owner_pid", lambda row: None)
    if kind == "token-identity":
        document = json.loads(token.read_text())
        document["nonce"] = "old"
        token.write_text(json.dumps(document))
    path.write_text(json.dumps(row))
    monkeypatch.setattr(client.socket, "create_connection", lambda *a, **k: pytest.fail("no connect"))
    with pytest.raises(ValueError):
        client.request(AID, {"type": "gui.state"})


def test_after_dispatch_timeout_is_ambiguous_not_retried(peer, monkeypatch):
    class Timed(Socket):
        def makefile(self, mode):
            class Stream:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def readline(self, limit):
                    if len(s.sent) == 1:
                        return (json.dumps({"type": "authenticated", "agent_id": AID, "nonce": "runtime-nonce"}) + "\n").encode()
                    raise socket.timeout()
            return Stream()
    s = Timed([])
    calls = []
    def connect(*a, **k):
        calls.append(a)
        return s
    monkeypatch.setattr(client.socket, "create_connection", connect)
    with pytest.raises(ValueError, match="ambiguous"):
        client.request(AID, {"type": "gui.state"})
    assert len(calls) == 1 and len(s.sent) == 2


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 301])
def test_timeout_bound_before_registry_or_network(timeout):
    with pytest.raises(ValueError, match="timeout"):
        client.request(AID, {"type": "gui.state"}, timeout=timeout)


@pytest.mark.parametrize("kind", ["presence-list", "token-list", "token-path-none", "nonce-none"])
def test_malformed_metadata_refuses_before_connection(peer, monkeypatch, kind):
    root, token, endpoint = peer
    path = root / "agents" / f"{AID}.json"
    row = json.loads(path.read_text())
    if kind == "presence-list":
        row = []
    elif kind == "token-list":
        token.write_text("[]")
    elif kind == "token-path-none":
        row["litetui_rpc"]["token_path"] = None
    else:
        row["litetui_rpc"]["nonce"] = None
    path.write_text(json.dumps(row))
    monkeypatch.setattr(client.socket, "create_connection", lambda *a, **k: pytest.fail("invalid metadata must not connect"))
    with pytest.raises(ValueError):
        client.request(AID, {"type": "gui.state"})
