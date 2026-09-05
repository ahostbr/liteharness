"""Recipient receipt, durable failure handling, and native-pipe integration."""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from liteharness.cli_scripts.codex import desktop_delivery as delivery


class Client:
    def __init__(self, fail=None):
        self.fail = fail
        self.sent = []

    def preflight(self):
        if self.fail == "preflight":
            raise delivery.DeliveryError("offline")

    def send(self, prompt):
        if self.fail == "not-submitted":
            raise delivery.NotSubmitted("connection failed before write")
        self.sent.append(prompt)
        if self.fail == "send":
            raise delivery.DeliveryError("reply lost after submit")


def envelope(root, ident="message-1", target="recipient", sender="sentinel"):
    folder = root / "inbox" / "new"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ident}.json"
    path.write_text(json.dumps({"id": ident, "to": target, "from": sender,
                                "body": "receipt-test-body", "type": "notification"}))
    return path


def test_app_acceptance_is_not_recipient_receipt(tmp_path, monkeypatch):
    client = Client()
    original = envelope(tmp_path)
    queue = delivery.DeliveryQueue(tmp_path, "recipient", client)
    queue.step()
    assert not original.exists()
    assert len(client.sent) == 1
    assert not list((tmp_path / "inbox" / "done").glob("*.json"))
    assert delivery.read_json(queue.state_path("message-1"))["state"] == "awaiting_ack"
    # A new watcher sees durable acceptance and must not submit again.
    restarted = delivery.DeliveryQueue(tmp_path, "recipient", client)
    restarted.step()
    assert len(client.sent) == 1
    monkeypatch.setenv("CODEX_THREAD_ID", "another-task")
    with pytest.raises(delivery.DeliveryError):
        delivery.acknowledge(tmp_path, "recipient", "message-1")
    monkeypatch.setenv("CODEX_THREAD_ID", "recipient")
    delivery.acknowledge(tmp_path, "recipient", "message-1")
    restarted.step()
    assert not list(queue.pending.glob("*.json"))
    assert delivery.read_json(queue.state_path("message-1"))["state"] == "acknowledged"
    assert len(list((tmp_path / "inbox" / "done").glob("*.json"))) == 1


@pytest.mark.parametrize("phase", ["submitting", "uncertain", "awaiting_ack"])
def test_restart_never_blindly_resubmits(tmp_path, phase):
    queue = delivery.DeliveryQueue(tmp_path, "recipient", Client())
    envelope(tmp_path)
    queue.collect()
    queue.save_state("message-1", phase)
    queue.step()
    assert queue.client.sent == []
    assert (queue.pending / "message-1.json").exists()


def test_preflight_failure_retains_mail_then_retries(tmp_path):
    client = Client("preflight")
    queue = delivery.DeliveryQueue(tmp_path, "recipient", client)
    envelope(tmp_path)
    queue.step()
    assert client.sent == []
    assert (queue.pending / "message-1.json").exists()
    assert delivery.read_json(queue.state_path("message-1"))["state"] == "pending"
    client.fail = None
    queue.save_state("message-1", "pending", retry_at=0)
    queue.step()
    assert len(client.sent) == 1


def test_uncertain_submission_preserved_and_acknowledgeable(tmp_path, monkeypatch):
    client = Client("send")
    queue = delivery.DeliveryQueue(tmp_path, "recipient", client)
    envelope(tmp_path)
    queue.step()
    assert delivery.read_json(queue.state_path("message-1"))["state"] == "uncertain"
    queue.step()
    assert len(client.sent) == 1
    monkeypatch.setenv("CODEX_THREAD_ID", "recipient")
    delivery.acknowledge(tmp_path, "recipient", "message-1")
    queue.step()
    assert delivery.read_json(queue.state_path("message-1"))["state"] == "acknowledged"


def test_known_no_submission_can_retry(tmp_path):
    client = Client("not-submitted")
    queue = delivery.DeliveryQueue(tmp_path, "recipient", client)
    envelope(tmp_path)
    queue.step()
    assert client.sent == []
    assert delivery.read_json(queue.state_path("message-1"))["state"] == "pending"
    client.fail = None
    queue.save_state("message-1", "pending", retry_at=0)
    queue.step()
    assert len(client.sent) == 1


