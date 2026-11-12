"""The shipped harness is project-agnostic: it carries mechanism, never a project's vocabulary
or a machine's own folders. People's and seats' names are allowed (ruling of 2026-11-11).

Three arms, all case-insensitive and path-separator-insensitive:
  - the lines this branch adds (committed, staged, unstaged and untracked) and its commit messages;
  - the whole tracked tree, so the guard does not go vacuous once the branch is merged;
  - a planted-leak control, so a scan that matches nothing cannot pass.
The words are assembled from fragments so that this file does not trip its own scan.
"""
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# What is refused, since the maintainer's ruling of 2026-11-11: one project's vocabulary and a
# machine's own paths. What is NOT refused any more: people's and seats' names. His words: names
# are fine; folders, keys, build logs and emails are what must not ship. Keys, emails and the
# hashed private identifiers are scripts/check_pii.py's job (tests/test_ships_no_personal_default.py).
NAMES_ALLOWED = ["Kub" + "rick", "Ry" + "an", "Senti" + "nel"]
WORDS = ["SO" + "TS", "Shad" + "ows", "Shu" + "rikens"]
RUN_TOGETHER = ["shad" + "owsand" + "shu" + "rikens"]  # the project's folder name, wherever it sits in a word
PATHS = ["c:/" + "projects", "e:/" + "sas", "g:/" + "sas", "users/" + "ry" + "an"]
PATTERN = re.compile("|".join([r"\b%s\b" % re.escape(w.lower()) for w in WORDS]
                              + [re.escape(p) for p in RUN_TOGETHER + PATHS]))

# The whole-tree arm cannot use every word: one is ordinary English.
# A tracked file may carry a matching LINE only if that exact line is listed here, by a hash of
# its stripped text (so this file does not itself contain the words). The exemption is per line,
# never per file: a new line, or an edit to an allowed one, in any of these files is a finding.
# Carriers: two fixture files that exercise the handoff-audit and deny-floor word lists. The
# lines that were listed only for a person's or a seat's name (the author line in LICENSE and
# pyproject, a polymath agent and its provenance stamp) need no exemption now and are gone.
TREE_WORDS = [w for w in WORDS if w != "Shad" + "ows"]
TREE_PATTERN = re.compile("|".join([r"\b%s\b" % re.escape(w.lower()) for w in TREE_WORDS]
                                   + [re.escape(p) for p in RUN_TOGETHER + PATHS]))
TREE_ALLOWED_LINES = {
    "tests/test_deny_floor.py": frozenset({
        "58877c2352d03de6",
    }),
    "tests/test_handoff_audit.py": frozenset({
        "03cb5d2857e17fef",
        "06fbb0d5fe0b911b",
        "1178b94f19aee47b",
        "186636b0b6375bed",
        "1f758deae26b600e",
        "22042d45b18b6c76",
        "3e75f65645fe298f",
        "544346ba2e473620",
        "649e360ca4f21338",
        "793bda67a384fe1e",
        "7fab69182ae815aa",
        "8b423ae2b230463e",
        "a613c65c03bb1eda",
        "ac2f0be42f1e6475",
        "c6bf394a1fc94735",
        "d9ab84206537d481",
        "f753850b16000473",
    }),
}


def line_id(line: str) -> str:
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]


def tree_findings(name: str, text: str) -> list[str]:
    """Matching lines of a tracked file that are not exact, listed exemptions."""
    allowed = TREE_ALLOWED_LINES.get(name, frozenset())
    return [line.strip() for line in text.splitlines()
            if TREE_PATTERN.search(line.replace("\\", "/").lower()) and line_id(line) not in allowed]


SKIP_UNTRACKED = (".litesuite/",)


