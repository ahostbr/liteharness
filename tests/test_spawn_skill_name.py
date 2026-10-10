"""A spawned seat is told to load the harness skill by its installed name.

The plugin is `liteharness`, the skill is `ls-liteharness`; a bare `/liteharness`
is "Unknown skill" in a fresh Claude Code seat (T0450).
"""
from liteharness import cli

SKILL = "/liteharness:ls-liteharness"


def test_claude_cmd_bootstrap_names_installed_skill():
    cmd, _model = cli._build_claude_cmd(name="Probe")
    assert f"1) Run {SKILL} to load the harness skill." in cmd
    assert "Run /liteharness " not in cmd


def test_spawn_brief_startup_names_installed_skill(tmp_path):
    brief = cli._generate_bootstrap(None, "Probe", None, str(tmp_path), {})
    assert f"1) Run {SKILL} to load the harness skill." in brief
    assert "Run /liteharness " not in brief
