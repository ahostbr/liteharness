"""T363: ordinary startup must not capture a later genuine resume's identity."""
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from liteharness import cli, config, hooks


STARTUP = "11111111-1111-4111-8111-111111111111"
RESUMED = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def identity_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: os.getpid())
    return tmp_path


def register(ident, source, *, explicit=False, cli_name="claude-code"):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI", "LITECODE"))}
    env.update(LITEHARNESS_CLI=cli_name, LITEHARNESS_MODEL="test-model")
    if cli_name == "claude-code":
        env["CLAUDE_CODE_SESSION_ID"] = ident
    if explicit:
        env["LITEHARNESS_AGENT_ID"] = ident
    with mock.patch.dict(os.environ, env, clear=True):
        hooks._apply_hook_context({
            "session_id": ident, "source": source, "hook_event_name": "SessionStart",
            "transcript_path": str(Path("transcripts") / f"{ident}.jsonl"),
        })
        hooks.register_presence()


def record(root, ident):
    return json.loads((root / "agents" / f"{ident}.json").read_text())


def test_real_resume_replaces_ordinary_startup(identity_root, capsys):
    register(STARTUP, "startup")
    capsys.readouterr()
    register(RESUMED, "resume")
    output = capsys.readouterr().out
    assert (identity_root / "agents" / f"{RESUMED}.json").exists()
    assert record(identity_root, RESUMED)["registration_source"] == "resume"
    assert cli._superseded_by_later_registration(STARTUP, record(identity_root, STARTUP))
    assert "resume supersedes this process's startup record" in output
    assert "authoritative (session payload)" not in output
    # A subsequent registration cannot bounce back to the retired startup id.
    register(RESUMED, "resume")
    assert not cli._superseded_by_later_registration(RESUMED, record(identity_root, RESUMED))


def test_explicit_cli_registration_is_protected(identity_root, capsys):
    register(STARTUP, "startup")
    cli.cmd_register(STARTUP, cli="claude-code", model="test-model", session_pid=os.getpid(), takeover=True)
    capsys.readouterr()
    register(RESUMED, "resume")
    output = capsys.readouterr().out
    assert not (identity_root / "agents" / f"{RESUMED}.json").exists()
    assert record(identity_root, STARTUP)["registration_source"] == "takeover"
    assert "explicit takeover" in output
    assert "authoritative (session payload)" not in output


def test_explicit_environment_override_is_protected(identity_root):
    register(STARTUP, "startup", explicit=True)
    register(RESUMED, "resume")
    assert not (identity_root / "agents" / f"{RESUMED}.json").exists()
    assert record(identity_root, STARTUP)["registration_source"] == "takeover"


def test_ordinary_new_seat_keeps_startup_identity(identity_root):
    register(STARTUP, "startup")
    assert record(identity_root, STARTUP)["registration_source"] == "startup"


def test_other_cli_is_not_adopted_into_claude_takeover(identity_root):
    # Codex desktop tasks can share one backend PID; PID equality is not identity.
    register(STARTUP, "startup", explicit=True)
    register(RESUMED, "resume", cli_name="codex-desktop")
    assert (identity_root / "agents" / f"{RESUMED}.json").exists()


def test_unknown_legacy_record_is_not_inferred_to_be_takeover(identity_root):
    register(STARTUP, "startup")
    path = identity_root / "agents" / f"{STARTUP}.json"
    data = record(identity_root, STARTUP)
    data.pop("registration_source", None)
    path.write_text(json.dumps(data))
    register(RESUMED, "resume")
    assert (identity_root / "agents" / f"{RESUMED}.json").exists()
