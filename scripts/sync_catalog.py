#!/usr/bin/env python
"""Sync the LiteHarness skills + agents catalog from liteharness-plugin into this pip package.

Run before `python -m build` to vendor the latest catalog into `liteharness/catalog/`.
The catalog is shipped as package_data via pyproject.toml.

Source-of-truth: the explicitly selected liteharness-plugin checkout (--source).
Destination:     ./liteharness/catalog/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

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
    # the user, 2026-08-16: never ship. Removed from PUBLIC_SKILLS in the same edit —
    # naming a skill in BOTH sets makes the sync ABORT by design, so a one-sided
    # move here would have looked like a denylist working and been a broken gate.
    # the user, 2026-08-16: the WHOLE ls-arch* family is private, not just -opus.
    # These read LiteSuite's own docs/architecture/ tree — ports, panel names,
    # doc filenames — so shipping them hands every end user a reference to a
    # codebase that is not theirs. The replacement is end-user arch docs
    # generated per project by that user's own the orchestrator after assistant setup.
    # Removed until that exists.
    #
    # 🔴 AMENDED by the user, 2026-08-16 (later the same day): "only arch-gen should ship",
    # and "ls-arch-gen and ls-init-liteharness go together, they ship". So ls-arch-gen is
    # now PUBLIC and the sentence that used to sit here — "ls-arch-gen goes too, by
    # explicit ruling" — is WITHDRAWN. Do not re-apply it from the commit message of
    # 508fb5f, which still carries the superseded wording.
    #
    # The distinction that makes the amendment coherent: the other three READ LiteSuite's
    # own architecture docs and hardcode their contents. ls-arch-gen GENERATES docs for
    # whatever project it is pointed at. Verified before shipping — its only two
    # "LiteSuite" mentions are a provenance sentence and a generic conditional; zero port
    # numbers, zero doc filenames, zero panel names (positive control: ls-arch-opus, 6 hits).
    "ls-arch-opus",
    "ls-arch",
    "ls-arch-fable",
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
    "ls-ao", "ls-canvas-design",
    "ls-casestudy", "ls-caveman-mode", "ls-comfy-to-liteimage",
    "ls-compile-cli", "ls-consult", "ls-consult-polymaths",
    "ls-conversation-lookup", "ls-debug", "ls-design-huashu",
    "ls-devstral", "ls-eva",
    "ls-eval-gate", "ls-find-skills", "ls-gen-image-or-video",
    # ls-arch-gen: PUBLIC by the user's amendment 2026-08-16 — it GENERATES arch docs for the
    # user's own project rather than hardcoding LiteSuite's. Ships together with
    # ls-init-liteharness; the pair is the end-user setup path the ls-arch* ban deferred to.
    "ls-arch-gen",
    "ls-generative-ui", "ls-init-liteharness", "ls-insights-deep",
    "ls-k-find-app", "ls-leader", "ls-librarian",
    "ls-library", "ls-liteharness", "ls-litetui",
    "ls-litewatch", "ls-local-lens", "ls-max-parallel",
    "ls-max-swarm", "ls-pdf",
    "ls-plan-w-quizmaster", "ls-plan-w-quizmaster-sonnet", "ls-playwright-e2e-screenshots",
    "ls-rebuild-release", "ls-repo-rank", "ls-reviewer",
    "ls-scout", "ls-self-improve", "ls-sentinel",
    "ls-sessions", "ls-skill-author", "ls-spawnteam",
    "ls-stitch-pipeline", "ls-taste", "ls-thinker",
    "ls-tldr", "ls-train", "ls-tts",
    "ls-typescript-react-reviewer", "ls-vault", "ls-video-download",
    "ls-video-lens", "ls-watch", "ls-worker",
    "ls-youtube",
    # the user, 2026-09-23 (liteask a-1fa60460): "Public, scrub gauntlet's paths".
    "ls-gauntlet", "ls-mark", "ls-local-batch-agent",
    # the user, 2026-09-25 (T916-H, via the orchestrator fce49c8c): theater is PUBLIC. It is
    # ls-mockup renamed upstream (LiteSuite 92c6a48d0), so ls-mockup left this set.
    # the user, 2026-09-26 (T947): "all shipped skills are prefixed with ls-" -> ls-theater.
    "ls-theater",
    # Legacy compatibility alias delegates to ls-theater, not a competing implementation.
    "ls-mockup",
})

# Skills whose version in THIS catalog wins over the source, kept across the wipe so the
# sync neither deletes nor reverts them, and named in PROVENANCE.json so the stamp says
# which skills did NOT come from source_commit. the orchestrator's ruling 6aa9feed (T916-H plan b).
# Drop a hold once the sync prints "upstream now identical" (the port is card T916-J).
# ls-draw: never in LiteSuite's plugin. If it lands there it is UNCLASSIFIED and the gate
# aborts, which is the cue to move it to PUBLIC_SKILLS and delete it from here.
# The other six: edited in this repo after the 2026-08-18 sync and never upstreamed; every
# LiteSuite blob in them is an EARLIER state of this repo (git log --all --find-object).
KEEP_FROM_CATALOG: dict[str, str] = {
    "ls-draw": "catalog-only, e9cd75b",
    "ls-consult": "ahead of source, 90410bf 20fe0e7",
    "ls-conversation-lookup": "ahead of source, 1173e8e",
    "ls-gen-image-or-video": "ahead of source, dbaf186 f38e0d9",
    "ls-liteharness": "ahead of source, 1380f18 ecb3488",
    "ls-mark": "ahead of source, ad8456e c9ee13e",
    "ls-youtube": "ahead of source, 3f49aef",
}


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


# The pristine module, kept OUTSIDE the directory this script wipes. Embedding it as a
# string constant here is what went wrong before: the constant drifted from the real
# module and nothing compared them. A file on disk, in git, is one source of truth.
CATALOG_INIT_TEMPLATE = Path(__file__).resolve().parent / "catalog_init_template.py"

# The names liteharness/installers.py imports at module scope. If the vendored
# __init__.py does not define these, `import liteharness.installers` raises and the
# LiteSuite first-run wizard's "Install skills into other CLIs" step dies with a
# traceback in the UI. Derived from the consumer, so it cannot silently disagree.
REQUIRED_CATALOG_NAMES = ("catalog_root", "subtree")


def capture_catalog_init(dest_root: Path) -> str | None:
    """Read the REAL __init__.py before anything deletes it. Call BEFORE the wipe."""
    init = dest_root / "__init__.py"
    if init.is_file():
        return init.read_text(encoding="utf-8")
    return None


def capture_kept_skills(dest_root: Path) -> dict[str, bytes]:
    """Read every KEEP_FROM_CATALOG skill's files before the wipe. Call BEFORE the wipe."""
    kept: dict[str, bytes] = {}
    for name in KEEP_FROM_CATALOG:
        d = dest_root / "skills" / name
        if not d.is_dir():
            raise SystemExit(f"[catalog] FATAL: kept skill {name} is not at {d} — nothing to keep")
        for f in d.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                kept[f.relative_to(dest_root).as_posix()] = f.read_bytes()
    return kept


