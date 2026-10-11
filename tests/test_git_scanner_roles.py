"""T2: commits carry `Agent-Role:`; the history scanner and the fleet brief accept it OR
the older `Agent-Tier:` so every commit already in a repo stays readable."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from liteharness.rag.scanners.git_scanner import GitScanner

ROOT = Path(__file__).resolve().parents[1]
FLEET = ROOT / "liteharness" / "catalog" / "hooks" / "mods" / "fleet.mjs"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo, subject, trailers):
    (repo / "f.txt").write_text(subject, encoding="utf-8")
    _git(repo, "add", "f.txt")
    msg = subject + "\n\nbody\n\n" + "\n".join(trailers) + "\n"
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "new style", ["Agent-Name: Alpha", "Agent-Role: owner", "Task-id: T1"])
    _commit(tmp_path, "old style", ["Agent-Name: Beta", "Agent-Tier: worker", "Task-id: T2"])
    return tmp_path


def _chunks(repo):
    return {c["content"].splitlines()[0]: c["content"] for c in GitScanner(str(repo)).parse_file("__git_log__")}


def test_both_trailers_are_parsed(repo):
    contents = list(_chunks(repo).values())
    new = next(c for c in contents if "Agent: Alpha" in c)
    old = next(c for c in contents if "Agent: Beta" in c)
    assert "Role: owner" in new
    assert "Role: builder" in old, "an old Agent-Tier word is shown as its role"


def test_role_wins_when_a_commit_carries_both(tmp_path):
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "both", ["Agent-Name: Alpha", "Agent-Tier: worker", "Agent-Role: critic"])
    (content,) = [c["content"] for c in GitScanner(str(tmp_path)).parse_file("__git_log__")]
    assert "Role: critic" in content and "builder" not in content


def test_an_unknown_trailer_value_is_kept_not_dropped(tmp_path):
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "odd", ["Agent-Name: Alpha", "Agent-Tier: librarian"])
    (content,) = [c["content"] for c in GitScanner(str(tmp_path)).parse_file("__git_log__")]
    assert "Role: librarian" in content


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _denial(prompt):
    script = (
        "import {spawnDenial} from %s;"
        "const e={prompt:%s,model:'claude-opus-5-5[1m]',cwd:'/w',fork:false};"
        "console.log(JSON.stringify(spawnDenial(e,'/w')))"
    ) % (json.dumps(FLEET.as_uri()), json.dumps(prompt))
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True,
                         text=True, check=True).stdout
    return json.loads(out)


@needs_node
@pytest.mark.parametrize("line", [
    "Agent-Role: owner", "Agent-Role: orchestrator", "Agent-Tier: leader", "Agent-Tier: worker",
    "Agent-Role: builder", "Agent-Tier: reviewer"])
def test_fleet_brief_accepts_either_trailer(line):
    assert _denial("go\n" + line + "\nmore") is None


@needs_node
@pytest.mark.parametrize("prompt", [
    "no line at all",
    "Agent-Role: janitor",
    "Agent-Role: owner\nAgent-Tier: leader",
    "Agent-Role: owner\nAgent-Role: scout",
])
def test_fleet_brief_still_refuses_missing_unknown_or_duplicate(prompt):
    denial = _denial(prompt)
    assert denial and "Agent-Role" in denial
