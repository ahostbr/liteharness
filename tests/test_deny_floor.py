"""The deny floor (liteharness/deny_floor.py) and its Claude Code PreToolUse gate.

Every arm judges a command STRING. Nothing here executes a delete: testing a
guard means asking the classifier, never running the command to see.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from liteharness import deny_floor, deny_gate

#: Portable reproduction of the profile-deletion command shape (2026-09-26).
INCIDENT = (r"$home='C:\workspace\LiteTUI\temp-working-dir\t1018-main-red-home'; "
            r"Remove-Item $home -Recurse -Force -ErrorAction SilentlyContinue")

@pytest.mark.parametrize("command, refused", [
    ("lst.exe run tasks action=list", False),
    ("python x.py 'please do run it'", False),
    ("if exist package.json bun run dev", False),
    ("Invoke-Headless lst.exe @('run','tasks','action=list')", False),
    ("$p.ArgumentList.Add('run')", False),
    ("run.bat", True),
    ("{owner}", True),
    ("'{owner}'", True),
    ("cmd /c '{owner}'", True),
    ("Start-Process -FilePath:{owner}", True),
    ("Start-Process -FilePath '{owner}'", True),
    ("Invoke-Item '{owner}'", True),
    ("cd {dir} && run", True),
    ("cmd /c run", True),
    ("run", True),
    ("call run", True),
    ("cmd /c @run", True),
    ("cmd /c ^run", True),
    ("cmd /c 2>nul run", True),
    ("if 1==1 run", True),
    ("if exist src run", True),
    ("if defined X run", True),
    ("for %i in (1) do run", True),
    ("if 1==2 (echo a) else run", True),
    ("for /f %i in ('echo') do run", True),
    ("Get-Content '{owner}'", False),
    ("python x.py '{owner}'", False),
    ("{other}", True),
    ("{missing}", True),
])
def test_owner_launcher_requires_executable_position_not_private_identity(
        tmp_path, monkeypatch, command, refused):
    """T0206: judge strings only, never execute the inert owner launcher."""
    owner = tmp_path / "owner" / "run.bat"
    (owner.parent / "src" / "litetui").mkdir(parents=True)
    owner.write_text("@echo off\n", encoding="utf-8")
    other = tmp_path / "other" / "run.bat"
    (other.parent / "src" / "litetui").mkdir(parents=True)
    other.write_text("@echo off\n", encoding="utf-8")
    missing = tmp_path / "missing" / "run.bat"
    (missing.parent / "src" / "litetui").mkdir(parents=True)
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    reason = deny_floor.refusal(command.format(owner=owner, other=other, missing=missing, dir=owner.parent),
                                owner.parent)
    assert bool(reason) is refused, reason
    if refused:
        assert "[owner-launcher]" in reason

@pytest.mark.parametrize("command, refused", [
    ("@'\nrun = vi.fn();\n'@ | Add-Content tests.ts", False),
    ("@'\nrun\n'@", False),
    ("@'\nrun\n'@ | Set-Content 'test cases.ts'", False),
    ("@'\nrun\n'@ | Out-File tests.ts", False),
    ("@'\nrun\n'@ | Add-Content tests.ts; Write-Output ok", False),
    ("@'\r\nrun\r\n'@ | Add-Content tests.ts", False),
    ("@'\nrun\n'@ | powershell -", True),
    ("@'\nrun\n'@ | pwsh -c -", True),
    ("@'\nrun\n'@ | cmd", True),
    ("@'\nrun\n'@ | iex", True),
    ("@'\nrun\n'@ | Invoke-Expression", True),
    ("iex @'\nrun\n'@", True),
    ("Invoke-Expression @'\nrun\n'@", True),
    ("& ([scriptblock]::Create(@'\nrun\n'@))", True),
    ("python -c @'\nrun\n'@", True),
    ("node -e @'\nrun\n'@", True),
    ("@'\nrun\n'@ | unknown-sink", True),
    ("@'\nrun\n'@ | Add-Content tests.ts | iex", True),
    ("@'\nrun\n'@\n | iex", True),
    ('@"\n$(run)\n"@ | Add-Content tests.ts', True),
    ("@'\nrun\n", True),
    ("run; @'\ntext\n'@ | Add-Content tests.ts", True),
    ("@'\ntext\n'@ | Add-Content tests.ts; run", True),
    ("@'\ntext\n'@\nrun", True),
    ("@'\nrun\n'@\n# continuation comment\n | iex", True),
    ("@'\ncd missing-folder\n'@ | Add-Content tests.ts; run", True),
    ("@'\ncd missing-folder\n'@ | Add-Content tests.ts\nrun", True),
    ("& '{owner}'", True),
    ("cmd /c '{owner}'", True),
    ("Start-Process -FilePath '{owner}'", True),
])
def test_owner_launcher_literal_here_string_data_only(tmp_path, monkeypatch, command, refused):
    """T0291: classify strings only; every launcher file is inert test data."""
    owner = tmp_path / "run.bat"
    owner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    reason = deny_floor.refusal(command.format(owner=owner), owner.parent, shell="powershell")
    assert bool(reason) is refused, reason
    if refused:
        assert "[owner-launcher]" in reason


def test_original_t0291_command_is_literal_test_data(tmp_path, monkeypatch):
    owner = tmp_path / "run.bat"
    owner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    command = (Path(__file__).parent / "fixtures" / "T0291-here-string.txt").read_text(encoding="utf-8")
    assert deny_floor.refusal(command, owner.parent, shell="powershell") is None




HOME_VARIABLE_DELETES = [
    INCIDENT,
    "rm -rf ~",
    'rm -rf "$HOME"',
    "rm -rf $HOME/",
    'rm -rf "$HOME"/*',
    "rm -rf ~;echo hi",
    "Remove-Item $env:USERPROFILE -Recurse",
    "Remove-Item -Path:$home -Recurse",
    "ri $HOME -r",
    "del $home",
    "rd /s /q %USERPROFILE%",
    "Get-ChildItem $home -Force | Remove-Item -Recurse -Force",
    'bash -c "rm -rf ~"',
    'powershell -Command "Remove-Item $env:HOME -Recurse"',
    "find ~ -delete",
]


@pytest.fixture
def box(tmp_path):
    """A fake machine: a profile with harness folders, a sibling profile, a repo."""
    home = tmp_path / "Users" / "someone"
    for name in (".claude", ".codex", ".liteharness", ".litesuite", "scratch"):
        (home / name).mkdir(parents=True)
    (tmp_path / "Users" / "other").mkdir()
    workspace = tmp_path / "work" / "proj"
    (workspace / ".git").mkdir(parents=True)
    (workspace / "build").mkdir()
    repo = tmp_path / "work" / "another"
    (repo / ".git").mkdir(parents=True)
    worktree = workspace / ".worktrees" / "wt"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    return tmp_path, home, workspace


@pytest.mark.parametrize("command", HOME_VARIABLE_DELETES)
def test_a_delete_of_a_home_variable_is_refused_and_names_its_rule(box, command):
    _, home, workspace = box
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[home-variable-delete]" in reason
    assert "do not retry" in reason


def test_protected_roots_refused_on_a_recursive_delete(box):
    root, home, workspace = box
    for target, why in [
        (home, "user profile root"),
        (home / ".claude", "~/.claude"),
        (home / ".codex", "~/.codex"),
        (home / ".liteharness", "~/.liteharness"),
        (home / ".litesuite", "~/.litesuite"),
        (root / "Users" / "other", "a user profile root"),
        (root / "Users", "user profile root"),
        (workspace, "a git repository root"),
        (workspace.parent, "a folder containing the workspace"),
        (root / "work" / "another", "a git repository root"),
        (Path(workspace.anchor), "a drive root"),
    ]:
        for command in (f'rm -rf "{target}"', f"Remove-Item '{target}' -Recurse -Force",
                        f'rm -rf "{target}"/*', f'rd /s /q "{target}"'):
            reason = deny_floor.refusal(command, workspace, home)
            assert reason and "[protected-root-delete]" in reason, command
            assert why in reason, (command, reason)
    for command in ("rm -rf .", "rm -rf ..", "rm -rf *", "rm -rf /"):
        assert deny_floor.refusal(command, workspace, home), command


@pytest.mark.skipif(os.name != "nt", reason="git-bash drive paths exist only on Windows")
def test_git_bash_drive_paths_resolve_before_judging(box):
    _, home, workspace = box
    msys = "/" + home.drive[0].lower() + home.as_posix()[2:]
    assert "[protected-root-delete]" in deny_floor.refusal(f"rm -rf {msys}", workspace, home)
    assert "a drive root" in deny_floor.refusal("rm -rf /c", workspace, home)


@pytest.mark.parametrize("command", [
    "rm -rf build",
    "rm -rf .worktrees/wt",  # a worktree's .git is a FILE: its history lives in the repo
    "Remove-Item build -Recurse -Force -ErrorAction SilentlyContinue",
    "rm -rf ~/scratch",
    "find . -name '*.pyc' -delete",
    r"find . -type d -name __pycache__ -exec rm -rf {} \;",
    "Remove-Item $tmp -Recurse",  # an arbitrary variable: the documented ceiling
    "Remove-Item -Force notes.txt",
    "rm ~/scratch/x.txt",  # not recursive, not a home variable
    "npm run format", "git log --format=%h", "ls ~ | head", "echo $HOME",
    "cd ~ && ls", "Get-ChildItem $home", "git rm --cached x", "Get-Process",
])
def test_ordinary_commands_pass_the_floor(box, command):
    _, home, workspace = box
    assert deny_floor.refusal(command, workspace, home) is None


def test_a_scratch_delete_outside_every_protected_root_passes(box):
    root, home, workspace = box
    scratch = root / "scratch-dir"
    scratch.mkdir()
    for command in (f'rm -rf "{scratch}"', f"Remove-Item '{scratch}' -Recurse -Force"):
        assert deny_floor.refusal(command, workspace, home) is None




# ── the Claude Code PreToolUse gate: deny_gate.py, run BY PATH ──────────────

GATE = Path(deny_gate.__file__).resolve()


def _payload(tool, command, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"command": command}, "cwd": str(cwd)}


def test_the_gate_denies_what_the_floor_refuses(box):
    _, _, workspace = box
    for tool in ("Bash", "PowerShell"):
        out = deny_gate.decide(_payload(tool, INCIDENT, workspace))["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "[home-variable-delete]" in out["permissionDecisionReason"]
    assert deny_gate.decide(_payload("Bash", "git status", workspace)) is None
    assert deny_gate.decide({"tool_name": "Read", "tool_input": {"file_path": "x"}}) is None


@pytest.mark.parametrize("command", [
    INCIDENT, "rm -rf x", "rm -fr x", "Remove-Item x -Recurse", "ri x -r",
    "Remove-Item -Recurse:$true x", "rd /s /q x", "rmdir /s x", "rm --recursive x",
    "python -c \"import shutil; shutil.rmtree('x')\"", "npx rimraf x",
    "git clean -fdx", "git clean -xf", "git worktree remove --force wt",
    "git worktree remove -f wt", "find x -delete",
])
def test_the_fallback_blocks_every_recursive_delete_shape(command):
    assert deny_gate._RECURSIVE_DELETE.search(command), command


@pytest.mark.parametrize("command", [
    "Remove-Item -Force notes.txt", "rm notes.txt", "git status", "Get-ChildItem",
    "git clean -n", "git worktree remove wt", "npm run format", "git log --format=%h",
])
def test_the_fallback_lets_the_ordinary_through(command):
    assert not deny_gate._RECURSIVE_DELETE.search(command), command


def _sandbox_env(tmp_path, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
            "LITEHARNESS_HOME": str(tmp_path / "harness"), **extra}


def _run_gate(gate, command, cwd, env):
    return subprocess.run([sys.executable, str(gate)],
                          input=json.dumps(_payload("PowerShell", command, cwd)),
                          capture_output=True, text=True, env=env, timeout=60,
                          cwd=env["HOME"])


@pytest.fixture
def broken_gate(tmp_path):
    """The real gate beside a deny_floor.py that raises: the hook crashes."""
    folder = tmp_path / "gate"
    folder.mkdir()
    (folder / "deny_gate.py").write_bytes(GATE.read_bytes())
    (folder / "deny_floor.py").write_text("raise RuntimeError('broken floor')\n",
                                          encoding="utf-8")
    return folder / "deny_gate.py"


@pytest.fixture
def poisoned_package(tmp_path):
    """A PYTHONPATH whose `liteharness` package cannot be imported."""
    shadow = tmp_path / "shadow" / "liteharness"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("raise ImportError('poisoned liteharness')\n",
                                        encoding="utf-8")
    return shadow.parent


def test_a_crashed_gate_blocks_the_incident_with_exit_2(box, tmp_path, broken_gate):
    _, _, workspace = box
    proc = _run_gate(broken_gate, INCIDENT, workspace, _sandbox_env(tmp_path))
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "deny-floor hook failed, refusing a recursive delete" in proc.stderr
    log = tmp_path / "harness" / "deny_gate.log"
    assert "BLOCKED" in log.read_text(encoding="utf-8")


def test_a_crashed_gate_lets_a_benign_command_through_and_logs_it(box, tmp_path, broken_gate):
    _, _, workspace = box
    proc = _run_gate(broken_gate, "Get-ChildItem", workspace, _sandbox_env(tmp_path))
    assert (proc.returncode, proc.stdout.strip()) == (0, "")
    log = (tmp_path / "harness" / "deny_gate.log").read_text(encoding="utf-8")
    assert "deny-floor hook failed (allowed)" in log and "broken floor" in log


def test_the_gate_never_imports_the_liteharness_package(box, tmp_path, poisoned_package):
    """By path it still denies through the real floor; `-m` cannot even start."""
    _, _, workspace = box
    env = _sandbox_env(tmp_path, PYTHONPATH=str(poisoned_package))
    proc = _run_gate(GATE, INCIDENT, workspace, env)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "[home-variable-delete]" in out["permissionDecisionReason"]

    # The contrast that forced the by-path entry: as a module it dies at the
    # package import with exit 1, which Claude Code does not treat as a block.
    via_module = subprocess.run([sys.executable, "-m", "liteharness.deny_gate"],
                                input=json.dumps(_payload("Bash", INCIDENT, workspace)),
                                capture_output=True, text=True, env=env, timeout=60,
                                cwd=env["HOME"])
    assert via_module.returncode == 1 and "poisoned liteharness" in via_module.stderr


def test_a_crashed_gate_blocks_even_with_the_package_poisoned(
        box, tmp_path, broken_gate, poisoned_package):
    _, _, workspace = box
    env = _sandbox_env(tmp_path, PYTHONPATH=str(poisoned_package))
    assert _run_gate(broken_gate, INCIDENT, workspace, env).returncode == 2


# ── review 2198d4ab / 8669231f ──────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "cd ~; Remove-Item * -Recurse -Force",               # F3, verbatim
    r"Set-Location $HOME; Remove-Item .\* -Recurse",      # F3, verbatim
    "cd; rm -rf *",                                       # bare cd goes home
    "sl ~ ; ri . -r",
    "pushd %USERPROFILE% && rd /s /q .",
])
def test_a_cd_to_home_moves_the_base_of_later_targets(box, command):
    _, home, workspace = box
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[protected-root-delete]" in reason, command


def test_a_cd_into_the_profile_protects_its_harness_folders(box):
    _, home, workspace = box
    assert "~/.claude" in deny_floor.refusal(f'cd "{home}"; rm -rf .claude', workspace, home)


@pytest.mark.parametrize("command", [
    "cd build; rm -rf *",           # the workspace's own build folder
    "cd ~/scratch; rm -rf *",       # an ordinary folder inside the profile
    "cd $x; rm -rf *",              # the documented ceiling: unknown base
    "git rm -r --cached .",         # F4: the index, not the tree
    "git rm -r --cached -- .",
])
def test_cd_and_git_rm_do_not_over_block(box, command):
    _, home, workspace = box
    assert deny_floor.refusal(command, workspace, home) is None, command


def test_the_gate_judges_the_tools_own_cwd_too(box, tmp_path):
    """The MCP shells (sots_run_pwsh, litesuite shell) carry a `cwd`. The
    session sits in the workspace; the tool runs in the profile."""
    _, home, workspace = box
    payload = _payload("mcp__SOTS_MCP_CORE__sots_run_pwsh", "rm -rf .claude", workspace)
    env = {**_sandbox_env(tmp_path), "HOME": str(home), "USERPROFILE": str(home)}

    def run(tool_cwd):
        payload["tool_input"]["cwd"] = tool_cwd
        return subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env, timeout=60,
                              cwd=str(tmp_path))

    denied = run(str(home))
    assert denied.returncode == 0 and "~/.claude" in denied.stdout, denied
    assert run(str(workspace)).stdout.strip() == ""   # workspace/.claude is ordinary
    assert deny_gate.decide({**payload, "tool_input": {"file_path": "x"}}) is None


@pytest.mark.parametrize("command", [
    "gci ~ -Recurse | Remove-Item -Force",
    "Get-ChildItem $home -r | ri -fo",
    "del /q %USERPROFILE%",
])
def test_a_crashed_gate_blocks_pipeline_and_home_variable_deletes(
        box, tmp_path, broken_gate, command):
    _, _, workspace = box
    assert _run_gate(broken_gate, command, workspace, _sandbox_env(tmp_path)).returncode == 2


INSTALLED = Path(os.environ.get("LITEHARNESS_DENY_GATE_DIR")
                 or Path.home() / ".claude" / "hooks" / "deny-floor")


@pytest.mark.parametrize("name", ["deny_gate.py", "deny_floor.py"])
def test_the_installed_gate_is_the_repo_gate(name):
    """Drift fails a TEST, not a seat. Not installed is an XFAIL that names the
    path (loud in -rx), never a skip: this suite also runs where no Claude
    Code is installed, and a hard failure there would be noise."""
    installed = INSTALLED / name
    if not installed.exists():
        pytest.xfail(f"deny gate not installed: {installed} is missing "
                     "(scripts/install_deny_gate.py installs it)")
    repo = GATE.with_name(name)
    assert installed.read_bytes() == repo.read_bytes(), (
        f"{installed} differs from {repo}: rerun scripts/install_deny_gate.py")


@pytest.mark.parametrize("name", ["deny_gate.py", "deny_floor.py"])
def test_the_gate_compiles_without_warnings(name):
    """An invalid escape is a DeprecationWarning on 3.11, a SyntaxWarning in the
    hook's stderr on 3.12+, and announced to become a SyntaxError, which would
    leave the floor unimportable (Dijkstra 9ba69cb1 item 3)."""
    source = GATE.with_name(name).read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(source, name, "exec")


def test_the_install_script_copies_both_files_and_names_them(tmp_path):
    sys.path.insert(0, str(GATE.parents[1] / "scripts"))
    try:
        import install_deny_gate
    finally:
        sys.path.pop(0)
    entry = install_deny_gate.install(tmp_path / "deny-floor")
    for name in ("deny_gate.py", "deny_floor.py"):
        assert (tmp_path / "deny-floor" / name).read_bytes() == GATE.with_name(name).read_bytes()
    # T1085: the file tools too, so a Write/Edit of jobs.json meets the floor.
    assert entry["matcher"] == "Bash|PowerShell|mcp__.*|Write|Edit|MultiEdit|NotebookEdit"
    assert entry["hooks"][0]["command"].endswith("/deny-floor/deny_gate.py")
    assert "async" not in entry["hooks"][0]


def test_a_non_utf8_codepage_cannot_open_the_gate(box, tmp_path):
    """U+201D is 0xE2 0x80 0x9D in UTF-8; 0x9D is undefined in cp1252.

    PYTHONIOENCODING=cp1252 is SET, not merely removed: with nothing set, this
    box's Windows stdin decodes cp1252 with surrogateescape and never raises,
    so removing the variables alone could not tell the fix from the bug
    (measured: the mutation stayed green). A strict cp1252 stdin raised before
    `raw` was assigned, and the gate allowed the call."""
    _, _, workspace = box
    env = {k: v for k, v in _sandbox_env(tmp_path).items() if k != "PYTHONUTF8"}
    env["PYTHONIOENCODING"] = "cp1252"
    body = json.dumps(_payload("Bash", "echo ”quoted”; rm -rf ~", workspace),
                      ensure_ascii=False).encode("utf-8")
    proc = subprocess.run([sys.executable, str(GATE)], input=body, capture_output=True,
                          env=env, timeout=60, cwd=env["HOME"])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"


# ── review 2be2a62c (re-check 2) ────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "git\nRemove-Item $home -Recurse -Force",       # N1, verbatim
    "Write-Output using git\nrm -rf ~",             # N1, verbatim
    "ls .git\nrm -rf ~",                            # N1, verbatim
    "x;git \nrm -rf ~",                             # `$` would match before the \n
    "echo git rm -rf ~",                            # git is not starting the command
])
def test_git_excuses_only_a_verb_it_starts_on_the_same_line(box, command):
    _, home, workspace = box
    assert deny_floor.refusal(command, workspace, home), command


@pytest.mark.parametrize("command", [
    "git rm -r --cached .", "echo x; git rm -r --cached .", "(git rm -r --cached .)",
])
def test_git_rm_still_passes_where_git_starts_the_command(box, command):
    _, home, workspace = box
    assert deny_floor.refusal(command, workspace, home) is None, command


@pytest.mark.parametrize("command", [
    "Set-Location ~; Get-ChildItem | Remove-Item -Recurse -Force",   # N2, verbatim
    "cd ~; ls | xargs rm -rf",                                       # N2, verbatim
    "Set-Location -Path:~; Remove-Item * -Recurse",
])
def test_a_listing_with_no_path_lists_the_tracked_folder(box, command):
    _, home, workspace = box
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[protected-root-delete]" in reason, command


def test_listings_that_name_their_folder_or_filter_still_pass(box):
    root, home, workspace = box
    scratch = root / "scratch-dir"
    scratch.mkdir()
    for command in (
        f"Set-Location -Path:{scratch}; Remove-Item * -Recurse",   # N3, the over-block
        "find . -name '*.pyc' | xargs rm -rf",                     # a filtered listing
        "Get-ChildItem build | Remove-Item -Recurse -Force",
        "cd build; ls | xargs rm -rf",
    ):
        assert deny_floor.refusal(command, workspace, home) is None, command


def _run_gate_payload(gate, payload, env):
    return subprocess.run([sys.executable, str(gate)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60, cwd=env["HOME"])


@pytest.fixture
def crashing_gate(tmp_path):
    """A gate whose decide() raises on EVERY payload. broken_gate only crashes
    once the floor is loaded, which a payload with no command never reaches,
    so it could not tell G3's fix from the bug (measured: mutation green)."""
    folder = tmp_path / "crashing"
    folder.mkdir()
    source = GATE.read_text(encoding="utf-8")
    anchor = "    command = _command(payload)\n    if not command:\n        return None\n"
    assert source.count(anchor) == 1
    (folder / "deny_gate.py").write_text(
        source.replace(anchor, "    raise RuntimeError('broken gate')\n" + anchor),
        encoding="utf-8")
    (folder / "deny_floor.py").write_bytes(GATE.with_name("deny_floor.py").read_bytes())
    return folder / "deny_gate.py"