def test_recipient_filter_nested_body_broadcast_and_duplicate(tmp_path):
    client = Client()
    queue = delivery.DeliveryQueue(tmp_path, "recipient", client)
    wrong = envelope(tmp_path, "wrong", "other")
    own = envelope(tmp_path, "own", sender="recipient")
    broadcast = envelope(tmp_path, "broadcast-id", "broadcast")
    nested = envelope(tmp_path, "nested")
    msg = delivery.read_json(nested)
    msg.pop("body")
    msg["payload"] = {"text": "NESTED-BODY"}
    nested.write_text(json.dumps(msg))
    queue.step()
    queue.step()
    assert wrong.exists() and own.exists() and broadcast.exists()
    assert len(client.sent) == 2
    assert any("NESTED-BODY" in prompt for prompt in client.sent)
    envelope(tmp_path, "nested")  # Duplicate id cannot start a second user turn.
    queue.step()
    assert len(client.sent) == 2


def test_unsent_ack_and_path_traversal_rejected(tmp_path, monkeypatch):
    queue = delivery.DeliveryQueue(tmp_path, "recipient", Client())
    queue.save_state("pending-id", "pending")
    monkeypatch.setenv("CODEX_THREAD_ID", "recipient")
    with pytest.raises(delivery.DeliveryError):
        delivery.acknowledge(tmp_path, "recipient", "pending-id")
    with pytest.raises(delivery.DeliveryError):
        delivery.acknowledge(tmp_path, "recipient", "../outside")
    with pytest.raises(ValueError):
        delivery.private_dir(tmp_path, "../outside")


def test_transport_requires_calling_task(monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "other")
    with pytest.raises(delivery.DeliveryError, match="mismatch"):
        delivery.DesktopClient("recipient", "real-turn")


def test_claude_hook_delivery_unchanged(tmp_path, monkeypatch, capsys):
    from liteharness import hooks, inbox
    from liteharness.cli_scripts.codex import liteharness_watcher_supervisor as supervisor
    monkeypatch.setattr(hooks, "_is_codex_hook_runtime", lambda: False)
    monkeypatch.setattr(hooks.config, "get_agent_id", lambda: "recipient")
    monkeypatch.setattr(hooks, "_should_check", lambda: True)
    monkeypatch.setattr(hooks, "_mark_checked", lambda: None)
    monkeypatch.setattr(hooks, "_refresh_presence_model", lambda: None)
    def unexpected(*args):
        raise AssertionError("Claude hook consulted Codex delivery ownership")
    monkeypatch.setattr(supervisor, "desktop_owner_active", unexpected)
    for name in ["NEW", "CUR", "DONE"]:
        monkeypatch.setattr(inbox, f"INBOX_{name}", tmp_path / "inbox" / name.lower())
    envelope(tmp_path)
    hooks.check_inbox()
    assert "receipt-test-body" in capsys.readouterr().out
    assert (tmp_path / "inbox" / "done" / "message-1.json").exists()


SERVER = r"""
const net=require('node:net'),fs=require('node:fs');
const [pipe,proof,ready]=process.argv.slice(1);
const server=net.createServer(s=>{
 let b=Buffer.alloc(0);
 s.on('data',d=>{b=Buffer.concat([b,d]);if(b.length<4||b.length<4+b.readUInt32LE(0))return;
  const q=JSON.parse(b.subarray(4,4+b.readUInt32LE(0)));let result;
  if(q.method==='tools/list') {
   if(q.params.threadStartKind!=='default') throw Error('invalid discovery schema');
   result={tools:['read_thread','send_message_to_thread'].map(name=>({namespace:'codex_app',name}))};
  }
  else {
   if(q.params.threadId!=='recipient'||q.params.arguments.threadId!=='recipient') throw Error('wrong target');
   const payload=q.params.tool==='read_thread' ? {thread:{id:'recipient'},turns:[{id:'real-current-turn'}]} : {threadId:'recipient'};
   if(q.params.tool==='send_message_to_thread')fs.appendFileSync(proof,JSON.stringify(q)+'\n');
   result={success:true,contentItems:[{type:'inputText',text:JSON.stringify(payload)}]};
  }
  const body=Buffer.from(JSON.stringify({jsonrpc:'2.0',id:q.id,result})),h=Buffer.alloc(4);h.writeUInt32LE(body.length);
  const frame=Buffer.concat([h,body]); s.write(frame.subarray(0,2));setTimeout(()=>s.end(frame.subarray(2)),5);
 });s.on('error',()=>{});
});server.listen(pipe,()=>fs.writeFileSync(ready,'ready'));
"""


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError("condition not reached")


