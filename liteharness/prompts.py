"""Tier-preamble + doctrine delivery — the piece that makes the prompt library REACH agents.

Before this module existed, the tier preambles (leader/worker/thinker/reviewer) and role
docs shipped in the installer but were delivered to NOBODY: the SessionStart hook told
every agent to "read your tier preamble before acting" without saying where, no Python
code loaded them, and the TS spawner that did load them (a) pointed at a directory absent
from packaged builds and (b) is dormant (worker_cli=litecode). Verified 2026-08-07.

Two delivery modes (Ryan's ruling, 2026-08-07 — "liteharness essentially has two modes"):

  litesuite   — the agent runs INSIDE a LiteSuite pane (bridge token / pane id present).
                LiteSuite itself teaches app operation via the Spatial Bootstrap cheatsheet
                (hooks.register_presence Block A), so this mode delivers tier doctrine +
                methodology and lets the app layer speak for itself.

  standalone  — a plain CLI session (like a Windows Terminal `claude`). Nothing else will
                ever teach this agent the harness, so it gets the WHOLE doctrine: tier
                file + methodology + its tier's protocol docs, inlined.

Composition is data (TIER_FILES / STANDALONE_EXTRAS below) — adjusting which mode gets
which layer is an edit to those tables, not to logic.

IMPORTANT CONSTRAINT NOTE (LiveArch, 2026-08-07): per-tier TOOL manifests were deleted in
496dbdd9 as never-enforced theater. Every agent gets every tool. The preamble text these
functions deliver is therefore the ONLY thing constraining tier behaviour — delivery
failing silently means an unconstrained agent, which is why every failure path here PRINTS.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

# ── Composition tables ───────────────────────────────────────────────────────

#: tier → the role/preamble file that defines it. Unknown tiers fall back to the
#: worker preamble — a REAL file (the old TS fallback pointed at "worker.md",
#: which never existed: an inclusion-list-shaped dead path for every tier added
#: later). Fallback must be a file that exists, plus a printed warning.
TIER_FILES = {
    "orchestrator": "orchestrator-role.md",
    "leader": "preambles/leader-preamble.md",
    "worker": "preambles/worker-preamble.md",
    "thinker": "preambles/thinker-preamble.md",
    "reviewer": "preambles/reviewer-preamble.md",
    "librarian": "librarian-role.md",
}
FALLBACK_TIER_FILE = "preambles/worker-preamble.md"

#: Delivered in BOTH modes — the harness methodology (tiers, task board, branching,
#: commit trailers, HITL).
ALWAYS_FILES = ["bootstrap-harness.md"]

#: standalone-only doctrine layer, per tier ("the whole litesuite prompts" — outside
#: LiteSuite these are unreachable by path, so they ride inline).
STANDALONE_EXTRAS = {
    "orchestrator": ["agent-pool-guide.md", "hitl-clause.md"],
    "leader": [
        "protocols/convergence-signals.md",
        "protocols/review-verdicts.md",
        "protocols/github-issue-protocol.md",
    ],
    "worker": ["protocols/github-issue-protocol.md"],
    "thinker": [],
    "reviewer": ["protocols/review-verdicts.md"],
    "librarian": [],
}

# ── Prompts-dir resolution ───────────────────────────────────────────────────


def _plugin_cache_candidates() -> list:
    """Highest-version-first prompts dirs from the Claude plugin cache."""
    cache = Path.home() / ".claude" / "plugins" / "cache" / "liteharness" / "liteharness"
    if not cache.is_dir():
        return []

    def _ver_key(p: Path):
        try:
            return [int(x) for x in p.name.split(".")]
        except ValueError:
            return [-1]

    return [
        d / "prompts"
        for d in sorted(cache.iterdir(), key=_ver_key, reverse=True)
        if (d / "prompts").is_dir()
    ]


def resolve_prompts_dir() -> Tuple[Optional[Path], str]:
    """Locate the canonical prompt library. Returns (dir, source_label).

    Order (freshest wins on a dev box, packaged install wins for end users):
      1. $LITEHARNESS_PROMPTS_DIR                    — explicit override
      2. repo checkout (walk up from this file)      — editable installs
      3. LiteSuite packaged install                  — end-user runtime
      4. Claude plugin cache (highest version)       — plugin-only installs

    (None, searched-paths) when nothing resolves — callers must be LOUD about it.
    """
    override = os.environ.get("LITEHARNESS_PROMPTS_DIR", "").strip()
    if override:
        p = Path(override)
        if p.is_dir():
            # Include the PATH, not just the var name. This label is a diagnosis
            # surface - a log line that says only "env:LITEHARNESS_PROMPTS_DIR" makes
            # the reader go find the variable to learn which tree answered, which is
            # the exact question the label exists to answer.
            return p, f"env:LITEHARNESS_PROMPTS_DIR={p}"

    here = Path(__file__).resolve()
    for parent in list(here.parents)[:8]:
        candidate = parent / "resources" / "liteharness-plugin" / "prompts"
        if candidate.is_dir():
            return candidate, f"repo:{candidate}"

    # SIBLING checkout. The walk-up above only fires when this package sits INSIDE
    # LiteSuite, which was true while it lived at LiteSuite/packages/liteharness and
    # stopped being true the moment it moved to its own repo (2026-08-12). From
    # C:\Projects\liteharness-oss the walk-up looks for C:\Projects\resources\... and
    # C:\resources\..., neither of which exists, so a dev box silently lost its repo
    # root and fell through to the packaged install - which still ships ryan.md.
    # Caught by diagnose() during the merge, not by any test: every resolution still
    # "worked", it just answered from the wrong tree.
    for parent in list(here.parents)[:8]:
        candidate = parent / "LiteSuite" / "resources" / "liteharness-plugin" / "prompts"
        if candidate.is_dir():
            return candidate, f"sibling-repo:{candidate}"

    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        packaged = (
            Path(local_app)
            / "Programs"
            / "litesuite-desktop"
            / "resources"
            / "liteharness-plugin"
            / "prompts"
        )
        if packaged.is_dir():
            return packaged, f"install:{packaged}"

    for candidate in _plugin_cache_candidates():
        return candidate, f"plugin-cache:{candidate}"

    return None, "searched: env override, repo walk-up, packaged install, plugin cache"


# All cognitive-architecture tier directories. The tree is organised by the
# polymath's DEFAULT tier, not the role it is serving this mission — linus.md
# lives under workers/ even when Linus sits as a reviewer (drmario run,
# 2026-08-07). Any resolver that looks only in the serving tier's directory
# silently misses every cross-tier assignment, so: prefer the serving tier's
# dir, then search ALL of them.
_COG_TIER_DIRS = ("thinkers", "reviewers", "workers", "leaders", "orchestrator")

_TIER_TO_COG_DIR = {
    "thinker": "thinkers",
    "reviewer": "reviewers",
    "worker": "workers",
    "leader": "leaders",
    "orchestrator": "orchestrator",
}


def resolve_cognitive_file(name: str, tier: str | None = None) -> Optional[Path]:
    """Map a polymath name to its cognitive-architecture file, if one ships.

    Returns the file for e.g. name="Linus" regardless of which tier directory
    holds it. None when the library is unresolvable or no file matches — a
    plain agent name like "BottleRender" is the normal miss, not an error.
    """
    # MUST use the same slug function the WRITE path uses (resolve_orchestrator_target).
    # A bare .lower() matches it for single-word names and DIVERGES for every multi-word
    # one: "The Warden" is written as `the-warden.md` and was looked up as `the warden.md`,
    # which silently fell through to the tier default. Invisible for "Sentinel" — the one
    # name the author tested — and broken for the first user who picks two words.
    slug = orchestrator_slug(name)
    if not name or not name.strip():
        return None
    pdir, _src = resolve_prompts_dir()
    if pdir is None:
        return None
    base = pdir / "cognitive-architectures"
    preferred = _TIER_TO_COG_DIR.get((tier or "").strip().lower())
    order = [preferred] if preferred else []
    order += [d for d in _COG_TIER_DIRS if d != preferred]
    for d in order:
        f = base / d / f"{slug}.md"
        if f.is_file():
            return f

    # Tier default. An orchestrator MUST have an architecture — orchestrator-role.md calls
    # it "your operating system" and marks the read MANDATORY — so a user whose orchestrator
    # is named anything other than the shipped exemplar must still resolve to something.
    # Before this, they resolved to None and the mandatory read silently loaded nothing.
    # Deliberately NARROW: only the PREFERRED tier, and only when that tier ships a
    # default.md. A worker named "BottleRender" still returns None, which the docstring
    # above promises and which a blanket fallback would have broken.
    if preferred:
        fallback = base / preferred / "default.md"
        if fallback.is_file():
            return fallback
    return None


def orchestrator_slug(name: str) -> str:
    """Filesystem slug for an orchestrator name. 'The Warden' -> 'the-warden'."""
    cleaned = "".join(c if (c.isalnum() or c in " -_") else "" for c in (name or "").strip().lower())
    slug = "-".join(cleaned.replace("_", " ").split())
    return slug or "default"


def resolve_orchestrator_target(name: str) -> Tuple[Optional[Path], str]:
    """Where a GENERATED orchestrator architecture must be written to be findable.

    Derived from resolve_prompts_dir() so it is correct BY CONSTRUCTION — the write
    location cannot drift from the read location, because they are the same lookup.

    Two bugs this exists to prevent, both real:

      1. `/ls-init-liteharness` documented its output as
         `.liteharness/prompts/cognitive-architectures/orchestrator/<username>.md`.
         resolve_prompts_dir() NEVER returns `.liteharness/` — it resolves to the repo
         checkout, the packaged install, or the plugin cache. A file written there is
         unfindable, and "read this file" has no failure mode, so nothing would report it.

      2. It named the file after the HUMAN (git user.name) while resolve_cognitive_file()
         looks up the AGENT's name. Naming a resource by a non-authoritative identifier:
         the human is not the key anyone searches by.

    Returns (path, reason). Path is None only when no prompt library resolves at all,
    which callers must be LOUD about — silently skipping identity generation produces an
    orchestrator with no architecture and no complaint.
    """
    pdir, src = resolve_prompts_dir()
    if pdir is None:
        return None, f"no prompt library resolved ({src})"
    target = pdir / "cognitive-architectures" / "orchestrator" / f"{orchestrator_slug(name)}.md"
    return target, f"from {src}"


# ── Mode detection ───────────────────────────────────────────────────────────


def detect_mode(litesuite_hint: Optional[bool] = None) -> str:
    """'litesuite' when the session runs inside a LiteSuite pane, else 'standalone'.

    $LITEHARNESS_MODE overrides everything (spawners may set it; the /canvas/claude
    fallback spawn is KNOWN to omit pane envs — the filed LITESUITE_PANE_ID gap —
    so an explicit override beats inference). Otherwise pane/bridge envs decide;
    callers that already computed the answer pass litesuite_hint.
    """
    explicit = os.environ.get("LITEHARNESS_MODE", "").strip().lower()
    if explicit in ("litesuite", "standalone"):
        return explicit
    if litesuite_hint is not None:
        return "litesuite" if litesuite_hint else "standalone"
    if (
        os.environ.get("LITESUITE_PANE_ID", "").strip()
        or os.environ.get("LITESUITE_BRIDGE_TOKEN", "").strip()
        or os.environ.get("LITESUITE_CANVAS_SESSION", "").strip()
    ):
        return "litesuite"
    return "standalone"


# ── Composition ──────────────────────────────────────────────────────────────


def _read(prompts_dir: Path, rel: str) -> Optional[str]:
    f = prompts_dir / rel
    if not f.is_file():
        return None
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _fill_slots(text: str) -> str:
    """Fill {{USER_TRUNK}} (orchestrator-role.md). Empty is a documented state —
    the role doc's default trunk fires when the slot is blank."""
    return text.replace("{{USER_TRUNK}}", os.environ.get("LITEHARNESS_USER_TRUNK", "").strip())


