"""T0678 WS2: clerk owns its identity/mailbox; tests never launch a seat."""
import json
import os
from datetime import datetime, timezone

import pytest

from liteharness import clerk, cli, config, inbox


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / ".liteharness"
    root.mkdir()
    monkeypatch.setattr(config, "HARNESS_ROOT", root)
    monkeypatch.setattr(config, "get_agent_id", lambda: "clerk-seat")
    for name in ("new", "cur", "done", "tmp"):
        monkeypatch.setattr(inbox, "INBOX_" + name.upper(), root / "inbox" / name)
    (root / "agents").mkdir()
    (root / "agents/orch.json").write_text(json.dumps({"agent_id": "orch", "name": "Coordinator"}), encoding="utf-8")
    return root


def cfg(root):
    return json.loads((root / "clerk.json").read_text(encoding="utf-8"))


def register(root, agent="clerk-seat", **changes):
    now = datetime.now(timezone.utc).isoformat()
    row = {"agent_id": agent, "name": "Clerk", "spawned_by": "orch", "model": "gpt-6-astra",
           "session_pid": os.getpid(), "registered_at": now, "last_seen": now}
    row.update(changes)
    (root / "agents" / f"{agent}.json").write_text(json.dumps(row), encoding="utf-8")


def enable_with_seat(root):
    clerk.enable("orch")
    data = cfg(root)
    data["clerk_id"] = "clerk-seat"
    (root / "clerk.json").write_text(json.dumps(data), encoding="utf-8")
    register(root)


def test_off_by_default_enable_disable_does_not_spawn(root, monkeypatch):
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: pytest.fail("enable/disable may not spawn"))
    assert clerk.status()["enabled"] is False
    assert not (root / "clerk.json").exists()
    clerk.enable("orch")
    assert cfg(root)["enabled"] is True
    assert not cfg(root).get("clerk_id")
    assert cfg(root)["direct_senders"] == []
    clerk.disable()
    assert cfg(root)["enabled"] is False


def test_the_shipped_default_names_no_direct_sender_and_a_local_list_is_kept(root, monkeypatch):
    """Whose sources bypass the clerk is one machine's fact: the package ships none (maintainer, 2026-11-11: "blank")."""
    from pathlib import Path
    assert clerk.DIRECT_SENDERS == []
    shipped = Path(clerk.__file__).read_text(encoding="utf-8")
    assert "DIRECT_SENDERS: list[str] = []" in shipped, "the default must be an empty literal, not a computed list"
    clerk.enable("orch")
    assert cfg(root)["direct_senders"] == []
    assert clerk_route_accepts(root), "an empty list is a valid configuration, not a disabled clerk"

    # A machine that already lists its own sources keeps them through enable and disable.
    data = cfg(root)
    data["direct_senders"] = ["my-voice-source", "my-page-source"]
    (root / "clerk.json").write_text(json.dumps(data), encoding="utf-8")
    clerk.disable()
    clerk.enable("orch")
    assert cfg(root)["direct_senders"] == ["my-voice-source", "my-page-source"]


def clerk_route_accepts(root):
    from liteharness import clerk_route
    data = cfg(root)
    data["clerk_id"] = "clerk-seat"
    (root / "clerk.json").write_text(json.dumps(data), encoding="utf-8")
    loaded = clerk_route.load_clerk_config()
    (root / "clerk.json").write_text(json.dumps({k: v for k, v in data.items() if k != "clerk_id"}), encoding="utf-8")
    return loaded is not None and loaded["direct_senders"] == []


@pytest.mark.parametrize("senders,expected", [([], "(none configured)"),
                                              (["my-voice-source", "my-page-source"], "(`my-voice-source`, `my-page-source`)")])
def test_the_role_names_this_machines_direct_senders_and_no_one_elses(root, monkeypatch, senders, expected):
    clerk.enable("orch")
    data = cfg(root)
    data["direct_senders"] = senders
    (root / "clerk.json").write_text(json.dumps(data), encoding="utf-8")
    seen = []
    def spawn(**kw):
        seen.append(kw["prompt"])
        register(root)
    monkeypatch.setattr(cli, "cmd_spawn", spawn)
    clerk.start(pane="clerk-pane", spawn_cli="litetui", model="gpt-6-astra", cwd=str(root.parent))
    assert "configuration " + expected + ";" in " ".join(seen[0].split())
    assert "<direct senders>" not in seen[0]


