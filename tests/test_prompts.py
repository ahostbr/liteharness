"""Prompt-library resolution — the parts test_cognitive_default_fallback does not cover.

That file covers slugging, the default fallback and the read/write round trip. This one
covers the ROOT CHAIN and the diagnosis surface, which is where the 2026-08-12 tree merge
actually went wrong: the code moved repos and silently lost a root, and every call still
"worked" because it answered from a different tree.
"""

import io
import os
from pathlib import Path

import pytest

from liteharness import prompts


def make_tree(root: Path, tier: str = "orchestrator", stems=("default",)) -> Path:
    d = root / "cognitive-architectures" / tier
    d.mkdir(parents=True, exist_ok=True)
    for s in stems:
        (d / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")
    return root


@pytest.fixture
def only(monkeypatch, tmp_path_factory):
    """Point resolution at exactly one tree, so a test measures the code and not the box.

    Isolates BOTH roots. There are two since 0.3.3: the shipped library, and the
    user-owned overlay that holds generated architectures. Pinning only the first let a
    test resolve a real file out of the developer's own home directory - which is exactly
    how test_does_not_resurrect_the_human_named_file started returning `marker` instead
    of `default`. It was reading the box, not the code, and the assertion it broke was the
    fixture's own promise.

    The overlay gets a FRESH directory per use, so migration (a read that legitimately
    writes) cannot leak between tests or into the developer's home.
    """
    def _use(root: Path):
        monkeypatch.setenv("LITEHARNESS_PROMPTS_DIR", str(root))
        overlay = tmp_path_factory.mktemp("user-overlay")
        monkeypatch.setenv("LITEHARNESS_USER_PROMPTS_DIR", str(overlay))
        return overlay
    return _use


class TestRootChain:
    def test_env_override_wins_and_is_labelled(self, tmp_path, only):
        make_tree(tmp_path)
        only(tmp_path)
        got, src = prompts.resolve_prompts_dir()
        assert got == tmp_path
        assert "env" in src

    def test_a_non_directory_override_is_ignored_not_obeyed(self, tmp_path, only):
        # An override pointing at nothing must fall through to the real chain rather
        # than resolving to a path that does not exist - otherwise a typo in one env
        # var silently blanks every architecture on the machine.
        only(tmp_path / "does-not-exist")
        got, src = prompts.resolve_prompts_dir()
        assert got is None or got.is_dir()
        assert "env" not in src or got is not None

    def test_source_label_always_says_which_tree(self, tmp_path, only):
        make_tree(tmp_path)
        only(tmp_path)
        _, src = prompts.resolve_prompts_dir()
        # "which file did I get" is only half the question; "from which tree" is the
        # half that explains why two machines disagree.
        assert str(tmp_path) in src


class TestSlugging:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("The Warden", "the-warden"),
            ("HARBOR", "harbor"),
            ("  Harbor  ", "harbor"),
            ("Iron_Rod", "iron-rod"),
            ("", "default"),
        ],
    )
    def test_multi_word_names_slug_to_hyphens(self, raw, expected):
        # THE REGRESSION THIS EXISTS FOR. A bare .lower() maps "The Warden" to
        # "the warden.md", which never exists, so it falls through to the tier default
        # - and a fallback that always succeeds is indistinguishable from a hit.
        # Invisible for every single-word name, i.e. for "the orchestrator", i.e. for the only
        # name the author tested.
        assert prompts.orchestrator_slug(raw) == expected


class TestFallbackIsVisible:
    def test_falling_back_to_default_is_reported_not_hidden(self, tmp_path, only):
        make_tree(tmp_path, stems=("default",))
        only(tmp_path)
        ok, detail = prompts.verify_orchestrator_identity("Warden")
        assert ok is False
        assert "default" in detail.lower()

    def test_a_present_architecture_without_a_skill_is_still_not_ok(self, tmp_path, only):
        # Generation is not done when the FILES exist - it is done when the resolver
        # returns them AND a command can invoke them. An architecture with no skill is
        # a personality nobody can call.
        make_tree(tmp_path, stems=("default", "warden"))
        only(tmp_path)
        ok, detail = prompts.verify_orchestrator_identity("Warden")
        assert ok is False
        assert "skill" in detail.lower()

    def test_does_not_resurrect_the_human_named_file(self, tmp_path, only):
        # The architecture was once generated as the user.md - named after the HUMAN - while
        # resolution keys on the AGENT. Falling back to it would make every caller work
        # and permanently hide that the shipped tree is stale.
        make_tree(tmp_path, stems=("default", "avery"))
        only(tmp_path)
        got = prompts.resolve_cognitive_file("Harbor", "orchestrator")
        assert got is not None
        assert got.stem == "default"


