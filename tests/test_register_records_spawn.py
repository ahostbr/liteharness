"""T916-A — the SessionStart register records what the spawner sent.

Measured 2026-09-25 on PassLink-Turing 5f47aa9c (a split spawn with
--cognitive turing): the hook PRINTED "## Cognitive Architecture — turing", but
the presence file had no `cognitive` key. The parent was recorded all along,
under `spawned_by`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from liteharness import config, hooks

SEAT = "55555555-5555-4555-8555-555555555555"
LEADER = "86ab370d-b030-45f1-ae47-39c2aec4ded2"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: os.getpid())
    (tmp_path / "agents").mkdir()
    (tmp_path / "names").mkdir()
    return tmp_path


def register(source: str, **env_extra) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI"))
    }
    env.update(LITEHARNESS_CLI="claude-code", LITEHARNESS_MODEL="test-model",
               CLAUDE_CODE_SESSION_ID=SEAT, **env_extra)
    with mock.patch.dict(os.environ, env, clear=True):
        hooks._apply_hook_context({
            "session_id": SEAT, "source": source, "hook_event_name": "SessionStart",
            "transcript_path": str(Path("transcripts") / f"{SEAT}.jsonl"),
        })
        hooks.register_presence()
    return json.loads((config.HARNESS_ROOT / "agents" / f"{SEAT}.json").read_text(encoding="utf-8"))


def test_presence_records_the_cognitive_and_the_parent(root, tmp_path, capsys):
    arch = tmp_path / "turing.md"
    arch.write_text("# POLYMATHIC TURING", encoding="utf-8")
    row = register(
        "startup",
        LITEHARNESS_COGNITIVE_FILE=str(arch),
        LITEHARNESS_SPAWNED_BY=LEADER,
        LITEHARNESS_TIER="worker",
    )
    assert "## Cognitive Architecture — turing" in capsys.readouterr().out
    assert row["cognitive"] == "turing"
    assert row["spawned_by"] == LEADER


def test_a_resume_without_the_env_keeps_the_cognitive(root, tmp_path):
    arch = tmp_path / "turing.md"
    arch.write_text("x", encoding="utf-8")
    register("startup", LITEHARNESS_COGNITIVE_FILE=str(arch), LITEHARNESS_SPAWNED_BY=LEADER)
    row = register("resume")
    assert row["cognitive"] == "turing"
    assert row["spawned_by"] == LEADER


def test_no_architecture_records_empty_not_a_guess(root):
    assert register("startup")["cognitive"] == ""


# ── T916 item 3: a turn stamps turn_seen_at; nothing else does ─────────────────

def _beat(event: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "LITEHARNESS"))}
    env.update(CLAUDE_CODE_SESSION_ID=SEAT, LITEHARNESS_HOOK_EVENT=event)
    with mock.patch.dict(os.environ, env, clear=True):
        hooks.update_heartbeat(SEAT)
    return json.loads((config.HARNESS_ROOT / "agents" / f"{SEAT}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("event", ["UserPromptSubmit", "PostToolUse"])
def test_a_turn_event_stamps_turn_seen_at(root, event):
    register("startup")
    assert "turn_seen_at" not in json.loads(
        (config.HARNESS_ROOT / "agents" / f"{SEAT}.json").read_text(encoding="utf-8"))
    assert _beat(event).get("turn_seen_at")


def test_a_session_start_beat_is_not_a_turn(root):
    register("startup")
    assert "turn_seen_at" not in _beat("SessionStart")


def test_the_stamp_is_written_once_per_registration(root, monkeypatch):
    register("startup")
    writes = []
    real = config.merge_presence_fields

    def spy(path, updates):
        writes.append(dict(updates))
        return real(path, updates)

    monkeypatch.setattr(config, "merge_presence_fields", spy)
    first = _beat("PostToolUse")["turn_seen_at"]
    assert _beat("PostToolUse")["turn_seen_at"] == first
    assert sum("turn_seen_at" in w for w in writes) == 1
    # A new registration (resume, a re-spawn) clears it for the next first turn.
    assert "turn_seen_at" not in register("resume")


# ── T916-B: a manual `hooks register` says what it did; `doctrine` reloads ─────

def _hooks_main(monkeypatch, *argv, hook_input=None, **env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "LITEHARNESS"))}
    env.update(CLAUDE_CODE_SESSION_ID=SEAT, **env_extra)
    monkeypatch.setattr(hooks, "_read_hook_stdin", lambda: dict(hook_input or {}))
    monkeypatch.setattr(hooks.sys, "argv", ["hooks", *argv])
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            hooks.main()
        except SystemExit as exc:
            return exc.code
    return 0


def test_a_manual_register_refuses_loudly_and_writes_nothing(root, monkeypatch, capsys):
    code = _hooks_main(monkeypatch, "register",
                       LITEHARNESS_TRANSCRIPT_PATH=str(Path("t") / f"{SEAT}.jsonl"))
    assert code == 1
    assert "did NOTHING" in capsys.readouterr().err
    assert list((root / "agents").iterdir()) == []  # no model=unknown row


def test_control_a_subagent_sessionstart_stays_silent(root, monkeypatch, capsys):
    # A real hook call with no transcript_path is a sub-agent: quiet, no row.
    code = _hooks_main(monkeypatch, "register", hook_input={"session_id": SEAT, "hook_event_name": "SessionStart"})
    assert code == 0
    assert "did NOTHING" not in capsys.readouterr().err
    assert list((root / "agents").iterdir()) == []


def test_doctrine_reprints_tier_and_architecture_and_writes_nothing(root, tmp_path, monkeypatch, capsys):
    from liteharness import prompts
    arch = tmp_path / "turing.md"
    arch.write_text("# POLYMATHIC TURING body", encoding="utf-8")
    row = root / "agents" / f"{SEAT}.json"
    row.write_text(json.dumps({"agent_id": SEAT, "tier": "leader", "cognitive": "turing"}), encoding="utf-8")
    before = row.read_bytes()
    monkeypatch.setattr(prompts, "emit", lambda tier, litesuite_hint=None: print(f"PREAMBLE<{tier}>"))
    monkeypatch.setattr(prompts, "resolve_cognitive_file", lambda n, t: arch if n == "turing" else None)
    assert _hooks_main(monkeypatch, "doctrine") == 0
    out = capsys.readouterr().out
    assert "tier leader" in out and "PREAMBLE<leader>" in out
    assert "# POLYMATHIC TURING body" in out
    assert row.read_bytes() == before


# ── T916 B-1, fix cycle 2: the env pane never overwrites a known pane ─────────

def test_a_pane_set_by_its_owner_survives_an_inherited_env_on_compact(root):
    # Linus round 2, probe (ii). The seat first registers with an INHERITED
    # pane; its owner (register --canvas-session, or split adoption) then
    # writes the right one; a compact re-registration, whose env still holds
    # the inherited value, must not overwrite it.
    register("startup", LITESUITE_CANVAS_SESSION="pty-7-inherited")
    path = config.HARNESS_ROOT / "agents" / f"{SEAT}.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["canvas_session_id"] = "pty-3-mine"
    path.write_text(json.dumps(row), encoding="utf-8")
    assert register("compact", LITESUITE_CANVAS_SESSION="pty-7-inherited")["canvas_session_id"] == "pty-3-mine"


def test_control_a_resume_with_no_pane_env_keeps_the_known_pane(root):
    register("startup", LITESUITE_CANVAS_SESSION="pty-1-old")
    assert register("resume")["canvas_session_id"] == "pty-1-old"
