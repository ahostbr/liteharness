"""T0043: compact tier cards and warn-only loop guards, isolated from the fleet."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from liteharness import cli, config, hooks, prompts

SEAT = "77777777-7777-4777-8777-777777777777"
ORCH = "88888888-8888-4888-8888-888888888888"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path / "harness")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "harness" / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: os.getpid())
    # inbox binds its maildir to the REAL ~/.liteharness/inbox at import time;
    # without this, the announce under test lands in the live fleet's inbox.
    from liteharness import inbox
    ib = tmp_path / "harness" / "inbox"
    for name, sub in (("INBOX_ROOT", ""), ("INBOX_NEW", "new"), ("INBOX_CUR", "cur"),
                      ("INBOX_DONE", "done"), ("INBOX_TMP", "tmp")):
        monkeypatch.setattr(inbox, name, ib / sub if sub else ib)
    for d in ("agents", "names", "inbox/new", "inbox/cur", "inbox/done", "inbox/tmp"):
        (tmp_path / "harness" / d).mkdir(parents=True, exist_ok=True)
    return tmp_path / "harness"


def _env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI"))}
    env.update(LITEHARNESS_CLI="claude-code", LITEHARNESS_MODEL="test-model",
               LITEHARNESS_NO_ANNOUNCE="1", **extra)
    return env


def _register(source: str, seat: str = SEAT, **extra):
    with mock.patch.dict(os.environ, _env(CLAUDE_CODE_SESSION_ID=seat, **extra), clear=True):
        hooks._apply_hook_context({"session_id": seat, "source": source, "hook_event_name": "SessionStart",
                                   "transcript_path": str(Path("t") / f"{seat}.jsonl")})
        hooks.register_presence()


# ── 2. the tier card ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def prompt_library(tmp_path, monkeypatch):
    library = tmp_path / "prompt library"
    for name in {*prompts.TIER_FILES.values(), *prompts.ALWAYS_FILES}:
        path = library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Test doctrine", encoding="utf-8")
    monkeypatch.setattr(prompts, "resolve_prompts_dir", lambda: (library, "isolated test"))
    return library


TIERS = ("orchestrator", "leader", "worker", "thinker", "reviewer", "librarian", "no-such-tier")


@pytest.mark.parametrize("tier", TIERS)
def test_every_card_fits_2_kb_and_points_at_the_full_doctrine(tier):
    card = prompts.tier_card(tier)
    assert len(card.encode("utf-8")) <= 2048
    assert "python -m liteharness.hooks doctrine" in card
    assert "Full doctrine: " in card
    assert ("THE LOOP" in card) == (tier == "orchestrator")  # the orchestrator 7a47fe07 (1)


def test_every_path_the_card_names_exists(prompt_library):
    # Knuth M-1: "bootstrap-harness.md beside it" was false for every tier whose
    # doctrine lives in preambles/. The card now names absolute paths; each must exist.
    for tier in ("orchestrator", "leader", "worker", "thinker", "reviewer"):
        line = prompts.tier_card(tier).splitlines()[-1]
        paths = [prompt_library / prompts.TIER_FILES[tier],
                 prompt_library / prompts.ALWAYS_FILES[0]]
        for path in paths:
            assert str(path) in line, (tier, path, line)
            assert path.is_absolute() and path.is_file(), (tier, path)


def test_the_rules_file_carries_its_ratification_label():
    data = json.loads(prompts.TIER_CARDS_FILE.read_text(encoding="utf-8"))
    assert "Orchestrator ratification (c119d2af), not the user's words" in data["_provenance"]
    assert data["ratified"] is True and "7a47fe07" in data["ratified_by"]
    for entry in [*data["loop"], *(r for t in data["tiers"].values() for r in t["rules"])]:
        assert entry["source"], entry  # every rule names where it came from


def test_a_compaction_reinjects_the_card_not_the_doctrine(root, monkeypatch, capsys):
    monkeypatch.setattr(prompts, "emit", lambda tier, litesuite_hint=None: print("FULL-DOCTRINE"))
    _register("startup", LITEHARNESS_TIER="leader")
    boot = capsys.readouterr().out
    assert "FULL-DOCTRINE" in boot and "Inter-agent messaging active" in boot  # control
    _register("compact", LITEHARNESS_TIER="leader")
    out = capsys.readouterr().out
    assert "## Tier card: leader" in out
    assert "FULL-DOCTRINE" not in out and "Inter-agent messaging active" not in out
    assert len(out.encode("utf-8")) <= prompts.TIER_CARD_MAX_BYTES + 400  # card + one identity line


# ── 3. the warn-only guard ────────────────────────────────────────────────────

def _seat(root, agent_id, tier, **extra):
    (root / "agents" / f"{agent_id}.json").write_text(
        json.dumps({"agent_id": agent_id, "tier": tier, **extra}), encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    return tmp_path / "repo"


def _guard(root, monkeypatch, seat_tier, tool, tool_input):
    _seat(root, SEAT, seat_tier)
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", SEAT)
    return hooks.pretooluse_guard({"tool_name": tool, "tool_input": tool_input})


def test_a_orchestrator_editing_source_is_warned(root, monkeypatch, repo):
    for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"),
                      ("NotebookEdit", "notebook_path")):
        warn = _guard(root, monkeypatch, "orchestrator", tool, {key: str(repo / "app.py")})
        assert warn and "THE LOOP 1" in warn[0], tool


@pytest.mark.parametrize("tier,path,tool", [
    ("worker", "app.py", "Edit"),               # a worker writes code: that is its job
    ("orchestrator", "notes.md", "Write"),      # docs are not source
    ("orchestrator", "app.py", "Bash"),         # Bash edits are not decidable: silent by design
])
def test_a_control_no_warning(root, monkeypatch, repo, tier, path, tool):
    assert _guard(root, monkeypatch, tier, tool, {"file_path": str(repo / path), "command": "x"}) == []


SCRATCH = "D:/sys/Temp/claude/C--workspace/00000000-0000-4000-8000-000000000000/scratchpad"


@pytest.mark.parametrize("path", [
    "C:/example/Docs/Handoffs/x/DONE.md",        # prose
    "C:/workspace/TODO.txt",                                      # prose
    f"{SCRATCH}/t918_cost.py",                                   # the session scratchpad
    f"{SCRATCH}/sub/probe.tsx",
    "D:/home/.claude/skills/harbor/SKILL.md",                  # skill doctrine
])
def test_a_harbor_d52beebe_exempt_paths_are_silent(root, monkeypatch, path):
    assert _guard(root, monkeypatch, "orchestrator", "Write", {"file_path": path}) == []


@pytest.mark.parametrize("path", [
    "C:/workspace/scripts/quick_fix.py",                          # sanctioned quick script: still code
    "C:/example/src/panel.tsx",
    "D:/loose/loose.py",                                        # outside any repo: still code
])
def test_a_harbor_d52beebe_kept_paths_warn(root, monkeypatch, path):
    warn = _guard(root, monkeypatch, "orchestrator", "Write", {"file_path": path})
    assert warn and "THE LOOP 1" in warn[0]


def test_a_the_scratchpad_shape_is_what_is_exempt_not_the_word():
    # A "scratchpad" folder that is not <temp>/claude/<project>/<session>/scratchpad is code.
    assert hooks._is_source("C:/workspace/app/scratchpad/tool.py")
    assert not hooks._is_source(f"{SCRATCH}/tool.py")


@pytest.mark.parametrize("kind", ["polymathic-linus", "liteharness:polymathic-knuth",
                                  "thinker-skeptic", "liteharness:thinker-red-team", "reviewer-x"])
def test_b_agent_standing_in_for_a_tier_role_is_warned(root, monkeypatch, kind):
    warn = _guard(root, monkeypatch, "leader", "Agent", {"subagent_type": kind, "prompt": "x"})
    assert warn and "THE LOOP 3" in warn[0]


@pytest.mark.parametrize("kind", ["Explore", "general-purpose", "Plan", "claude-code-guide", ""])
def test_b_scouts_are_exempt(root, monkeypatch, kind):
    assert _guard(root, monkeypatch, "orchestrator", "Agent", {"subagent_type": kind, "prompt": "review"}) == []


def test_the_obs_dispatch_warns_without_blocking(root, monkeypatch, repo, capsys):
    _seat(root, SEAT, "orchestrator")
    monkeypatch.setattr(hooks, "emit_obs_event", lambda *a, **k: None)  # never the live bridge
    monkeypatch.setattr(hooks, "_read_hook_stdin", lambda: {
        "session_id": SEAT, "hook_event_name": "PreToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / "app.py")}})
    monkeypatch.setattr(hooks.sys, "argv", ["hooks", "obs", "PreToolUse"])
    with mock.patch.dict(os.environ, _env(CLAUDE_CODE_SESSION_ID=SEAT), clear=True):
        hooks.main()  # returns normally: exit 0
    out = json.loads(capsys.readouterr().out)
    assert "THE LOOP 1" in out["systemMessage"]
    assert out["hookSpecificOutput"]["additionalContext"] == out["systemMessage"]
    assert "permissionDecision" not in out["hookSpecificOutput"]  # warn only


def test_c_the_orchestrator_addressing_a_non_leader_is_flagged(root):
    _seat(root, ORCH, "orchestrator")
    _seat(root, SEAT, "worker", name="Carmack", spawned_by=ORCH)
    warn = cli._not_a_leader_warning(ORCH, SEAT)
    assert warn and "not leader" in warn and "Carmack" in warn


def test_c_control_a_leader_or_a_non_orchestrator_sender_is_silent(root):
    _seat(root, ORCH, "orchestrator")
    _seat(root, SEAT, "leader", spawned_by=ORCH)
    assert cli._not_a_leader_warning(ORCH, SEAT) is None
    _seat(root, SEAT, "worker")
    _seat(root, "99999999-9999-4999-8999-999999999999", "leader")
    assert cli._not_a_leader_warning("99999999-9999-4999-8999-999999999999", SEAT) is None


def test_a_guard_that_raises_never_takes_the_tool_call_down(root, monkeypatch, capsys):
    # Knuth M-3: narrowing the catch in the obs dispatch passed every arm.
    def boom(_hook_input):
        raise RuntimeError("guard bug")

    monkeypatch.setattr(hooks, "pretooluse_guard", boom)
    monkeypatch.setattr(hooks, "emit_obs_event", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "_read_hook_stdin", lambda: {
        "session_id": SEAT, "hook_event_name": "PreToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": "x.py"}})
    monkeypatch.setattr(hooks.sys, "argv", ["hooks", "obs", "PreToolUse"])
    with mock.patch.dict(os.environ, _env(CLAUDE_CODE_SESSION_ID=SEAT), clear=True):
        hooks.main()  # returns normally: exit 0, the tool call proceeds
    assert capsys.readouterr().out == ""


def test_c_cmd_send_from_the_orchestrator_to_a_worker_prints_the_warning(root, capsys):
    # Knuth M-3: removing the send-path warning passed every arm.
    _seat(root, ORCH, "orchestrator")
    _seat(root, SEAT, "worker", name="Carmack", spawned_by=ORCH)
    cli.cmd_send(SEAT, "hello", from_id=ORCH, force=True)
    out = capsys.readouterr().out
    assert "Sent message" in out and "T918 guard (warn only)" in out and "not leader" in out


@pytest.mark.parametrize("tier", ["worker", "thinker", "leader"])
def test_discover_warns_for_restarted_orchestrator_child_only(root, monkeypatch, capsys, tier):
    from datetime import datetime, timezone
    from liteharness import terminal_automation
    now = datetime.now(timezone.utc).isoformat()
    _seat(root, ORCH, "orchestrator", last_seen=now)
    _seat(root, SEAT, tier, spawned_by=ORCH, last_seen=now, name="RestartedSeat")
    monkeypatch.setattr(terminal_automation, "list_panes", lambda: [])
    cli.cmd_discover()
    out = capsys.readouterr().out
    seat_line = next(line for line in out.splitlines() if SEAT in line)
    assert ("THE LOOP 2" in seat_line) == (tier != "leader")
    persisted = json.loads((root / "agents" / f"{SEAT}.json").read_text(encoding="utf-8"))
    assert persisted["tier"] == tier  # warnings never silently repair governing roles


def test_discover_retains_parent_tier_evidence_outside_display_count(root, monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone
    from liteharness import terminal_automation
    now = datetime.now(timezone.utc)
    _seat(root, ORCH, "orchestrator", last_seen=(now - timedelta(seconds=700)).isoformat())
    _seat(root, SEAT, "worker", spawned_by=ORCH, last_seen=now.isoformat())
    monkeypatch.setattr(terminal_automation, "list_panes", lambda: [])
    cli.cmd_discover(count=1)
    out = capsys.readouterr().out
    assert SEAT in out and "THE LOOP 2" in out


def test_doctrine_pointer_command_reloads_role_and_bootstrap(root, prompt_library, capsys):
    _seat(root, SEAT, "leader")
    (prompt_library / prompts.TIER_FILES["leader"]).write_text("LEADER-DOCTRINE", encoding="utf-8")
    (prompt_library / prompts.ALWAYS_FILES[0]).write_text("BOOTSTRAP-DOCTRINE", encoding="utf-8")
    with mock.patch.dict(os.environ, _env(LITEHARNESS_AGENT_ID=SEAT), clear=True):
        hooks.reload_doctrine([])
    out = capsys.readouterr().out
    assert "LEADER-DOCTRINE" in out and "BOOTSTRAP-DOCTRINE" in out
    assert "Nothing was registered" in out


def test_missing_prompt_library_is_reported_not_a_fake_path(monkeypatch):
    monkeypatch.setattr(prompts, "resolve_prompts_dir", lambda: (None, "missing test assets"))
    card = prompts.tier_card("leader")
    assert "<prompt library not found: missing test assets>" in card


def test_compact_does_not_replay_pending_boot_architecture_or_brief(root, tmp_path, capsys):
    cognitive = tmp_path / "method.md"
    cognitive.write_text("METHOD-MARKER" * 1000, encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("BRIEF-MARKER" * 1000, encoding="utf-8")
    _register("compact", LITEHARNESS_TIER="leader",
              LITEHARNESS_COGNITIVE_FILE=str(cognitive), LITEHARNESS_SPAWN_BRIEF=str(brief))
    out = capsys.readouterr().out
    assert "METHOD-MARKER" not in out and "BRIEF-MARKER" not in out
    assert "## Tier card: leader" in out
    assert brief.exists()  # compact must not consume a pending startup brief
    assert len(out.encode("utf-8")) <= 2448
