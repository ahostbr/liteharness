"""Owned-path and committed-content boundaries use temporary files/mock Git only."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from liteharness import retirement_handoff as h
from liteharness.retirement import RetirementRefused


@pytest.mark.parametrize("count", [0, 1, 2])
def test_default_only_one_verified_owned_conversation(tmp_path, monkeypatch, count):
    from liteharness import agent_store, owned_launch
    home = tmp_path / "agent-home"
    home.mkdir()
    conversations = []
    for index in range(count):
        path = home / str(index)
        path.mkdir()
        conversations.append(path)
    class Store:
        def __init__(self, root): assert root == tmp_path
        def find_agent(self, *, agent_id):
            assert agent_id == "agent"
            return SimpleNamespace(directory=home)
        def list_conversations(self, agent): return conversations
    monkeypatch.setattr(agent_store, "AgentStore", Store)
    monkeypatch.setattr(owned_launch, "data_root", lambda: tmp_path)
    roots, default = h.owned_handoff_paths("agent", {"cli": "litetui", "cwd": str(tmp_path)})
    assert roots == (tmp_path, home)
    assert default == (conversations[0] / "handoff.md" if count == 1 else None)


def test_unverified_owned_home_does_not_allow_default_or_unknown_root(tmp_path, monkeypatch):
    from liteharness import owned_launch
    def unavailable(): raise owned_launch.RootUnknown("no configured root")
    monkeypatch.setattr(owned_launch, "data_root", unavailable)
    assert h.owned_handoff_paths("agent", {"cli": "litetui", "cwd": str(tmp_path)}) == ((tmp_path,), None)


@pytest.mark.parametrize("change", ["none", "different-commit", "oversize-blob"])
def test_claude_handoff_checks_frozen_blob_without_any_git_write(tmp_path, monkeypatch, change):
    handoff = tmp_path / "handoff.md"
    handoff.write_bytes(b"Committed next steps\r\n")
    # This test isolates committed content, not host file/wall-clock skew.
    evidence = h.verify_handoff(handoff, (tmp_path,), now=handoff.stat().st_mtime)
    commands = []
    blob = "a" * 40
    def run(argv, **kwargs):
        commands.append(argv)
        assert kwargs["cwd"] == tmp_path and "-C" not in argv and "--git-dir" not in argv
        if argv == ["git", "rev-parse", "--show-toplevel"]:
            output = str(tmp_path)
        elif argv == ["git", "rev-parse", "HEAD:handoff.md"]:
            output = blob
        elif argv == ["git", "cat-file", "-s", blob]:
            output = str(h.HANDOFF_MAX_BYTES + 1) if change == "oversize-blob" else "21"
        elif argv == ["git", "cat-file", "blob", blob]:
            output = b"different text" if change == "different-commit" else b"Committed next steps\n"
        else:
            pytest.fail("unexpected Git command")
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(h.subprocess, "run", run)
    if change == "none":
        h.verify_committed_handoff(evidence, tmp_path)
    else:
        with pytest.raises(RetirementRefused):
            h.verify_committed_handoff(evidence, tmp_path)
    assert handoff.read_bytes() == b"Committed next steps\r\n"
    if change == "oversize-blob": assert len(commands) == 3


def test_reparse_or_symbolic_handoff_is_not_owned_file(tmp_path, monkeypatch):
    path = tmp_path / "handoff.md"
    path.write_text("saved")
    original = Path.lstat
    def lstat(selected, *args, **kwargs):
        result = original(selected, *args, **kwargs)
        if selected == path:
            return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
        return result
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(RetirementRefused, match="reparse"):
        h.verify_handoff(path, (tmp_path,))
