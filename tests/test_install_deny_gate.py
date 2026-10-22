"""scripts/install_deny_gate.py installs a named COMMIT, one file at a time, and records it.

T0091 (incident 2026-09-26): the installer copied from the WORKING TREE, so an
install could ship uncommitted code to every seat. Every test here uses a temp
git repo and a temp target folder; the live ~/.claude/hooks/deny-floor is never
touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FILES = ("deny_gate.py", "deny_floor.py")


@pytest.fixture
def installer():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import install_deny_gate
    finally:
        sys.path.pop(0)
    return install_deny_gate


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                          capture_output=True).stdout.decode().strip()


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _commit(repo, contents: dict, message: str) -> str:
    for name, data in contents.items():
        (repo / "liteharness" / name).write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class Repo(os.PathLike):
    """A temp checkout that also carries its two commit ids: .a (older), .b (newer)."""

    def __init__(self, root: Path):
        self.root = root

    def __fspath__(self):
        return str(self.root)

    __str__ = __fspath__

    def __truediv__(self, other):
        return self.root / other


@pytest.fixture
def repo(tmp_path):
    """Two commits, A then B, touching both files. Bytes keep CRLF and a non-UTF-8 byte
    so a text-mode read or newline translation changes them."""
    root = Repo(tmp_path / "repo")
    (root / "liteharness").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "true")
    root.a = _commit(root, {"deny_gate.py": b"# gate A\r\nx = 1\r\n\xff\xfe\n",
                            "deny_floor.py": b"# floor A\r\ny = 1\r\n"}, "A")
    root.b = _commit(root, {"deny_gate.py": b"# gate B\r\nx = 2\r\n\xff\xfe\n",
                            "deny_floor.py": b"# floor B\r\ny = 2\r\n"}, "B")
    return root


def _bytes_at(repo, sha, name):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), "show", f"{sha}:liteharness/{name}"],
                          check=True, env=env, capture_output=True).stdout


def _record(target):
    return json.loads((target / "INSTALLED.json").read_text(encoding="utf-8"))


def test_an_install_without_a_ref_is_refused_and_writes_nothing(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    with pytest.raises(SystemExit):
        installer.install(target, repo=repo)
    assert not target.exists() or not any(target.iterdir())


def test_a_ref_that_does_not_exist_is_refused_and_writes_nothing(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    with pytest.raises(SystemExit):
        installer.install(target, ref="deadbeef" * 5, repo=repo)
    assert not target.exists() or not any(target.iterdir())


def test_a_dirty_file_being_installed_is_refused_and_writes_nothing(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    (repo / "liteharness" / "deny_gate.py").write_bytes(b"# half-edited\n")
    with pytest.raises(SystemExit) as refused:
        installer.install(target, ref=repo.b, repo=repo)
    assert "deny_gate.py" in str(refused.value)
    assert not target.exists() or not any(target.iterdir())


def test_the_install_writes_the_commit_bytes_not_the_working_tree(installer, repo, tmp_path):
    """HEAD is B; install A. Floor is edited but --only gate leaves it out of scope."""
    target = tmp_path / "deny-floor"
    (repo / "liteharness" / "deny_floor.py").write_bytes(b"# unrelated WIP\n")
    installer.install(target, ref=repo.a, only=["deny_gate.py"], repo=repo)
    assert (target / "deny_gate.py").read_bytes() == _bytes_at(repo, repo.a, "deny_gate.py")
    assert not (target / "deny_floor.py").exists()


def test_both_files_install_by_default_with_a_per_file_record(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    entry = installer.install(target, ref=repo.a, repo=repo)
    record = _record(target)
    assert set(record) == set(FILES)
    for name in FILES:
        data = (target / name).read_bytes()
        assert data == _bytes_at(repo, repo.a, name)
        assert record[name]["sha"] == repo.a  # the full commit id, not what was typed
        assert record[name]["blob"] == _blob(data)
        assert record[name]["installed_at"]
    assert entry["hooks"][0]["command"].endswith("/deny-floor/deny_gate.py")


def test_a_short_ref_is_recorded_as_the_full_sha(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    installer.install(target, ref=repo.a[:7], only=["deny_floor.py"], repo=repo)
    assert _record(target)["deny_floor.py"]["sha"] == repo.a


def test_only_installs_one_file_and_leaves_the_other_untouched(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    installer.install(target, ref=repo.a, repo=repo)
    before = _record(target)
    floor_bytes = (target / "deny_floor.py").read_bytes()
    installer.install(target, ref=repo.b, only=["deny_gate.py"], repo=repo)
    after = _record(target)
    assert (target / "deny_gate.py").read_bytes() == _bytes_at(repo, repo.b, "deny_gate.py")
    assert after["deny_gate.py"]["sha"] == repo.b
    assert (target / "deny_floor.py").read_bytes() == floor_bytes
    assert after["deny_floor.py"] == before["deny_floor.py"]


def test_a_corrupt_record_is_refused_before_anything_is_written(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    target.mkdir()
    (target / "INSTALLED.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        installer.install(target, ref=repo.a, repo=repo)
    assert not (target / "deny_gate.py").exists()


def test_record_only_writes_the_record_for_bytes_already_installed(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    target.mkdir()
    for name, sha in (("deny_gate.py", repo.b), ("deny_floor.py", repo.a)):
        (target / name).write_bytes(_bytes_at(repo, sha, name))
    installer.record_only(target, {"deny_gate.py": repo.b, "deny_floor.py": repo.a[:7]}, repo=repo)
    record = _record(target)
    assert record["deny_gate.py"]["sha"] == repo.b
    assert record["deny_floor.py"]["sha"] == repo.a
    assert record["deny_floor.py"]["blob"] == _blob((target / "deny_floor.py").read_bytes())


def test_record_only_refuses_a_mismatch_and_writes_nothing(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    target.mkdir()
    (target / "deny_gate.py").write_bytes(_bytes_at(repo, repo.b, "deny_gate.py"))
    (target / "deny_floor.py").write_bytes(_bytes_at(repo, repo.a, "deny_floor.py"))
    snapshot = {p.name: p.read_bytes() for p in target.iterdir()}
    # gate is right, floor claims B but the installed bytes are A's
    with pytest.raises(SystemExit) as refused:
        installer.record_only(target, {"deny_gate.py": repo.b, "deny_floor.py": repo.b}, repo=repo)
    assert "deny_floor.py" in str(refused.value)
    assert {p.name: p.read_bytes() for p in target.iterdir()} == snapshot
    assert not (target / "INSTALLED.json").exists()


def test_record_only_refuses_a_file_that_is_not_installed(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    target.mkdir()
    with pytest.raises(SystemExit):
        installer.record_only(target, {"deny_gate.py": repo.a}, repo=repo)
    assert not (target / "INSTALLED.json").exists()


def test_record_only_keeps_the_entry_it_was_not_asked_about(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    installer.install(target, ref=repo.a, repo=repo)
    before = _record(target)
    (target / "deny_gate.py").write_bytes(_bytes_at(repo, repo.b, "deny_gate.py"))
    installer.record_only(target, {"deny_gate.py": repo.b}, repo=repo)
    after = _record(target)
    assert after["deny_gate.py"]["sha"] == repo.b
    assert after["deny_floor.py"] == before["deny_floor.py"]


def test_the_command_line_installs_a_ref_and_refuses_without_one(repo, tmp_path):
    target = tmp_path / "deny-floor"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    script = [sys.executable, str(SCRIPTS / "install_deny_gate.py")]
    # --repo is a test seam: the script otherwise reads the checkout it lives in
    bare = subprocess.run([*script, str(target), "--repo", str(repo)],
                          capture_output=True, env=env)
    assert bare.returncode != 0 and not target.exists()
    ok = subprocess.run([*script, str(target), "--repo", str(repo), "--ref", repo.a,
                         "--only", "deny_gate.py"], capture_output=True, env=env)
    assert ok.returncode == 0, ok.stderr
    assert b"ADD to hooks.PreToolUse" in ok.stdout
    assert (target / "deny_gate.py").read_bytes() == _bytes_at(repo, repo.a, "deny_gate.py")
    rec = subprocess.run([*script, str(target), "--repo", str(repo), "--record-only",
                          "--ref-floor", repo.b], capture_output=True, env=env)
    assert rec.returncode != 0  # floor is not installed: nothing to record


# ── the drift test reads the record, not the working tree ────────────────────

DRIFT = "tests/test_deny_floor.py::test_the_installed_gate_is_the_recorded_commit"


def _drift(target, repo, *extra):
    """Run the real drift test in a child pytest pointed at a temp target and temp repo."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LITEHARNESS_DENY_GATE_DIR"] = str(target)
    env["LITEHARNESS_DENY_GATE_REPO"] = str(repo)
    return subprocess.run(
        [sys.executable, "-m", "pytest", DRIFT, "-p", "no:cacheprovider", "-rxw", "-q", *extra],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)


