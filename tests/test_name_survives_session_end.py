"""T418-A — a seat's name must survive its session ending.

🔴 THE MECHANISM, END TO END. `cleanup_stale_names()` deleted a `names/<id>`
override the instant no presence file existed for that id. But a presence file is
removed by ORDINARY SESSION END — `deregister()`'s own docstring says so: "Remove
agent presence file when the SESSION ends." So the sequence was:

    register --name OpenBolt   ->  names/<id> written
    session ends               ->  agents/<id>.json unlinked (deregister)
    the sweep runs             ->  names/<id> DELETED as "stale"
    resume                     ->  register finds no row and no override, and
                                   falls through to generate_name(<id>)

and the seat came back under a hash of its own uuid. Measured on this machine:
`generate_name("11679396-…")` is "StormBit", and `names/` held the three live
seats' overrides only from 10:32–10:34 on 2026-09-06 — the minutes their humans
re-took the names by hand.

    A CLEANUP KEYED ON "THE PRESENCE FILE IS GONE" CANNOT TELL A SEAT THAT ENDED
    FROM A SEAT THAT DIED, BECAUSE ENDING IS HOW A SEAT STOPS.

⬜ WHAT IS NOT CHANGED, AND WHY IT WOULD BE THE SAME BUG AGAIN. The card offers
"explicit deregister/eviction OR a retention window". `deregister()` is the
ROUTINE SessionEnd path, so clearing the override there would delete the name of
every seat that shuts down cleanly — precisely the defect. And the explicit
eviction path is ALREADY correct: `cli._evict_agent_records` (cli.py:1049) moves
`names/<id>` into the backup with the presence file. So the retention window is
the whole fix, and it is one function.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from liteharness import cli, config, hooks, naming


SEAT = "33333333-3333-4333-8333-333333333333"
GHOST = "44444444-4444-4444-8444-444444444444"
CHOSEN = "OpenBolt"


@pytest.fixture
def name_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: os.getpid())
    (tmp_path / "agents").mkdir(parents=True, exist_ok=True)
    (tmp_path / "names").mkdir(parents=True, exist_ok=True)
    return tmp_path


def register_session(ident: str, source: str) -> None:
    """Drive the real SessionStart register hook for `ident`."""
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


def end_the_session(root: Path, ident: str) -> None:
    """What `deregister()` does at SessionEnd: the presence file goes."""
    (root / "agents" / f"{ident}.json").unlink(missing_ok=True)


def age_override(root: Path, ident: str, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(root / "names" / ident, (old, old))


def test_a_name_survives_the_session_that_chose_it(name_root, capsys):
    """🔴 THE EXACT SHAPE FROM THE CARD: name, end, sweep, resume."""
    register_session(SEAT, "startup")
    cli.cmd_register(
        SEAT, cli="claude-code", model="test-model", name=CHOSEN,
        session_pid=os.getpid(), takeover=True,
    )
    capsys.readouterr()
    assert naming.get_name(SEAT) == CHOSEN, "the takeover did not write the override"

    end_the_session(name_root, SEAT)
    naming.cleanup_stale_names()

    assert naming.get_override(SEAT) == CHOSEN, (
        "the sweep deleted the name of a seat that merely ended its session"
    )

    register_session(SEAT, "resume")
    row = json.loads((name_root / "agents" / f"{SEAT}.json").read_text())
    assert row["name"] == CHOSEN, f"the seat came back as {row['name']!r} instead of {CHOSEN!r}"
    assert row["name"] != naming.generate_name(SEAT), (
        "the seat fell through to the generated name — the override was lost"
    )


def test_a_genuinely_stale_ghost_still_loses_its_override(name_root):
    """🔴 CONTROL — the sweep must still sweep.

    Without this, "never delete" passes the arm above while turning `names/` into
    an append-only squat list: a dead agent's name could never be reclaimed, and
    `--takeover`'s whole reason for existing is that names get stuck.
    """
    naming.set_override(GHOST, "DeadName")
    age_override(name_root, GHOST, days=400)

    removed = naming.cleanup_stale_names()

    assert removed == 1, "an ancient override with no presence file was kept"
    assert naming.get_override(GHOST) is None


def test_a_live_seat_is_never_swept_however_old_its_override(name_root):
    """CONTROL — presence beats age.

    A long-lived seat's override can be arbitrarily old. Ageing alone must not
    reclaim a name that is currently in use.
    """
    register_session(SEAT, "startup")
    naming.set_override(SEAT, CHOSEN)
    age_override(name_root, SEAT, days=400)

    removed = naming.cleanup_stale_names()

    assert removed == 0
    assert naming.get_override(SEAT) == CHOSEN


def test_a_long_named_seat_that_keeps_registering_keeps_its_name(name_root, capsys):
    """🔴 THE WINDOW MEASURES ABANDONMENT, NOT AGE OF THE NAME.

    `set_override` writes ONCE, so the file's mtime is when the name was CHOSEN.
    Keyed on that alone, a seat named 31 days ago that has registered every day
    since is swept the first night it is not running — the original complaint
    back again on a 30-day delay. Registration must refresh the clock.
    """
    naming.set_override(SEAT, CHOSEN)
    age_override(name_root, SEAT, days=40)

    register_session(SEAT, "startup")  # a normal day at work, 40 days later
    capsys.readouterr()
    end_the_session(name_root, SEAT)

    # The sweep runs a day after that registration, not a day after the naming.
    removed = naming.cleanup_stale_names(now=time.time() + 86400)

    assert removed == 0, "a seat that registered yesterday was swept as abandoned"
    assert naming.get_override(SEAT) == CHOSEN

    register_session(SEAT, "resume")
    row = json.loads((name_root / "agents" / f"{SEAT}.json").read_text())
    assert row["name"] == CHOSEN


def test_the_same_old_override_with_no_registration_is_still_swept(name_root):
    """🔴 CONTROL — the pair to the arm above, differing only in the registration.

    Same override, same age, same sweep time. If touching on registration were
    replaced by touching on anything (a read, a name-collision scan), or if the
    window simply stopped expiring, this arm fails while the one above still
    passes. Two arms one variable apart are the only way to show the touch is
    what did it.
    """
    naming.set_override(GHOST, "DeadName")
    age_override(name_root, GHOST, days=40)

    removed = naming.cleanup_stale_names(now=time.time() + 86400)

    assert removed == 1, "an override abandoned for 40 days was kept"
    assert naming.get_override(GHOST) is None


def test_reading_a_name_does_not_refresh_it(name_root):
    """⚠️ `get_name` MUST NOT TOUCH. `is_name_taken` walks every agent and calls
    `get_name` on each, so refreshing on read would keep every ghost in the
    registry alive forever on any collision check.
    """
    naming.set_override(GHOST, "DeadName")
    age_override(name_root, GHOST, days=40)

    assert naming.get_name(GHOST) == "DeadName"
    naming.is_name_taken("DeadName")

    assert naming.cleanup_stale_names(now=time.time() + 86400) == 1, (
        "reading the name refreshed its clock — ghosts would never expire"
    )


def test_explicit_eviction_still_takes_the_name(name_root):
    """CONTROL — the deliberate path is unchanged.

    Retention must not become a way for a ghost to hold a name against an
    explicit takeover. `_evict_agent_records` moves the override out with the
    presence file, and that is how a name is actually reclaimed on demand.
    """
    naming.set_override(GHOST, "DeadName")
    (name_root / "agents" / f"{GHOST}.json").write_text("{}", encoding="utf-8")

    backup = cli._evict_agent_records(GHOST)

    assert naming.get_override(GHOST) is None
    assert (name_root / backup / GHOST).exists(), "the evicted name was destroyed, not banked"