def compose(tier: str, mode: str) -> str:
    """Build the full preamble payload for a tier+mode. ALWAYS returns printable
    text — on failure that text is a loud diagnostic, never empty/None, because a
    silently-undelivered preamble is an unconstrained agent (see module docstring).
    """
    prompts_dir, source = resolve_prompts_dir()
    if prompts_dir is None:
        return (
            "[LITEHARNESS] ⚠ TIER PREAMBLE NOT DELIVERED — no prompt library found "
            f"({source}). You are operating WITHOUT tier doctrine: stay within your "
            f"spawn prompt, make no destructive or outward-facing moves, and report "
            f"this delivery failure to your orchestrator/human immediately."
        )

    tier_key = (tier or "").strip().lower()
    tier_file = TIER_FILES.get(tier_key)
    warn = ""
    if tier_file is None:
        tier_file = FALLBACK_TIER_FILE
        warn = (
            f"\n> ⚠ Unknown tier '{tier}' — delivering the worker preamble as the "
            f"safe default. If your tier is real, add it to TIER_FILES in "
            f"liteharness/prompts.py and to the prompt library."
        )

    sections = []
    missing = []

    body = _read(prompts_dir, tier_file)
    if body is None:
        missing.append(tier_file)
    else:
        sections.append(_fill_slots(body))

    for rel in ALWAYS_FILES + (STANDALONE_EXTRAS.get(tier_key, []) if mode == "standalone" else []):
        text = _read(prompts_dir, rel)
        if text is None:
            missing.append(rel)
        else:
            sections.append(f"<!-- {rel} -->\n{text}")

    where = (
        "INSIDE a LiteSuite pane — the Spatial Bootstrap above is your app cheatsheet"
        if mode == "litesuite"
        else "in a STANDALONE CLI (no LiteSuite pane around you) — app/bridge features "
        "need a running LiteSuite; your doctrine is inlined below in full"
    )
    header = (
        f"## Tier Preamble — {tier_key or 'worker'} ({mode})\n"
        f"You are running {where}.\n"
        f"Tool access is NOT tier-gated (manifests deleted 496dbdd9): these rules are "
        f"your ONLY constraint — a thinker/reviewer edits nothing because doctrine says "
        f"so, not because it can't.\n"
        f"Prompt library: {prompts_dir} (source {source}) — protocols/, "
        f"cognitive-architectures/ live there.{warn}"
    )
    if missing:
        header += (
            f"\n> ⚠ MISSING from the library and NOT delivered: {', '.join(missing)} — "
            f"report this to your orchestrator."
        )

    return header + "\n\n" + "\n\n---\n\n".join(sections)


