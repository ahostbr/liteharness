"""Strict guard CLI/index coverage; all repositories are inert temporary fixtures."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

GUARD = Path(__file__).parents[1] / "scripts/check_pii.py"
spec = importlib.util.spec_from_file_location("strict_pii", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def run(repo, *args):
    return subprocess.run([sys.executable, str(GUARD), *args], cwd=repo,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


def stage(repo, name, data):
    path = repo / name
    path.write_bytes(data)
    subprocess.run(["git", "add", "--", name], cwd=repo, check=True, capture_output=True)
    return path


def private_actor():
    return bytes.fromhex("73656e74696e656c")


def private_codename():
    # A token that still blocks; a name alone is only a printed note.
    return bytes.fromhex("4b75726f72797575")


@pytest.mark.parametrize("args", [(), ("--all",), ("--alll",), ("extra-positional",)])
def test_nothing_or_unknown_arguments_are_error_not_pass(repo, args):
    assert run(repo, *args).returncode == 2


def test_outside_git_errors(tmp_path):
    assert run(tmp_path, "--all").returncode == 2


def test_selftest_positive_negative_controls(repo):
    assert run(repo, "--selftest").returncode == 0


def test_binary_non_utf8_index_is_not_skipped(repo):
    stage(repo, "fixture.bin", b"\x00\xff" + private_codename())
    result = run(repo)
    assert result.returncode == 1
    assert "fixture.bin" in result.stdout
    assert private_codename().decode().lower() not in result.stdout.lower()


def test_nul_listing_preserves_space_and_unicode_paths(repo):
    stage(repo, "space name ü.txt", b"public fixture")
    result = run(repo)
    assert result.returncode == 0
    assert "1 of 1" in result.stdout


def test_all_missing_worktree_is_error_while_index_remains_scannable(repo):
    path = stage(repo, "notes.txt", b"public fixture")
    path.unlink()
    assert run(repo).returncode == 0
    assert run(repo, "--all").returncode == 2


def test_all_binary_clean_counts_as_scanned(repo):
    stage(repo, "public.bin", b"\x00\xffpublic fixture")
    result = run(repo, "--all")
    assert result.returncode == 0
    assert "1 of 1" in result.stdout


def test_old_scanner_and_test_filename_exclusions_do_not_exempt_values(repo):
    stage(repo, "check_pii.py", private_codename())
    assert run(repo).returncode == 1


def test_name_alone_is_a_printed_note_and_a_codename_still_blocks(repo):
    stage(repo, "notes.md", b"Ask " + private_actor())
    result = run(repo)
    assert result.returncode == 0
    assert "note: 1 name mention(s), not blocking" in result.stdout
    assert "1 of 1" in result.stdout
    assert private_actor().decode() not in result.stdout.lower()
    stage(repo, "notes.md", b"Ask " + private_actor() + b" about " + private_codename())
    result = run(repo)
    assert result.returncode == 1
    assert "notes.md:1 [private codename]" in result.stdout
    assert "identity]" not in result.stdout


def test_git_read_errors_fail_closed(monkeypatch):
    def fail(args):
        raise RuntimeError("fixture Git read error")
    monkeypatch.setattr(guard, "git_output", fail)
    assert guard.main([]) == 2


def test_staged_blob_read_error_fail_closed(monkeypatch):
    def fail(args):
        if args[0] == "diff":
            return b"notes.txt\0"
        raise RuntimeError("fixture index read error")
    monkeypatch.setattr(guard, "git_output", fail)
    assert guard.main([]) == 2


def test_exact_legal_field_does_not_mask_actor_value_or_duplicate_key():
    human = bytes.fromhex("7279616e").decode()
    author = human.title() + " Devlin"
    assert not guard.scan_file(".codex-plugin/plugin.json", json.dumps({"author": {"name": author}}).encode())
    assert guard.scan_file(".codex-plugin/plugin.json", json.dumps({"author": {"name": author}, "actor": human}).encode())
    duplicate = '{"author":{"name":' + json.dumps(author) + '},"actor":"public","actor":' + json.dumps(human) + '}'
    assert guard.scan_file(".codex-plugin/plugin.json", duplicate.encode())
    assert guard.scan_file("notes.md", author.encode())


def test_escaped_private_json_scalar_is_scanned():
    actor = private_actor().decode()
    escaped = "".join("\\u%04x" % ord(c) for c in actor)
    assert guard.scan_file(".codex-plugin/plugin.json", ('{"actor":"' + escaped + '"}').encode())


def test_stable_slug_does_not_exempt_longer_actor_label():
    actor = private_actor().decode()
    assert not guard.scan_text("ls-" + actor)
    assert guard.scan_text("ls-" + actor + "-operator")
    assert guard.scan_text("actor=" + actor)
