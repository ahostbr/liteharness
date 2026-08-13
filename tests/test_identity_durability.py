"""The generated identity must survive an upgrade, and the template must not lie.

Both defects were found by an agent running inside a virgin Windows Sandbox (WhiteDuct,
2026-08-12) against the shipped 0.0.52 installer — neither was visible from the host, and
neither was covered by the 119 tests that were green at the time.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from liteharness import prompts


def make_shipped(root: Path, stems=("default",)) -> Path:
    d = root / "cognitive-architectures" / "orchestrator"
    d.mkdir(parents=True, exist_ok=True)
    for s in stems:
        (d / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")
    return d


@pytest.fixture
def roots(monkeypatch, tmp_path):
    shipped = tmp_path / "shipped"
    overlay = tmp_path / "overlay"
    monkeypatch.setenv("LITEHARNESS_PROMPTS_DIR", str(shipped))
    monkeypatch.setenv("LITEHARNESS_USER_PROMPTS_DIR", str(overlay))
    return shipped, overlay


class TestSurvivesUpgrade:
    def test_identity_survives_deletion_of_the_entire_shipped_library(self, roots):
        """The exact failure: `plugin update` replaces the tree the architecture lived in.

        Before 0.3.3 the interview's output was written under resolve_prompts_dir(), whose
        every branch is installer-managed - so this test could not have passed by any
        amount of retrying. It is the whole point of the change.
        """
        shipped, overlay = roots
        make_shipped(shipped, stems=("default", "ghost-bridge"))

        first = prompts.resolve_cognitive_file("Ghost Bridge", "orchestrator")
        assert first is not None and first.stem == "ghost-bridge"

        shutil.rmtree(shipped)  # the upgrade

        after = prompts.resolve_cognitive_file("Ghost Bridge", "orchestrator")
        assert after is not None, "architecture LOST when the shipped library was replaced"
        assert after.stem == "ghost-bridge"
        assert str(after).startswith(str(overlay))

    def test_write_target_is_never_inside_the_shipped_library(self, roots):
        shipped, overlay = roots
        make_shipped(shipped)
        target, _why = prompts.resolve_orchestrator_target("Ghost Bridge")
        assert target is not None
        assert str(target).startswith(str(overlay))
        assert not str(target).startswith(str(shipped))

    def test_round_trip_write_then_resolve(self, roots):
        """Write where the resolver says; the resolver must return exactly that file.

        This is the property the old derive-write-from-read design protected, and it must
        still hold now that the two are derived from DIFFERENT roots.
        """
        _shipped, _overlay = roots
        target, why = prompts.resolve_orchestrator_target("Ghost Bridge")
        assert target is not None, why
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Ghost Bridge\n", encoding="utf-8")
        assert prompts.resolve_cognitive_file("Ghost Bridge", "orchestrator") == target


class TestMigrationRescuesButDoesNotCorrupt:
    def test_stranded_architecture_is_rescued_into_the_overlay(self, roots):
        shipped, overlay = roots
        make_shipped(shipped, stems=("default", "ghost-bridge"))
        got = prompts.resolve_cognitive_file("Ghost Bridge", "orchestrator")
        rescued = overlay / "cognitive-architectures" / "orchestrator" / "ghost-bridge.md"
        assert rescued.is_file(), "a file one upgrade from deletion was not rescued"
        assert got == rescued, "resolved through the doomed copy instead of the rescued one"

    def test_the_shipped_template_is_never_migrated(self, roots):
        """Copying default.md would manufacture a fake 'personalised' file.

        verify_orchestrator_identity() detects a missing interview by noticing the
        architecture resolved to default.md. A migration that copied the template to
        `<slug>.md` would defeat exactly that check.
        """
        shipped, overlay = roots
        make_shipped(shipped, stems=("default",))
        prompts.resolve_cognitive_file("default", "orchestrator")
        assert not (overlay / "cognitive-architectures" / "orchestrator" / "default.md").exists()

    def test_shipped_polymaths_are_not_copied_into_the_user_overlay(self, roots):
        """A rescue that cannot tell generated from shipped content is a corruption.

        The first version migrated any tier, so resolving "Linus" as a worker copied a
        SHIPPED library file into the overlay - where it would shadow the shipped one
        permanently and silently stop receiving updates.
        """
        shipped, overlay = roots
        d = shipped / "cognitive-architectures" / "workers"
        d.mkdir(parents=True)
        (d / "linus.md").write_text("# linus (shipped)\n", encoding="utf-8")

        got = prompts.resolve_cognitive_file("Linus", "worker")
        assert got is not None and str(got).startswith(str(shipped))
        assert not (overlay / "cognitive-architectures" / "workers" / "linus.md").exists(), (
            "a shipped polymath was copied into the user overlay and will now shadow it forever"
        )


class TestTemplateScaffolding:
    NOTICE = (
        f"{prompts.TEMPLATE_ONLY_START}\n"
        "> **This file is a TEMPLATE until you run `/ls-init-liteharness`.**\n"
        "> Every `{{SLOT}}` below is a question the interview answers.\n"
        f"{prompts.TEMPLATE_ONLY_END}\n"
    )
    BODY = "# {{ORCHESTRATOR_NAME}}\n\n**Trunk:** {{USER_TRUNK}}\n"

    def test_scaffolding_is_deleted_not_substituted(self):
        out = prompts.render_architecture(
            self.NOTICE + self.BODY,
            {"ORCHESTRATOR_NAME": "Ghost Bridge", "USER_TRUNK": "his daughter"},
        )
        assert "TEMPLATE until you run" not in out, "the generated file still calls itself a template"
        assert "{{" not in out

    def test_unstripped_notice_is_caught_rather_than_shipped(self):
        """The literal {{SLOT}} in the notice is a TRIPWIRE, kept on purpose.

        It is prose ABOUT slots, so there is no value to substitute it with - which is what
        made SKILL.md's rule "leave NO {{SLOT}} unreplaced" unsatisfiable. Wrapped in
        markers it gets deleted; if a caller forgets to strip, this is what fails them.
        """
        unmarked = (
            self.NOTICE.replace(prompts.TEMPLATE_ONLY_START, "").replace(prompts.TEMPLATE_ONLY_END, "")
            + self.BODY
        )
        with pytest.raises(ValueError, match=r"\{\{SLOT\}\}"):
            prompts.render_architecture(
                unmarked, {"ORCHESTRATOR_NAME": "X", "USER_TRUNK": "Y"}
            )

    def test_a_genuinely_missing_slot_still_raises(self):
        with pytest.raises(ValueError, match=r"USER_TRUNK"):
            prompts.render_architecture(self.NOTICE + self.BODY, {"ORCHESTRATOR_NAME": "X"})

    def test_the_real_shipped_template_renders_clean(self):
        """Against the actual file, not a fixture - the fixture is the thing that lied."""
        pdir, _src = prompts.resolve_prompts_dir()
        if pdir is None:
            pytest.skip("no prompt library resolves on this box")
        tpl = pdir / "cognitive-architectures" / "orchestrator" / "default.md"
        if not tpl.is_file():
            pytest.skip("shipped default.md not present")
        text = tpl.read_text(encoding="utf-8")
        assert "{{SLOT}}" in text, (
            "the tripwire is gone from the shipped template - if the notice was rewritten, "
            "re-check that unstripped scaffolding is still detectable"
        )
        assert "{{SLOT}}" not in prompts.strip_template_scaffolding(text), (
            "the template notice is not wrapped in TEMPLATE-ONLY markers, so generation "
            "cannot satisfy 'leave no slot unreplaced'"
        )
