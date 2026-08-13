#!/usr/bin/env python
"""Sync the LiteHarness skills + agents catalog from liteharness-plugin into this pip package.

Run before `python -m build` to vendor the latest catalog into `liteharness/catalog/`.
The catalog is shipped as package_data via pyproject.toml.

Source-of-truth: C:/Projects/LiteSuite/resources/liteharness-plugin/
Destination:     ./liteharness/catalog/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SOURCE = Path("C:/Projects/LiteSuite/resources/liteharness-plugin")
THIS_PKG_ROOT = Path(__file__).resolve().parents[1]
DEST_ROOT = THIS_PKG_ROOT / "liteharness" / "catalog"

# Subtrees to vendor — each tuple is (relative_src, relative_dest)
SUBTREES: tuple[tuple[str, str], ...] = (
    ("skills", "skills"),
    ("agents", "agents"),
    ("commands", "commands"),
    ("hooks", "hooks"),
)

# Private (personal) skills — NEVER vendor into the public pip catalog. These
# contain PII (cameras, family, identity) and are local-only on the source
# machine. fnmatch-matched as directory basenames during copytree.
#
# ls-release-litesuite is here for a different reason: it is operator-internal.
# It names every build-time secret variable and states which conditions leave
# licence verification disabled. No secret VALUES, but it maps the enforcement
# seams. It was absent from this list, so it vendored into the catalog and
# shipped inside the published 0.2.2/0.2.3 wheels.
#
# 🔴 THIS LIST IS NO LONGER THE CONTROL, and the sentence that used to sit here
# — "this list, not review, is what keeps a skill out of the package" — was an
# admission that the gate FAILED OPEN. A denylist only excludes what somebody
# remembered to write down, so every skill added after it was written shipped by
# default. That is how ls-release-litesuite leaked in the first place: nobody
# decided to publish it, nobody decided not to, and "no decision" resolved to
# "publish". PUBLIC_SKILLS below is the actual gate now; this tuple survives only
# as the "already decided, stop asking" set and as the copytree ignore pattern.
PRIVATE_SKILLS: tuple[str, ...] = (
    "ls-discord-watch",
    "discord-watch",
    "ls-streaming-sl-obs",
    "streaming-sl-obs",
    "ls-release-litesuite",
)

# The allowlist. A skill ships ONLY if it is named here — the inverse of the
# denylist above, so an unclassified skill is excluded rather than published.
#
# Every entry was verified by content scan (2026-08-12, 305 files across 61
# skills) for personal identifiers, home paths, credentials and the build-time
# secret variable names, not merely by "it was in the directory".
#
# Adding a skill to the plugin does NOT add it here. That is the entire point:
# the sync ABORTS on anything it has never been told about, so the decision is
# forced to a human at publish time instead of defaulting either way.
PUBLIC_SKILLS: frozenset[str] = frozenset({
    "ls-ao", "ls-arch", "ls-arch-fable",
    "ls-arch-gen", "ls-arch-opus", "ls-canvas-design",
    "ls-casestudy", "ls-caveman-mode", "ls-comfy-to-liteimage",
    "ls-compile-cli", "ls-consult", "ls-consult-polymaths",
    "ls-conversation-lookup", "ls-debug", "ls-design-huashu",
    "ls-devstral", "ls-dream-consolidate", "ls-eva",
    "ls-eval-gate", "ls-find-skills", "ls-gen-image-or-video",
    "ls-generative-ui", "ls-init-liteharness", "ls-insights-deep",
    "ls-k-find-app", "ls-leader", "ls-librarian",
    "ls-library", "ls-liteharness", "ls-litetui",
    "ls-litewatch", "ls-local-lens", "ls-max-parallel",
    "ls-max-swarm", "ls-mockup", "ls-pdf",
    "ls-plan-w-quizmaster", "ls-plan-w-quizmaster-sonnet", "ls-playwright-e2e-screenshots",
    "ls-rebuild-release", "ls-repo-rank", "ls-reviewer",
    "ls-scout", "ls-self-improve", "ls-sentinel",
    "ls-sessions", "ls-skill-author", "ls-spawnteam",
    "ls-stitch-pipeline", "ls-taste", "ls-thinker",
    "ls-tldr", "ls-train", "ls-tts",
    "ls-typescript-react-reviewer", "ls-vault", "ls-video-download",
    "ls-video-lens", "ls-watch", "ls-worker",
    "ls-youtube-transcript",
})


def gate_skill_classification(src_root: Path) -> None:
    """Refuse to vendor anything nobody has classified. Fail CLOSED and LOUD.

    Three outcomes, and only one of them is silent:

      * unknown  -> SystemExit. A skill present in the plugin but absent from
                    both PUBLIC_SKILLS and PRIVATE_SKILLS has never been ruled
                    on. Publishing it is a guess and excluding it silently is
                    also a guess, so the sync stops and names it.
      * missing  -> warn. A skill listed PUBLIC that is no longer in the source
                    has been deleted or renamed upstream. Harmless to ship
                    around, but a silent drop is how a skill disappears from the
                    package while everyone assumes it is still there.
      * matched  -> proceed.

    The empty-source check is a POSITIVE CONTROL. Without it this function
    returns "clean" when handed a wrong or empty path — the same shape as a PII
    scan reporting "clean (0 files scanned)", which is not a pass, it is a
    broken instrument.
    """
    skills_dir = src_root / "skills"
    if not skills_dir.is_dir():
        raise SystemExit(f"[gate] FATAL: no skills/ directory under {src_root} — refusing to vendor")

    present = {p.name for p in skills_dir.iterdir() if p.is_dir()}
    if not present:
        raise SystemExit(f"[gate] FATAL: {skills_dir} contains no skill directories — refusing to vendor")

    known = PUBLIC_SKILLS | set(PRIVATE_SKILLS)
    unknown = sorted(present - known)
    missing = sorted(PUBLIC_SKILLS - present)

    for name in missing:
        print(f"[gate][warn] listed PUBLIC but absent from source: {name} — deleted upstream?")

    if unknown:
        listed = "\n".join(f"    {n}" for n in unknown)
        raise SystemExit(
            f"[gate] FATAL: {len(unknown)} unclassified skill(s) in {skills_dir}:\n{listed}\n\n"
            "  Nothing ships until each one is placed deliberately. Edit scripts/sync_catalog.py:\n"
            "    PUBLIC_SKILLS  — vendored into the public pip package. Read it first: no personal\n"
            "                     identifiers, no home paths, no credentials, no map of the licence\n"
            "                     enforcement seams.\n"
            "    PRIVATE_SKILLS — local-only, excluded from the copy.\n"
        )

    print(f"[gate] {len(present)} skills: {len(present & PUBLIC_SKILLS)} public, "
          f"{len(present & set(PRIVATE_SKILLS))} private, 0 unclassified")


CATALOG_INIT = """\"\"\"Vendored LiteHarness catalog (skills, agents, commands, hooks).\"\"\"
"""


def restore_catalog_init(dest_root: Path) -> None:
    """Re-create liteharness/catalog/__init__.py after the copytree wipes it.

    LOAD-BEARING. This script replaces the catalog directory wholesale, which deletes
    __init__.py every single run. Without that file the directory is a PEP 420 NAMESPACE
    package: the ~400 data files still ship, `import liteharness.catalog` still appears to
    succeed, and `catalog.__file__` is None - so `from liteharness.catalog import
    catalog_root` raises ImportError with an "(unknown location)" traceback.

    That is precisely how 0.2.4 shipped dead on arrival for every user, and it would have
    happened again on 0.3.1: the sync ran, the file vanished, and the wheel built cleanly.
    Nothing in the build complains, because a namespace package is legal.
    """
    init = dest_root / "__init__.py"
    if not init.exists():
        init.write_text(CATALOG_INIT, encoding="utf-8")
        print(f"[fix] restored {init} (copytree deletes it every run)")


def short_sha(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    else:
        for f in sorted(path.rglob("*")):
            if f.is_file():
                h.update(f.read_bytes())
    return h.hexdigest()[:12]


def sync(src_root: Path, dest_root: Path, clean: bool) -> dict[str, str | int]:
    if not src_root.exists():
        raise SystemExit(f"Source repo not found: {src_root}")

    # BEFORE anything is copied or deleted. After the gate passes, `present` is
    # exactly PUBLIC ∪ PRIVATE, and ignore_patterns strips PRIVATE — so what
    # lands in the package is exactly PUBLIC, by construction rather than by
    # anybody remembering.
    gate_skill_classification(src_root)

    if clean and dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)

    file_count = 0
    for rel_src, rel_dest in SUBTREES:
        s = src_root / rel_src
        d = dest_root / rel_dest
        if not s.exists():
            print(f"[skip] {rel_src} not present in source")
            continue
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(
            s,
            d,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", *PRIVATE_SKILLS),
        )
        n = sum(1 for _ in d.rglob("*") if _.is_file())
        file_count += n
        print(f"[copy] {rel_src} -> {d.relative_to(THIS_PKG_ROOT)} ({n} files)")

    manifest = {
        "source_repo": "ahostbr/liteharness-plugin",
        "source_path": str(src_root),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "subtrees": [rel_dest for _, rel_dest in SUBTREES if (dest_root / rel_dest).exists()],
        "file_count": file_count,
        "hash": short_sha(dest_root),
    }
    (dest_root / "PROVENANCE.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    restore_catalog_init(DEST_ROOT)
    print(f"[ok] wrote PROVENANCE.json — {file_count} files, hash {manifest['hash']}")
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Path to liteharness-plugin checkout (default: %(default)s)",
    )
    p.add_argument(
        "--dest",
        type=Path,
        default=DEST_ROOT,
        help="Destination catalog dir inside the pip pkg (default: %(default)s)",
    )
    p.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip cleaning the destination dir before sync",
    )
    args = p.parse_args()
    try:
        sync(args.source, args.dest, clean=not args.no_clean)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