def test_a_crashed_gate_passes_a_message_that_only_mentions_a_delete(box, tmp_path, crashing_gate):
    """G3: parsed JSON with nothing executable is not judged as raw text."""
    _, _, workspace = box
    payload = {"tool_name": "mcp__litesuite-tools__inbox", "cwd": str(workspace),
               "tool_input": {"action": "send", "message": "never run rm -rf ~ again"}}
    proc = _run_gate_payload(crashing_gate, payload, _sandbox_env(tmp_path))
    assert (proc.returncode, proc.stdout.strip()) == (0, ""), proc.stderr
    log = (tmp_path / "harness" / "deny_gate.log").read_text(encoding="utf-8")
    assert "broken gate" in log   # it DID crash: the pass came from the fallback


def test_a_crashed_gate_blocks_an_unparseable_payload_naming_a_delete(tmp_path, broken_gate):
    proc = subprocess.run([sys.executable, str(broken_gate)], input="not json: rm -rf ~",
                          capture_output=True, text=True, env=_sandbox_env(tmp_path),
                          timeout=60, cwd=str(tmp_path))
    assert proc.returncode == 2, proc.stderr


@pytest.mark.parametrize("key", ["text", "input", "data"])
@pytest.mark.parametrize("action,typed", [
    ("write", "rm -rf ~\n"),
    ("write", "rm -rf ~\r"),     # R3: a PTY submits on \r
    ("submit", "rm -rf ~"),      # R3: submit presses Enter itself
])
def test_a_keystroke_stream_is_judged_whenever_it_is_a_string(
        box, tmp_path, broken_gate, key, action, typed):
    """STOPGAP until LiteSuite's PTY write door (review 2be2a62c, a93de8a9 R3)."""
    _, _, workspace = box
    payload = {"tool_name": "mcp__litesuite-tools__terminal", "cwd": str(workspace),
               "tool_input": {"action": action, key: typed}}
    out = deny_gate.decide(payload)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert _run_gate_payload(broken_gate, payload, _sandbox_env(tmp_path)).returncode == 2
    payload["tool_input"][key] = "ls\r"   # an ordinary line still passes
    assert deny_gate.decide(payload) is None


