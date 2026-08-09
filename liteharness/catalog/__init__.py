"""Locators for the shipped LiteHarness catalog (skills, agents, commands, hooks).

🔴 ITS ABSENCE FROM `main` MADE `liteharness install` UNRUNNABLE FOR EVERY 0.2.x USER.

`installers.py:23` does `from liteharness.catalog import catalog_root, subtree`. On `main`
this module did not exist, so `liteharness.catalog` resolved as a *namespace package* — a
directory Python can see but which exports nothing — and every install died at import:

    ImportError: cannot import name 'catalog_root' from 'liteharness.catalog' (unknown location)

⭐ `(unknown location)` is the namespace-package tell: Python found the directory and no
module. Reach for packaging before suspecting the code.

Observed end-to-end 2026-08-09: in a virgin Windows Sandbox, the LiteSuite first-run
wizard's "Install skills into other CLIs" step rendered this traceback and offered
"Install 0 CLIs".

## It is a REGRESSION VIA PARTIAL MERGE, not a file nobody wrote

The module was written in `76a9ab9` (2026-05-28) on `feat/universal-cli-installer`, and
**that branch was never merged**. `main` acquired `installers.py` and the ~380 catalog data
files by a partial port that left this one Python file behind. There is no deletion commit
because it was never on `main` at all — which is why searching `main`'s history for a
removal finds nothing and makes it look like it never existed.

⭐ Do NOT "fix" this by merging the branch. `main` has diverged since (92 files apart, newer
skills, `scripts/check_pii.py`), so a merge would resurrect deleted skills and drop newer
work. The missing file was the entire gap.

`CATALOG_DIR` and `provenance()` are restored from the original rather than reinvented: a
reimplementation that quietly narrows a package's public surface is its own defect, and it
only ever surfaces as an AttributeError in somebody else's code.

Note on packaging — adding this file makes `catalog` a real package, so
`[tool.setuptools.packages.find]` now discovers `liteharness.catalog` and the `package-data`
globs written relative to the `liteharness` package no longer necessarily reach inside it.
The data is declared under both keys; verify by listing the built wheel, not the config.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["CATALOG_DIR", "catalog_root", "provenance", "subtree"]

# `__file__`-relative rather than importlib.resources: the catalog ships as package-data, so
# pip lays it down as real files beside this module, and the consumer
# (`installers._copy_subtree`) walks a real directory tree. `importlib.resources.files()`
# returns a Traversable, only guaranteed to be a real filesystem path for unzipped installs.
CATALOG_DIR: Path = Path(__file__).resolve().parent


def catalog_root() -> Path:
    """Absolute path to the directory holding the shipped catalog data."""
    return CATALOG_DIR


def provenance() -> dict[str, object]:
    """PROVENANCE.json metadata (source repo, sync time, file count, hash)."""
    pf = CATALOG_DIR / "PROVENANCE.json"
    if not pf.exists():
        return {"error": "PROVENANCE.json not present — catalog may not be synced"}
    return json.loads(pf.read_text(encoding="utf-8"))


def subtree(name: str) -> Path:
    """Absolute path to one catalog subtree: ``skills`` / ``agents`` / ``commands`` / ``hooks``.

    Raises FileNotFoundError rather than returning a missing path, because the caller copies
    from it: a silently-absent source would report "Copied 0 files" and look like a
    successful install of nothing.
    """
    path = CATALOG_DIR / name
    if not path.is_dir():
        available = sorted(p.name for p in CATALOG_DIR.iterdir() if p.is_dir())
        raise FileNotFoundError(
            f"catalog subtree {name!r} not found under {CATALOG_DIR}. Available: {available}. "
            "If this is an installed package, the catalog data did not ship — check "
            "[tool.setuptools.package-data] in pyproject.toml."
        )
    return path