@pytest.mark.parametrize("route,model,backend", [("litetui", "gpt-6-astra", "codex"),
    ("litetui", "gpt-6.1-sol", "codex"), ("claude-code", "claude-sonnet-5-5", None),
    ("claude-code", "claude-haiku-5-5", None)])
def test_start_uses_existing_spawn_then_commits_registered_identity(root, monkeypatch, route, model, backend):
    clerk.enable("orch")
    seen = []
    def spawn(**kw):
        seen.append(kw)
        assert not cfg(root).get("clerk_id")
        assert "Clerk (not Coordinator):" in kw["prompt"]
        if route == "litetui":
            from pathlib import Path
            assert "Never approve, grant, merge, assign or decide." in Path(kw["system_prompt_file"]).read_text(encoding="utf-8")
        register(root, model=model)
    monkeypatch.setattr(cli, "cmd_spawn", spawn)
    result = clerk.start(pane="clerk-pane", spawn_cli=route, model=model, cwd=str(root.parent))
    assert result["clerk_id"] == "clerk-seat"
    assert cfg(root)["clerk_id"] == "clerk-seat"
    assert seen[0]["spawn_cli"] == ("claude" if route == "claude-code" else "litetui")
    assert seen[0]["backend"] == backend
    # cmd_spawn refuses --thinking-level on a fresh Claude spawn (cli.py "require --resume or --convo");
    # the mocked spawn above takes **kw and cannot see that, so pin the value it is given.
    assert seen[0]["thinking_level"] == ("high" if route == "litetui" else None)
    assert seen[0]["split_mode"] is True
    assert seen[0]["split_pane"] == "clerk-pane"
    assert seen[0]["cognitive"] == "aurelius"
    assert seen[0]["cwd"] == str(root.parent)


@pytest.mark.parametrize("pane_args", [[], ["--pane", ""], ["--pane", "   "]])
def test_real_cli_requires_explicit_nonempty_pane_before_spawn(root, pane_args):
    import subprocess
    import sys
    from pathlib import Path

    clerk.enable("orch")
    repo = Path(__file__).resolve().parents[1]
    marker = root / "spawn-called"
    probe = '''
import sys
from pathlib import Path
from liteharness import cli, config
# Exercise the actual CLI entry/dispatch/parser; replace only the dangerous
# external spawn boundary, so the red run cannot launch an actual seat.
def forbidden_spawn(**kwargs):
    (config.get_root() / "spawn-called").write_text("called", encoding="utf-8")
    raise SystemExit(97)
cli.cmd_spawn = forbidden_spawn
sys.argv = ["liteharness", "clerk", "start", *sys.argv[1:]]
cli.main()
'''
    result = subprocess.run([sys.executable, "-c", probe, *pane_args], cwd=repo,
        env={**os.environ, "HOME": str(root.parent), "USERPROFILE": str(root.parent), "PYTHONPATH": str(repo)},
        capture_output=True, text=True, timeout=10)
    assert not marker.exists(), f"CLI reached spawn without an explicit pane: {result.returncode}"
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--pane" in result.stderr
    assert not cfg(root).get("clerk_id")


def test_cli_explicit_pane_reaches_existing_spawn_argument(root, monkeypatch):
    clerk.enable("orch")
    seen = []
    def spawn(**kwargs):
        seen.append(kwargs)
        register(root)
    monkeypatch.setattr(cli, "cmd_spawn", spawn)
    clerk.main(["start", "--pane", "clerk-pane", "--cwd", str(root.parent)])
    assert seen[0]["split_pane"] == "clerk-pane"
    assert cfg(root)["clerk_id"] == "clerk-seat"


def test_start_refuses_disallowed_model_before_spawn(root, monkeypatch):
    clerk.enable("orch")
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: pytest.fail("disallowed model launched"))
    with pytest.raises(ValueError, match="Allowed models"):
        clerk.start(pane="clerk-pane", model="claude-opus-9", cwd=str(root.parent))
    assert not cfg(root).get("clerk_id")


def test_local_model_is_user_configurable(root, monkeypatch):
    clerk.enable("orch")
    data = cfg(root)
    data["allowed_models"].append("my-local-model")
    (root / "clerk.json").write_text(json.dumps(data), encoding="utf-8")
    def spawn(**kw):
        assert kw["backend"] == "lmstudio"
        register(root, model="my-local-model")
    monkeypatch.setattr(cli, "cmd_spawn", spawn)
    assert clerk.start(pane="clerk-pane", model="my-local-model", backend="lmstudio", cwd=str(root.parent))["clerk_id"] == "clerk-seat"


