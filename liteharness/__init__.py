"""LiteHarness — Portable cross-CLI agent orchestration."""

# Derived from installed package metadata, never hardcoded.
#
# 🔴 This was the literal "0.2.3" while pyproject.toml said 0.2.6 — the package had been
# misreporting its own version across three releases (0.2.4, 0.2.5, 0.2.6). Caught 2026-08-09
# by probing a real install in a sandbox and reading back `liteharness.__version__`, which is
# the only way a drift like this shows up: nothing fails, the number is just wrong, and every
# consumer that branches on it branches on a lie.
#
# Two literals for one fact will always drift; the fix is to keep one. importlib.metadata
# reads what pip actually installed, so it cannot disagree with the artifact.
try:  # pragma: no cover - trivial
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("liteharness")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+source"
except Exception:  # importlib.metadata unavailable — never let a version string break import
    __version__ = "0.0.0+unknown"
