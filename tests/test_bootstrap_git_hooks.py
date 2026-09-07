"""T467 — `liteharness bootstrap` installs board-sync git hooks."""

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from liteharness.cli import _install_board_sync_hooks, _BOARD_SYNC_MARKER


@pytest.fixture()
def temp_repo(tmp_path: Path) -> Path:
    """A bare git repo with the board_sync script present."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "board_sync_post_commit.py").write_text("# stub\n", encoding="utf-8")
    return tmp_path


def test_creates_both_hooks(temp_repo: Path) -> None:
    _install_board_sync_hooks(temp_repo)
    for name in ("post-commit", "post-merge"):
        hook = temp_repo / ".git" / "hooks" / name
        assert hook.exists(), f"{name} not created"
        content = hook.read_text(encoding="utf-8")
        assert _BOARD_SYNC_MARKER in content
        assert "board_sync_post_commit.py" in content
        assert content.startswith("#!/bin/sh")
        if os.name != "nt":
            assert hook.stat().st_mode & stat.S_IXUSR


def test_idempotent(temp_repo: Path) -> None:
    _install_board_sync_hooks(temp_repo)
    _install_board_sync_hooks(temp_repo)
    for name in ("post-commit", "post-merge"):
        content = (temp_repo / ".git" / "hooks" / name).read_text(encoding="utf-8")
        assert content.count(_BOARD_SYNC_MARKER) == 1


def test_appends_to_existing_hook(temp_repo: Path) -> None:
    hook = temp_repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")
    _install_board_sync_hooks(temp_repo)
    content = hook.read_text(encoding="utf-8")
    assert "echo existing" in content
    assert _BOARD_SYNC_MARKER in content


def test_skipped_when_no_script(temp_repo: Path) -> None:
    (temp_repo / "scripts" / "board_sync_post_commit.py").unlink()
    _install_board_sync_hooks(temp_repo)
    assert not (temp_repo / ".git" / "hooks" / "post-commit").exists()


def test_skipped_with_flag(temp_repo: Path) -> None:
    _install_board_sync_hooks(temp_repo, skip=True)
    assert not (temp_repo / ".git" / "hooks" / "post-commit").exists()
