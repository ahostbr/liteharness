"""T441 — a PostCompact register resurrected an id a takeover had already retired.

🔴 MEASURED, 2026-09-06 19:39 (SilverBolt). His session was `/clear`-ed at 17:0x
and self-registered as 3505390e, taking the SilverBolt name from the dead
7a0b70cd (evicted to `.ghost_evicted_20260906`). At his 19:3x `/compact` the
PostCompact hook resolved the agent id FROM THE ENVIRONMENT — which still
carried 7a0b70cd, because the `claude.exe` process survived the `/clear` — and
wrote `agents/7a0b70cd….json` again. Two presence files for one seat: `discover`
rendered a ghost row and hid the live seat behind "superseded on the same PID",
and a `send` to 7a0b70cd was accepted by a maildir with no consumer.

⚠️ THE GUARD ALREADY EXISTED AND STILL FAILED, which is the whole finding.
`_adopt_pid_owner` has re-resolved to the pid's takeover owner since `fa37913`
(2026-09-03) — before the incident. What turned it off was its own gate:

    owner = _authoritative_owner_of_pid(...) if config.get_cli() == "claude-code" else None

`config.get_cli()` (config.py:257-274) reads ONLY environment variables, so a
hook running with a bare environment gets "unknown" and the lookup was skipped
entirely. Measured 2026-09-07: strip every CLI variable and `get_cli()` returns
"unknown", so the condition evaluated False and the retired id was adopted.

    A GUARD KEYED ON DETECTION FAILS OPEN WHEN DETECTION IS BLIND.

⬜ AND THE MIRROR CASE MUST KEEP WORKING. OpenBolt's seat returns on the
PROTECTED TAKEOVER path — the hook resolves a new session to the OLD harness id
on purpose. The last arm pins that, because a fix that stopped resurrecting
ghosts by refusing to adopt anything would break the path this repo relies on.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from liteharness import config, hooks


GHOST = "77777777-7777-4777-8777-777777777777"
LIVE = "88888888-8888-4888-8888-888888888888"
SESSION_PID = 4242


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: SESSION_PID)
    # The owner record must look like it was written while this pid was running.
    monkeypatch.setattr(hooks, "_record_belongs_to_process", lambda agent_id, pid: True)
    (tmp_path / "agents").mkdir(parents=True, exist_ok=True)
    return tmp_path


def write_presence(root: Path, agent_id: str, **fields) -> None:
    data = {
        "agent_id": agent_id,
        "cli": "claude-code",
        "session_pid": SESSION_PID,
        "registered_at": "2026-09-06T17:00:00+00:00",
        **fields,
    }
    (root / "agents" / f"{agent_id}.json").write_text(json.dumps(data), encoding="utf-8")


def bare_env() -> dict[str, str]:
    """What a PostCompact hook actually sees: no CLI variables at all."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI", "LITECODE", "OPENCODE")
        )
    }


def test_the_bare_environment_really_is_blind(registry):
    """CONTROL — the premise. If `get_cli()` ever learns to detect without the
    environment, the arm below stops testing what it claims to."""
    with mock.patch.dict(os.environ, bare_env(), clear=True):
        assert config.get_cli() == "unknown"


def test_a_retired_id_resolves_to_the_pid_owner_with_no_cli_in_the_environment(registry):
    """🔴 THE 2026-09-06 SHAPE."""
    write_presence(registry, GHOST, registration_source="startup", name="Ghost")
    write_presence(
        registry,
        LIVE,
        registration_source="takeover",
        name="SilverBolt",
        registered_at="2026-09-06T17:05:00+00:00",
    )

    with mock.patch.dict(os.environ, bare_env(), clear=True):
        resolved = hooks._adopt_pid_owner(GHOST)

    assert resolved == LIVE, (
        "the retired env id was adopted as-is — this is the resurrection itself, "
        "and it writes a second presence file for one seat"
    )


def test_a_known_non_claude_cli_is_still_excluded(registry):
    """⬜ CONTROL — the Codex reason the gate existed in the first place.

    Codex Desktop tasks share a backend PID, so a Codex session must never be
    adopted into a Claude seat's identity. A Codex session sets CODEX_SESSION_ID
    and therefore detects as "codex-cli" — never as "unknown" — so widening the
    gate to the blind case must not widen it to a DETECTED other CLI.
    """
    write_presence(registry, GHOST, registration_source="startup", name="Ghost")
    write_presence(registry, LIVE, registration_source="takeover", name="SilverBolt")

    env = bare_env()
    env["CODEX_SESSION_ID"] = GHOST
    with mock.patch.dict(os.environ, env, clear=True):
        resolved = hooks._adopt_pid_owner(GHOST)

    assert resolved == GHOST, "a detected Codex session was adopted into a Claude seat"


def test_the_protected_takeover_path_still_returns_the_old_id(registry):
    """⬜ CONTROL — OpenBolt's mirror case, which the card warns not to break.

    A seat that returns on the protected-takeover path is SUPPOSED to resolve a
    new session to the old harness id. The fix must keep adopting there; a
    version that refused to adopt anything would pass the arm above and break
    every seat that comes back this way.
    """
    write_presence(
        registry,
        LIVE,
        registration_source="takeover",
        name="OpenBolt",
        registered_at="2026-09-07T10:00:00+00:00",
    )
    new_session_id = "99999999-9999-4999-8999-999999999999"

    with mock.patch.dict(os.environ, bare_env(), clear=True):
        resolved = hooks._adopt_pid_owner(new_session_id)

    assert resolved == LIVE, "the protected takeover path stopped adopting — this is the regression"