def test_live_seat_is_not_replaced(root, monkeypatch):
    enable_with_seat(root)
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: pytest.fail("live seat replaced"))
    with pytest.raises(ValueError, match="already online"):
        clerk.start(pane="clerk-pane", cwd=str(root.parent))


def test_failed_spawn_never_enables_routing_to_unregistered_id(root, monkeypatch):
    clerk.enable("orch")
    def spawn(**kw):
        raise SystemExit(2)
    monkeypatch.setattr(cli, "cmd_spawn", spawn)
    with pytest.raises(SystemExit):
        clerk.start(pane="clerk-pane", cwd=str(root.parent))
    assert not cfg(root).get("clerk_id")


def test_offline_seat_resumes_same_identity(root, monkeypatch):
    enable_with_seat(root)
    register(root, exited_at="gone")
    def spawn(**kw):
        assert kw["resume_agent_id"] == "clerk-seat"
        assert kw["split_pane"] == "clerk-pane"
        register(root)
    monkeypatch.setattr(cli, "cmd_spawn", spawn)
    assert clerk.start(pane="clerk-pane", cwd=str(root.parent))["clerk_id"] == "clerk-seat"


def test_forward_uses_own_id_verbatim_body_and_readback_digest(root):
    enable_with_seat(root)
    original = "MR: request merge\n  code `$x` café\n\n"
    msg_id = inbox.send("worker", "orch", original)
    pending = inbox.poll("clerk-seat", include_broadcast=False)
    assert len(pending) == 1
    forwarded_id = clerk.forward(msg_id)
    received = inbox.poll("orch", include_broadcast=False)
    assert len(received) == 1
    msg = received[0]
    assert msg["id"] == forwarded_id
    assert msg["from"] == "clerk-seat"
    assert msg["forwarded_for"] == "worker"
    assert msg["reply_to"] == msg_id
    assert msg["body"] == f"Clerk (not Coordinator):\n[FWD from worker · msg {msg_id}]\n" + original
    # No extra reader/claim on the orchestrator or clerk's mailbox.
    assert len(inbox.poll("clerk-seat", include_broadcast=False)) == 1
    assert not list(inbox.INBOX_CUR.glob("*.json"))
    digest = [json.loads(line) for line in (root / "clerk/digest-orch.jsonl").read_text(encoding="utf-8").splitlines()]
    assert digest[0]["msg_id"] == msg_id
    assert digest[0]["action"] == "forwarded"
    assert len(digest[0]["summary"]) <= 140
    # Sequential retry reuses the actual existing outbound receipt, no duplicate send.
    assert clerk.forward(msg_id) == forwarded_id
    assert len(inbox.poll("orch", include_broadcast=False)) == 1


def test_forward_refuses_other_identity_and_foreign_mail(root, monkeypatch):
    enable_with_seat(root)
    mine = inbox.send("worker", "clerk-seat", "hello")
    monkeypatch.setattr(config, "get_agent_id", lambda: "not-clerk")
    with pytest.raises(ValueError, match="own identity"):
        clerk.forward(mine)
    monkeypatch.setattr(config, "get_agent_id", lambda: "clerk-seat")
    foreign = inbox.send("worker", "somebody-else", "private")
    with pytest.raises(ValueError, match="addressed to the Clerk"):
        clerk.forward(foreign)
    assert inbox.poll("orch", include_broadcast=False) == []


def test_digest_records_actions_without_sending_notices(root):
    enable_with_seat(root)
    msg_id = inbox.send("worker", "clerk-seat", "[LiteTUI error] noisy")
    clerk.record_digest(msg_id, "digested", "x" * 150)
    rows = (root / "clerk/digest-orch.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    assert row["action"] == "digested"
    assert len(row["summary"]) == 140
    assert inbox.poll("orch", include_broadcast=False) == []

@pytest.mark.parametrize("options", [
    {"spawn_cli": "codex"}, {"spawn_cli": "litetui", "model": "claude-sonnet-5-5"},
    {"spawn_cli": "claude-code", "model": "gpt-6-astra"},
    {"spawn_cli": "claude-code", "backend": "codex"},
    {"model": "gpt-6-astra", "backend": "lmstudio"},
])
def test_unsupported_route_never_launches(root, monkeypatch, options):
    clerk.enable("orch")
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: pytest.fail("unsupported route launched"))
    with pytest.raises(ValueError):
        clerk.start(pane="clerk-pane", cwd=str(root.parent), **options)