def restore_catalog_init(dest_root: Path, preserved: str | None) -> None:
    """Put the catalog module back after the wipe, and PROVE it is the real one.

    🔴 HOW THIS FAILED ON 0.3.1, because the previous version of this function looked
    correct and was not. It wrote a one-line docstring constant, guarded by
    `if not init.exists()`. That guard reads as "never clobber a good file", and it is
    true as far as it goes - but `sync(--clean)` rmtree's `dest_root` itself, so by the
    time this ran there was NO file to clobber, and the stub won uncontested. The real
    57-line module - defining catalog_root and subtree - was replaced by 1 line, and the
    commit that did it was titled "restore catalog/__init__.py".

    ⭐ The predicate was "a file exists at this path". The promise was "the catalog
    imports". A namespace package satisfies the first and fails the second, which is
    exactly the 0.2.4 failure this function was written to prevent - reintroduced by its
    own fix, one level in: ModuleNotFoundError became
    `ImportError: cannot import name 'catalog_root'`.

    So: prefer the content that was actually there (preserved across the wipe), fall back
    to the tracked template, and then ASSERT the required names are present. Existence is
    never again treated as sufficient.
    """
    init = dest_root / "__init__.py"

    if preserved is not None:
        init.write_text(preserved, encoding="utf-8")
        source = "preserved across the wipe"
    elif CATALOG_INIT_TEMPLATE.is_file():
        init.write_text(CATALOG_INIT_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        source = f"template {CATALOG_INIT_TEMPLATE.name}"
    else:
        raise SystemExit(
            f"[catalog] FATAL: nothing to restore {init} from - no preserved copy and no "
            f"template at {CATALOG_INIT_TEMPLATE}. Refusing to ship a namespace package."
        )

    text = init.read_text(encoding="utf-8")
    missing = [n for n in REQUIRED_CATALOG_NAMES if f"def {n}" not in text]
    if missing:
        raise SystemExit(
            f"[catalog] FATAL: restored {init} from {source} but it does not define "
            f"{', '.join(missing)}. liteharness.installers imports these at module scope, "
            f"so the wheel would build clean and die on first use. This is the 0.3.1 bug."
        )
    print(f"[fix] restored {init} from {source} ({len(text.splitlines())} lines, "
          f"defines {', '.join(REQUIRED_CATALOG_NAMES)})")


def assert_catalog_imports(pkg_root: Path) -> None:
    """The gate that would have caught 0.3.1: IMPORT it, in a fresh interpreter.

    Reading the file proves the text is right; only an import proves the package is.
    A subprocess is used deliberately - this process may already have a stale
    `liteharness.catalog` in sys.modules from an earlier import, and a cached module is
    the one thing that could make a broken package look fine.
    """
    code = (
        "from liteharness.catalog import " + ", ".join(REQUIRED_CATALOG_NAMES) + "\n"
        "r = catalog_root()\n"
        "assert r.is_dir(), f'catalog_root() -> {r} which is not a directory'\n"
        "s = subtree('skills')\n"
        "assert s.is_dir(), f'subtree(skills) -> {s} which is not a directory'\n"
        "n = sum(1 for _ in s.iterdir() if _.is_dir())\n"
        "assert n > 0, 'subtree(skills) is EMPTY - a clean import over nothing'\n"
        "print(f'[gate] catalog imports: {n} skills under {s}')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=str(pkg_root),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(
            "[catalog] FATAL: the vendored catalog does not import.\n"
            + (r.stderr or r.stdout).strip()
            + "\n  This is precisely how 0.2.4 and 0.3.1 shipped dead on arrival: the wheel "
              "builds clean because a namespace package is legal."
        )
    print(r.stdout.strip())


# Files in the catalog dir that are not vendored content. The hash skips them so it can be
# recomputed on an installed or checked-out catalog (tests/test_catalog_provenance.py does).
UNHASHED = {"__init__.py", "PROVENANCE.json"}


def is_runtime_catalog_path(relative: str | Path) -> bool:
    """Precise runtime families, folding ASCII A-Z only on every platform.

    Unicode near misses remain content. Release acceptance separately verifies
    actual artifacts; this predicate is not a universal setuptools guarantee.
    """
    ascii_fold = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    parts = tuple(part.translate(ascii_fold) for part in Path(relative).parts)
    name = parts[-1] if parts else ""
    if "__pycache__" in parts or name in {".last_indexed", "index.lock"}:
        return True
    extensions = (".db", ".sqlite", ".sqlite3")
    sidecars = tuple(extension + suffix for extension in extensions
                     for suffix in ("-wal", "-shm", "-journal"))
    return name.endswith((".pyc", *extensions, *sidecars))


def hashed_files(path: Path) -> dict[str, Path]:
    """Release-content projection, keyed by POSIX path relative to `path`.

    The historical UNHASHED basenames are excluded at every depth. Payload
    verification must separately compare their presence and bytes. Kept-skill
    equality notes compare this same release-content domain, not runtime state;
    capture/copy/restore still preserves the original files independently.
    """
    return {f.relative_to(path).as_posix(): f for f in path.rglob("*")
            if f.is_file() and f.name not in UNHASHED
            and not is_runtime_catalog_path(f.relative_to(path))}


def short_sha(path: Path) -> str:
    """Hash what ships, identically on every copy of it, with no git in the loop.

    🔴 0.4.x main went RED on its own stamp: this used to hash raw working-tree bytes.
    A Windows checkout holds CRLF for files whose git blob is LF (7 of them on main at
    ed102a9, `git ls-files --eol`: i/lf w/crlf), so the same catalog hashed two ways.
    It also ignored names, so a rename never moved the hash, and it hashed an empty or
    missing directory to sha256("") = e3b0c44298fc instead of refusing.

    Per file, in sorted relpath order: relpath, NUL, sha256(content). EOL rule: a file
    with no NUL byte is text and is hashed with CRLF -> LF (git's canonical form here:
    every text blob in the index is LF); a file with a NUL byte is hashed raw.
    """
    files = hashed_files(path)
    if not files:
        raise SystemExit(f"[catalog] FATAL: no files to hash under {path} — wrong path?")
    h = hashlib.sha256()
    for rel in sorted(files):
        data = files[rel].read_bytes()
        if b"\0" not in data:
            data = data.replace(b"\r\n", b"\n")
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()[:12]


def sync(src_root: Path, dest_root: Path, clean: bool,
         source_commit: str | None = None) -> dict[str, str | int]:
    if not src_root.exists():
        raise SystemExit(f"Source repo not found: {src_root}")

    # BEFORE anything is copied or deleted. After the gate passes, `present` is
    # exactly PUBLIC ∪ PRIVATE, and ignore_patterns strips PRIVATE — so what
    # lands in the package is exactly PUBLIC, by construction rather than by
    # anybody remembering.
    gate_skill_classification(src_root)

    # Read the real catalog module BEFORE anything deletes it. `--clean` rmtree's
    # dest_root itself, which is how the 57-line module became a 1-line stub on 0.3.1.
    preserved_init = capture_catalog_init(dest_root)
    kept = capture_kept_skills(dest_root)

    if clean and dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)

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
        print(f"[copy] {rel_src} -> {d.relative_to(THIS_PKG_ROOT)} ({n} files)")

    # Replace each kept skill WHOLE: rmtree first, or the source's stale files would
    # survive beside the kept ones (ls-gen-image-or-video's chatgpt_*.py, deleted in dbaf186).
    for name in KEEP_FROM_CATALOG:
        d = dest_root / "skills" / name
        upstream = short_sha(d) if d.is_dir() else None
        if d.is_dir():
            shutil.rmtree(d)
        for rel, data in kept.items():
            if rel.startswith(f"skills/{name}/"):
                (dest_root / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest_root / rel).write_bytes(data)
        note = " — upstream now identical, drop this hold" if upstream == short_sha(d) else ""
        print(f"[keep] {name}: {KEEP_FROM_CATALOG[name]}{note}")
    file_count = len(hashed_files(dest_root))

    # With a commit, the source is a `git archive` export whose temp path means nothing
    # (and would be a home path), so stamp the repo, commit and in-repo path instead.
    manifest = {
        "source_repo": "ahostbr/LiteSuite" if source_commit else "ahostbr/liteharness-plugin",
        "source_commit": source_commit,
        "source_path": "resources/liteharness-plugin" if source_commit else str(src_root),
        "kept_from_catalog": KEEP_FROM_CATALOG,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "subtrees": [rel_dest for _, rel_dest in SUBTREES if (dest_root / rel_dest).exists()],
        "file_count": file_count,
        "hash": short_sha(dest_root),
    }
    (dest_root / "PROVENANCE.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    restore_catalog_init(DEST_ROOT, preserved_init)
    # Text-level checks above prove the FILE is right; this proves the PACKAGE is.
    assert_catalog_imports(THIS_PKG_ROOT)
    print(f"[ok] wrote PROVENANCE.json — {file_count} files, hash {manifest['hash']}")
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Required path to the reviewed liteharness-plugin checkout",
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
    p.add_argument(
        "--source-commit",
        help="LiteSuite commit that --source was `git archive`d from; stamped into PROVENANCE.json",
    )
    args = p.parse_args()
    try:
        sync(args.source, args.dest, clean=not args.no_clean, source_commit=args.source_commit)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
