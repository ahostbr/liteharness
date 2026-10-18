"""T0586: malformed literal targets are refused, without executing a command."""
import os
from pathlib import Path

import pytest

from liteharness import deny_floor, deny_gate

INCIDENT = "Stopped \x00b7 approval policy (not completed),"


def assert_refused(reason):
    assert reason and "DENY FLOOR [unresolvable-path]" in reason
    assert "cannot resolve" in reason
    assert "\x00" not in reason


@pytest.mark.parametrize("command", [
    f"echo data > '{INCIDENT}'",
    f"Set-Location '{INCIDENT}'; echo data > jobs.json",
    f"Remove-Item -Recurse '{INCIDENT}'",
    "Set-Content -LiteralPath 'jobs.json\x00' -Value '[]'",
])
def test_incident_and_native_writer_are_refused(tmp_path, command):
    assert_refused(deny_floor.refusal(command, tmp_path, shell="powershell"))


def test_real_hook_returns_deny_not_fallback(tmp_path):
    decision = deny_gate.decide({"tool_name": "Bash", "cwd": str(tmp_path),
                                 "tool_input": {"command": f"echo data > '{INCIDENT}'"}})
    assert decision
    output = decision["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert_refused(output["permissionDecisionReason"])


@pytest.mark.parametrize("path", [INCIDENT, "$unknown\x00", "bad\x00/*", "ordinary.txt:stream\x00"])
def test_malformed_direct_paths_are_not_erased(tmp_path, path):
    assert_refused(deny_floor.write_refusal(path, tmp_path))


@pytest.mark.parametrize("path", ["bad|path", "bad<path", 'bad"path', "bad\x01path",
                                   "ordinary.txt:bad|stream", "ordinary.txt:bad?stream"])
@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
def test_invalid_windows_characters_before_alias_reduction(tmp_path, path):
    assert_refused(deny_floor.write_refusal(path, tmp_path))


@pytest.mark.parametrize("error", [ValueError("invalid path"), OSError("invalid path"),
                                    RuntimeError("symlink loop")])
def test_failed_resolution_never_becomes_a_safe_lexical_path(tmp_path, monkeypatch, error):
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path.name == "bad-path":
            raise error
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert_refused(deny_floor.refusal("echo data > bad-path", tmp_path, shell="powershell"))
    assert_refused(deny_floor.write_refusal("bad-path", tmp_path))


@pytest.mark.parametrize("command, home, workspace", [
    ("cd; rm -rf ordinary", INCIDENT, None),
    ("rm -rf ordinary", INCIDENT, None),
    ("rm -rf ordinary", None, INCIDENT),
    ("cd 'bad\x00'; rm -rf *", None, None),
])
def test_other_resolution_sites_fail_closed(tmp_path, command, home, workspace):
    assert_refused(deny_floor.refusal(command, workspace or tmp_path,
                                      home=home or tmp_path, jobs=False))


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
def test_bad_git_environment_only_refuses_git_writers(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "bad|repo"))
    assert deny_floor.refusal("echo ok", tmp_path, shell="powershell") is None
    assert deny_floor.refusal("git status", tmp_path, shell="powershell") is None
    assert_refused(deny_floor.refusal("git add ordinary.txt", tmp_path, shell="powershell"))


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
def test_invalid_data_root_is_not_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(tmp_path / "bad|root"))
    assert_refused(deny_floor.write_refusal("jobs.json", tmp_path))


def test_valid_unicode_missing_paths_and_computed_redirects_are_preserved(tmp_path):
    for command in ("echo data > 'Stopped \u00b7 approval policy'", "echo data > new.txt",
                    "echo data > >(cat)"):
        assert deny_floor.refusal(command, tmp_path, shell="bash") is None
    assert deny_floor.write_refusal("new/subfolder/file.txt", tmp_path) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
def test_valid_extended_length_paths_are_preserved(tmp_path):
    path = "\\\\?\\" + str(tmp_path / "ordinary.txt")
    assert deny_floor.write_refusal(path, tmp_path) is None
    assert deny_floor.resolve_path(Path(path)) == Path(path).resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
def test_embedded_quote_in_parsed_redirect_remains_invalid(tmp_path):
    assert_refused(deny_floor.refusal("echo data > 'bad\"path'", tmp_path, shell="powershell"))