# ── review a93de8a9 (re-check 3) ────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "find ~ -name '*' | xargs rm -rf",                        # R1, verbatim
    "find $HOME -type d -name .claude | xargs rm -rf",        # R1, verbatim
    "find ~ -name '*' -delete",                               # already denied; kept
])
def test_a_filtered_find_of_a_home_variable_is_still_refused(box, command):
    _, home, workspace = box
    assert deny_floor.refusal(command, workspace, home), command


def test_a_wildcard_only_pattern_narrows_nothing(box):
    """R2: `-name '*'` matches everything, so the find is judged as a recursive
    delete of its root, like `-mindepth 1 -delete` already was."""
    _, home, workspace = box
    for command in (f"find '{home}' -name '*' -delete",
                    f"find '{home}' -name '*' | xargs rm -rf",
                    f"find '{home}' -iname '*.*' -delete",
                    f"find '{home}' -mindepth 1 -delete"):
        reason = deny_floor.refusal(command, workspace, home)
        assert reason and "[protected-root-delete]" in reason, command
    # A real pattern still narrows: the repo-root case stays allowed.
    assert deny_floor.refusal("find . -name '*.pyc' | xargs rm -rf", workspace, home) is None
    assert deny_floor.refusal("find . -name '*.pyc' -delete", workspace, home) is None


# ── review 1daaa224 (approval, two SHOULDs folded in) ───────────────────────

def test_a_negated_or_aimed_name_test_narrows_nothing(box):
    """S1: `! -name x` keeps everything but x. S2: `-name .claude` / `'.c*'`
    under the profile aims at a protected folder. Both empty what they name."""
    _, home, workspace = box
    claude = home / ".claude"
    for command in (
        "find ~/.claude ! -name settings.json -delete",                    # S1
        f"find '{claude}' -not -name settings.json -delete",               # S1
        f"find '{claude}' -mindepth 1 ! -name settings.json | xargs rm -rf",  # S1
        f"find '{home}' -maxdepth 1 -name .claude | xargs rm -rf",         # S2
        f"find '{home}' -maxdepth 1 -name '.c*' -exec rm -rf {{}} +",      # S2
        f"find '{home}' -maxdepth 1 -iname '.GIT' -exec rm -rf {{}} +",    # S2, -iname
    ):
        reason = deny_floor.refusal(command, workspace, home)
        assert reason and "[protected-root-delete]" in reason, command
    for command in ("find . -name '*.pyc' | xargs rm -rf", "find . -name '*.pyc' -delete",
                    f"find '{home}' -maxdepth 1 -name '*.log' -delete"):
        assert deny_floor.refusal(command, workspace, home) is None, command


def test_a_lone_quote_is_not_a_drive_root(box):
    """Measured on the live gate, 2026-09-26: writing the S2 arm above through a
    heredoc was refused as "recursively deletes C:\\, a drive root". The line's
    f-string closing quote became an empty pipeline word, and "" resolved to "/"."""
    _, home, workspace = box
    line = 'f"find \'{home}\' -maxdepth 1 -name .claude | xargs rm -rf",  # S2'
    reason = deny_floor.refusal(line, workspace, home)
    assert reason is None or "drive root" not in reason, reason
    assert deny_floor.refusal('rm -rf ""', workspace, home) is None
    # An unresolvable word is skipped, never a reason to allow the REST.
    assert "[home-variable-delete]" in deny_floor.refusal('rm -rf "" ~', workspace, home)
    assert "a drive root" in deny_floor.refusal('rm -rf "" /', workspace, home)
    assert "user profile root" in deny_floor.refusal(f"rm -rf '' '{home}'", workspace, home)
    # The pipeline head is where an empty word used to survive (a direct delete
    # drops it); the "skip, don't allow" arms go through it too.
    assert "[home-variable-delete]" in deny_floor.refusal('ls "" ~ | xargs rm -rf', workspace, home)
    assert "user profile root" in deny_floor.refusal(
        f"ls '' '{home}' | xargs rm -rf", workspace, home)
    # Any unresolvable word -- a variable too -- is skipped, not an allow.
    assert "[home-variable-delete]" in deny_floor.refusal("rm -rf $x ~", workspace, home)
    # The guard itself, since callers now drop empty words before resolving.
    assert deny_floor._resolve('""', workspace, home) is None


# ── review 400014dd (Dijkstra re-check 5) ───────────────────────────────────

def test_every_path_in_a_powershell_comma_array_is_judged(box):
    """F-A: `'a','b'` is ONE shell word. Measured ALLOW before the split,
    including the card's own clause ("ANY delete whose target is the literal
    $home") behind a comma."""
    root, home, workspace = box
    a, b = root / "scratch-a", root / "scratch-b"
    a.mkdir()
    b.mkdir()
    for command, rule in (
        (f"Remove-Item '{a}','{home}' -Recurse -Force", "[protected-root-delete]"),
        (f"Remove-Item {a},{home} -Recurse -Force", "[protected-root-delete]"),
        (f"Remove-Item '{a}',$home -Recurse -Force", "[home-variable-delete]"),
        (f"Remove-Item -Path \"\",'{home}' -Recurse", "[protected-root-delete]"),
        (f"Get-ChildItem '{a}',$home | Remove-Item -Recurse", "[home-variable-delete]"),
    ):
        reason = deny_floor.refusal(command, workspace, home)
        assert reason and rule in reason, command
    assert deny_floor.refusal(f"Remove-Item '{a}','{b}' -Recurse", workspace, home) is None
    # A comma inside quotes is part of one path, not a separator.
    assert deny_floor.refusal("Remove-Item 'a,~' -Recurse", workspace, home) is None
    assert "[home-variable-delete]" in deny_floor.refusal("Remove-Item a,~ -Recurse", workspace, home)


# ── review 10cfe750 (Dijkstra re-check 6) ───────────────────────────────────

@pytest.mark.parametrize("command", [
    "rm -rf {x,~}", "rm -rf {build,$HOME}", "rm -rf ~{,}",   # F-C, verbatim
    "ls {x,~} | xargs rm -rf",
])
def test_bash_brace_expansion_is_judged_piece_by_piece(box, command):
    """F-C: brace expansion runs before tilde/parameter expansion."""
    _, home, workspace = box
    assert "[home-variable-delete]" in deny_floor.refusal(command, workspace, home), command


def test_braces_that_name_ordinary_folders_pass(box):
    _, home, workspace = box
    for command in ("rm -rf {build,dist}", "rm -rf ${x}/build"):
        assert deny_floor.refusal(command, workspace, home) is None, command
    assert "[home-variable-delete]" in deny_floor.refusal("rm -rf ${HOME}", workspace, home)
    # An enclosing script block still starts the pipeline head at its opener.
    for command in ("& { Get-ChildItem ~ | Remove-Item -Recurse }",
                    "Invoke-Command { gci $home -r | ri -fo }"):
        assert "[home-variable-delete]" in deny_floor.refusal(command, workspace, home), command
    # The shape that NEEDS the cut: the block's listing names no path, so it
    # lists the tracked folder. Uncut, `Get-ChildItem` was read as a path.
    reason = deny_floor.refusal("Set-Location ~; & { Get-ChildItem | Remove-Item -Recurse }",
                                workspace, home)
    assert reason and "[protected-root-delete]" in reason


def test_include_exclude_filter_values_are_patterns_not_targets(box):
    """Over-block (10cfe750): the pattern `*.log` resolved to the repo root."""
    _, home, workspace = box
    for command in (
        r"Remove-Item .\build -Recurse -Include *.log,*.tmp",
        r"Remove-Item .\build -Recurse -Exclude keep.txt",
        r"Remove-Item .\build -Recurse -Filter:*.log",
        r"Remove-Item .\build -Recurse -inc *.log",
        "Get-ChildItem build -Filter *.log,*.tmp | Remove-Item -Recurse",
        "Get-ChildItem -Filter *.log | Remove-Item -Recurse",
    ):
        assert deny_floor.refusal(command, workspace, home) is None, command
    for command in (f"Remove-Item '{home}' -Recurse -Include *.log",
                    "Get-ChildItem -Filter * | Remove-Item -Recurse"):  # * narrows nothing
        assert deny_floor.refusal(command, workspace, home), command


# ── review 0f435720 (Dijkstra re-check 7) ───────────────────────────────────

