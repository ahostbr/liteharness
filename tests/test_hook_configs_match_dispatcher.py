"""
Every action a shipped hook config invokes must exist in the dispatcher.

THIS CLASS OF DEFECT HAS SHIPPED TWICE, AND THE SECOND TIME IT SHIPPED INSIDE THE RELEASE
THAT FIXED THE FIRST.

  * 0.2.5 registered `liteharness.hooks memory-nudge` on UserPromptSubmit and did not
    implement it. Every prompt the user typed printed "Unknown action: memory-nudge".
    Measured on a virgin Windows Sandbox, four times in one screenshot.
  * 0.2.7 fixed memory-nudge and simultaneously shipped `checkpoint-save` and
    `checkpoint-restore` on PreCompact/PostCompact, implemented nowhere in the package.

The config and the dispatcher are two halves of one contract written in two files, and
nothing compared them. A human reviewing either half sees something entirely consistent.

This is deliberately a STRUCTURAL check rather than a list of known-bad names: it derives the
valid set from the dispatcher source, so it keeps working when actions are added or renamed,
and it fails the moment the two halves disagree again for any reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "liteharness"
CONFIG_DIR = PACKAGE_ROOT / "hooks_configs"


def _known_actions() -> set[str]:
    """Read KNOWN_ACTIONS from the module itself.

    This used to scrape the set out of hooks.py with a regex, to avoid importing the module.
    That was brittle in exactly the way this file exists to warn about: hoisting the set to
    module scope and wrapping it in frozenset() changed the literal's shape, the regex matched
    nothing, and the check would have started passing vacuously — a guard silently ceasing to
    guard, which is the same disease as a config naming an action nobody implements.

    Its own positive control caught that (see below), which is the only reason it surfaced as
    a red test rather than a green no-op. Importing removes the failure mode entirely: there
    is no longer a second representation of the set to drift from the first.
    """
    from liteharness import hooks

    actions = set(hooks.KNOWN_ACTIONS)
    # Positive control retained: an empty or bogus set would make every assertion below pass
    # for the wrong reason.
    assert "watch" in actions, f"KNOWN_ACTIONS looks wrong: {actions}"
    return actions


def _invoked_actions() -> list[tuple[str, str]]:
    """Every (config file, action) invoked as `python -m liteharness.hooks <action>`."""
    found: list[tuple[str, str]] = []
    for config in sorted(CONFIG_DIR.glob("*.json")):
        raw = config.read_text(encoding="utf-8")
        json.loads(raw)  # a malformed config is its own failure
        for action in re.findall(r"liteharness\.hooks\s+([a-z0-9\-]+)", raw):
            found.append((config.name, action))
    return found


def test_every_configured_hook_action_is_implemented() -> None:
    known = _known_actions()
    invoked = _invoked_actions()

    # Positive control: if no config invokes anything, the assertion below is vacuous.
    assert invoked, f"no liteharness.hooks invocations found under {CONFIG_DIR}"

    unimplemented = sorted({(f, a) for f, a in invoked if a not in known})
    assert not unimplemented, (
        "shipped hook config invokes actions the dispatcher does not implement — "
        "every one of these fails on a user's machine every time its event fires: "
        + ", ".join(f"{f} -> {a}" for f, a in unimplemented)
    )


def test_codex_configs_reference_a_real_module() -> None:
    """The codex configs call liteharness.codex_hooks; make sure that module exists."""
    assert (PACKAGE_ROOT / "codex_hooks.py").exists()