def emit(tier: str, litesuite_hint: Optional[bool] = None) -> None:
    """Print the composed preamble into SessionStart stdout (→ agent context).

    Called from hooks.register_presence. $LITEHARNESS_NO_PREAMBLE=1 skips (escape
    hatch for probes/tests). Any exception is CAUGHT AND PRINTED by the caller —
    never let preamble delivery break session start, never let it fail silently.
    """
    if os.environ.get("LITEHARNESS_NO_PREAMBLE", "").strip() == "1":
        return
    print("\n" + compose(tier, detect_mode(litesuite_hint)) + "\n")


# ---------------------------------------------------------------------------
# Generated orchestrator IDENTITY — the slash-command skill and its personality
# ---------------------------------------------------------------------------


def resolve_skill_target(name: str) -> Tuple[Optional[Path], str]:
    """Where the GENERATED slash-command skill for an orchestrator must be written.

    `/ls-init-liteharness` produced a personality file and nothing else, so the
    command that loads it — Ryan's `/sentinel` — was a hand-built personal skill that
    existed on exactly one machine. Every other user finished the interview with an
    architecture they had no way to invoke.

    Claude Code discovers skills at `~/.claude/skills/<dir>/SKILL.md` and exposes each
    as `/<dir>`, so the directory name IS the command name. We slug it with the same
    `orchestrator_slug()` the architecture file uses, which is what keeps the two
    halves addressable by one key.

    Returns (path, reason). Never invents a fallback location: a skill written
    somewhere Claude Code does not scan is not a command, it is an unread file.
    """
    home = Path.home()
    if not home or not str(home).strip():
        return None, "no home directory resolved"
    slug = orchestrator_slug(name)
    return home / ".claude" / "skills" / slug / "SKILL.md", f"claude-code skills dir for {slug!r}"