@pytest.mark.parametrize("command", [
    # X1 (a regression from 602d231): a pattern AIMED at a protected folder
    "Get-ChildItem -Filter .claude | Remove-Item -Recurse -Force",
    "gci -Filter .c* | ri -r -fo",
    "gci -Include .claude,.codex | ri -r",
    "cd ~; gci -Filter .claude | ri -r -fo",
    "gci -Filter:.claude | ri -r",
    # X2: -Exclude is a negation, it lists everything else
    "gci -Exclude settings.json | ri -r -fo",
    "Get-ChildItem -Exclude NTUSER.DAT | Remove-Item -Recurse -Force",
    "cd ~/.claude; gci -Exclude settings.json | ri -r -fo",
    # X3: a parenthesised head
    "(gci ~) | ri -r -fo",
    "(Get-ChildItem $home) | Remove-Item -Recurse -Force",
])
def test_pattern_parameters_that_do_not_narrow_leave_the_profile_protected(box, command):
    """The cwd is the profile-shaped folder itself."""
    _, home, _ = box
    assert deny_floor.refusal(command, home, home), command


def test_exclude_in_a_repo_root_does_not_narrow(box):
    _, home, workspace = box
    reason = deny_floor.refusal("gci -Exclude .git | ri -r -fo", workspace, home)
    assert reason and "a git repository root" in reason


def test_real_patterns_from_the_profile_still_pass(box):
    _, home, _ = box
    for command in ("gci -Filter *.x | ri -r",
                    r"Remove-Item .\build -Recurse -Include *.log,*.tmp"):
        assert deny_floor.refusal(command, home, home) is None, command


def test_a_path_pattern_aimed_at_a_protected_folder_narrows_nothing(box):
    """F-B: S2 covered -name/-iname only; -path matches on its last segment."""
    _, home, workspace = box
    for command in (f"find '{home}' -maxdepth 1 -path '*/.claude' | xargs rm -rf",
                    f"find '{home}' -maxdepth 1 -ipath '*/.CODEX' -delete"):
        reason = deny_floor.refusal(command, workspace, home)
        assert reason and "[protected-root-delete]" in reason, command
    assert deny_floor.refusal("find . -path '*/build/*.pyc' -delete", workspace, home) is None


# ── owner-launcher: agents may not run LiteTUI's run.bat (T1054) ─────────────
#
# the user, Theater sots-quick5 "runbat": "Deny floor blocks agents from it".
# run.bat sets LITETUI_OWNER=1 behind a CLAUDECODE / LITETUI_AGENT_SHELL check
# that a Codex seat or a plain subprocess does not carry. Every string below is
# judged, never run.

@pytest.fixture
def checkouts(box):
    """A LiteTUI checkout (run.bat beside src/litetui) and a LiteSuite one."""
    root, home, workspace = box
    litetui = root / "work" / "LiteTUI"
    (litetui / "src" / "litetui").mkdir(parents=True)
    (litetui / "run.bat").write_text("@echo off\n", encoding="utf-8")
    litesuite = root / "work" / "LiteSuite"
    litesuite.mkdir(parents=True)
    (litesuite / "run.bat").write_text("@echo off\n", encoding="utf-8")
    return home, workspace, litetui, litesuite


LAUNCHES = [
    "{bat}", '"{bat}"', "'{bat}'", "{fwd}", "{upper}",
    'cmd /c "{bat}"', "cmd /d /c {bat}", 'cmd.exe /d /c "{bat}"',
    'start "" "{bat}"', "call {bat}", "& '{bat}'", '& "{fwd}"',
    "Start-Process '{bat}'", 'Start-Process -FilePath "{bat}" -WindowStyle Hidden',
    "Start-Process -FilePath:{bat}", "Invoke-Item '{bat}'", "ii {bat}",
    "echo go; {bat}", "cd /d {dir} && run.bat", 'cmd /c "cd /d {dir} && run.bat"',
    "cd {dir}; .\\run.bat", "Set-Location {dir}; & .\\run.bat", "cd {dir} && cmd /c run",
    "Start-Process cmd -ArgumentList '/c','{bat}'", "{dir}\\run",
    "Start-Process -FilePath cmd.exe -ArgumentList '/d','/c','{bat}'",
    # Dijkstra B1: a bare `run` OPENING a segment runs the folder's run.bat.
    'cmd /c "cd /d {dir} && run"', 'cmd /c "cd /d {dir} & run"',
    "cd /d {dir} && run", "cd /d {dir} & run & echo done",
    # Dijkstra N1 (9ba69cb1): cmd's command-position syntax in front of a bare
    # `run`; cmd RAN each against an echo-only run.bat.
    "cd /d {dir} && @run", "cd /d {dir} && ^run", "cd /d {dir} && 2>nul run",
    "cd /d {dir} && if 1==1 run", "cd /d {dir} && for %i in (1) do run",
    "cd /d {dir} && if exist src run", "cd /d {dir} && if defined X run",
    # Dijkstra P1 (91f71e1d): a closed group ahead of the launch; its first word
    # (echo, type) is not the command that runs it. cmd RAN the else and for /f forms.
    "if 1==2 (echo a) else {bat}", "for /f %i in ('echo x') do {bat}",
    "cd /d {dir} && if 1==2 (echo a) else run", "cd /d {dir} && if 1==2 (type x) else run",
    "cd /d {dir} && for /f %i in ('echo') do run",
]
IN_CHECKOUT = [".\\run.bat", "./run.bat", "run.bat", "cmd /c run.bat", "cmd /c run",
               "call run", "start run.bat", "& .\\RUN.BAT", "..\\LiteTUI\\run.bat",
               "run"]


def _spell(template: str, litetui: Path, other: Path | None = None) -> str:
    bat = litetui / "run.bat"
    return template.format(bat=bat, fwd=bat.as_posix(), upper=str(bat).upper(),
                           dir=litetui, other=other, odir=other and other.parent)


@pytest.mark.parametrize("template", LAUNCHES)
def test_every_spelling_of_the_litetui_launcher_is_refused(checkouts, template):
    home, workspace, litetui, _ = checkouts
    command = _spell(template, litetui)
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[owner-launcher]" in reason, command
    assert "owner-capable" in reason and "spawn" in reason


@pytest.mark.parametrize("command", IN_CHECKOUT)
def test_relative_spellings_are_refused_from_inside_the_checkout(checkouts, command):
    home, _, litetui, _ = checkouts
    reason = deny_floor.refusal(command, litetui, home)
    assert reason and "[owner-launcher]" in reason, command


@pytest.mark.parametrize("template", [
    # CONTROL: another project's run.bat is not the owner launcher.
    '"{other}"', "cmd /d /c {other}", "Start-Process '{other}'", "& '{other}'",
    # CONTROL: reading the launcher does not run it.
    "type {bat}", "cat '{fwd}'", "Get-Content '{bat}'", "gc {bat} | Select-Object -First 5",
    "cmd /c type {bat}", "git -C {dir} diff run.bat", "rg LITETUI_OWNER {bat}",
    # CONTROL: the word "run" that names no launcher.
    "npm run format", "uv run python -V", "rerun.bat", "cd {dir} && npm run build",
    # CONTROL: a bare `run` where no LiteTUI launcher is (9ba69cb1 item 2).
    "run", "cd /d {odir} && run",
])
def test_other_launchers_and_reads_of_the_launcher_pass(checkouts, template):
    home, workspace, litetui, litesuite = checkouts
    command = _spell(template, litetui, litesuite / "run.bat")
    assert bool(deny_floor.refusal(command, workspace, home)) is (
        "{other}" in template or template in ("run", "cd /d {odir} && run")), command


@pytest.mark.parametrize("command", [
    # CONTROL, cwd = the checkout: `run` as a SUBCOMMAND is not the launcher.
    "bun run dev", "npm run build", "uv run pytest", "lst run tasks action=list",
    "uv run --no-sync python -m pytest tests", "echo run", "git log -- run.bat",
    "(bun run dev)", "if 1==1 (bun run dev)",
    # CONTROL: copies and moves carry the file, never run it.
    "copy run.bat run.bak", "Copy-Item .\\run.bat C:\\tmp\\", "robocopy . C:\\tmp run.bat",
])
def test_run_as_a_subcommand_and_copies_pass_inside_the_checkout(checkouts, command):
    home, _, litetui, _ = checkouts
    assert deny_floor.refusal(command, litetui, home) is None, command


@pytest.mark.parametrize("command, refused", [
    ("git add run.bat", False),        # git is a reader: it never runs the file
    ("echo run.bat", False),           # output heads print, never run
    ("Write-Host 'starting run.bat'", False),
    ("echo x & run.bat", True),        # the pass is per segment
    ("echo x; .\\run.bat", True),
    ("echo $(run.bat)", True),         # a substitution executes
    ("echo `run.bat`", True),
    ("python -c \"print(open('run.bat').read())\"", True),   # KNOWN OVER-BLOCK
    ("if exist package.json bun run dev", False),  # 8bee16c: run is bun's operand
    ("python x.py \"please do run it\"", False),    # 8bee16c: quoted argument is not argv0
    ("echo please do run it", False),              # ...unless a reader heads it
])
def test_the_fail_closed_edge_is_where_it_says(checkouts, command, refused):
    """The cost of failing closed, made visible: a command that only MENTIONS
    run.bat is refused unless a known reader or output head starts ITS segment.
    Agents read and edit files with their Read/Edit/Write tools, not a shell
    `python -c`."""
    home, _, litetui, _ = checkouts
    reason = deny_floor.refusal(command, litetui, home)
    assert bool(reason) is refused, (command, reason)


