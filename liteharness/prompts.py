"""
Cognitive-architecture prompt resolution.

WHY THIS MODULE EXISTS
The `/sentinel` skill opens with a MANDATORY first action:

    python -c "from liteharness.prompts import resolve_cognitive_file as r; \
               print(r('Sentinel','orchestrator'))"
    python -c "from liteharness.prompts import verify_orchestrator_identity as v; \
               print(v('Sentinel'))"

Neither existed. `liteharness.prompts` was not a module at all, so the step that
exists specifically to catch "you silently loaded the generic default" raised
ModuleNotFoundError instead of returning a verdict. The guard was missing, which
is the one failure a guard cannot survive.

THE FAILURE IT GUARDS AGAINST, WHICH IS REAL AND STILL PARTLY LIVE
The personal architecture was originally generated as `ryan.md` — named after the
HUMAN — while resolution keys on the AGENT's name. Every Sentinel session
therefore resolved `default.md` and nobody could tell, because a fallback that
always succeeds is indistinguishable from a hit. It was renamed to `sentinel.md`
in the LiteSuite repo on 2026-08-09.

That rename did NOT reach the installed plugin. Measured 2026-08-12:

    LiteSuite repo         orchestrator/  default.md  sentinel.md
    plugin cache 1.0.9     orchestrator/  default.md  ryan.md
    plugin cache 1.0.8     orchestrator/              ryan.md

So a session running off the installed plugin still falls back to `default.md`,
and one running off the repo does not. Same command, two answers, no error either
way. `diagnose()` exists to make exactly that divergence visible in one call
rather than requiring someone to think of comparing two trees.

DELIBERATE NON-BEHAVIOUR: nothing here falls back to `ryan.md`. Resolving the
old human-named file would make every caller "work" again and permanently hide
the fact that the shipped tree is stale. A silent success is what created this
bug; it is not going to be the fix for it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

DEFAULT_NAME = "default"
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _plugin_cache_roots() -> list[Path]:
    """
    Installed plugin prompt trees, newest version first.

    Versions are sorted NUMERICALLY. A lexical sort puts 1.0.9 above 1.0.10,
    which would silently pin resolution to a stale tree the moment a two-digit
    patch ships — the kind of bug that only appears months later.
    """
    base = Path.home() / ".claude" / "plugins" / "cache" / "liteharness" / "liteharness"
    if not base.is_dir():
        return []
    versioned: list[tuple[tuple[int, int, int], Path]] = []
    for child in base.iterdir():
        m = _VERSION_RE.match(child.name)
        if m and (child / "prompts").is_dir():
            versioned.append((tuple(int(g) for g in m.groups()), child / "prompts"))
    return [p for _, p in sorted(versioned, reverse=True)]


def prompt_roots() -> list[tuple[str, Path]]:
    """
    Every place a prompt library can live, in resolution order, labelled.

    The label travels with the path on purpose. "Which file did I get?" is only
    half the question; "which TREE did it come from?" is the half that explains
    why two machines disagree.
    """
    # An explicit root is EXCLUSIVE, not merely first. Prepending it instead would
    # mean a tree that lacks the requested file silently falls through to another
    # one — so you could never isolate a single tree to ask what IT holds, and a
    # test of the installed plugin would quietly answer with the repo. That is
    # exactly the mistake this override exists to let you avoid.
    override = os.environ.get("LITEHARNESS_PROMPTS_ROOT")
    if override:
        p = Path(override)
        return [("env", p)] if p.is_dir() else []

    roots: list[tuple[str, Path]] = []

    # Dev checkout: liteharness-oss and LiteSuite are siblings.
    repo = Path(__file__).resolve().parents[2] / "LiteSuite" / "resources" / "liteharness-plugin" / "prompts"
    roots.append(("repo", repo))

    for i, cache in enumerate(_plugin_cache_roots()):
        roots.append((f"plugin:{cache.parent.name}", cache))

    # Future-proofing: a prompts tree shipped inside the wheel itself.
    roots.append(("package", Path(__file__).resolve().parent / "catalog" / "prompts"))

    return [(label, p) for label, p in roots if p.is_dir()]


def _candidate(root: Path, tier: str, stem: str) -> Path:
    return root / "cognitive-architectures" / tier / f"{stem}.md"


def resolve_cognitive_file(name: str, tier: str = "orchestrator") -> Optional[Path]:
    """
    Path to the cognitive architecture for `name` at `tier`.

    Falls back to `default.md` so an agent always has SOMETHING — that fallback
    is deliberate and is also precisely why callers must not treat a returned
    path as proof they got their own file. Use `verify_orchestrator_identity`
    (or check `.stem`) to find out which one you actually received.

    Returns None only when no prompt tree exists anywhere.
    """
    stem = name.strip().lower()
    roots = prompt_roots()
    for _, root in roots:
        hit = _candidate(root, tier, stem)
        if hit.is_file():
            return hit
    for _, root in roots:
        fallback = _candidate(root, tier, DEFAULT_NAME)
        if fallback.is_file():
            return fallback
    return None


def verify_orchestrator_identity(name: str, tier: str = "orchestrator") -> dict:
    """
    Did `name` get its OWN architecture, or the generic default?

    Returns a dict whose `str()` leads with OK or FALLBACK, because the entire
    point is that the two must not look alike at a glance.
    """
    stem = name.strip().lower()
    roots = prompt_roots()
    resolved = resolve_cognitive_file(name, tier)

    personal = [f"{label}:{_candidate(r, tier, stem)}"
                for label, r in roots if _candidate(r, tier, stem).is_file()]

    ok = resolved is not None and resolved.stem == stem
    verdict = {
        "ok": ok,
        "agent": name,
        "tier": tier,
        "resolved": str(resolved) if resolved else None,
        "resolved_stem": resolved.stem if resolved else None,
        "personal_file_found_in": personal,
        "roots_searched": [f"{label}:{p}" for label, p in roots],
    }
    if not ok:
        verdict["problem"] = (
            f"resolved '{verdict['resolved_stem']}' instead of '{stem}'. "
            f"This agent is running the GENERIC template. Generate "
            f"cognitive-architectures/{tier}/{stem}.md, or point "
            f"LITEHARNESS_PROMPTS_ROOT at the tree that has it."
        )
    return verdict


def diagnose(name: str = "Sentinel", tier: str = "orchestrator") -> str:
    """
    Show what EVERY root would resolve to, not just the winner.

    A single resolved path cannot reveal that two trees disagree, and disagreeing
    trees are the actual defect here: the repo carries sentinel.md while the
    installed plugin still carries ryan.md, so the same command answers
    differently depending on which one a session happens to reach first.
    """
    stem = name.strip().lower()
    lines = [f"cognitive architecture for {name!r} at tier {tier!r}", ""]
    roots = prompt_roots()
    if not roots:
        return "no prompt tree found in ANY known location"
    for label, root in roots:
        tier_dir = root / "cognitive-architectures" / tier
        present = sorted(p.stem for p in tier_dir.glob("*.md")) if tier_dir.is_dir() else []
        mark = "HIT " if stem in present else "miss"
        lines.append(f"  [{mark}] {label:<16} {tier_dir}")
        lines.append(f"         holds: {', '.join(present) if present else '(nothing)'}")
    v = verify_orchestrator_identity(name, tier)
    lines += ["", f"  => {'OK' if v['ok'] else 'FALLBACK'}: {v['resolved']}"]
    if not v["ok"]:
        lines.append(f"     {v['problem']}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    import sys
    print(diagnose(sys.argv[1] if len(sys.argv) > 1 else "Sentinel"))
