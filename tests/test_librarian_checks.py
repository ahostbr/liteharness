"""Unit tests for librarian_checks — repo, sha, path, and attestation resolution.

The nested-repo case is the reason repo qualification exists: C:\\Projects is
itself a git repo with other repos NESTED inside it, so an unqualified
`git cat-file` from the outer root REFUTES perfectly valid inner-repo shas.
The checker must resolve the INNERMOST enclosing root and must refuse to guess
when no root can be resolved at all.

The behavioral tests pin the doctrine seam: a passing structural check must
never launder behavioral prose — promotion happens only through a WS3 human
attestation, which the checker looks up through the same effective-state fold
the pattern store itself uses (one implementation, imported from cli).
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from liteharness.librarian_checks import check_notes


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _make_repo(root: Path) -> str:
    """git init + one commit; returns the commit sha."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "readme.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "readme.md")
    _git(
        root, "-c", "user.name=t", "-c", "user.email=t@example.com",
        "commit", "-q", "-m", "init",
    )
    return _git(root, "rev-parse", "HEAD")


class LibrarianChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.notes = self.base / "notes"
        self.notes.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_note(self, text: str, name: str = "2099-01-01.md") -> Path:
        p = self.notes / name
        p.write_text(text, encoding="utf-8")
        return p

    def _run(self) -> dict:
        return check_notes(str(self.notes / "*.md"), days=None)

    def test_qualified_sha_verifies(self) -> None:
        repo = self.base / "repoA"
        sha = _make_repo(repo)
        self._write_note(f"landed {sha[:8]} in {repo / 'readme.md'}\n")

        result = self._run()
        self.assertEqual(result["summary"]["verified"], 1, json.dumps(result, indent=1))
        claim = result["claims"][0]
        self.assertEqual(claim["repo"], str(repo))
        self.assertEqual(claim["parts"][0]["resolved_by"], "explicit-path")

    def test_unqualified_sha_is_unverifiable_not_guessed(self) -> None:
        repo = self.base / "repoA"
        sha = _make_repo(repo)
        # A perfectly REAL sha — but nothing in the note says which repo.
        self._write_note(f"landed {sha[:8]} tonight\n")

        result = self._run()
        claim = result["claims"][0]
        self.assertEqual(claim["status"], "unverifiable")
        self.assertIn("no repo could be resolved", claim["parts"][0]["detail"])

    def test_nested_repo_resolves_innermost(self) -> None:
        """The hazard case: outer repo contains inner repo; the inner sha does
        not exist in the outer. An outer-root check would REFUTE it."""
        outer = self.base / "outer"
        _make_repo(outer)
        inner = outer / "inner"
        inner_sha = _make_repo(inner)

        self._write_note(f"pushed {inner_sha[:8]} touching {inner / 'readme.md'}\n")
        result = self._run()
        claim = result["claims"][0]
        self.assertEqual(claim["status"], "verified", json.dumps(claim, indent=1))
        self.assertEqual(claim["repo"], str(inner), "outer root won the walk")

    def test_wrong_sha_is_refuted(self) -> None:
        repo = self.base / "repoA"
        _make_repo(repo)
        self._write_note(f"landed 0123456789abcdef0123 in {repo / 'readme.md'}\n")

        result = self._run()
        self.assertEqual(result["claims"][0]["status"], "refuted")

    def test_note_context_carries_forward(self) -> None:
        repo = self.base / "repoA"
        sha = _make_repo(repo)
        self._write_note(
            f"working in {repo / 'readme.md'} today\n"
            "\n"
            f"later: landed {sha[:8]}\n"
        )

        result = self._run()
        by_line = {c["line"]: c for c in result["claims"]}
        self.assertEqual(by_line[3]["status"], "verified")
        self.assertEqual(by_line[3]["parts"][0]["resolved_by"], "note-context")

    def test_behavioral_without_attestation_awaits(self) -> None:
        repo = self.base / "repoA"
        sha = _make_repo(repo)
        self._write_note(f"{sha[:8]} fixed the wake VAD, see {repo / 'readme.md'}\n")

        result = self._run()
        claim = result["claims"][0]
        self.assertEqual(claim["status"], "verified", "structural half passes")
        self.assertEqual(claim["behavioral"]["status"], "awaiting-human",
                         "an existence proof laundered a behavioral claim")
        self.assertEqual(result["summary"]["awaiting_human"], 1)

    def test_behavioral_with_human_attestation_attests(self) -> None:
        repo = self.base / "repoA"
        sha = _make_repo(repo)
        lh = repo / ".liteharness"
        lh.mkdir()
        pattern = {
            "task_id": "T-1",
            "pattern_id": "11111111-2222-4333-8444-555555555555",
            "session": "s",
            "outcome": "success",
            "complexity": "medium",
            "description": f"the wake VAD fix landed as {sha[:8]}",
            "verified": "unverified",
            "timestamp": "2026-08-25T00:00:00+00:00",
        }
        (lh / "patterns.jsonl").write_text(json.dumps(pattern) + "\n", encoding="utf-8")
        (lh / "pattern-attestations.jsonl").write_text(
            json.dumps({
                "type": "verification",
                "attestation_id": "a1a1a1a1-b2b2-4c3c-8d4d-e5e5e5e5e5e5",
                "pattern_id": pattern["pattern_id"],
                "level": "human",
                "actor": "ryan",
                "evidence_ref": "conv:test",
                "timestamp": "2026-08-25T01:00:00+00:00",
            }) + "\n",
            encoding="utf-8",
        )

        self._write_note(f"{sha[:8]} fixed the wake VAD, see {repo / 'readme.md'}\n")
        result = self._run()
        claim = result["claims"][0]
        self.assertEqual(claim["behavioral"]["status"], "attested")
        self.assertEqual(result["summary"]["attested"], 1)

    def test_tick_paths_verify_and_refute(self) -> None:
        repo = self.base / "repoA"
        _make_repo(repo)
        self._write_note(
            f"touched `{repo / 'readme.md'}` and `{repo / 'missing.md'}`\n"
        )
        result = self._run()
        statuses = {p["value"]: p["status"] for p in result["claims"][0]["parts"]}
        self.assertEqual(statuses[str(repo / "readme.md")], "verified")
        self.assertEqual(statuses[str(repo / "missing.md")], "refuted")

    def test_days_filter_excludes_old_dated_notes(self) -> None:
        repo = self.base / "repoA"
        sha = _make_repo(repo)
        line = f"landed {sha[:8]} in {repo / 'readme.md'}\n"
        self._write_note(line, name="2020-01-01.md")

        fresh = check_notes(str(self.notes / "*.md"), days=2)
        self.assertEqual(fresh["summary"]["notes_matched"], 0)
        unfiltered = check_notes(str(self.notes / "*.md"), days=None)
        self.assertEqual(unfiltered["summary"]["notes_matched"], 1)


if __name__ == "__main__":
    unittest.main()