def test_the_gate_denies_the_launcher(checkouts):
    _, _, litetui, _ = checkouts
    command = f"cmd /d /c {litetui / 'run.bat'}"
    for tool in ("Bash", "PowerShell"):
        out = deny_gate.decide(_payload(tool, command, litetui))["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny", tool
        assert "[owner-launcher]" in out["permissionDecisionReason"]


@pytest.mark.parametrize("shell", [None, "cmd", "bash", "powershell"])
@pytest.mark.parametrize("original", [False, True])
def test_here_string_exemption_requires_proven_shell(tmp_path, monkeypatch, shell, original):
    owner = tmp_path / "run.bat"
    owner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    command = "@'\nrun\n'@"
    if original:
        command = (Path(__file__).parent / "fixtures/T0291-here-string.txt").read_text(encoding="utf-8")
    kwargs = {} if shell is None else {"shell": shell}
    reason = deny_floor.refusal(command, tmp_path, **kwargs)
    assert bool(reason) is (shell != "powershell"), reason


@pytest.mark.parametrize("tool_name", ["Bash", "powershell", "mcp__litesuite-tools__shell", "terminal"])
@pytest.mark.parametrize("key", ["command", "text", "input", "data"])
def test_hook_unknown_shell_keeps_here_string_scanned(tmp_path, monkeypatch, tool_name, key):
    # Hook tool names are not proof of a shell; load the real floor with a
    # monkeypatched inert owner while retaining decide's real routing.
    owner = tmp_path / "run.bat"
    owner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    from types import SimpleNamespace
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
    monkeypatch.setattr(deny_gate.importlib.util, "spec_from_file_location", lambda *a: spec)
    monkeypatch.setattr(deny_gate.importlib.util, "module_from_spec", lambda spec: deny_floor)
    result = deny_gate.decide({"tool_name": tool_name, "cwd": str(tmp_path),
                              "tool_input": {key: "@'\nrun\n'@", "shell": "powershell"}})
    assert result and "[owner-launcher]" in result["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("closer", ["\u2018", "\u2019", "\u201a", "\u201b", "\r'"])
@pytest.mark.parametrize("separator", ["; ", "\n"])
def test_here_string_ambiguous_terminator_falls_back(tmp_path, monkeypatch, closer, separator):
    owner = tmp_path / "run.bat"
    owner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    command = "@'\nfoo\n" + closer + "@" + separator + "run\n'@ | Add-Content x\n'"
    if closer == "\r'":
        command = "@'\nfoo" + closer + "@" + separator + "run\n'@ | Add-Content x\n'"
    default = deny_floor.refusal(command, tmp_path)
    assert default and "[owner-launcher]" in default
    assert deny_floor.refusal(command, tmp_path, shell="powershell") == default


@pytest.mark.parametrize("command", [
    "@'\r\nrun\r\n'@ | Add-Content tests.ts",
    "Write-Output \u2019; run",
    "Write-Output ok\rrun",
    "Write-Output \u2019",
    "Write-Output ok\rWrite-Output done",
    "Write-Output \u2019; @'\nrun\n'@ | Add-Content x",
    "Write-Output ok\r\n@'\nrun\n'@ | Add-Content x\r",
])
def test_here_string_unusual_syntax_is_conservative(tmp_path, monkeypatch, command):
    owner = tmp_path / "run.bat"
    owner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    if "\u2019" not in command and "\r" not in command.replace("\r\n", ""):
        assert deny_floor.refusal(command, tmp_path, shell="powershell") is None
    else:
        assert deny_floor.refusal(command, tmp_path, shell="powershell") == deny_floor.refusal(command, tmp_path)


GAP_COMMANDS_T0291A = [
    "Get-Date\rrun", "Get-Date\r\r\nrun",
    "& { run }", "1 | % {run}", "1 | ForEach-Object { run }",
    "& { & { run } }", "1 | % { & {run} }",
    "Get-Date(run", "Get-Date(run)", "Write-Output $(run)",
    "Get-Date($(run))", "Get-Date(1 | % {run})",
]

COST_COMMANDS_T0291A = [
    'python -c "print(run)"', 'python -c "print({run})"',
    'python -c "print( run )"', 'python -c "print({ run })"',
    'node -e "console.log(run)"', 'node -e "console.log({run})"',
    'node -e "console.log({ run })"',
    'git commit -m "fix(run): keep {run} as data"',
    "Get-Date(1)", "Format-Value($value)", "Invoke-Headless lst.exe @('run','tasks')",
    "$p.ArgumentList.Add('run')", "python x.py 'please do run it'",
    "Get-Content run.bat", "rg 'run(' tests", "rg '{run}' tests",
]

HERE_COST_COMMANDS_T0291A = [
    "@'\nrun = () => ({ run });\nfunction test() { run(); }\n'@ | Add-Content tests.ts",
    "@'\ndef run():\n    return {run}\nprint( run )\n'@ | Set-Content test.py",
    "@'\r\nrun = () => ({run});\r\n'@ | Add-Content tests.ts",
]


def _gap_owner_t0291a(tmp_path, monkeypatch):
    owner = tmp_path / "run.bat"
    owner.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    return owner


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", GAP_COMMANDS_T0291A)
def test_older_owner_command_position_gaps(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason and "[owner-launcher]" in reason


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", COST_COMMANDS_T0291A)
def test_owner_position_cost_corpus(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason is None


@pytest.mark.parametrize("command", HERE_COST_COMMANDS_T0291A)
def test_owner_position_here_string_cost(tmp_path, monkeypatch, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell="powershell") is None


QUOTE_GUARD_COMMANDS_T0291A = [
    'python -c "print(run)"; run', 'node -e "console.log(run)" & run',
    'python -c "print(run)"\nrun', 'python -c "print(run)"\rrun',
    'node -e "console.log(run)" | run',
    'python -c "print(run)"; & {run}', 'node -e "console.log(run)"; Get-Date(run)',
    'run; python -c "print(run)"', 'run | node -e "console.log(run)"',
    'python -c "print({ run })', 'node -e "console.log({ run })',
    'python -c "print(\'{ run }\')"',
    r'python -c "print(\"{ run }\")"',
    'python -c "print(\n{ run }\n)"', 'node -e "console.log(\r{ run }\r)"',
    'python -c "print($(run))"', 'node -e "console.log(`run`)"',
    'unknown -c "print({run})"', 'python -x "print({ run })"',
    'python -c other "print({ run })"', 'node -e other "console.log({ run })"',
]


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", QUOTE_GUARD_COMMANDS_T0291A)
def test_owner_position_quote_guard_fails_closed(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    # Brace objects after a parenthesis are no longer command boundaries.
    # These code/quote ceilings deliberately retain parent classification.
    data_object = "print({" in command or "console.log({" in command or "print(\'{" in command or 'print(\\"{' in command
    assert bool(reason) is (not data_object or command == 'python -c "print(\'{ run }\')"')


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", [
    'unknown "prefix; python -c "print({ run })"',
    '$p.ArgumentList.Add(\'run)', "Invoke-Headless @('run)",
    r'python -c \"print({ run })"',
])
def test_owner_position_ambiguous_quote_context(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert bool(reason) is ("ArgumentList" in command or "Invoke-Headless" in command)


READER_EXEC_COMMANDS_T0291A = [
    'git -c alias.x="!(./run.bat)" x run',
    'git -c alias.x="!(cmd /c run.bat)" x',
    'git -c alias.x="!(cmd.exe /c run.bat)" x',
    'git -c alias.x="!(start run.bat)" x',
    'git -c alias.x="!(call run.bat)" x',
    'git -c alias.x="!(powershell -c run.bat)" x',
    'git -c "alias.x=!(./run.bat)" x run',
    'git --config alias.x="!(./run.bat)" x run',
    'git -c core.pager="(./run.bat)" log',
    'git -c core.editor="(./run.bat)" commit',
    'git -c core.sshCommand="(./run.bat)" fetch',
    'git --exec="(./run.bat)" status',
    'git --upload-pack="(./run.bat)" fetch',
    'X=value git -c alias.x="!(./run.bat)" x',
    'git commit -m other -c alias.x="!(./run.bat)"',
    'git commit -m "!(./run.bat)"',
    'sed -e "(./run.bat)" file',
    'awk -e "(./run.bat)" file',
    'vim -c "(./run.bat)" file',
    'rg --pre "(./run.bat)" text',
    'git commit -m "prose $(./run.bat)"',
    'Write-Output "prose $(./run.bat)"',
]


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", READER_EXEC_COMMANDS_T0291A)
def test_owner_reader_executable_option_not_data(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason and "[owner-launcher]" in reason


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", [
    'git commit -m "fix: ( run ) and { run } are prose"',
    'git tag -m "fix: ( run ) and { run } are prose" v1',
    'git commit --message "fix: ( run ) and { run } are prose"',
    'git tag --message "fix: ( run ) and { run } are prose" v1',
    'Write-Output "examples: { run } and (run)"',
    "Write-Host 'examples: {run} and ( run )'",
    'echo "examples: {run} and ( run )"',
    'printf "examples: {run} and ( run )"',
])
def test_owner_explicit_quoted_data_positions(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert bool(reason) is command.startswith("git tag ")


SHAPE_ATTACKS_T0291A = [
    'Write-Output "prefix\u201d; run; \u201csuffix"',
    'python -c "prefix\u201d; run; \u201csuffix"',
    'echo ^"& run & ^"', 'python -c ^"& run & ^"',
    "echo 'prefix & run & suffix'", "python -c 'prefix & run & suffix'",
    'echo "prefix\u2019; run; \u2018suffix"',
    'python -c "prefix\u2019; run; \u2018suffix"',
    'echo "{ run } %UNTRUSTED%"', 'echo "%Q% & run & %Q%"',
    'python -c "print({ run })"; run',
    'run; python -c "print({ run })"',
    'git -c alias.x="!(./run.bat)" x run',
    'git -c alias.x="!(cmd /c run.bat)" x',
]


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SHAPE_ATTACKS_T0291A)
def test_owner_whole_shape_does_not_swallow_execution(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert bool(reason) is (command != 'echo "{ run } %UNTRUSTED%"')


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("char", ["^", "`", "$", "\\", "!", "%", "\r", "\n", "'",
    "\u2018", "\u2019", "\u201a", "\u201b", "\u201c", "\u201d", "\u201e", "\u201f",
    "\u00ab", "\u00bb", "\u2039", "\u203a", "\uff02", "\uff07", "\u200b", "\u202e", "\x00"])
def test_owner_whole_shape_ambiguity_is_scanned(tmp_path, monkeypatch, shell, char):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    command = 'echo "( run )' + char + '"'
    assert not deny_floor._launcher_quoted_argument(command, command.index('run'))
    assert deny_floor.refusal(command, owner.parent, shell=shell)


SHAPE_COST_COMMANDS_T0291A = [
    "python -c 'print(run)'", "python -c 'print({ run })'",
    "node -e 'console.log({run})'", "git commit -m 'fix: { run }'",
    'git tag -m "fix: { run }"',
    'git tag -m "fix: { run }" v1',
]


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SHAPE_COST_COMMANDS_T0291A)
def test_owner_single_quotes_and_trailing_arguments_cost(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert bool(reason) is (command == 'git tag -m "fix: { run }" v1')


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", [
    'git commit -c core.editor="(./run.bat)" -m text',
    'git tag -c core.pager="(./run.bat)" -m text',
    'less "!(cmd /c run.bat)"', 'more "!(cmd /c run.bat)"',
    'git commit -m "!(cmd /c run.bat)"',
    'echo "!(cmd /c run.bat)"',
    'echo "& run &"\n',
])
def test_owner_positive_shape_execution_siblings(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell)


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command, exempt", [
    ('Write-Output\t"( run )"', True),
    ('git\tcommit\t-m\t"fix: { run }"', True),
    ('echo\t"{ run }"', True),
    ('echo\v"( run )"', False),
    ('echo\u00a0"( run )"', False),
    ('echo "( run )"\n', False),
])
def test_owner_shape_token_whitespace(tmp_path, monkeypatch, shell, command, exempt):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor._launcher_quoted_argument(command, command.index('run')) is exempt
    assert bool(deny_floor.refusal(command, owner.parent, shell=shell)) is (not exempt)


CODE_SINK_COMMANDS_T0291A = [
    'python -c "import os; run = 0; os.system(dir()[-1])"',
    'python -c "import os; run = 0; os.system([*locals()][-1])"',
    'python -c "import os; run = 0; os.system(max(dir()))"',
    'python.exe -c "import os; run = 0; os.system(max(dir()))"',
    'node -e "var run,child_process;require(Object.keys({child_process})[0]).execSync(Object.keys({run})[0])"',
    'node.exe -e "var run,child_process;require(Object.keys({child_process})[0]).execSync(Object.keys({run})[0])"',
]

