"""Locators for the shipped LiteHarness catalog (skills, agents, commands, hooks).

🔴 THIS FILE HAD NEVER EXISTED, AND ITS ABSENCE MADE `liteharness install` UNRUNNABLE.

`installers.py:23` does `from liteharness.catalog import catalog_root, subtree`, and neither
function was defined anywhere in the source tree. `catalog/` held only data (PROVENANCE.json,
agents/, commands/, hooks/, skills/), and `[tool.setuptools.packages.find]` discovers a
directory as a package only when it contains an `__init__.py`. So `liteharness.catalog`
resolved as a *namespace package* — a directory with no module and therefore no names to
import — while `package-data` cheerfully shipped all ~380 files inside it.

Data present, module absent. Every install of 0.2.x died at import with:

    ImportError: cannot import name 'catalog_root' from 'liteharness.catalog' (unknown location)

⭐ `(unknown location)` is the namespace-package tell: Python found the directory and no
module. Reach for packaging before suspecting the code.

Verified 2026-08-09 in a virgin Windows Sandbox: the LiteSuite first-run wizard's "Install
skills into other CLIs" step rendered this traceback and offered "Install 0 CLIs".
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["catalog_root", "subtree"]

# Deliberately `__file__`-relative rather than importlib.resources: the catalog ships as
# package-data, so pip lays it down as real files next to this module, and the consumer
# (`installers._copy_subtree`) walks a real directory tree. `importlib.resources.files()`
# returns a Traversable, which is only guaranteed to be a real filesystem path for
# unzipped installs — it would buy nothing here and lose `Path` semantics the caller needs.


def catalog_root() -> Path:
    """Absolute path to the directory holding the shipped catalog data."""
    return Path(__file__).resolve().parent


def subtree(name: str) -> Path:
    """Absolute path to one catalog subtree, e.g. ``skills`` / ``agents`` / ``commands``.

    Raises FileNotFoundError rather than returning a missing path, because the caller
    copies from it: a silently-absent source would report "Copied 0 files" and look like a
    successful install of nothing.
    """
    root = catalog_root()
    path = root / name
    if not path.is_dir():
        available = sorted(p.name for p in root.iterdir() if p.is_dir())
        raise FileNotFoundError(
            f"catalog subtree {name!r} not found under {root}. Available: {available}. "
            "If this is an installed package, the catalog data did not ship — check "
            "[tool.setuptools.package-data] in pyproject.toml."
        )
    return path
