"""
Tests for cognitive-architecture resolution.

The behaviour under test is a FALLBACK, which means the dangerous failure is a
silent success: resolution always returns a usable path, so "it returned
something" proves nothing about whether the agent got its own architecture. Every
test here is therefore written around the DISTINCTION between a hit and a
fallback, not around whether a path came back.
"""

import os
from pathlib import Path

import pytest

from liteharness import prompts


def make_tree(root: Path, tier: str, stems: list[str]) -> Path:
    d = root / "cognitive-architectures" / tier
    d.mkdir(parents=True, exist_ok=True)
    for s in stems:
        (d / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")
    return root


@pytest.fixture
def isolated(monkeypatch):
    """
    Point resolution at exactly one tree.

    Without this the tests would read the developer's real trees and pass or fail
    according to what happens to be on that machine — a suite that measures the
    box instead of the code.
    """
    def _use(root: Path):
        monkeypatch.setenv("LITEHARNESS_PROMPTS_ROOT", str(root))
    return _use


def test_resolves_the_personal_file_when_present(tmp_path, isolated):
    make_tree(tmp_path, "orchestrator", ["default", "sentinel"])
    isolated(tmp_path)
    got = prompts.resolve_cognitive_file("Sentinel", "orchestrator")
    assert got is not None and got.stem == "sentinel"


def test_falls_back_to_default_when_the_personal_file_is_absent(tmp_path, isolated):
    make_tree(tmp_path, "orchestrator", ["default"])
    isolated(tmp_path)
    got = prompts.resolve_cognitive_file("Sentinel", "orchestrator")
    assert got is not None and got.stem == "default"


def test_verify_reports_the_fallback_rather_than_hiding_it(tmp_path, isolated):
    """
    THE ASSERTION THIS MODULE EXISTS FOR.

    A mislocated personal file is indistinguishable from success unless something
    checks WHICH file came back. This is that something.
    """
    make_tree(tmp_path, "orchestrator", ["default"])
    isolated(tmp_path)
    v = prompts.verify_orchestrator_identity("Sentinel")
    assert v["ok"] is False
    assert v["resolved_stem"] == "default"
    assert "sentinel" in v["problem"]


def test_verify_says_ok_when_the_personal_file_is_there(tmp_path, isolated):
    # Directional control. Without it, a verify() hardwired to False would pass
    # the test above and look like a working guard.
    make_tree(tmp_path, "orchestrator", ["default", "sentinel"])
    isolated(tmp_path)
    v = prompts.verify_orchestrator_identity("Sentinel")
    assert v["ok"] is True
    assert v["resolved_stem"] == "sentinel"
    assert "problem" not in v


def test_does_not_resurrect_the_old_human_named_file(tmp_path, isolated):
    """
    The architecture was once generated as `ryan.md` — named after the human —
    while resolution keys on the AGENT's name. Falling back to it would make
    every caller "work" and permanently hide that the shipped tree is stale.
    """
    make_tree(tmp_path, "orchestrator", ["default", "ryan"])
    isolated(tmp_path)
    v = prompts.verify_orchestrator_identity("Sentinel")
    assert v["ok"] is False
    assert v["resolved_stem"] == "default"


def test_name_matching_is_case_insensitive(tmp_path, isolated):
    make_tree(tmp_path, "orchestrator", ["sentinel"])
    isolated(tmp_path)
    assert prompts.resolve_cognitive_file("SENTINEL").stem == "sentinel"
    assert prompts.resolve_cognitive_file("  Sentinel  ").stem == "sentinel"


def test_env_root_is_exclusive_not_merely_first(tmp_path, isolated):
    """
    An explicit root must WIN, not just sort first. If it merely sorted first,
    a tree lacking the file would silently fall through to another one — so you
    could never isolate a tree to ask what IT holds, and a check of the installed
    plugin would quietly answer with the developer's repo. That exact mistake
    produced a false 'ok=True' while this module was being built.
    """
    make_tree(tmp_path, "orchestrator", ["default"])
    isolated(tmp_path)
    roots = prompts.prompt_roots()
    assert len(roots) == 1 and roots[0][0] == "env"
    assert prompts.verify_orchestrator_identity("Sentinel")["ok"] is False


def test_plugin_cache_versions_sort_numerically(tmp_path, monkeypatch):
    """
    1.0.10 must beat 1.0.9. A lexical sort silently pins resolution to a stale
    tree the moment a two-digit patch ships, and nothing would look wrong.
    """
    base = tmp_path / ".claude" / "plugins" / "cache" / "liteharness" / "liteharness"
    for v in ("1.0.9", "1.0.10", "1.0.2"):
        (base / v / "prompts").mkdir(parents=True)
    monkeypatch.delenv("LITEHARNESS_PROMPTS_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    ordered = [p.parent.name for p in prompts._plugin_cache_roots()]
    assert ordered == ["1.0.10", "1.0.9", "1.0.2"]


def test_returns_none_when_no_tree_exists_anywhere(tmp_path, isolated):
    isolated(tmp_path / "nothing-here")
    assert prompts.resolve_cognitive_file("Sentinel") is None


def test_diagnose_names_every_root_and_the_verdict(tmp_path, isolated):
    make_tree(tmp_path, "orchestrator", ["default"])
    isolated(tmp_path)
    out = prompts.diagnose("Sentinel")
    assert "FALLBACK" in out
    assert "default" in out