EXEC_IDENT_HEADS_T0291A = [
    "Start-Process", "saps", "start", "Invoke-Item", "ii", "iex", "Invoke-Expression",
    "Invoke-Command", "icm", "Start-Job", "sajb", "call", "cmd", "cmd.exe",
    "powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash", "sh", "Start-ThreadJob",
]


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", CODE_SINK_COMMANDS_T0291A)
def test_owner_interpreter_code_not_data(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    # D1: richer anchored interpreter code mentioning the launcher is refused.
    # Classification only: never invoke the candidates.
    assert deny_floor.refusal(command, owner.parent, shell=shell)


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("head", EXEC_IDENT_HEADS_T0291A)
@pytest.mark.parametrize("upper", [False, True])
def test_owner_execution_sink_ident_argument(tmp_path, monkeypatch, shell, head, upper):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    command = (head.upper() if upper else head) + '("run")'
    assert deny_floor.refusal(command, owner.parent, shell=shell)


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", [
    'Invoke-Expression("run.bat")', '& { run }', '1 | % { run }',
    'if ($true) { run }', 'try { run } catch {}', 'function f { run }',
    '& { & { run } }', '1 | % { & { run } }', '. { run }',
])
def test_owner_brace_command_bodies_remain_scanned(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell)


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", [
    'rg "{run}" tests', "rg '{run}' tests", 'echo \'{"run": 1}\'',
    'git grep -n "{ run }"', 'sed s/{run}/{exec}/',
    'python -c \'print(run)\'', 'node -e \'console.log({run})\'',
])
def test_owner_brace_data_cost_reduction(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


@pytest.mark.parametrize("shell", [None, "cmd", "powershell", "bash"])
@pytest.mark.parametrize("command, refused", [('Get-Date\rrun', True), ('& { run }', True), ('1 | % {run}', True), ('1 | ForEach-Object { run }', True), ('& { if ($true) { run } }', True), ('Get-Date(run)', True), ('git commit -m "fix(run): keep owner launcher"', False), ("rg '{run}' tests", False), ('rg "{run}" tests', False), ("node -e 'console.log({run})'", False)])
def test_owner_harbor_corpus_regressions(tmp_path, monkeypatch, shell, command, refused):
    """the orchestrator corpus_a/corpus4 data; no candidate string is executed."""
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert bool(deny_floor.refusal(command, owner.parent, shell=shell)) is refused


FINAL_ALLOWED_T0291A = [
    "git commit -m 'fix(run): x'", "git commit -am 'fix(run): x'",
    "git commit --amend -m 'fix(run): x'", 'git commit --amend -m "fix(run): x"',
    'git commit -am "fix(run): x"', "git commit -a --amend --message 'fix(run): x'",
    'echo \'{"run": 1}\'', "python -c 'print(run)'", 'python -c \'print("x")\'',
    'python -c "print(run)"', 'PYTHON.EXE -c "print(run)"',
    'python -c "print ( run )"', "node -e 'console.log({run})'",
    'NODE.EXE -e "console.log( {run} )"',
]
FINAL_RICH_CODE_T0291A = [
    'python -c "print(dir())"', 'python -c "print(dir()[-1])"',
    'python -c "print([*locals()][-1])"', 'python -c "print(max(dir()))"',
    'python -c "print(run())"', 'node -e "console.log(run())"',
    'python -c "print(run);x=1"', 'node -e "console.log(`run`)"',
    'python -c "print(run[0])"', 'python -c "print(run+1)"',
    'python -c "Print(run)"', 'node -e "Console.log(run)"',
]

@pytest.mark.parametrize("shell", [None, "cmd", "powershell", "bash"])
@pytest.mark.parametrize("command", FINAL_ALLOWED_T0291A)
def test_owner_final_data_and_one_call_allow_shapes(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None
    assert deny_floor._launcher_quoted_argument(command, command.index('run') if 'run' in command else command.index('x'))

@pytest.mark.parametrize("command", FINAL_RICH_CODE_T0291A)
def test_owner_final_one_call_richer_code_has_no_exemption(command):
    # No run token means no public owner refusal; this pins the shape boundary.
    assert not deny_floor._launcher_quoted_argument(command, command.index('(') + 1)

@pytest.mark.parametrize("shell", [None, "cmd", "powershell", "bash"])
@pytest.mark.parametrize("command, refused", [
    ('git commit -m "$(cat <<\'EOF\'\nfix(run): x\nEOF\n)"', True),
    ('git commit -m "$(cat <<\'EOF\'\nplain message\nEOF\n)"', False),
])
def test_owner_heredoc_message_current_classification(tmp_path, monkeypatch, shell, command, refused):
    # By design for now: needs its own multi-line grammar (T0291-A-A).
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert bool(deny_floor.refusal(command, owner.parent, shell=shell)) is refused

@pytest.mark.parametrize("shell", [None, "cmd", "powershell", "bash"])
@pytest.mark.parametrize("char", ["&", "|", "<", ">", "^", "%", "`", "$", "\\", "!", "\r", "\n", "'", "\u2018", "\u2019", "\u201c", "\uff02", "\u200b", "\x00"])
def test_owner_single_quote_token_ambiguity_no_exemption(tmp_path, monkeypatch, shell, char):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    command = "echo '( run )" + char + "'"
    assert not deny_floor._launcher_quoted_argument(command, command.index('run'))
    assert deny_floor.refusal(command, owner.parent, shell=shell)


@pytest.mark.parametrize("shell", [None, "cmd", "powershell", "bash"])
@pytest.mark.parametrize("command", [c for c in FINAL_RICH_CODE_T0291A if "run" in c])
def test_owner_final_richer_code_mentions_launcher_refused(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell)

@pytest.mark.parametrize("shell", [None, "cmd", "powershell", "bash"])
@pytest.mark.parametrize("command", [c for c in FINAL_RICH_CODE_T0291A if "run" not in c])
def test_owner_final_richer_code_without_launcher_untouched(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


@pytest.mark.parametrize("head, flag, call", [("python", "-c", "print"), ("PYTHON.EXE", "-c", "print"), ("node", "-e", "console.log"), ("NODE.EXE", "-e", "console.log")])
@pytest.mark.parametrize("space", [" ", "\t", "  "])
@pytest.mark.parametrize("quote", ["'", '"'])
def test_owner_final_call_case_spacing_matrix(tmp_path, monkeypatch, head, flag, call, space, quote):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    command = space + head + space + flag + space + quote + call + space + "( run )" + quote + space
    assert deny_floor.refusal(command, owner.parent) is None
    bad = command.replace(call, call.upper())
    assert not deny_floor._launcher_quoted_argument(bad, bad.index("run"))
    assert deny_floor.refusal(bad, owner.parent)

@pytest.mark.parametrize("command", [
    'node -e "console.log(run())"', 'python -c "print(run[0])"',
    'node -e "Object.keys({run})"',
])
def test_owner_rich_code_still_requires_identity(tmp_path, monkeypatch, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert "[owner-launcher]" in deny_floor.refusal(command, other)
    owner.unlink()
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path)

@pytest.mark.parametrize("command", [
    "git commit -m 'prefix & run & suffix'", "git commit -am 'prefix | run | suffix'",
    "git commit --amend -m 'prefix & run & suffix'",
])
def test_owner_git_message_options_do_not_hide_operators(tmp_path, monkeypatch, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert not deny_floor._launcher_quoted_argument(command, command.index('run'))
    assert deny_floor.refusal(command, owner.parent)


# F1: grouping parens can carry executable scriptblocks; call parens are data.
GROUPED_BLOCKS_T0291A = [
    "&({run})", "& ({run})", "&({run.bat})", "({run}).Invoke()",
    "&(({run}))", "1 | ForEach-Object ({run})", "1 | % ({run})", ". ({run})",
    "({ run }).Invoke()", "& ({ run })", "&({ run })", "(({run})).Invoke()",
]
GROUPED_DATA_T0291A = [
    'foo({run: 1})', 'foo]({run})', 'foo()({run})',
    'rg "{run}" tests', 'sed s/{run}/{exec}/', 'echo \'{"run": 1}\'',
    'git grep -n "{ run }"', "python -c 'print(run)'", "node -e 'console.log({run})'",
]

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", GROUPED_BLOCKS_T0291A)
def test_owner_grouped_scriptblock_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason and "[owner-launcher]" in reason

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", GROUPED_BLOCKS_T0291A)
def test_owner_grouped_scriptblock_identity_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert "[owner-launcher]" in deny_floor.refusal(command, other, shell=shell)
    owner.unlink()
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path, shell=shell)

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", GROUPED_DATA_T0291A)
def test_owner_grouped_scriptblock_data_controls_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


SCRIPTBLOCK_CLOSURES_T0291A = [
    "& {{ run }}", "1 | ?{ run }", "{ run }.Invoke()", "{run}.Invoke()",
    "if($true){run}", "while($true){run}", "switch(1){1{run}}",
    "try{run}catch{}", "do{run}while(0)", "{ run }", "{run}",
    "else{run}", "finally{run}", "catch{run}", "begin{run}", "process{run}",
    "end{run}", "trap{run}", "function f{run}", "filter f{run}",
    "TRY{run}", "function F_1{run}",
]
CLOSURE_DATA_T0291A = [
    "foo{run}", "foo1{run}", "foo.try{run}", "${run}",
    'rg "{run}" tests', "rg '{run}' tests", 'sed s/{run}/{exec}/',
    'echo \'{"run": 1}\'', 'git grep -n "{ run }"',
    "python -c 'print(run)'", "node -e 'console.log({run})'", 'foo({run: 1})',
]

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SCRIPTBLOCK_CLOSURES_T0291A)
def test_owner_scriptblock_closures_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason and "[owner-launcher]" in reason

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SCRIPTBLOCK_CLOSURES_T0291A)
def test_owner_scriptblock_closures_identity_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert "[owner-launcher]" in deny_floor.refusal(command, other, shell=shell)
    owner.unlink()
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path, shell=shell)

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", CLOSURE_DATA_T0291A)
def test_owner_scriptblock_closures_data_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", ["a1{run}", "${1}{run}", "foo{1{run}", "foo/try{run}", "foo\\try{run}"])
def test_owner_numeric_label_and_keyword_data_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


NESTED_GROUPS_T0291A = [
    "&{({run}).Invoke()}", "try{({run}).Invoke()}catch{}", "if(1){({run})}",
    "&{&({run})}", "1 | ?({run})", "foreach($x in 1){({run}).Invoke()}",
]

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", NESTED_GROUPS_T0291A)
def test_owner_nested_group_scriptblock_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason and "[owner-launcher]" in reason

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", NESTED_GROUPS_T0291A)
def test_owner_nested_group_identity_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert "[owner-launcher]" in deny_floor.refusal(command, other, shell=shell)
    owner.unlink()
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path, shell=shell)

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", ['foo({run: 1})', 'console.log({run})', 'foo]({run})', 'foo()({run})'])
def test_owner_nested_group_call_data_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


