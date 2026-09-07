"""T418 — a resumed seat's watcher armed on the id the resume had already retired.

🔴 MEASURED, 2026-09-06, from this machine's own `identity-log.jsonl`. One Claude
Code session, one pid (30216), two SessionStart hooks six seconds apart:

    14:30:29.799  hook_source "startup"  resolved f755a243-…  (the new session uuid)
    14:30:35.623  hook_source "resume"   resolved 11679396-…  (the seat's real id)

The register side got this RIGHT: T363's rule retired the startup record and kept
the seat's identity. But `watch-auto` runs in a SEPARATE PROCESS, reads the same
environment chain, and has no idea any of that happened — so it armed on
f755a243 and printed a healthy-looking line while every other seat went on
addressing 11679396.

    A WATCHER ON THE WRONG ID IS INDISTINGUISHABLE FROM A HEALTHY ONE. It prints
    "Watching inbox for agent …", it stays green, and the id it names is the one
    nobody writes to. The failure is silent at BOTH ends: the sender's `send`
    succeeds, and the recipient never wakes.

⚠️ THE TWO RESOLVERS ALREADY SHARE THEIR ENV CHAIN — that is not the gap.
`config.get_agent_id()` and watch-auto's SESSION_ENV_VARS list the same variables
in the same order. What register has and watch-auto does not is the PID-AWARE
step: `_adopt_pid_owner`, whose `os.environ` write is process-local and therefore
invisible to a watcher launched as its own process.

⬜ NOT A SECOND IDENTITY, and worth stating because the obvious reading is wrong:
no presence file for f755a243 survives, and the seat was never "renamed" by the
resume. `generate_name("11679396-…")` IS "StormBit" — the deterministic name of
the seat's OWN id, in force only because no `names/<id>` override existed for it
yet. The registry was not lying about who the seat was; the WATCHER was listening
as somebody else.
"""
from __future__ import annotations

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
    # Same harness as tests/test_startup_then_resume_identity.py — the register
    # side of this exact scenario is already covered there; this file covers the
    # watcher, which is the half that had nothing.
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: os.getpid())
    return tmp_path


def register(ident: str, source: str) -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI", "LITECODE")
        )
    }
    env.update(LITEHARNESS_CLI="claude-code", LITEHARNESS_MODEL="test-model")
    env["CLAUDE_CODE_SESSION_ID"] = ident
    with mock.patch.dict(os.environ, env, clear=True):
        hooks._apply_hook_context(
            {
                "session_id": ident,
                "source": source,
                "hook_event_name": "SessionStart",
                "transcript_path": str(Path("transcripts") / f"{ident}.jsonl"),
            }
        )
        hooks.register_presence()


def run_watch_auto(session_env_id: str):
    """Drive `hooks.main("watch-auto")` and report what it would have watched.

    Returns (watched_id_or_None, printed_output). `watch_inbox` is replaced so the
    test never opens a real watch loop.
    """
    watched: list[str] = []
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI", "LITECODE")
        )
    }
    env.update(LITEHARNESS_CLI="claude-code")
    env["CLAUDE_CODE_SESSION_ID"] = session_env_id
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
        hooks, "watch_inbox", lambda override_agent_id=None, **_k: watched.append(override_agent_id)
    ), mock.patch.object(hooks.sys, "argv", ["hooks", "watch-auto"]):
        hooks.main()
    return (watched[0] if watched else None), watched


def test_the_register_side_is_already_right(identity_root):
    """CONTROL — the premise this whole file rests on.

    If the resume did NOT retire the startup record, the watcher would be arming
    on a perfectly current id and there would be nothing here to fix. This is the
    T363 behaviour, asserted so a change there cannot silently make the arms
    below meaningless.
    """
    register(STARTUP, "startup")
    register(RESUMED, "resume")

    startup_row = json.loads((identity_root / "agents" / f"{STARTUP}.json").read_text())
    assert cli._superseded_by_later_registration(STARTUP, startup_row), (
        "the startup record is no longer retired by the resume — T363 changed"
    )
    resumed_row = json.loads((identity_root / "agents" / f"{RESUMED}.json").read_text())
    assert not cli._superseded_by_later_registration(RESUMED, resumed_row)


def test_watch_auto_does_not_arm_on_a_retired_startup_id(identity_root, capsys):
    """🔴 THE 2026-09-06 SHAPE, and the arm that was missing.

    The watcher's environment still carries the STARTUP uuid — that is exactly
    what happened on this machine, because the watcher process was launched with
    the environment the session had before the resume rewrote it.
    """
    register(STARTUP, "startup")
    register(RESUMED, "resume")
    capsys.readouterr()

    watched, _ = run_watch_auto(STARTUP)
    out = capsys.readouterr()
    printed = out.out + out.err

    assert watched != STARTUP, (
        "watch-auto armed on the id the resume retired — the seat is deaf and looks healthy"
    )
    assert watched in (RESUMED, None), f"watch-auto armed on an unexpected id: {watched!r}"
    # Whichever it does — follow or refuse — it must NAME BOTH so the reader can
    # tell which seat is actually being watched.
    assert STARTUP in printed and RESUMED in printed, (
        f"the disagreement was not reported with both ids: {printed!r}"
    )