def verify_orchestrator_identity(name: str) -> Tuple[bool, str]:
    """Assert a generated orchestrator is actually LOADABLE under its own name.

    Generation is not done when the files exist — it is done when the RESOLVER
    returns them. Two distinct failures this catches, both of which were live:

      1. The architecture resolves to `default.md`. `resolve_cognitive_file()` falls
         back to the tier default so an orchestrator always has SOMETHING, which is
         correct behaviour and also means a personalised file that landed in the wrong
         place is indistinguishable from success. Checking only "did it resolve" can
         never see this; you must check WHICH FILE resolved.
      2. The architecture is named after the HUMAN. A `ryan.md` sitting beside
         `default.md` looks like a completed interview and is never loaded by anything,
         because the resolver keys on the AGENT's name.

    Returns (ok, detail). Callers must be LOUD on False — an orchestrator silently
    running the generic architecture is the exact outcome the interview exists to avoid.
    """
    slug = orchestrator_slug(name)
    arch = resolve_cognitive_file(name, "orchestrator")
    if arch is None:
        return False, f"no architecture resolves for {name!r} (slug {slug!r})"
    if arch.stem.lower() == "default" and slug != "default":
        return (
            False,
            f"{name!r} resolves to the SHIPPED DEFAULT ({arch}), not a generated "
            f"architecture — the personalised file is missing or was written where the "
            f"resolver cannot see it",
        )
    if arch.stat().st_size == 0:
        return False, f"architecture for {name!r} is EMPTY ({arch})"

    skill, why = resolve_skill_target(name)
    if skill is None:
        return False, f"no skill target resolved: {why}"
    if not skill.is_file():
        return False, f"architecture OK ({arch.name}) but NO SKILL at {skill} — the user has a personality with no command to invoke it"
    if arch.name not in skill.read_text(encoding="utf-8", errors="replace"):
        return (
            False,
            f"skill {skill} does not reference its architecture {arch.name} — the two "
            f"halves are not linked, so the command will load nothing",
        )
    return True, f"{name!r}: architecture {arch} + skill {skill}, linked"


