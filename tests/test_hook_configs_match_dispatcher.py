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
    """Read KNOWN_ACTIONS out of hooks.py without importing it.

    Importing would execute the module and drag in its dependencies; this check must run
    anywhere, including on a machine where those are absent.
    """
    source = (PACKAGE_ROOT / "hooks.py").read_text(encoding="utf-8")
    block = re.search(r"KNOWN_ACTIONS\s*=\s*\{(.*?)\}", source, re.DOTALL)
    assert block, "KNOWN_ACTIONS not found in hooks.py — this test's own anchor moved"
    actions = set(re.findall(r'"([a-z0-9\-]+)"', block.group(1)))
    # Positive control: if the parse silently returned nothing, every assertion below would
    # pass vacuously and this test would be decorative.
    assert "watch" in actions, f"parsed KNOWN_ACTIONS looks wrong: {actions}"
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
