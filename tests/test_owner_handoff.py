"""T4: `liteharness owner-handoff --memory <dir>` is the owner's rolling self-handoff.

It validates NOW.md against the template, commits NOW.md and DOMAIN.md (exact paths, nothing
else) with an Agent-Role: owner trailer, then prints the two pointer lines for the compaction.
"""
import subprocess

import pytest

from liteharness import cli, config, owner

AGENT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

GOOD_NOW = """\
# NOW

## Updated
2026-10-11 14:00

## Doing now
Reconciling the widgets backlog.

## Next 3 steps
1. Check the open cards
2. Ask Beta about the shared interface
3. Write the summary

## Waiting on
Beta: the interface answer

## Crew alive
none

## Do NOT redo
The audit of the widgets index (done, see DOMAIN.md)
"""


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", AGENT_ID)
    monkeypatch.setattr(config, "get_root", lambda: tmp_path / "h")
    (tmp_path / "h" / "names").mkdir(parents=True)
    root = tmp_path / "repo"
    memory = root / "owners" / "alpha"
    memory.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "tester")
    _git(root, "config", "user.email", "t@example.invalid")
    (memory / "SOUL.md").write_text("charter\n", encoding="utf-8")
    (memory / "DOMAIN.md").write_text("domain v1\n", encoding="utf-8")
    (memory / "NOW.md").write_text("# NOW\nold\n", encoding="utf-8")
    (root / "other.txt").write_text("unrelated\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root, memory


def _handoff(memory):
    try:
        cli.cmd_owner_handoff(["--memory", str(memory)])
    except SystemExit as exc:
        return exc.code
    return None


# ── the template ─────────────────────────────────────────────────────────────────

def test_a_good_now_has_no_problems():
    assert owner.validate_now(GOOD_NOW) == []


def test_over_sixty_lines_is_refused():
    text = GOOD_NOW + "filler\n" * 40
    problems = owner.validate_now(text)
    assert any("60" in p and "lines" in p for p in problems)


def test_exactly_sixty_lines_is_allowed():
    pad = 60 - len(GOOD_NOW.splitlines())
    assert owner.validate_now(GOOD_NOW.rstrip("\n") + "\n" + "x\n" * pad) == []


@pytest.mark.parametrize("heading", ["Updated", "Doing now", "Next 3 steps", "Waiting on",
                                     "Crew alive", "Do NOT redo"])
def test_each_required_heading_is_enforced(heading):
    text = GOOD_NOW.replace("## " + heading, "## Something else")
    assert any(heading in p for p in owner.validate_now(text))


def test_an_empty_section_is_refused():
    text = GOOD_NOW.replace("Reconciling the widgets backlog.\n", "")
    assert any("Doing now" in p and "empty" in p for p in owner.validate_now(text))


def test_headings_match_case_insensitively_and_with_a_timestamp_on_the_line():
    text = GOOD_NOW.replace("## Updated\n2026-10-11 14:00", "### UPDATED: 2026-10-11 14:00")
    assert owner.validate_now(text) == []


PLAIN_NOW = """\
Updated: 2026-10-11 14:00
Doing now: Reconciling the widgets backlog.
Next 3 steps:
1. Check the open cards
2. Ask Beta about the shared interface
3. Write the summary
Waiting on: Beta: the interface answer
Crew alive: none
Do NOT redo: The audit of the widgets index (done, see DOMAIN.md)
"""


def test_the_plain_colon_spelling_passes():
    assert owner.validate_now(PLAIN_NOW) == []


def test_the_plain_spelling_matches_case_insensitively():
    assert owner.validate_now(PLAIN_NOW.replace("Doing now:", "DOING NOW:").replace("Updated:", "updated:")) == []


def test_markdown_and_plain_headings_can_be_mixed():
    text = GOOD_NOW.replace("## Doing now\nReconciling the widgets backlog.", "Doing now: Reconciling the widgets backlog.")
    text = text.replace("## Waiting on\nBeta: the interface answer", "Waiting on:\nBeta: the interface answer")
    assert "## Updated" in text and "Doing now: " in text and "Waiting on:\n" in text
    assert owner.validate_now(text) == []


def test_a_missing_plain_heading_is_refused_by_name():
    text = PLAIN_NOW.replace("Crew alive: none\n", "")
    assert any("Crew alive" in p and "no" in p for p in owner.validate_now(text))


def test_a_duplicate_plain_heading_is_refused():
    text = PLAIN_NOW + "Doing now: again\n"
    assert any("Doing now" in p and "2 times" in p for p in owner.validate_now(text))


def test_a_markdown_heading_makes_a_plain_line_of_the_same_name_body_text():
    # Decided per NAME: once the file has a markdown heading for a name, a `Name: text` line is
    # that section's body, not a second heading (the previous rule counted it as a duplicate).
    text = GOOD_NOW + "Waiting on: someone else\n"
    assert owner.validate_now(text) == []


def test_body_lines_named_like_headings_in_a_markdown_file_are_body():
    # The shipped template spells every heading `## Name`; a body line that starts with one of
    # the names and a colon is prose under the heading above it.
    text = (GOOD_NOW.replace("Reconciling the widgets backlog.",
                             "Reconciling the widgets backlog.\nWaiting on: the reviewer")
            .replace("2026-10-11 14:00", "2026-10-11 14:00\nUpdated: parser only")
            .replace("The audit of the widgets index (done, see DOMAIN.md)",
                     "The audit of the widgets index\nDo NOT redo: the cache"))
    assert "Waiting on: the reviewer" in text and "Updated: parser only" in text
    assert "Do NOT redo: the cache" in text
    assert owner.validate_now(text) == []


def test_two_markdown_headings_of_one_name_are_still_two_headings():
    text = GOOD_NOW + "\n## Crew alive\nnone\n"
    assert any("Crew alive" in p and "2 times" in p for p in owner.validate_now(text))


def test_a_plain_body_line_naming_another_heading_is_a_second_heading_in_a_plain_file():
    # The documented cost of the plain spelling: with no markdown heading to anchor the name,
    # an unindented `Name: text` line IS the heading.
    text = PLAIN_NOW.replace("Doing now: Reconciling the widgets backlog.\n",
                             "Doing now: Reconciling the widgets backlog.\nWaiting on: x\n")
    assert any("Waiting on" in p and "2 times" in p for p in owner.validate_now(text))


def test_an_empty_plain_section_is_refused():
    text = PLAIN_NOW.replace("Crew alive: none\n", "Crew alive:\n")
    assert any("Crew alive" in p and "empty" in p for p in owner.validate_now(text))


def test_a_list_item_or_body_line_starting_with_a_name_is_not_a_heading():
    text = PLAIN_NOW.replace(
        "3. Write the summary\n",
        "3. Write the summary\n2. Updated: the list item is body text\nWaiting on peers is not a heading\n"
        "Doing nowhere: nor is this\n")
    assert owner.validate_now(text) == []


def test_body_lines_under_a_plain_heading_belong_to_its_section():
    text = PLAIN_NOW.replace("Crew alive: none\n", "Crew alive:\nnone\n")
    assert owner.validate_now(text) == []


# ── the command ──────────────────────────────────────────────────────────────────

def test_a_valid_handoff_commits_only_now_and_domain_with_the_owner_trailer(repo, capsys):
    root, memory = repo
    (memory / "NOW.md").write_text(GOOD_NOW, encoding="utf-8")
    (memory / "DOMAIN.md").write_text("domain v2\n", encoding="utf-8")
    (root / "other.txt").write_text("edited but not mine\n", encoding="utf-8")
    assert _handoff(memory) is None
    assert sorted(_git(root, "show", "--name-only", "--format=", "HEAD").split()) == [
        "owners/alpha/DOMAIN.md", "owners/alpha/NOW.md"]
    body = _git(root, "log", "-1", "--format=%B")
    assert "Agent-Role: owner" in body
    assert f"Agent-ID: {AGENT_ID}" in body and "Agent-Name: " in body
    assert "other.txt" in _git(root, "status", "--porcelain"), "unrelated edits stay uncommitted"


def test_it_prints_compact_then_the_reload_pointer(repo, capsys):
    root, memory = repo
    (memory / "NOW.md").write_text(GOOD_NOW, encoding="utf-8")
    assert _handoff(memory) is None
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    base = memory.resolve().as_posix()
    assert lines[-2:] == ["/compact", f"Reload: read {base}/NOW.md then {base}/DOMAIN.md"]


def test_an_invalid_now_refuses_with_the_reason_and_commits_nothing(repo, capsys):
    root, memory = repo
    before = _git(root, "rev-parse", "HEAD")
    (memory / "NOW.md").write_text(GOOD_NOW + "filler\n" * 50, encoding="utf-8")
    assert _handoff(memory) == 2
    out = capsys.readouterr().out
    assert "HANDOFF REFUSED" in out and "60" in out
    assert "/compact" not in out
    assert _git(root, "rev-parse", "HEAD") == before


def test_a_now_that_was_not_rewritten_is_refused(repo, capsys):
    root, memory = repo
    (memory / "NOW.md").write_text(GOOD_NOW, encoding="utf-8")
    assert _handoff(memory) is None
    capsys.readouterr()
    assert _handoff(memory) == 2
    out = capsys.readouterr().out
    assert "HANDOFF REFUSED" in out and "NOW.md" in out and "/compact" not in out


def test_a_folder_outside_git_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", AGENT_ID)
    monkeypatch.setattr(config, "get_root", lambda: tmp_path / "h")
    (tmp_path / "h" / "names").mkdir(parents=True)
    memory = tmp_path / "plain"
    memory.mkdir()
    for name, text in {"SOUL.md": "c\n", "DOMAIN.md": "d\n", "NOW.md": GOOD_NOW}.items():
        (memory / name).write_text(text, encoding="utf-8")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert _handoff(memory) == 2
    assert "git" in capsys.readouterr().out.lower()


def test_a_missing_file_is_refused(repo, capsys):
    root, memory = repo
    (memory / "DOMAIN.md").unlink()
    assert _handoff(memory) == 2
    assert "DOMAIN.md" in capsys.readouterr().out


def test_memory_flag_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_owner_handoff([])
    assert exc.value.code == 2
    assert "--memory" in capsys.readouterr().out


# ── critic round 1: N1 (pre-staged file), N5 (rejecting hook) ──────────────────────────────

def _staged(root):
    return sorted(_git(root, "diff", "--cached", "--name-only").split())


def test_a_file_staged_before_the_handoff_stays_staged_and_out_of_the_commit(repo, capsys):
    root, memory = repo
    (root / "other.txt").write_text("staged by someone else\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    (memory / "NOW.md").write_text(GOOD_NOW, encoding="utf-8")
    (memory / "DOMAIN.md").write_text("domain v2\n", encoding="utf-8")
    assert _handoff(memory) is None
    assert sorted(_git(root, "show", "--name-only", "--format=", "HEAD").split()) == [
        "owners/alpha/DOMAIN.md", "owners/alpha/NOW.md"]
    assert _staged(root) == ["other.txt"], "the unrelated staged file must still be staged"


def test_a_rejecting_hook_restores_the_index_for_the_two_files_only(repo, capsys):
    root, memory = repo
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho rejected-by-test >&2\nexit 1\n", encoding="utf-8")
    (root / "other.txt").write_text("staged by someone else\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    (memory / "NOW.md").write_text(GOOD_NOW, encoding="utf-8")
    (memory / "DOMAIN.md").write_text("domain v2\n", encoding="utf-8")
    before = _git(root, "rev-parse", "HEAD")
    assert _handoff(memory) == 2
    out = capsys.readouterr().out
    assert "HANDOFF REFUSED" in out and "rejected-by-test" in out and "restored" in out
    assert "/compact" not in out
    assert _git(root, "rev-parse", "HEAD") == before
    assert _staged(root) == ["other.txt"], "NOW.md and DOMAIN.md must be unstaged again, other.txt untouched"
    assert (memory / "NOW.md").read_text(encoding="utf-8") == GOOD_NOW, "the owner's edit survives"
