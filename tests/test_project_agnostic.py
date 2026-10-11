"""The shipped harness is project-agnostic: it carries mechanism, never a project's vocabulary.

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

WORDS = ["Kub" + "rick", "SO" + "TS", "Shad" + "ows", "Shu" + "rikens", "Ry" + "an", "Senti" + "nel"]
PATHS = ["c:/" + "projects", "e:/" + "sas", "users/" + "ry" + "an"]
PATTERN = re.compile("|".join([r"\b%s\b" % re.escape(w.lower()) for w in WORDS]
                              + [re.escape(p) for p in PATHS]))

# The whole-tree arm cannot use every word: some are ordinary English or a real polymath's name.
# A tracked file may carry a matching LINE only if that exact line is listed here, by a hash of
# its stripped text (so this file does not itself contain the words). The exemption is per line,
# never per file: a new line, or an edit to an allowed one, in any of these files is a finding.
# Carriers: a shipped polymath agent whose subject shares a famous director's name, the
# provenance stamp that records it, the author line in LICENSE and pyproject, and two fixture
# files that exercise the handoff-audit and deny-floor word lists.
TREE_WORDS = [w for w in WORDS if w not in ("Shad" + "ows", "Senti" + "nel")]
TREE_PATTERN = re.compile("|".join([r"\b%s\b" % re.escape(w.lower()) for w in TREE_WORDS]
                                   + [re.escape(p) for p in PATHS]))
TREE_ALLOWED_LINES = {
    "LICENSE": frozenset({
        "4c9fc40fc8b51da2",
    }),
    "liteharness/catalog/PROVENANCE.json": frozenset({
        "5a040f8fa9334537",
    }),
    "liteharness/catalog/agents/polymathic-ive.md": frozenset({
        "8f8b39de53e029b5",
        "b6151eaf960a244a",
    }),
    "liteharness/catalog/agents/polymathic-" + "kub" + "rick.md": frozenset({
        "2697abf1b7f5b902",
        "39e09acf32c77848",
        "3aaf7d247c694457",
        "46fb174cbb2d77f2",
        "5151885965024e5d",
        "814fc4ce2b2313ec",
        "861487c731904921",
        "90dc544e43a2419b",
        "98d1f9b846a043ce",
        "9f14240573c1120b",
        "a43c8814336ab343",
        "a8f0232d7e8f7906",
        "e673b236d58d8dfb",
        "eca201a2c6681d0d",
    }),
    "pyproject.toml": frozenset({
        "83063798b1dea405",
        "c759fec564c823bc",
    }),
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
        "+++ b/x.py", "+ok line", "+" + "SO" + "TS roster", "+" + "kub" + "rick in lower case",
        "+path C:\\" + "Projects\\x", "+" + "Senti" + "nel", "-" + "Ry" + "an removed", "+" + "Ry" + "an" + "ish is fine",
    ])
    assert hits(added_lines(diff)) == [
        "SO" + "TS roster", "kub" + "rick in lower case", "path C:\\" + "Projects\\x", "Senti" + "nel"]


def test_removed_and_header_lines_are_not_scanned():
    assert added_lines("+++ b/Ry" + "an.py\n-" + "SO" + "TS\n context\n") == []


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
