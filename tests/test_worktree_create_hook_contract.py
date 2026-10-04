"""T0189 — WorktreeCreate must print ONLY an existing absolute path on stdout.

A WorktreeCreate command hook replaces Claude Code's own `git worktree add`, and Claude reads
stdout as the worktree path (raw docs: code.claude.com/docs/en/hooks.md, WorktreeCreate output). The handler
used to print a registration banner and create nothing, so `isolation: "worktree"` failed for
every agent. These run the REAL entry point as a subprocess with a real hook payload on stdin,
because the defect lived in what the process wrote, not in any function's return value.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOG_HOOKS = REPO / "liteharness" / "catalog" / "hooks" / "hooks.json"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    return repo


def _run(tmp_path: Path, payload: dict) -> subprocess.CompletedProcess:
    home = tmp_path / "home"  # registration writes under ~/.liteharness; keep it off the real one
    home.mkdir(exist_ok=True)
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home), "PYTHONPATH": str(REPO)}
    return subprocess.run(
        [sys.executable, "-m", "liteharness.hooks", "worktree-create"],
        # shape of the documented example: common fields + `name`
        input=json.dumps({"session_id": "abc123", "transcript_path": str(tmp_path / "t.jsonl"),
                          "hook_event_name": "WorktreeCreate", **payload}),
        capture_output=True, text=True, env=env, cwd=REPO,
    )


def test_stdout_is_exactly_one_existing_absolute_directory(tmp_path):
    repo = _make_repo(tmp_path)
    r = _run(tmp_path, {"cwd": str(repo), "name": "bold-oak"})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) == 1, f"stdout must be the path and nothing else, got {r.stdout!r}"
    path = Path(lines[0])
    assert path.is_absolute() and path.is_dir()
    assert path == (repo / ".worktrees" / "bold-oak").resolve()
    assert (path / ".git").exists()  # a real linked worktree, not a bare mkdir
    assert "worktree-bold-oak" in subprocess.run(
        ["git", "-C", str(repo), "branch", "--list"], capture_output=True, text=True
    ).stdout
    # the registration side effect survives, and its banner went to stderr
    assert "Worktree registered" in r.stderr
    assert list((tmp_path / "home" / ".liteharness" / "worktrees").glob("*.json"))


def test_second_request_for_same_name_returns_same_tree(tmp_path):
    repo = _make_repo(tmp_path)
    first = _run(tmp_path, {"cwd": str(repo), "name": "again"})
    second = _run(tmp_path, {"cwd": str(repo), "name": "again"})
    assert first.returncode == second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_failure_exits_nonzero_with_empty_stdout(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    r = _run(tmp_path, {"cwd": str(not_a_repo), "name": "x"})
    assert r.returncode != 0 and r.stdout == ""  # empty stdout + non-zero is what Claude reads as "failed"
    assert _run(tmp_path, {"cwd": str(not_a_repo)}).returncode != 0  # no name


def test_unregistered_directory_is_never_adopted_or_clobbered(tmp_path):
    repo = _make_repo(tmp_path)
    squatter = repo / ".worktrees" / "mine"
    squatter.mkdir(parents=True)
    (squatter / "keep.txt").write_text("user data")
    r = _run(tmp_path, {"cwd": str(repo), "name": "mine"})
    assert r.returncode != 0 and r.stdout == ""
    assert (squatter / "keep.txt").read_text() == "user data"


def test_shipped_config_runs_the_hook_synchronously():
    """An async hook cannot hand back a path: Claude would never see the stdout."""
    cfg = json.loads(CATALOG_HOOKS.read_text(encoding="utf-8"))
    entries = [
        h
        for block in cfg["hooks"]["WorktreeCreate"]
        for h in block["hooks"]
        if h["command"].endswith("hooks worktree-create")
    ]
    assert len(entries) == 1
    assert not entries[0].get("async")