def test_the_drift_test_passes_for_an_install_that_matches_its_record(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    installer.install(target, ref=repo.a, repo=repo)
    (repo / "liteharness" / "deny_gate.py").write_bytes(b"# working tree moved on\n")
    done = _drift(target, repo)
    assert done.returncode == 0, done.stdout
    assert "2 passed" in done.stdout


def test_the_drift_test_goes_red_on_a_tampered_installed_byte(installer, repo, tmp_path):
    target = tmp_path / "deny-floor"
    installer.install(target, ref=repo.a, repo=repo)
    data = bytearray((target / "deny_floor.py").read_bytes())
    data[-2] ^= 1
    (target / "deny_floor.py").write_bytes(bytes(data))
    done = _drift(target, repo)
    assert done.returncode != 0
    assert "1 failed, 1 passed" in done.stdout


def test_the_drift_test_xfails_naming_the_record_when_there_is_none(repo, tmp_path):
    target = tmp_path / "deny-floor"
    target.mkdir()
    for name in FILES:
        (target / name).write_bytes(_bytes_at(repo, repo.a, name))
    done = _drift(target, repo)
    assert done.returncode == 0
    assert "INSTALLED.json" in done.stdout and "2 xfailed" in done.stdout


def test_the_drift_test_warns_when_the_recorded_commit_is_not_an_ancestor(installer, repo, tmp_path):
    _git(repo, "checkout", "-q", "-b", "side", repo.a)
    side = _commit(repo, {"deny_gate.py": b"# gate on a side branch\n"}, "side")
    _git(repo, "checkout", "-q", "-")  # back to the branch that has A and B
    target = tmp_path / "deny-floor"
    installer.install(target, ref=side, only=["deny_gate.py"], repo=repo)
    done = _drift(target, repo, "-k", "deny_gate")
    assert done.returncode == 0, done.stdout
    assert "not an ancestor of HEAD" in done.stdout