SWITCH_LABELS_T0291A = [
    'switch(10){10{run}}', "switch(1){'1'{run}}", 'switch(1){"1"{run}}',
    'switch(1){default{run}}', 'switch(1){{$_ -gt 0}{run}}',
]

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SWITCH_LABELS_T0291A)
def test_owner_switch_labels_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason and "[owner-launcher]" in reason

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SWITCH_LABELS_T0291A)
def test_owner_switch_labels_identity_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert "[owner-launcher]" in deny_floor.refusal(command, other, shell=shell)
    owner.unlink()
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path, shell=shell)

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", ["foo{10{run}", "a'1'{run}", "${1}{run}", "foo{default{run}"])
def test_owner_switch_labels_data_f1(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", [
    r"&{({.\run.bat}).Invoke()}", r"try{({.\run.bat}).Invoke()}catch{}",
    r"switch(10){10{.\run.bat}}", r"switch(1){'1'{.\run.bat}}",
    r'switch(1){"1"{.\run.bat}}', r"switch(1){default{.\run.bat}}",
    r"switch(1){{$_ -gt 0}{.\run.bat}}",
])
def test_owner_f1_nested_and_label_paths(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert "[owner-launcher]" in deny_floor.refusal(command, other, shell=shell)
    owner.unlink()
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path, shell=shell)


SWITCH_LABEL_QUOTING_T0291A = [
    "switch(\"it's\"){'it''s'{run}}", 'switch(1){"a""b"{run}}',
    "switch(1){ '1'{run}}", 'switch(10){\r\n10{run}}',
]

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SWITCH_LABEL_QUOTING_T0291A)
def test_owner_switch_label_quoting_spacing(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    reason = deny_floor.refusal(command, owner.parent, shell=shell)
    assert reason and "[owner-launcher]" in reason

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", SWITCH_LABEL_QUOTING_T0291A)
def test_owner_switch_label_quoting_spacing_identity(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert "[owner-launcher]" in deny_floor.refusal(command, other, shell=shell)
    owner.unlink()
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path, shell=shell)

@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("command", ["foo{10{run}", "a'1'{run}", "${1}{run}", "foo({run:1})", "foo{ '1'{run}"])
def test_owner_switch_label_quoting_spacing_data(tmp_path, monkeypatch, shell, command):
    owner = _gap_owner_t0291a(tmp_path, monkeypatch)
    assert deny_floor.refusal(command, owner.parent, shell=shell) is None


# ── jobs-file: a LiteTUI data root's jobs.json is the user's (T1085) ─────────
#
# Since T1082 a job's recorded tool_profile IS its authority, so a row written
# into jobs.json runs at that level in the user's own LiteTUI (Dijkstra f0ae21c1
# P1). Every string below is judged, never run.

@pytest.fixture
def roots(checkouts, monkeypatch):
    """The checkout (the default data root), a MARKED data root that is not a
    checkout (the user's LiteGUI root, Dijkstra 0fd0f2d0 T1), and a plain folder."""
    home, workspace, litetui, _ = checkouts
    marked = litetui.parent / "LORA_DATASET"
    marked.mkdir()
    (marked / ".litetui-data.json").write_text("{}", encoding="utf-8")
    plain = litetui.parent / "plain"
    plain.mkdir()
    monkeypatch.delenv("LITETUI_DATA_ROOT", raising=False)
    return home, workspace, litetui, marked, plain


JOBS_WRITES = [
    "echo [] > {dir}\\jobs.json", "echo x >> {dir}/jobs.json", "echo x 2>{dir}\\jobs.json",
    "Set-Content {dir}\\jobs.json '[]'", "'[]' | Out-File {dir}\\jobs.json",
    "Add-Content -Path {dir}\\jobs.json -Value x", "Get-Content x | tee {dir}\\jobs.json",
    "cp evil.json {dir}/jobs.json", "copy evil.json {dir}\\jobs.json",
    "Copy-Item x.json -Destination {dir}\\jobs.json",
    "Copy-Item x.json -Destination:{dir}\\jobs.json",
    "move {dir}\\jobs.json C:\\tmp\\x.json", "ren {dir}\\jobs.json old.json",
    "python -c \"open(r'{dir}\\jobs.json','w').write('[]')\"", "sed -i s/a/b/ {dir}\\jobs.json",
    "notepad {dir}\\jobs.json", "del {dir}\\jobs.json", "Remove-Item {dir}\\jobs.json",
    "cd /d {dir} && echo x > JOBS.JSON", "cd {dir}; Set-Content .\\jobs.json x",
    "echo [] > {marked}\\jobs.json",   # T1: a marked data root that is not a checkout
]
JOBS_READS = [
    "cat {dir}\\jobs.json", "type {dir}\\jobs.json",
    "Get-Content {dir}\\jobs.json | ConvertFrom-Json",
    "rg cron {dir}\\jobs.json", "copy {dir}\\jobs.json C:\\tmp\\b.json",
    "Test-Path {dir}\\jobs.json", "git -C {dir} status", "echo updated {dir}\\jobs.json",
    # another folder's jobs.json, and another file's name
    "echo [] > {plain}\\jobs.json", "echo [] > {dir}\\myjobs.json",
]


def _jobs(template, litetui, marked, plain):
    return template.format(dir=litetui, marked=marked, plain=plain)


@pytest.mark.parametrize("template", JOBS_WRITES)
def test_a_write_to_a_data_roots_jobs_json_is_refused(roots, template):
    home, workspace, litetui, marked, plain = roots
    command = _jobs(template, litetui, marked, plain)
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[jobs-file]" in reason, command
    assert "recorded level" in reason and "/cron" in reason


@pytest.mark.parametrize("template", JOBS_READS)
def test_reading_the_schedule_and_other_files_passes(roots, template):
    home, workspace, litetui, marked, plain = roots
    command = _jobs(template, litetui, marked, plain)
    assert deny_floor.refusal(command, workspace, home) is None, command


@pytest.mark.parametrize("command", ["echo [] > jobs.json", "tee ./jobs.json", "del JOBS.json"])
def test_relative_writes_are_refused_from_inside_the_checkout(roots, command):
    home, _, litetui, _, _ = roots
    reason = deny_floor.refusal(command, litetui, home)
    assert reason and "[jobs-file]" in reason, command


def test_the_env_data_root_is_protected_too(roots, monkeypatch):
    home, workspace, _, _, plain = roots
    command = f"echo [] > {plain}\\jobs.json"
    assert deny_floor.refusal(command, workspace, home) is None   # control: unmarked
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(plain))
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[jobs-file]" in reason


def test_jobs_false_leaves_the_rule_to_the_caller(roots, monkeypatch):
    """LiteTUI's own floor passes jobs=False: its seat decides (the user's own
    seat may write its schedule). The other rules are untouched by the switch."""
    home, workspace, litetui, _, _ = roots
    # Current owner-launcher contract is path identity, not checkout shape.
    command = f"echo [] > {litetui}\\jobs.json"
    assert deny_floor.refusal(command, workspace, home, jobs=False) is None
    launch = deny_floor.refusal(f"cmd /c {litetui}\\run.bat", workspace, home, jobs=False)
    assert launch and "[owner-launcher]" in launch


def _write(tool, path, cwd):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {key: str(path)}, "cwd": str(cwd)}


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_the_gate_refuses_a_file_tool_writing_jobs_json(roots, tool):
    _, workspace, litetui, marked, _ = roots
    for path in (litetui / "jobs.json", litetui / "JOBS.JSON", marked / "jobs.json"):
        out = deny_gate.decide(_write(tool, path, workspace))
        assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny", (tool, path)
        assert "[jobs-file]" in out["hookSpecificOutput"]["permissionDecisionReason"]
    relative = deny_gate.decide(_write(tool, "jobs.json", litetui))
    assert relative and "[jobs-file]" in relative["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_file_tool_meets_the_jobs_rule_only(roots):
    """BINDING (Dijkstra 0fd0f2d0 b): a Write never meets the delete or launcher
    rules. A run.bat, a harness folder, another jobs.json, another file: all pass."""
    home, workspace, litetui, _, plain = roots
    for path in (litetui / "run.bat", home / ".claude" / "settings.json",
                 plain / "jobs.json", litetui / "other.json"):
        assert deny_gate.decide(_write("Write", path, workspace)) is None, path


def test_a_file_tool_edit_not_named_jobs_json_never_loads_the_floor(roots, monkeypatch):
    """BINDING (Dijkstra 0fd0f2d0 a): the short-circuit comes BEFORE the floor is
    loaded, so an ordinary edit cannot reach the floor to crash in it."""
    _, workspace, litetui, _, _ = roots
    monkeypatch.setattr(deny_gate, "FLOOR", workspace / "no-such-floor.py")
    assert deny_gate.decide(_write("Edit", litetui / "other.json", workspace)) is None
    with pytest.raises(Exception):
        deny_gate.decide(_write("Edit", litetui / "jobs.json", workspace))


@pytest.fixture
def top_crashing_gate(tmp_path):
    """A gate whose decide() raises FIRST, on every payload: the crash path for a
    file tool, which a floor-level crash could not reach for a non-jobs path."""
    folder = tmp_path / "topcrash"
    folder.mkdir()
    source = GATE.read_text(encoding="utf-8")
    anchor = '    """The Claude Code decision for one hook payload, from the real floor."""\n'
    assert source.count(anchor) == 1
    (folder / "deny_gate.py").write_text(
        source.replace(anchor, anchor + "    raise RuntimeError('broken gate')\n"),
        encoding="utf-8")
    (folder / "deny_floor.py").write_bytes(GATE.with_name("deny_floor.py").read_bytes())
    return folder / "deny_gate.py"


def test_a_crashed_gate_blocks_only_a_jobs_json_file_write(tmp_path, top_crashing_gate):
    """Ruled by Dijkstra (0fd0f2d0): on a crash, a parsed file-tool write to a
    file named jobs.json is blocked (exit 2); every other edit passes (exit 0)."""
    env = _sandbox_env(tmp_path)
    jobs = _run_gate_payload(top_crashing_gate,
                             _write("Write", tmp_path / "x" / "jobs.json", tmp_path), env)
    assert jobs.returncode == 2, (jobs.returncode, jobs.stderr)
    assert "refusing a write to" in jobs.stderr
    other = _run_gate_payload(top_crashing_gate,
                              _write("Edit", tmp_path / "x" / "other.txt", tmp_path), env)
    assert other.returncode == 0, (other.returncode, other.stderr)


# ── A1 (Dijkstra 69c7c209): Windows aliases write the SAME jobs.json ────────
#
# MEASURED by Dijkstra with a real open() in a marker folder: `jobs.json::$DATA`,
# `jobs.json.` and "jobs.json " each overwrote jobs.json. A named stream
# (`jobs.json:x`) is folded in too: fail-safe. An 8.3 alias stays in the ceiling.

ALIASES = ["jobs.json::$DATA", "jobs.json::$data", "jobs.json.", "jobs.json ", "jobs.json:x"]


@pytest.mark.parametrize("alias", ALIASES)
def test_every_windows_alias_of_jobs_json_is_the_same_file(roots, alias):
    home, workspace, litetui, _, _ = roots
    target = f"{litetui}\\{alias}"
    shell = deny_floor.refusal(f'echo [] > "{target}"', workspace, home)
    assert shell and "[jobs-file]" in shell, ("shell", alias)
    assert "[jobs-file]" in (deny_floor.write_refusal(target, workspace) or ""), ("write_refusal", alias)
    out = deny_gate.decide(_write("Write", target, workspace))
    assert out and "[jobs-file]" in out["hookSpecificOutput"]["permissionDecisionReason"], ("gate", alias)


@pytest.mark.parametrize("name, canonical", [
    ("jobs.json::$DATA", "jobs.json"), ("JOBS.JSON:x", "jobs.json"), ("jobs.json. .", "jobs.json"),
    ("jobs.json.bak", "jobs.json.bak"), ("myjobs.json", "myjobs.json"),
    ("C:\\x\\jobs.json::$DATA", "jobs.json"), ("C:jobs.json", "jobs.json"),
])
def test_the_canonical_name_is_what_windows_opens(name, canonical):
    assert deny_floor.canonical_name(name) == canonical


def test_the_gate_prefilter_is_a_superset_of_the_canonical_name():
    """The gate may not load the floor before its short-circuit (binding a), nor
    rely on it when it crashes, so it pre-filters on the substring "jobs.json".
    That can never miss an alias: the canonical name is a PREFIX of the lowered
    last segment, so canonical == "jobs.json" implies the substring is there."""
    for name in [*ALIASES, "JOBS.JSON", "x\\jobs.json:stream"]:
        assert deny_floor.canonical_name(name) == "jobs.json"
        assert deny_gate._names_jobs_file(name), name
    assert not deny_gate._names_jobs_file("other.json")


@pytest.mark.parametrize("path", ["{dir}\\jobs.json.bak", "{dir}\\other.json",
                                  "{plain}\\jobs.json::$DATA"])
def test_alias_controls_pass(roots, path):
    home, workspace, litetui, marked, plain = roots
    target = _jobs(path, litetui, marked, plain)
    assert deny_floor.refusal(f"echo [] > {target}", workspace, home) is None, target
    assert deny_gate.decide(_write("Write", target, workspace)) is None, target


def test_a_crashed_gate_blocks_a_stream_alias_too(tmp_path, top_crashing_gate):
    env = _sandbox_env(tmp_path)
    proc = _run_gate_payload(top_crashing_gate,
                             _write("Write", f"{tmp_path}\\x\\jobs.json::$DATA", tmp_path), env)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)