def test_attached_watcher_native_pipe_ack_and_hook_exclusion(tmp_path):
    node = os.environ.get("CODEX_MCP_NODE_PATH") or shutil.which("node")
    if not node:
        pytest.skip("Node runtime required for real native pipe fixture")
    pipe = (r"\\.\pipe\liteharness-test-" + uuid.uuid4().hex) if os.name == "nt" else str(tmp_path / "pipe.sock")
    proof, ready = tmp_path / "proof.jsonl", tmp_path / "ready"
    server = subprocess.Popen([node, "-e", SERVER, pipe, str(proof), str(ready)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    env = {**os.environ, "CODEX_THREAD_ID": "recipient", "LITEHARNESS_AGENT_ID": "recipient",
           "CODEX_APP_TOOLS_PIPE_PATH": pipe, "CODEX_MCP_NODE_PATH": node, "PYTHONIOENCODING": "utf-8"}
    command = [sys.executable, "-u", "-m", "liteharness.cli_scripts.codex.liteharness_watcher_supervisor",
               "--root", str(tmp_path), "--agent-id", "recipient", "--turn-id", "real-originating-turn"]
    watcher = None
    try:
        wait_for(ready.exists)
        watcher = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        record = tmp_path / "codex_sessions" / "monitors" / "recipient.json"
        wait_for(record.exists)
        duplicate = subprocess.run(command, env=env, capture_output=True, timeout=8)
        assert duplicate.returncode == 3
        from liteharness.cli_scripts.codex.liteharness_watcher_supervisor import desktop_owner_active
        assert desktop_owner_active(tmp_path, "recipient")
        assert not desktop_owner_active(tmp_path, "other")
        envelope(tmp_path)
        state = tmp_path / "codex_sessions" / "delivery" / "recipient" / "ledger" / "message-1.json"
        wait_for(lambda: state.exists() and delivery.read_json(state)["state"] == "awaiting_ack")
        submitted = [json.loads(line) for line in proof.read_text().splitlines()]
        assert len(submitted) == 1
        assert submitted[0]["params"]["turnId"] == "real-current-turn"
        assert "receipt-test-body" in submitted[0]["params"]["arguments"]["prompt"]
        # Real hook invocation must leave wake-owned mail alone.
        unread = envelope(tmp_path, "hook-race")
        hook = subprocess.run([sys.executable, "-c", (
            "from pathlib import Path; from liteharness.cli_scripts.codex.liteharness_watcher_supervisor import configure_root; "
            f"configure_root(Path({str(tmp_path)!r})); from liteharness.hooks import check_inbox; check_inbox()"
        )], env=env, capture_output=True, timeout=8)
        assert hook.returncode == 0 and b"receipt-test-body" not in hook.stdout
        assert not (tmp_path / "inbox" / "done" / unread.name).exists()
        ack = subprocess.run([sys.executable, "-m", "liteharness.cli_scripts.codex.desktop_delivery",
                              "--root", str(tmp_path), "--agent-id", "recipient", "--ack", "message-1"],
                             env=env, capture_output=True, timeout=8)
        assert ack.returncode == 0, ack.stderr
        wait_for(lambda: delivery.read_json(state)["state"] == "acknowledged")
    finally:
        if watcher:
            watcher.terminate(); watcher.communicate(timeout=8)
        server.terminate(); server.communicate(timeout=8)
