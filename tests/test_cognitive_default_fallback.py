"""An orchestrator of ANY name must resolve to a cognitive architecture.

`orchestrator-role.md` calls the architecture file "your operating system" and marks
reading it MANDATORY. Before this fallback existed, `resolve_cognitive_file` matched on
`{name}.md` only — so an orchestrator named anything other than the shipped exemplar
resolved to None and the mandatory read silently loaded nothing. "Read this file" has no
failure mode, so nothing anywhere reported it.

The fallback is deliberately NARROW. `resolve_cognitive_file`'s docstring promises that a
plain agent name like "BottleRender" is a normal miss rather than an error, and a blanket
default would break that: every unnamed worker would start inheriting an orchestrator
architecture. So the fallback fires only for the PREFERRED tier, and only when that tier
actually ships a `default.md`.
"""

from __future__ import annotations

import pytest

from liteharness.prompts import resolve_cognitive_file, resolve_prompts_dir


@pytest.fixture(autouse=True)
def _isolated_user_overlay(monkeypatch, tmp_path_factory):
    """Every test in this file writes real files at whatever the resolver points to.

    That was ALWAYS non-hermetic - before 0.3.3 the write target was the resolved prompt
    library, so these tests were creating `the-warden.md` and `roundtripprobe.md` inside
    the developer's actual LiteSuite checkout and deleting them again. The 0.3.3 move to a
    user-owned overlay relocated the mess to `~/.liteharness/prompts` without fixing it,
    and a crashed run left five stray files behind - which the tests then correctly
    refused to run against, because each one asserts its target does not already exist.

    ⭐ Those `assert not target.exists()` guards are why this was visible at all. A test
    that silently overwrites whatever is in its way would have hidden the pollution AND
    the migration bug it exposed.
    """
    monkeypatch.setenv("LITEHARNESS_USER_PROMPTS_DIR", str(tmp_path_factory.mktemp("overlay")))


pytestmark = pytest.mark.skipif(
    resolve_prompts_dir()[0] is None,
    reason="prompt library not resolvable in this environment",
)


def test_exemplar_still_wins_over_default():
    """A name with its own file must NOT be shadowed by the fallback.

    Fixture updated 2026-08-09: this asserted `ryan.md` — the architecture named after
    the HUMAN. The resolver keys on the AGENT's name, so that file resolved for nobody
    and the real orchestrator ("Sentinel") silently ran `default.md` for months. The
    file was migrated to `sentinel.md`; the invariant here is unchanged.
    """
    hit = resolve_cognitive_file("Sentinel", "orchestrator")
    assert hit is not None
    assert hit.name == "sentinel.md", (
        f"expected the generated architecture, got {hit.name} — a personalised file that "
        f"resolves to default.md is indistinguishable from one that was never written"
    )


def test_read_and_write_paths_slug_identically():
    """THE REGRESSION: the write path slugged, the read path only lowercased.

    `resolve_orchestrator_target` writes `orchestrator_slug(name).md`, which turns
    "The Warden" into `the-warden.md`. `resolve_cognitive_file` used a bare
    `.lower()`, so it looked for `the warden.md` — a SPACE, not a hyphen — missed, and
    fell through to the tier default.

    The two agree for every single-word name, including "Sentinel", which is why this
    survived: it is invisible on the only configuration the author ever ran, and breaks
    for the first user who picks a two-word name.

    Exercises the ROUND TRIP — write where the write path says, then demand the READ
    path find it. Asserting only that the write path slugs correctly would pass against
    the buggy code, because the write path was never the broken half.
    """
    from liteharness.prompts import resolve_orchestrator_target

    for name in ("The Warden", "Atlas Prime"):
        target, _why = resolve_orchestrator_target(name)
        assert target is not None
        assert not target.exists(), f"{target} already exists — pick a fixture name nobody uses"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\n", encoding="utf-8")
        try:
            hit = resolve_cognitive_file(name, "orchestrator")
            assert hit is not None, f"{name!r} resolved to None after being written"
            assert hit == target, (
                f"wrote {target.name} but the resolver returned {hit.name} — the read path "
                f"does not agree with the write path for a multi-word name"
            )
        finally:
            target.unlink(missing_ok=True)


@pytest.mark.parametrize("name", ["Warden", "Sentinel", "Atlas", "zzz-no-such-agent"])
def test_any_orchestrator_name_resolves(name):
    """THE REGRESSION. Any orchestrator resolves to something readable, never None."""
    hit = resolve_cognitive_file(name, "orchestrator")
    assert hit is not None, f"orchestrator {name!r} resolved to None — mandatory read loads nothing"
    assert hit.is_file()
    assert hit.stat().st_size > 0, "resolved to a 0-byte file, which reads as 'no architecture'"