# ---------------------------------------------------------------------------
# Divergence diagnosis
# ---------------------------------------------------------------------------


def diagnose(name: str = "Sentinel", tier: str = "orchestrator") -> str:
    """Show what EVERY candidate root holds, not just the winner.

    `resolve_prompts_dir()` returns the FIRST root that exists, which is the right
    behaviour and also means a single resolved path cannot reveal that two roots
    DISAGREE. Disagreeing roots is not hypothetical: on 2026-08-12 the repo tree
    carried `sentinel.md` while the installed plugin cache still carried `ryan.md`,
    so the same call answered differently depending on which machine ran it, with no
    error either way.

    Reading one path tells you what you got. This tells you what you could have got.
    """
    slug = orchestrator_slug(name)
    lines = [f"cognitive architecture for {name!r} (slug {slug!r}) at tier {tier!r}", ""]

    # Same candidate order resolve_prompts_dir() uses, walked exhaustively instead of
    # stopping at the first hit.
    roots: list[tuple[str, Path]] = []
    override = os.environ.get("LITEHARNESS_PROMPTS_DIR", "").strip()
    if override:
        roots.append(("env", Path(override)))
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:8]:
        cand = parent / "resources" / "liteharness-plugin" / "prompts"
        if cand.is_dir():
            roots.append(("repo", cand))
            break
    for parent in list(here.parents)[:8]:
        cand = parent / "LiteSuite" / "resources" / "liteharness-plugin" / "prompts"
        if cand.is_dir():
            roots.append(("sibling-repo", cand))
            break
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        packaged = (
            Path(local_app) / "Programs" / "litesuite-desktop"
            / "resources" / "liteharness-plugin" / "prompts"
        )
        if packaged.is_dir():
            roots.append(("install", packaged))
    for cand in _plugin_cache_candidates():
        roots.append((f"plugin-cache:{cand.parent.name}", cand))

    if not roots:
        return "no prompt library resolved in ANY known location"

    for label, root in roots:
        tier_dir = root / "cognitive-architectures" / (_TIER_TO_COG_DIR.get(tier, tier))
        present = sorted(p.stem for p in tier_dir.glob("*.md")) if tier_dir.is_dir() else []
        mark = "HIT " if slug in present else "miss"
        lines.append(f"  [{mark}] {label:<22} {tier_dir}")
        lines.append(f"         holds: {', '.join(present) if present else '(nothing)'}")

    ok, detail = verify_orchestrator_identity(name)
    lines += ["", f"  => {'OK' if ok else 'PROBLEM'}: {detail}"]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    import sys

    print(diagnose(sys.argv[1] if len(sys.argv) > 1 else "Sentinel"))