def test_an_ordinary_session_is_untouched(identity_root, capsys):
    """CONTROL — no supersede, no interference.

    Without this, a reconciler that refused EVERYTHING would pass the arm above
    while disarming every healthy watcher on the machine. This is the common case
    and it must behave exactly as it did before.
    """
    register(STARTUP, "startup")
    capsys.readouterr()

    watched, _ = run_watch_auto(STARTUP)

    assert watched == STARTUP, "a healthy single-session watcher was disturbed"


def test_an_id_with_no_presence_row_still_arms(identity_root, capsys):
    """CONTROL — the registry is not a precondition for watching.

    A watcher can legitimately start before its own registration lands. Refusing
    on "no presence file" would make the reconciler a new failure mode rather
    than a guard, and would break every first-run seat.
    """
    capsys.readouterr()

    watched, _ = run_watch_auto(STARTUP)

    assert watched == STARTUP, "an unregistered-but-legitimate seat was refused a watcher"


def test_no_session_id_still_skips_rather_than_dies(identity_root, capsys):
    """CONTROL — the pre-existing refusal path is preserved.

    watch-auto is invoked from monitors.json at every SessionStart, so an
    unresolvable environment must SKIP with a reason, never exit(1) and never
    guess. That rule predates this card and must survive it.
    """
    watched, _ = run_watch_auto("")
    err = capsys.readouterr().err

    assert watched is None
    assert "skipped" in err.lower()


# ---------------------------------------------------------------------------
# T462 — the case T418 left open: the env names an id that was NEVER registered.
# ---------------------------------------------------------------------------

TAKEOVER = "33333333-3333-4333-8333-333333333333"
UNREGISTERED = "44444444-4444-4444-8444-444444444444"


def register_takeover(ident: str) -> None:
    """Register `ident` the way an explicit takeover does.

    `_authoritative_owner_of_pid` only honours a record whose
    `registration_source` is "takeover" (hooks.py:1259) — a plain startup record
    must never capture a later session on the same pid — and that stamp comes
    from `_explicit_identity_override()`, i.e. LITEHARNESS_AGENT_ID being set.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI", "LITECODE")
        )
    }
    env.update(LITEHARNESS_CLI="claude-code", LITEHARNESS_MODEL="test-model")
    env["LITEHARNESS_AGENT_ID"] = ident
    env["CLAUDE_CODE_SESSION_ID"] = ident
    with mock.patch.dict(os.environ, env, clear=True):
        hooks._apply_hook_context(
            {
                "session_id": ident,
                "source": "resume",
                "hook_event_name": "SessionStart",
                "transcript_path": str(Path("transcripts") / f"{ident}.jsonl"),
            }
        )
        hooks.register_presence()


def test_watch_auto_prefers_the_pid_takeover_over_an_unregistered_env_id(
    identity_root, capsys
):
    """🔴 THE 2026-09-07 SHAPE — six minutes of unread mail on an orchestrator.

    The watcher's environment named the new Claude session uuid; the register
    path, running in a LATER environment, resolved the seat's real id. Both
    resolvers walk the identical variable list, so the shared chain never made
    them agree — they were reading different processes' environments. The
    watcher then armed on an id no sender uses and printed a healthy line.

        A WATCHER ON THE WRONG ID IS INDISTINGUISHABLE FROM A HEALTHY ONE.
    """
    register_takeover(TAKEOVER)
    capsys.readouterr()

    watched, _ = run_watch_auto(UNREGISTERED)
    out = capsys.readouterr()
    printed = out.out + out.err

    assert watched != UNREGISTERED, (
        "watch-auto armed on an id that was never registered while an explicit "
        "takeover owned this pid — that is the six-minute deafness itself"
    )
    assert watched == TAKEOVER, f"expected the pid's takeover owner, watched {watched!r}"
    # The card's requirement: SAY BOTH. A corrected id that does not name what it
    # corrected leaves the next reader unable to tell this from a plain start.
    assert UNREGISTERED in printed and TAKEOVER in printed, printed


def test_a_genuine_first_run_still_arms_on_its_own_env_id(identity_root, capsys):
    """⬜ CONTROL — the fix must not turn "not registered yet" into a failure.

    A watcher may legitimately start before its own registration lands. With no
    takeover on this pid there is nothing to prefer, and refusing or redirecting
    here would invent a new failure mode for every first-run seat — which is the
    reason the `if not data` branch returned early in the first place.
    """
    capsys.readouterr()
    watched, _ = run_watch_auto(UNREGISTERED)
    assert watched == UNREGISTERED, (
        "a first-run watcher must still arm on its own id when no takeover owns the pid"
    )