def added_lines(diff_text: str) -> list[str]:
    return [line[1:] for line in diff_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")]


def hits(lines, pattern=PATTERN) -> list[str]:
    return [line.strip() for line in lines if pattern.search(line.replace("\\", "/").lower())]


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def _text(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in raw else raw.decode("utf-8", errors="replace")


# ── the control: the scan must bite ─────────────────────────────────────────────────────

def test_the_scan_catches_planted_leaks_in_any_case():
    diff = "\n".join([
        "+++ b/x.py", "+ok line", "+" + "SO" + "TS roster", "+" + "so" + "ts in lower case",
        "+path C:\\" + "Projects\\x", "+cwd G:\\" + "SAS\\x", "+cwd e:/" + "sas/x",
        "+docs in My" + "Shad" + "owsAnd" + "Shu" + "rikens" + "Docs", "+home C:\\Users\\" + "Ry" + "an\\x",
        "-" + "SO" + "TS removed", "+" + "SO" + "TSish is fine",
    ])
    assert hits(added_lines(diff)) == [
        "SO" + "TS roster", "so" + "ts in lower case", "path C:\\" + "Projects\\x", "cwd G:\\" + "SAS\\x",
        "cwd e:/" + "sas/x", "docs in My" + "Shad" + "owsAnd" + "Shu" + "rikens" + "Docs", "home C:\\Users\\" + "Ry" + "an\\x"]


def test_names_of_people_and_seats_are_not_a_leak():
    """The maintainer's ruling of 2026-11-11: names are fine. A name inside a personal path is still the path."""
    lines = ["asked " + name for name in NAMES_ALLOWED] + [name.lower() + " said so" for name in NAMES_ALLOWED]
    assert hits(lines) == [] and hits(lines, TREE_PATTERN) == []
    assert hits(["C:/Users/" + NAMES_ALLOWED[1] + "/notes"]) != [], "a home folder is a path, whoever it names"


def test_removed_and_header_lines_are_not_scanned():
    assert added_lines("+++ b/" + "SO" + "TS.py\n-" + "SO" + "TS\n context\n") == []


def test_the_tree_scan_catches_a_planted_leak_and_honours_the_allowlist():
    assert hits(["see C:/" + "Projects/x"], TREE_PATTERN)
    assert not hits(["a box " + "shad" + "ows rule"], TREE_PATTERN), "ordinary English is not a leak"
    name = next(iter(TREE_ALLOWED_LINES))
    listed = next(l for l in (ROOT / name).read_text(encoding="utf-8").splitlines()
                  if TREE_PATTERN.search(l.replace("\\", "/").lower()))
    assert tree_findings(name, listed) == [], "the exact listed line is exempt"
    assert tree_findings(name, listed + " more") != [], "the same line edited is not"
    assert tree_findings(name, "\n".join([listed, listed.upper()])) != [], "a new line is not, even a copy"
    assert tree_findings("not/listed.txt", listed) != [], "the exemption belongs to the file"


def test_runtime_ignore_excludes_only_untracked_state(tmp_path, monkeypatch):
    """Runtime notes are ignored; even an ignored path remains guarded if tracked."""
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "ROOT", tmp_path)
    _git("init")
    runtime = tmp_path / ".liteharness" / "notes.txt"
    runtime.parent.mkdir()
    runtime.write_text(WORDS[0] + " private note\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="untracked files carry"):
        test_untracked_new_files_are_scanned_too()

    (tmp_path / ".gitignore").write_text(ignore_rules, encoding="utf-8")
    test_untracked_new_files_are_scanned_too()

    nested = tmp_path / "nested" / ".liteharness" / "notes.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text(WORDS[0] + " private note\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"nested/\.liteharness/notes\.txt"):
        test_untracked_new_files_are_scanned_too()

    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    _git("add", ".liteharness/notes.txt")
    (tmp_path / ".gitignore").write_text(ignore_rules, encoding="utf-8")
    with pytest.raises(AssertionError, match="tracked files carry"):
        test_no_tracked_file_carries_project_specific_text()


# ── the branch ──────────────────────────────────────────────────────────────────────────

def _develop_exists() -> bool:
    try:
        _git("rev-parse", "--verify", "-q", "develop")
        return True
    except subprocess.CalledProcessError:
        return False


def test_this_branch_adds_nothing_project_specific():
    if not _develop_exists():
        pytest.skip("no develop ref to diff against")
    diff = _git("diff", "develop...HEAD") + _git("diff", "HEAD")
    found = hits(added_lines(diff))
    for entry in filter(None, _git("log", "develop..HEAD", "--format=%H%x00%B%x01").split("\x01")):
        sha, _, body = entry.strip().partition("\x00")
        found += hits(body.splitlines())
    assert found == [], "project-specific text in the shipped harness: %r" % found


def test_untracked_new_files_are_scanned_too():
    found = []
    for name in filter(None, _git("ls-files", "-o", "--exclude-standard", "-z").split("\0")):
        if name.startswith(SKIP_UNTRACKED):
            continue
        text = _text(ROOT / name)
        if text is not None and hits(text.splitlines()):
            found.append(name)
    assert found == [], "untracked files carry project-specific text: %r" % found


# ── the whole tree: does not depend on a base ref, so it survives the merge ──────────────

def test_no_tracked_file_carries_project_specific_text():
    found = {}
    for name in filter(None, _git("ls-files", "-z").split("\0")):
        text = _text(ROOT / name)
        bad = tree_findings(name, text) if text is not None else []
        if bad:
            found[name] = bad[:3]
    assert found == {}, "tracked files carry project-specific text: %r" % found


def test_every_listed_exemption_still_matches_a_real_line():
    stale = []
    for name, ids in TREE_ALLOWED_LINES.items():
        text = _text(ROOT / name) or ""
        present = {line_id(l) for l in text.splitlines()
                   if TREE_PATTERN.search(l.replace("\\", "/").lower())}
        stale += ["%s:%s" % (name, i) for i in sorted(ids - present)]
    assert stale == [], "exemptions that no longer match any line (delete them): %r" % stale