def test_default_is_a_template_not_a_persons_architecture():
    """The shipped default must be generic — it is what every new user starts from."""
    hit = resolve_cognitive_file("anyone", "orchestrator")
    assert hit is not None
    text = hit.read_text(encoding="utf-8")
    assert "{{ORCHESTRATOR_NAME}}" in text, "default.md lost its name slot"
    assert "{{USER_TRUNK}}" in text, "default.md lost its trunk slot"


def test_plain_agent_name_still_misses():
    """NEGATIVE PATH. The fallback must not turn every unknown name into an architecture."""
    assert resolve_cognitive_file("BottleRender", "worker") is None
    assert resolve_cognitive_file("BottleRender", None) is None
    assert resolve_cognitive_file("", "orchestrator") is None


def test_real_polymath_still_resolves():
    """POSITIVE CONTROL. Proves the lookup itself works, so the Nones above are real."""
    hit = resolve_cognitive_file("linus", "worker")
    assert hit is not None
    assert hit.name == "linus.md"


# ── T6: generation target ────────────────────────────────────────────────────
# The generated architecture must be written where the runtime READS. Before this,
# /ls-init-liteharness saved to `.liteharness/prompts/...`, which resolve_prompts_dir()
# never returns — so a perfectly-run interview produced an unfindable file.


def test_generation_target_is_user_owned_and_not_installer_managed(monkeypatch, tmp_path):
    """The write target must OUTLIVE the package, not merely agree with it.

    This test used to assert the opposite - that the target sits under
    resolve_prompts_dir(). That was written to stop the architecture landing somewhere the
    resolver never reads, which is a real failure, but it fixed it by aiming the WRITE at
    a tree the installer OWNS: the packaged install, or the version-pinned plugin cache at
    `.../liteharness/<version>/...`. Both are replaced wholesale on upgrade, so a
    completed interview was one `plugin update` from silently reverting to default.md.

    Found by an agent inside a virgin Sandbox (WhiteDuct, 2026-08-12) which noticed the
    resolved target was a version-pinned cache path. Agreement between read and write is
    still required - the round-trip test below covers it - but it must be reached by
    pointing the READ at a durable location, not by pointing the WRITE at a disposable one.
    """
    from liteharness.prompts import resolve_orchestrator_target, resolve_prompts_dir

    overlay = tmp_path / "user"
    monkeypatch.setenv("LITEHARNESS_USER_PROMPTS_DIR", str(overlay))

    target, why = resolve_orchestrator_target("Warden")
    assert target is not None, why
    assert target.name == "warden.md"
    assert target.parent.name == "orchestrator"

    # The invariant that matters: not inside anything a package manager replaces.
    assert str(target).startswith(str(overlay)), f"target {target} is not in the user overlay"
    lowered = str(target).lower()
    for doomed in ("plugins\\cache", "plugins/cache", "programs\\litesuite", "programs/litesuite"):
        assert doomed not in lowered, (
            f"generated architecture would land in an installer-managed directory ({doomed}) "
            f"and be destroyed by the next upgrade"
        )

    # And it must NOT be under the shipped library, which is the thing that changed.
    pdir, _ = resolve_prompts_dir()
    if pdir is not None:
        assert not str(target).startswith(str(pdir)), (
            "write target is back inside the shipped library - an upgrade will delete it"
        )


def test_generated_file_is_then_resolvable_round_trip(tmp_path):
    """Write where the resolver says; the resolver must find exactly that file."""
    from liteharness.prompts import resolve_orchestrator_target, resolve_cognitive_file

    name = "RoundTripProbe"
    target, why = resolve_orchestrator_target(name)
    assert target is not None, why
    assert not target.exists(), "probe name collided with a real architecture"

    assert resolve_cognitive_file(name, "orchestrator").name == "default.md"
    # The overlay is user-owned and may not exist yet on a first run. This mkdir used to be
    # unnecessary only because the old write target was the SHIPPED library, which always
    # exists - the test was relying on a directory the installer happened to provide.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# RoundTripProbe\n\nprobe\n", encoding="utf-8")
    try:
        found = resolve_cognitive_file(name, "orchestrator")
        assert found is not None and found.resolve() == target.resolve()
    finally:
        target.unlink()
    assert resolve_cognitive_file(name, "orchestrator").name == "default.md"


def test_slug_is_filesystem_safe():
    from liteharness.prompts import orchestrator_slug

    assert orchestrator_slug("The Warden") == "the-warden"
    assert orchestrator_slug("  Iron Bell  ") == "iron-bell"
    assert orchestrator_slug("atlas_prime") == "atlas-prime"
    # Path separators and reserved chars are stripped, not substituted — a name can
    # never escape the orchestrator/ directory or produce an invalid Windows filename.
    assert orchestrator_slug("Sen/tin\\el:*?") == "sentinel"
    assert "/" not in orchestrator_slug("../../etc/passwd")
    assert "\\" not in orchestrator_slug("..\\..\\windows")
    assert orchestrator_slug("") == "default"
    assert orchestrator_slug(None) == "default"