# P1: git is not a reader merely because it is the command head. Classify only;
# these strings never execute git or alter a tracked schedule.
@pytest.mark.parametrize("verb", ["restore --", "checkout HEAD --", "rm --", "mv --", "clean -fx --", "-C {root} restore --", "-C {root} clean -fx --"])
def test_git_explicit_schedule_writers_are_refused(roots, verb):
    home, workspace, litetui, _, _ = roots
    verb = verb.format(root=litetui)
    target = "jobs.json" if verb.startswith("-C") else f"{litetui}/jobs.json"
    command = f"git {verb} {target} backup.json"
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[jobs-file]" in reason
    out = deny_gate.decide(_payload("Bash", command, workspace))
    assert out and "[jobs-file]" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("verb", ["show HEAD:", "diff -- ", "status -- ", "-C {root} diff -- ",
                                      "log -- ", "blame -- ", "ls-files -- "])
def test_git_schedule_reads_are_allowed(roots, verb):
    home, workspace, litetui, _, _ = roots
    verb = verb.format(root=litetui)
    target = "jobs.json" if verb.startswith("-C") else f"{litetui}/jobs.json"
    command = f"git {verb}{target}"
    assert deny_floor.refusal(command, workspace, home) is None
    assert deny_gate.decide(_payload("Bash", command, workspace)) is None


FINAL_GIT_WITNESSES = [
    "git --no-pager -C {root} clean -fx -- jobs.json",
    "git --no-pager -C {root} restore -- jobs.json",
    "git --git-dir={root}/.git --work-tree={root} clean -fx -- jobs.json",
    "git -c core.worktree={root} clean -fx -- jobs.json",
    "GIT_WORK_TREE={root} git clean -fx -- jobs.json",
    "git -C {root} clean -fx -- notes.md",  # accepted relocation friction
    "git diff --output={root}/jobs.json HEAD",
    "git log --output={root}/jobs.json",
    "git show HEAD:{root}/jobs.json > {root}/jobs.json",
    "git --no-pager -C {root} diff -- jobs.json > {root}/jobs.json",
    "git log -o {root}/jobs.json",
    "git log --output {root}/jobs.json",
    "git diff --output={root}/jobs.json -- jobs.json",
]

@pytest.mark.parametrize("template", FINAL_GIT_WITNESSES)
def test_final_git_guard_cross_root_witnesses(roots, monkeypatch, template):
    home, workspace, protected, _, plain = roots
    monkeypatch.chdir(plain)
    command = template.format(root=protected)
    assert "[jobs-file]" in (deny_floor.refusal(command, plain, home) or "")
    out = deny_gate.decide(_payload("Bash", command, plain))
    assert out and "[jobs-file]" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("template", ["git show HEAD:{root}/jobs.json",
                                      "git --no-pager -C {root} diff -- jobs.json"])
def test_git_cross_root_schedule_reads_are_allowed(roots, monkeypatch, template):
    home, _, protected, _, plain = roots
    monkeypatch.chdir(plain)
    command = template.format(root=protected)
    assert deny_floor.refusal(command, plain, home) is None
    assert deny_gate.decide(_payload("Bash", command, plain)) is None


@pytest.mark.parametrize("command", ["git status", "git diff -- notes.md", "git add notes.md",
                                      "git restore -- notes.md", "git show HEAD:notes.md"])
def test_normal_worktree_git_friction_controls(roots, command):
    home, _, _, _, plain = roots
    assert deny_floor.refusal(command, plain, home) is None
    assert deny_gate.decide(_payload("Bash", command, plain)) is None


GIT_ENV_NAMES = ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"]
GIT_ENV_ASSIGNMENTS = ["set {name}={root} &&", "export {name}={root};",
                       "$env:{name}='{root}';", "{name}={root}"]


@pytest.mark.parametrize("name", GIT_ENV_NAMES)
@pytest.mark.parametrize("assignment", GIT_ENV_ASSIGNMENTS)
@pytest.mark.parametrize("writer", ["git clean -fx -- jobs.json", "git add notes.md"])
def test_git_env_assignment_taints_submission(roots, name, assignment, writer):
    home, _, protected, _, plain = roots
    command = assignment.format(name=name, root=protected) + " " + writer
    assert "[jobs-file]" in (deny_floor.refusal(command, plain, home) or "")
    assert deny_gate.decide(_payload("Bash", command, plain)) is not None


@pytest.mark.parametrize("name", GIT_ENV_NAMES)
def test_git_env_inherited_protected_root_is_relocation(roots, monkeypatch, name):
    home, _, protected, _, plain = roots
    target = protected / (".git/index" if name == "GIT_INDEX_FILE" else ".git")
    monkeypatch.setenv(name, str(target))
    assert "[jobs-file]" in (deny_floor.refusal("git clean -fx -- jobs.json", plain, home) or "")
    assert deny_gate.decide(_payload("Bash", "git add notes.md", plain)) is not None
    assert deny_floor.refusal("git status", plain, home) is None
    monkeypatch.setenv(name, str(plain / ".git"))
    assert deny_floor.refusal("git add notes.md", plain, home) is None


@pytest.mark.parametrize("name", GIT_ENV_NAMES)
def test_git_env_submission_wide_taint_and_read_control(roots, name):
    home, _, protected, _, plain = roots
    assignment = f"set {name}={protected}"
    assert deny_floor.refusal(f"git add notes.md; {assignment}", plain, home) is not None
    assert deny_floor.refusal(f"{assignment}; git status", plain, home) is None


@pytest.mark.parametrize("verb", ["Set-Content", "Add-Content"])
def test_quoted_separator_writer_is_refused_by_shipped_gate(roots, verb):
    home, workspace, protected, _, _ = roots
    command = f'{verb} -Value "x;git status --porcelain" -Path {protected}/jobs.json'
    out = deny_gate.decide(_payload("Bash", command, workspace))
    assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny", "writer was allowed"
    assert deny_floor.jobs_write_target(command, workspace, home) is not None


GIT_PAYLOAD_WRITERS = [
    'Set-Content {root}/jobs.json "git status --porcelain"',
    'Add-Content {root}/jobs.json "git status --porcelain"',
    '"git status --porcelain" | Out-File {root}/jobs.json',
    'echo "git status --porcelain" > {root}/jobs.json',
    'echo "git status --porcelain" >> {root}/jobs.json',
    'echo "git status --porcelain" | tee {root}/jobs.json',
    'printf "%s" "git status --porcelain" > {root}/jobs.json',
]


@pytest.mark.parametrize("template", GIT_PAYLOAD_WRITERS)
def test_writer_payload_cannot_acquire_git_reader_exemption(roots, template):
    home, workspace, protected, _, _ = roots
    command = template.format(root=protected)
    assert "[jobs-file]" in (deny_floor.refusal(command, workspace, home) or "")
    assert deny_floor.jobs_write_target(command, workspace, home) is not None
    out = deny_gate.decide(_payload("Bash", command, workspace))
    assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny"


T0116_LITERAL_WRITERS = [
    'cd . > {root}/jobs.json',
    'echo x >& {root}/jobs.json',
    '(rm {root}/jobs.json)',
    'echo x > >(tee {root}/jobs.json)',
    'cat <(rm {root}/jobs.json)',
    "rm $'{root}/jobs.json'",
    'git -C{root} reset --hard',
    'bash -c "rm {root}/jobs.json"',
]

@pytest.mark.parametrize("template", T0116_LITERAL_WRITERS)
def test_t0116_literal_boundary_writers_shipped_gate(roots, template):
    _, workspace, protected, _, _ = roots
    command = template.format(root=protected.as_posix())
    out = deny_gate.decide(_payload("Bash", command, workspace))
    assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_t0116_jobs_line_continuation_is_not_a_new_path(roots):
    _, workspace, protected, _, _ = roots
    command = f"rm {protected.as_posix()}/jo" + "\\\n" + "bs.json"
    assert deny_gate.decide(_payload("Bash", command, workspace))


@pytest.mark.parametrize("command", [
    "Copy-Item -Destination backup.json -Path jobs.json",
    "Copy-Item -Path jobs.json -Destination backup.json",
])
def test_t0116_jobs_named_copy_source_remains_a_read(roots, command):
    home, _, protected, _, _ = roots
    assert deny_floor.jobs_write_target(command, protected, home, shell="powershell") is None
    assert deny_gate.decide(_payload("Bash", command, protected)) is None


def test_t0116_jobs_named_copy_destination_remains_protected(roots):
    home, _, protected, _, _ = roots
    command = "Copy-Item -Path backup.json -Destination jobs.json"
    assert deny_floor.jobs_write_target(command, protected, home, shell="powershell")
    assert deny_gate.decide(_payload("Bash", command, protected))


def test_t0116_jobs_depth_bound_never_erases_a_writer(roots):
    _, _, protected, _, _ = roots
    command = f"rm {protected.as_posix()}/jobs.json"
    # Exercise the bound directly: nesting shell-escaped quote strings grows
    # exponentially and tests resource use rather than the fail-closed branch.
    parts = deny_floor.shell_commands(command, "bash", _depth=16)
    assert parts and not parts[0]["complete"]
    assert parts[0]["words"][-1][0] == command


def test_t0116_jobs_small_nested_shell_shipped_gate(roots):
    import shlex
    _, workspace, protected, _, _ = roots
    command = f"rm {protected.as_posix()}/jobs.json"
    for _ in range(3):
        command = "bash -c " + shlex.quote(command)
    assert len(command) < 4096
    assert deny_gate.decide(_payload("Bash", command, workspace))


@pytest.mark.parametrize("head", ["/bin/bash", "env bash", "sudo bash"])
def test_t0116_review_heredoc_protected_writer_actual_hook(roots, head):
    home, workspace, protected, _, _ = roots
    command = head + " <<'EOF'\nrm " + protected.as_posix() + "/jobs.json\nEOF"
    out = deny_gate.decide(_payload("Bash", command, workspace))
    proven = deny_floor.refusal(command, workspace, home, shell="bash")
    print("RECEIPT HOOK", repr(command), repr(out), "PROVEN BASH", repr(proven))
    assert out, "actual hook protected writer allowed"


@pytest.mark.parametrize("depth", [3, 18])
def test_t0116_review2_jobs_linear_depth_actual_floor(roots, depth):
    home, workspace, protected, _, _ = roots
    command = "echo " + "$(" * depth + "rm " + protected.as_posix() + "/jobs.json" + ")" * depth
    assert len(command) < 4096
    out = deny_gate.decide(_payload("Bash", command, workspace))
    proven = deny_floor.refusal(command, workspace, home, shell="bash")
    print("REVIEW2 DEPTH", depth, repr(out), repr(proven))
    assert out and proven, "actual floor must preserve depth exhaustion writer"
