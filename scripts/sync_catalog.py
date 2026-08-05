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
# shipped inside the published 0.2.2/0.2.3 wheels. Adding it here is the fix —
# this list, not review, is what keeps a skill out of the package.
PRIVATE_SKILLS: tuple[str, ...] = (
    "ls-discord-watch",
    "discord-watch",
    "ls-streaming-sl-obs",
    "streaming-sl-obs",
    "ls-release-litesuite",
)


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