def test_unconfirmed_spawn_print_is_not_a_registration(root, monkeypatch):
    clerk.enable("orch")
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: print("Spawned fake-id successfully"))
    with pytest.raises(ValueError, match="confirmed Clerk registration"):
        clerk.start(pane="clerk-pane", cwd=str(root.parent))
    assert not cfg(root).get("clerk_id")


def test_forward_ts_payload_preserves_text(root):
    enable_with_seat(root)
    inbox.ensure_dirs()
    msg = {"id": "ts-original", "from": "worker", "to": "clerk-seat", "ts": 1,
           "payload": {"text": "build request\n  do not edit me\n"}}
    (inbox.INBOX_DONE / "ts-original.json").write_text(json.dumps(msg), encoding="utf-8")
    clerk.forward("ts-original")
    received = inbox.poll("orch", include_broadcast=False)[0]
    assert received["body"].split("\n", 2)[2] == msg["payload"]["text"]
    assert received["forwarded_for"] == "worker"


def test_forward_retry_repairs_digest_without_duplicate_mail(root, monkeypatch):
    enable_with_seat(root)
    msg_id = inbox.send("worker", "clerk-seat", "merge request")
    real = clerk._digest
    def fail(*args, **kw):
        raise OSError("controlled digest write failure")
    monkeypatch.setattr(clerk, "_digest", fail)
    with pytest.raises(ValueError, match="delivered, but digest failed"):
        clerk.forward(msg_id)
    monkeypatch.setattr(clerk, "_digest", real)
    clerk.forward(msg_id)
    assert len(inbox.poll("orch", include_broadcast=False)) == 1
    rows = (root / "clerk/digest-orch.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["action"] == "forwarded"


def test_real_cli_dispatch_status_enable_disable_and_forward(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    home = tmp_path / "cli-home"
    home.mkdir()
    root = home / ".liteharness"
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONPATH": str(repo),
           "LITEHARNESS_AGENT_ID": "clerk-seat"}
    def run(*args):
        result = subprocess.run([sys.executable, "-m", "liteharness.cli", "clerk", *args], cwd=repo,
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return result.stdout
    assert json.loads(run("status"))["enabled"] is False
    run("enable", "--for", "orch")
    data = json.loads((root / "clerk.json").read_text())
    data["clerk_id"] = "clerk-seat"
    (root / "clerk.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "inbox/done").mkdir(parents=True)
    (root / "inbox/done/source.json").write_text(json.dumps({"id": "source", "from": "worker", "to": "clerk-seat", "body": "MR original body\n"}), encoding="utf-8")
    run("forward", "source")
    receipt = json.loads(next((root / "inbox/new").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["from"] == "clerk-seat"
    assert receipt["to"] == "orch"
    assert receipt["body"].split("\n", 2)[2] == "MR original body\n"
    assert receipt["forwarded_for"] == "worker"
    run("disable")
    assert json.loads((root / "clerk.json").read_text())["enabled"] is False


def test_forward_receipt_passes_independent_audit(root):
    from scripts.clerk_audit import audit
    enable_with_seat(root)
    msg_id = inbox.send("worker", "orch", "MR ready; please decide\n")
    clerk.forward(msg_id)
    counts = audit(root, "2000-01-01T00:00:00+00:00")
    assert counts == {"reached_sentinel": 1, "handled": 0, "forwarded": 1, "never_list_violations": 0}


def test_disable_during_spawn_is_not_overwritten(root, monkeypatch):
    clerk.enable("orch")
    def spawn(**kw):
        register(root)
        clerk.disable()
    monkeypatch.setattr(cli, "cmd_spawn", spawn)
    with pytest.raises(ValueError, match="changed during spawn"):
        clerk.start(pane="clerk-pane", cwd=str(root.parent))
    assert cfg(root)["enabled"] is False
    assert not cfg(root).get("clerk_id")


def test_overlapping_forward_is_refused_then_safe_to_retry(root, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    enable_with_seat(root)
    msg_id = inbox.send("worker", "clerk-seat", "merge original")
    sending, release = Event(), Event()
    real_send = inbox.send
    first = True
    def delayed_send(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            sending.set()
            assert release.wait(5)
        return real_send(*args, **kwargs)
    monkeypatch.setattr(inbox, "send", delayed_send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(clerk.forward, msg_id)
        assert sending.wait(5)
        try:
            with pytest.raises(OSError):
                clerk.forward(msg_id)
        finally:
            release.set()
        forwarded_id = task.result(timeout=5)
    assert clerk.forward(msg_id) == forwarded_id
    assert len(inbox.poll("orch", include_broadcast=False)) == 1
