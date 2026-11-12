"""The rule "close a terminal only when the human asks" was dropped; it must not come back by a copy.

The maintainer dropped it everywhere on 2026-11-12, in the prompts and in these three skills.
`ls-liteharness` is one of the skills this package keeps ahead of its source (KEEP_FROM_CATALOG
in scripts/sync_catalog.py), so a catalog sync does not correct it and nothing else would
notice older text being pasted back in.
"""
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "liteharness" / "catalog" / "skills"
FILES = ("ls-liteharness/SKILL.md", "ls-init-liteharness/skill-template.md", "ls-sentinel/SKILL.md")
DROPPED = ("**Closing terminals:**", "close only when the human asks",
           "Close a terminal only when the human explicitly asks",
           "Close the terminal only when the human explicitly asks",
           "` when the human explicitly asks |")


def test_the_dropped_close_rule_is_in_no_shipped_skill():
    found = [(name, phrase) for name in FILES for phrase in DROPPED
             if phrase in (SKILLS / name).read_text(encoding="utf-8")]
    assert not found, found


def test_the_commands_themselves_are_still_documented():
    """Control: the check above is not passing because the files are empty or were removed."""
    skill = (SKILLS / "ls-liteharness/SKILL.md").read_text(encoding="utf-8")
    assert "liteharness pty-kill <agent-id>" in skill
    assert '| "close that terminal" | `liteharness pty-kill <id>` |' in skill
    assert "Only the leader resumes the same named seat after its process is gone:" in skill
    assert "DELETE http://127.0.0.1:7423/pty/<sessionId>" in (SKILLS / "ls-sentinel/SKILL.md").read_text(encoding="utf-8")