class TestDiagnose:
    def test_diagnose_names_every_root_and_ends_in_a_verdict(self, tmp_path, only):
        make_tree(tmp_path, stems=("default",))
        only(tmp_path)
        out = prompts.diagnose("Warden")
        assert str(tmp_path) in out
        assert "PROBLEM" in out or "OK" in out

    def test_diagnose_reports_what_each_root_HOLDS(self, tmp_path, only):
        # A single resolved path cannot reveal that two trees DISAGREE, and disagreeing
        # trees were the actual defect: repo carried marker.md while the installed
        # plugin still carried the user.md, so the same call answered differently per machine.
        make_tree(tmp_path, stems=("default", "avery"))
        only(tmp_path)
        out = prompts.diagnose("Harbor")
        assert "holds:" in out
        assert "avery" in out


class TestModeDetection:
    def test_explicit_mode_env_beats_inference(self, monkeypatch):
        monkeypatch.setenv("LITEHARNESS_MODE", "standalone")
        monkeypatch.setenv("LITESUITE_PANE_ID", "canvas-pane-3")
        # The /canvas/claude fallback spawn is KNOWN to omit pane envs, so an explicit
        # override has to beat inference in both directions.
        assert prompts.detect_mode() == "standalone"

    def test_pane_env_implies_litesuite(self, monkeypatch):
        monkeypatch.delenv("LITEHARNESS_MODE", raising=False)
        monkeypatch.setenv("LITESUITE_PANE_ID", "canvas-pane-3")
        assert prompts.detect_mode() == "litesuite"

    def test_bare_cli_is_standalone(self, monkeypatch):
        for v in ("LITEHARNESS_MODE", "LITESUITE_PANE_ID", "LITESUITE_BRIDGE_TOKEN",
                  "LITESUITE_CANVAS_SESSION"):
            monkeypatch.delenv(v, raising=False)
        assert prompts.detect_mode() == "standalone"


class TestComposeNeverReturnsNothing:
    def test_compose_returns_a_loud_diagnostic_rather_than_empty(self, tmp_path, only):
        # A silently-undelivered preamble is an unconstrained agent: per-tier TOOL
        # manifests were deleted as never-enforced theater, so this text is the ONLY
        # thing constraining tier behaviour. Failure must be printable, never empty.
        only(tmp_path / "nothing-here")
        out = prompts.compose("worker", "standalone")
        assert isinstance(out, str) and out.strip()


@pytest.mark.parametrize("mode", ["litesuite", "standalone"])
@pytest.mark.parametrize("tier", list(prompts.TIER_FILES))
def test_hook_tiers_get_canvas_text_only_inside_litesuite(tmp_path, only, mode, tier):
    only(tmp_path)
    tier_file = tmp_path / prompts.TIER_FILES[tier]
    tier_file.parent.mkdir(parents=True, exist_ok=True)
    tier_file.write_text("# Role\n", encoding="utf-8")
    (tmp_path / "bootstrap-harness.md").write_text("# Harness\n", encoding="utf-8")
    shared = "Canvas RESULT: present/center/background/state/clear; canvas action=help."
    (tmp_path / "canvas-display.md").write_text(shared, encoding="utf-8")
    out = prompts.compose(tier, mode)
    assert out.count(shared) == (1 if mode == "litesuite" else 0)
    if mode == "standalone":
        assert "canvas-display.md" not in out


@pytest.mark.parametrize("mode", ["litesuite", "standalone"])
def test_missing_canvas_file_warns_only_inside_litesuite(tmp_path, only, mode):
    only(tmp_path)
    (tmp_path / "preambles").mkdir()
    (tmp_path / "preambles/thinker-preamble.md").write_text("# Role\n", encoding="utf-8")
    (tmp_path / "bootstrap-harness.md").write_text("# Harness\n", encoding="utf-8")
    out = prompts.compose("thinker", mode)
    if mode == "litesuite":
        assert "MISSING from the library and NOT delivered: canvas-display.md" in out
    else:
        assert "canvas-display.md" not in out
        assert "MISSING from the library" not in out
