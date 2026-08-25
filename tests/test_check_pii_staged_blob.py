"""The staged-mode PII gate must scan the INDEX BLOB, not the worktree file.

On 2026-08-25 the gate correctly blocked a commit, the author fixed the file in
the WORKTREE but never re-staged it, and the retry committed the stale index —
private data reached the public repo behind a `check_pii: clean` verdict. The
gate listed files from `git diff --cached` but read content with
`Path.read_text()`: it certified an object that was not being committed.

The fix-and-retry cycle is the ONLY flow where worktree and index routinely
differ, and it is the flow every blocked commit funnels the author into — so
the gate was blind exactly where it mattered. These tests stage one thing and
put a different thing in the worktree, in both directions: only the pair
proves the gate reads the index (a gate that merely "blocks more" would pass
the first case and fail the second).

The dirty-index case was run against the unmodified gate first and seen RED
(exit 0, "clean" — the leak scenario reproduced); output in the commit body.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECK_PII = str(Path(__file__).resolve().parent.parent / "scripts" / "check_pii.py")

# One denylist literal the gate must always trip on (private codename).
DIRTY = "meeting notes for the Kuroryuu build\n"
CLEAN = "meeting notes for the public build\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _run_gate(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, CHECK_PII],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )


class StagedBlobGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dirty_index_clean_worktree_still_blocks(self) -> None:
        """The leak scenario: blocked commit, fix written to the worktree,
        never re-staged. The retry must still block — the index is dirty."""
        f = self.repo / "notes.md"
        f.write_text(DIRTY, encoding="utf-8")
        _git(self.repo, "add", "notes.md")
        f.write_text(CLEAN, encoding="utf-8")  # sanitized — but never re-staged

        proc = _run_gate(self.repo)
        self.assertEqual(
            proc.returncode, 1,
            f"gate certified a dirty index behind a clean worktree:\n{proc.stdout}",
        )
        self.assertIn("notes.md", proc.stdout)

    def test_clean_index_dirty_worktree_passes(self) -> None:
        """The inverse control: staged content is clean, the worktree copy is
        dirty. A gate that reads the index passes; one that reads the worktree
        blocks. Without this pair, 'block always' would satisfy the other test."""
        f = self.repo / "notes.md"
        f.write_text(CLEAN, encoding="utf-8")
        _git(self.repo, "add", "notes.md")
        f.write_text(DIRTY, encoding="utf-8")  # scratch content, not staged

        proc = _run_gate(self.repo)
        self.assertEqual(
            proc.returncode, 0,
            f"gate read the worktree, not the staged blob:\n{proc.stdout}",
        )

    def test_staged_file_missing_from_worktree_is_still_scanned(self) -> None:
        """A file can be staged then deleted from disk; the commit still ships
        the blob, so the gate must still scan it — never skip on !is_file()."""
        f = self.repo / "notes.md"
        f.write_text(DIRTY, encoding="utf-8")
        _git(self.repo, "add", "notes.md")
        f.unlink()

        proc = _run_gate(self.repo)
        self.assertEqual(
            proc.returncode, 1,
            f"staged-but-deleted blob escaped the scan:\n{proc.stdout}",
        )


if __name__ == "__main__":
    unittest.main()
