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

@pytest.mark.parametrize("shell", ["powershell", None])
@pytest.mark.parametrize("command, refused", [
    ("Get-ChildItem . | Where-Object Name -Match 'a|run'", False),
    ('Get-ChildItem . | Where-Object Name -Match "a|run"', False),
    ("Get-ChildItem . | Where-Object Name -Match 'receipt|run|build'", False),
    ("run.bat", True),
    ("& 'run.bat'", True),
    (". 'run.bat'", True),
    ('& "run"', True),
    ('"./run.bat"', True),
    ("bash -c 'run'", True),
    ('cmd /c "run.bat"', True),
    ("Get-ChildItem . | Where-Object Name -Match 'a|run'; run.bat", True),
    ("Get-ChildItem . | Where-Object Name -Match 'a' | run", True),
    ('Get-ChildItem . | Where-Object Name -Match "a|$(run)"', True),
    ("bash -c 'echo a|run'", True),
    ('cmd /c "echo a|run"', True),
    ("'x' | & 'run'", True),
    ("'run'", True),
    ("Write-Output 'receipt|run|build'", False),
    ('Select-Object -Property "receipt|run|build"', False),
    ("Select-Object -Property 'receipt|run|build'", False),
    ("Invoke-Expression 'echo a|run'", True),
    ("& 'cmd' /c 'echo a|run'", True),
    ("Get-ChildItem . | Where-Object Name -Match 'a|run", True),
    ("helper.cmd 'a|run|build'", True),
    ("foo.exe 'a|run'", True),
])
def test_quoted_match_pattern_is_data_not_a_launcher(tmp_path, command, refused, shell):
    """T0710: only proven PowerShell can treat apostrophes as literal quotes."""
    # Unknown shell retains CMD's interpretation: apostrophes do not quote pipes.
    refused = refused or shell is None
    reason = deny_floor.refusal(command, tmp_path, shell=shell)
    assert bool(reason) is refused, reason
    if refused:
        assert "[owner-launcher]" in reason


@pytest.mark.parametrize("extensions, refused", [
    ([], True),
    ([".exe"], False),
    ([".cmd"], True),
    ([".exe", ".cmd"], True),
    ([".exe", ".ps1"], True),
])
def test_plain_quoted_pipe_requires_native_argv_proof(tmp_path, monkeypatch, extensions, refused):
    """Inert PATH fixtures prove forwarding, never execute candidate commands."""
    monkeypatch.setattr(deny_floor, "_is_windows", lambda: True)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;.BAT")
    for extension in extensions:
        (tmp_path / ("probe" + extension)).write_text("", encoding="utf-8")
    command = "probe 'a|run|build'"
    reason = deny_floor.refusal(command, tmp_path, shell="powershell")
    assert bool(reason) is refused, reason
    assert "[owner-launcher]" in deny_floor.refusal(command, tmp_path, shell=None)


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


#: The checkout whose commits the installed record names (a seam for the nested
#: runs in test_install_deny_gate.py; the default is the checkout under test).
REPO = Path(os.environ.get("LITEHARNESS_DENY_GATE_REPO") or GATE.parents[1])


def _git_bytes(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(REPO), *args], env=env, capture_output=True)


@pytest.mark.parametrize("name", ["deny_gate.py", "deny_floor.py"])
def test_the_installed_gate_is_the_recorded_commit(name):
    """T0091: compare the installed file with the COMMIT the installer recorded for
    it (INSTALLED.json, `git show <sha>:liteharness/<name>`), never the working
    tree: a mid-edit checkout is not what is live. Drift fails a TEST, not a
    seat. Not installed, or no record, is an XFAIL that names what to create
    (loud in -rx), never a skip: this suite also runs where no Claude Code is
    installed, and a hard failure there would be noise. Whether the recorded sha
    is an ancestor of this checkout's HEAD is reported as a warning, not a failure:
    a gate can be live from a branch this checkout has not merged."""
    installed = INSTALLED / name
    record_file = INSTALLED / "INSTALLED.json"
    if not installed.exists():
        pytest.xfail(f"deny gate not installed: {installed} is missing "
                     "(scripts/install_deny_gate.py --ref <sha> installs it)")
    if not record_file.exists():
        pytest.xfail(f"no install record: create {record_file} with "
                     "scripts/install_deny_gate.py --record-only --ref-gate <sha> --ref-floor <sha>")
    entry = json.loads(record_file.read_text(encoding="utf-8")).get(name)
    if entry is None:
        pytest.xfail(f"{record_file} has no entry for {name}: record it with "
                     "scripts/install_deny_gate.py --record-only")
    sha = entry["sha"]
    shown = _git_bytes("show", f"{sha}:liteharness/{name}")
    if shown.returncode != 0:
        pytest.xfail(f"{record_file} names {sha} for {name}, which {REPO} does not have")
    print(f"{name}: installed from {sha} (recorded {entry.get('installed_at')})")
    assert installed.read_bytes() == shown.stdout, (
        f"{installed} differs from {sha}:liteharness/{name}, the commit {record_file} "
        "says it was installed from: reinstall with scripts/install_deny_gate.py --ref")
    if _git_bytes("merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
        warnings.warn(f"{name}: recorded commit {sha} is not an ancestor of HEAD in {REPO}; "
                      "the live gate comes from a commit this checkout does not contain",
                      UserWarning, stacklevel=1)


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
    # Read-only on the real repo: install the commit the checkout is on, with an
    # explicit ref. The dirty-tree refusal covers the two gate files only.
    head = _git_bytes("rev-parse", "HEAD").stdout.decode().strip()
    try:
        entry = install_deny_gate.install(tmp_path / "deny-floor", ref=head, repo=REPO)
    except SystemExit as refused:
        pytest.skip(f"gate files have uncommitted edits in this checkout: {refused}")
    for name in ("deny_gate.py", "deny_floor.py"):
        assert (tmp_path / "deny-floor" / name).read_bytes() == _git_bytes(
            "show", f"{head}:liteharness/{name}").stdout
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


# ── the recognizer's `name` keyword (T0306): the same questions about another file ──
#
# A caller may ask about another file in a data root. Each question is asked
# under the default name and under another, beside each other: the answer must
# follow the name asked about, and never the other one.

SCHEDULE, OTHER_NAME = "jobs.json", "settings.json"
NAMED_SHELL = [   # (command, is it a write)
    ("echo x > {dir}/NAME", True),                                  # a redirect
    ("rm {dir}/NAME", True),                                        # a delete
    ("python -c \"open('{dir}/NAME','w').write('x')\"", True),      # code handed to an interpreter
    ("cat {dir}/NAME", False),                                      # a reader
]
NAMED_GIT = [
    ("git restore -- {dir}/NAME", True),
    ("git diff -- {dir}/NAME", False),
]


def _other(name):
    return OTHER_NAME if name == SCHEDULE else SCHEDULE


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
@pytest.mark.parametrize("template, writes", NAMED_SHELL)
def test_the_name_keyword_asks_the_shell_question_of_that_file(roots, template, writes, name):
    home, workspace, litetui, _, _ = roots
    command = template.format(dir=litetui.as_posix()).replace("NAME", name)
    target = deny_floor.jobs_write_target(command, workspace, home, name=name)
    assert (target is not None) is writes, command
    if writes:
        assert target == litetui / name
    assert deny_floor.jobs_write_target(command, workspace, home, name=_other(name)) is None, command


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
@pytest.mark.parametrize("template, writes", NAMED_GIT)
def test_the_name_keyword_asks_the_git_question_of_that_file(roots, template, writes, name):
    home, workspace, litetui, _, _ = roots
    command = template.format(dir=litetui.as_posix()).replace("NAME", name)
    assert bool(deny_floor.jobs_git_refusal(command, workspace, home, name=name)) is writes, command
    assert deny_floor.jobs_git_refusal(command, workspace, home, name=_other(name)) is None, command


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
def test_the_name_keyword_asks_the_path_questions_of_that_file(roots, name):
    home, workspace, litetui, marked, plain = roots
    for root in (litetui, marked):
        assert deny_floor.is_jobs_file(root / name, name)
        assert not deny_floor.is_jobs_file(root / name, _other(name))
        assert deny_floor.write_refusal(str(root / name), workspace, home, name=name)
        assert deny_floor.write_refusal(str(root / name), workspace, home, name=_other(name)) is None
    assert not deny_floor.is_jobs_file(plain / name, name)
    assert deny_floor.write_refusal(str(plain / name), workspace, home, name=name) is None


def test_the_default_name_is_still_the_schedule_file_and_refusal_asks_about_no_other(roots):
    home, workspace, litetui, _, _ = roots
    schedule = f"echo x > {litetui.as_posix()}/{SCHEDULE}"
    assert deny_floor.jobs_write_target(schedule, workspace, home) == litetui / SCHEDULE
    assert deny_floor.is_jobs_file(litetui / SCHEDULE) and not deny_floor.is_jobs_file(litetui / OTHER_NAME)
    assert "[jobs-file]" in deny_floor.refusal(schedule, workspace, home)
    assert deny_floor.refusal(f"echo x > {litetui.as_posix()}/{OTHER_NAME}", workspace, home) is None


# ── the recognizer's `under` keyword (T0306-R): a file at any depth below one
#    named folder of a data root. Without it the file must sit IN the data root. ──

UNDER = ".agents"
DEPTHS = ["{under}/NAME", "{under}/Seat/NAME", "{under}/Seat/conversations/c1/NAME"]
UNDER_SHELL = [   # (command, is it a write)
    ("echo x > {file}", True),                                     # a redirect
    ("rm {file}", True),                                           # a delete
    ("cp other.json {file}", True),                                # a copy onto it
    ("python -c \"open('{file}','w').write('x')\"", True),         # code handed to an interpreter
    ("cat {file}", False),                                         # a reader
    ("cp {file} other.json", False),                               # a copy from it
]


def _below(root, depth, name=OTHER_NAME, under=UNDER):
    return root / depth.format(under=under).replace("NAME", name)


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
def test_under_asks_the_path_questions_at_any_depth_below_the_folder(roots, depth, name):
    home, workspace, litetui, marked, plain = roots
    for root in (litetui, marked):
        path = _below(root, depth, name)
        assert deny_floor.is_jobs_file(path, name, UNDER), path
        assert not deny_floor.is_jobs_file(path, name), "without `under` the file must sit in the data root"
        assert not deny_floor.is_jobs_file(path, _other(name), UNDER)
        assert deny_floor.write_refusal(str(path), workspace, home, name=name, under=UNDER)
        assert deny_floor.write_refusal(str(path), workspace, home, name=name) is None
    assert not deny_floor.is_jobs_file(_below(plain, depth, name), name, UNDER), "not a data root"
    assert deny_floor.write_refusal(str(_below(plain, depth, name)), workspace, home, name=name, under=UNDER) is None


def test_under_does_not_ask_about_the_file_in_the_data_root_itself(roots):
    home, workspace, litetui, _, _ = roots
    assert deny_floor.is_jobs_file(litetui / OTHER_NAME, OTHER_NAME)
    assert not deny_floor.is_jobs_file(litetui / OTHER_NAME, OTHER_NAME, UNDER)


@pytest.mark.parametrize("place", ["agents/Seat", ".agents2/Seat", "x.agents/Seat",
                                   "sub/.agents/Seat", ".agents.d/Seat"])
def test_under_means_that_folder_directly_inside_the_data_root(roots, place):
    """Another folder's name, or the right name one level too deep, is not it."""
    home, workspace, litetui, _, _ = roots
    path = litetui / place / OTHER_NAME
    assert not deny_floor.is_jobs_file(path, OTHER_NAME, UNDER), path
    assert deny_floor.jobs_write_target(f"echo x > {path.as_posix()}", workspace, home,
                                        name=OTHER_NAME, under=UNDER) is None


@pytest.mark.parametrize("spelling", [".AGENTS", ".Agents", ".agents.", ".agents ", ".agents..",
                                      ".agents::$INDEX_ALLOCATION", ".agents:$I30:$INDEX_ALLOCATION"])
def test_under_compares_the_folders_name_as_a_files_name_is_compared(roots, spelling):
    """Case, trailing dots and spaces and a stream suffix name the same folder
    (canonical_name). The path is judged as spelled: no folder exists on disk
    here, so nothing but the comparison can make these equal."""
    home, workspace, litetui, _, _ = roots
    assert deny_floor.is_jobs_file(litetui / spelling / "Seat" / OTHER_NAME, OTHER_NAME, UNDER)
    assert deny_floor.is_jobs_file(litetui / UNDER / "Seat" / OTHER_NAME, OTHER_NAME, spelling), \
        "the caller's spelling of `under` is folded too"


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("template, writes", UNDER_SHELL)
def test_under_asks_the_shell_question_below_the_folder(roots, template, writes, depth):
    home, workspace, litetui, _, plain = roots
    command = template.format(file=_below(litetui, depth).as_posix())
    target = deny_floor.jobs_write_target(command, workspace, home, name=OTHER_NAME, under=UNDER)
    assert (target is not None) is writes, command
    if writes:
        assert target == _below(litetui, depth)
    assert deny_floor.jobs_write_target(command, workspace, home, name=OTHER_NAME) is None, \
        "without `under` a file below the folder is not the data root's own"
    assert deny_floor.jobs_write_target(command, workspace, home, name=SCHEDULE, under=UNDER) is None
    elsewhere = template.format(file=_below(plain, depth).as_posix())
    assert deny_floor.jobs_write_target(elsewhere, workspace, home, name=OTHER_NAME, under=UNDER) is None


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("template, writes", NAMED_GIT)
def test_under_asks_the_git_question_below_the_folder(roots, template, writes, depth):
    home, workspace, litetui, _, plain = roots
    command = template.format(dir=_below(litetui, depth).parent.as_posix()).replace("NAME", OTHER_NAME)
    assert bool(deny_floor.jobs_git_refusal(command, workspace, home, name=OTHER_NAME, under=UNDER)) is writes, command
    assert deny_floor.jobs_git_refusal(command, workspace, home, name=OTHER_NAME) is None, command
    elsewhere = template.format(dir=_below(plain, depth).parent.as_posix()).replace("NAME", OTHER_NAME)
    assert deny_floor.jobs_git_refusal(elsewhere, workspace, home, name=OTHER_NAME, under=UNDER) is None


def test_under_knows_a_data_root_by_the_environment_too(roots, monkeypatch):
    home, workspace, _, _, plain = roots
    path = _below(plain, DEPTHS[1])
    assert not deny_floor.is_jobs_file(path, OTHER_NAME, UNDER)   # control: an unmarked folder
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(plain))
    assert deny_floor.is_jobs_file(path, OTHER_NAME, UNDER)
    assert deny_floor.jobs_write_target(f"rm {path.as_posix()}", workspace, home,
                                        name=OTHER_NAME, under=UNDER) == path


def test_under_follows_the_folders_short_name_when_the_folder_is_on_disk(roots):
    """A volume may give the folder a second, short name (`AGENTS~1`). That
    word is not the folder's name as spelled, so the bare path question says
    no; the tool and shell questions resolve the path on disk first and are
    answered for the folder it really is. Skipped where the volume gives none."""
    if os.name != "nt":
        pytest.skip("short names are a Windows volume feature")
    import ctypes
    home, workspace, litetui, _, _ = roots
    path = _below(litetui, DEPTHS[1])
    path.parent.mkdir(parents=True)
    buffer = ctypes.create_unicode_buffer(600)
    ctypes.windll.kernel32.GetShortPathNameW(str(litetui / UNDER), buffer, 600)
    short = Path(buffer.value).name
    if not short or short.lower() == UNDER:
        pytest.skip("this volume gives the folder no short name")
    spelled = (litetui / short / "Seat" / OTHER_NAME).as_posix()
    assert not deny_floor.is_jobs_file(Path(spelled), OTHER_NAME, UNDER), "as spelled, another folder"
    assert deny_floor.write_refusal(spelled, workspace, home, name=OTHER_NAME, under=UNDER)
    assert deny_floor.jobs_write_target(f"echo x > {spelled}", workspace, home,
                                        name=OTHER_NAME, under=UNDER) == path


def _asked_every_way(path, workspace, home):
    """The path question, the tool question, a redirect and a delete, each with `under`."""
    return [bool(deny_floor.is_jobs_file(path, OTHER_NAME, UNDER)),
            bool(deny_floor.write_refusal(str(path), workspace, home, name=OTHER_NAME, under=UNDER)),
            deny_floor.jobs_write_target(f"echo x > {path.as_posix()}", workspace, home,
                                         name=OTHER_NAME, under=UNDER) == path,
            deny_floor.jobs_write_target(f"rm {path.as_posix()}", workspace, home,
                                         name=OTHER_NAME, under=UNDER) == path]


@pytest.mark.parametrize("depth", ["{under}/Seat/conversations/c1/attachments/NAME",
                                   "{under}/Seat/conversations/c1/a/b/c/NAME"])
def test_under_reaches_deeper_than_the_deepest_file_that_exists_today(roots, depth):
    """ANY depth: five and seven folders below the data root. A walk that
    stopped four folders up would still catch a conversation's own file, the
    deepest one any other test here names, so only these rows hold "any"."""
    home, workspace, litetui, marked, plain = roots
    for root in (litetui, marked):
        assert _asked_every_way(_below(root, depth), workspace, home) == [True] * 4, root
    assert _asked_every_way(_below(plain, depth), workspace, home) == [False] * 4, "not a data root"


def test_under_with_two_folders_of_that_name_in_one_path(roots):
    """EVERY folder of that name on the path is tried, not the nearest alone and
    not the outermost alone. A second one inside an agent's home: the outer one
    is the data root's. A data root kept below another folder of that name: the
    inner one is the data root's."""
    home, workspace, litetui, marked, plain = roots
    inside_a_home = litetui / UNDER / "Seat" / UNDER / "x" / OTHER_NAME
    kept = plain / UNDER / "kept"
    kept.mkdir(parents=True)
    (kept / ".litetui-data.json").write_text("{}", encoding="utf-8")
    root_below_another = kept / UNDER / "Seat" / OTHER_NAME
    for path in (inside_a_home, root_below_another):
        assert _asked_every_way(path, workspace, home) == [True] * 4, path
    # CONTROL: two folders of that name and no data root above either.
    neither = plain / UNDER / "other" / UNDER / "Seat" / OTHER_NAME
    assert _asked_every_way(neither, workspace, home) == [False] * 4


# T0105: a reader excuses the launcher path only in a READ position. The six shapes
# MEASURED allowed at oss 5365282 each hand the path to a reader's own exec feature.
# The layout is the `checkouts` fixture: run.bat there is `@echo off`, never the real file.
READER_EXEC_SHAPES_T0105 = [
    'git -c alias.x="!{bat}" x',          # git alias: `!` runs the value
    "git -c core.pager={bat} log",        # git pager
    "git rebase -x {bat}",                # git rebase --exec
    "sed '1e {bat}'",                     # sed `e` command
    "sed 1e\\ {fwd}",                     # ...unquoted, the space escaped
    'vim -c "!{bat}"',                    # vim ex `:!`
    "less +!{bat}",                       # less `!` shell escape as the +cmd
    # The same family: an option VALUE or a subcommand that runs its operand.
    "git -c core.editor={fwd} commit", "git --upload-pack={fwd} fetch",
    "git rebase --exec {bat}", "git bisect run {bat}", "git submodule foreach {bat}",
    "vim -S {bat}", "nano -s {fwd} x", "rg --pre {bat} pattern x",
]
READER_READ_SHAPES_T0105 = [
    "cat {bat}", "type {bat}", "Get-Content {bat}", "Get-Content -Raw {bat}", "gc '{bat}'",
    "git log -- {bat}", "git diff -- {bat}", "git add {bat}", "git show HEAD:{rel}",
    "git -C {dir} log -- run.bat", "git blame -- {fwd}",
    "rg pattern {bat}", "rg -n LITETUI_OWNER {bat}", "grep -n x {bat}", "head -n 5 {bat}",
    "less {bat}", "vim {bat}", "bat {bat}", "sed -n p {bat}", "echo {bat}",
    "Write-Host 'starting {bat}'", "cmd /c type {bat}",
]


def _read_spell(template: str, litetui: Path) -> str:
    bat = litetui / "run.bat"
    return template.format(bat=bat, fwd=bat.as_posix(), dir=litetui,
                           rel=f"{litetui.name}/run.bat")


@pytest.mark.parametrize("template", READER_EXEC_SHAPES_T0105)
def test_a_reader_does_not_excuse_the_launcher_in_an_exec_position(checkouts, template):
    home, workspace, litetui, _ = checkouts
    command = _read_spell(template, litetui)
    reason = deny_floor.refusal(command, workspace, home)
    assert reason and "[owner-launcher]" in reason, command


@pytest.mark.parametrize("template", READER_READ_SHAPES_T0105)
def test_a_reader_still_excuses_the_launcher_in_a_read_position(checkouts, template):
    home, workspace, litetui, _ = checkouts
    command = _read_spell(template, litetui)
    assert deny_floor.refusal(command, workspace, home) is None, command


def test_git_dash_C_with_a_spaced_quoted_directory_is_still_a_read(checkouts, tmp_path):
    """The verb is found with the quoted directory kept as ONE word (T0105)."""
    home, workspace, _, _ = checkouts
    spaced = tmp_path / "Program Files" / "LiteTUI"
    (spaced / "src" / "litetui").mkdir(parents=True)
    (spaced / "run.bat").write_text("@echo off\n", encoding="utf-8")
    assert deny_floor.refusal(f'git -C "{spaced}" log -- run.bat', workspace, home) is None
    reason = deny_floor.refusal("git -c core.pager=run.bat log", spaced, home)
    assert reason and "[owner-launcher]" in reason


# T0105 round 2: a wrapper head that RUNS its argument puts the wrapped command's first
# word in command position, so a launcher there is a launch. Every shape below was
# MEASURED allowed on 9b690be; run.bat in the `checkouts` layout is `@echo off`.
WRAPPER_LAUNCHES_T0105 = [
    # xargs, find -exec family
    "xargs <bat>", "xargs -n1 <fwd>", "echo x | xargs <bat>", "xargs -I{} <bat> {}",
    "xargs -0 -- <bat>", "xargs sh -c '<fwd>'", "xargs bash -c <fwd>",
    "find . -exec <bat> ;", "find . -exec <fwd> {} +", "find . -execdir <bat> ;",
    "find . -ok <bat> ;", "find . -okdir <bat> ;", "find . -name x -exec cmd /c <bat> ;",
    "find . -exec sh -c '<fwd>' ;", "find . -exec cat {} \\; -exec <bat> \\;",
    # priority / environment / limit wrappers
    "nice <bat>", "nice -n 5 <fwd>", "nice sh -c <fwd>", "ionice -c3 <fwd>", "setsid <fwd>",
    "stdbuf -oL <fwd>", "env -u X <fwd>", "env -S <fwd>", "timeout -s KILL 5 <fwd>",
    "timeout -k 1 5 <fwd>", "exec -a x <fwd>", "taskset 1 <fwd>", "chrt 1 <fwd>",
    "cpulimit -l 50 <fwd>", "flock /tmp/x <fwd>", "strace <fwd>", "ltrace <fwd>",
    # privilege / supervision / terminal wrappers
    "sudo <fwd>", "doas <fwd>", "watch <fwd>", "watch -n 1 <fwd>", "chronic <fwd>",
    "unbuffer <fwd>", "script -c <fwd> /dev/null", "parallel <fwd>", "parallel <fwd> ::: a b",
    # shells and evaluators
    "eval <fwd>", "eval '<fwd>'", "zsh -c '<fwd>'", "source <fwd>", ". <fwd>", "busybox sh <fwd>",
    # Windows
    "start /b <bat>", "start /wait <bat>", "start \"title\" <bat>", "powershell -File <bat>",
    "pwsh -File <bat>", "powershell -ExecutionPolicy Bypass -File <bat>",
    "Invoke-Expression '<bat>'", "iex '<bat>'", ". '<bat>'", "Start-Job <bat>",
    "wmic process call create <bat>",
    "Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList <bat>",
    "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='<bat>'}",
    "schtasks /create /tn x /tr <bat> /sc once /st 00:00", "at 00:00 <bat>",
    "runas /user:x <bat>", "conhost <bat>", "wt <bat>", "wt.exe <bat>", "explorer <bat>",
    "rundll32 <bat>", "mshta <bat>",
]
#: The same family spelled relative, judged from inside the checkout.
WRAPPER_LAUNCHES_RELATIVE_T0105 = [
    "xargs ./run.bat", "xargs run.bat", "xargs run", "find . -exec ./run.bat ;",
    "find . -exec run.bat {} ;", "find . -exec run ;", "nice run.bat", "nice run",
    "env -u X run", "timeout -s KILL 5 ./run.bat",
]
#: Wrappers whose wrapped command is something else, or that only look at the name.
WRAPPER_CONTROLS_T0105 = [
    "find . -name run.bat", "find . -name run.bat -print", "find <dir> -name run.bat",
    "find . -exec cat {} ;", "find . -exec grep -l x {} +", "find . -exec cat <fwd> ;",
    "find . -exec git log -- <fwd> ;", "find . -exec npm run x ;",
    "xargs cat <fwd>", "xargs grep foo <bat>", "xargs git log -- <bat>", "echo <bat> | xargs cat",
    "xargs -n1 uv run x", "nice -n 5 cat <bat>", "nice -n 10 uv run pytest",
    "timeout -s KILL 5 cat <bat>", "timeout 5 uv run pytest", "env -u X uv run pytest",
    "env FOO=1 python x.py", "sudo uv run pytest", "time uv run pytest", "command -v run.bat",
    "powershell -Command Get-Date", "Start-Process notepad", "Invoke-Expression 'Get-Date'",
    "cmd /c echo run.bat", "type <bat> | more", "bun run dev",
]


def _wrap_spell(template: str, litetui: Path) -> str:
    bat = litetui / "run.bat"
    return (template.replace("<bat>", str(bat)).replace("<fwd>", bat.as_posix())
            .replace("<dir>", str(litetui)))


@pytest.mark.parametrize("template", WRAPPER_LAUNCHES_T0105)
@pytest.mark.parametrize("shell", [None, "powershell"])
def test_a_wrapper_that_runs_its_argument_does_not_hide_the_launcher(checkouts, template, shell):
    home, workspace, litetui, _ = checkouts
    command = _wrap_spell(template, litetui)
    reason = deny_floor.refusal(command, workspace, home, shell=shell)
    assert reason and "[owner-launcher]" in reason, command


@pytest.mark.parametrize("command", WRAPPER_LAUNCHES_RELATIVE_T0105)
def test_a_wrapper_does_not_hide_a_relative_launcher_inside_the_checkout(checkouts, command):
    home, _, litetui, _ = checkouts
    reason = deny_floor.refusal(command, litetui, home)
    assert reason and "[owner-launcher]" in reason, command


@pytest.mark.parametrize("template", WRAPPER_CONTROLS_T0105)
def test_a_wrapper_around_something_else_still_passes(checkouts, template):
    home, _, litetui, _ = checkouts
    command = _wrap_spell(template, litetui)
    assert deny_floor.refusal(command, litetui, home) is None, command

# ── the recognizer's `more` keyword (T0306-S): recognitions a caller ADDS ──
#
# Each is named in MORE and is OFF unless the caller names it. Every row is
# asked four ways: the default (no), its own class alone (yes), every OTHER class
# together (no), and MORE (yes). So the default does not see it, the class's own
# name is what turns it on, and no other class turns it on by accident.

def _more_asked(command, cwd, home, name, under=None, more=frozenset()):
    return (deny_floor.jobs_git_refusal(command, cwd, home, name=name, under=under, more=more)
            or deny_floor.jobs_write_target(command, cwd, home, name=name, under=under, more=more))


def _four_ways(command, cwd, home, cls, name, under=None):
    return [bool(_more_asked(command, cwd, home, name, under, more))
            for more in (frozenset(), frozenset({cls}), deny_floor.MORE - {cls}, deny_floor.MORE)]


MORE_FORMS = [   # (class, command): {root} a data root, {src} a plain folder, NAME the file
    # A: a copy, move or link of a same-named file INTO the folder
    (deny_floor.COPY_INTO, "cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "cp {src}/NAME {root}"),
    (deny_floor.COPY_INTO, "mv {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "cp -t {root} {src}/NAME"),
    (deny_floor.COPY_INTO, "cp --target-directory={root} {src}/NAME"),
    (deny_floor.COPY_INTO, "Copy-Item {src}/NAME -Destination {root}"),
    (deny_floor.COPY_INTO, "Copy-Item -Path {src}/NAME -Destination:{root}"),
    (deny_floor.COPY_INTO, "Copy-Item {src}/NAME -Dest {root}"),
    (deny_floor.COPY_INTO, "Copy-Item -Dest {root} {src}/NAME"),       # the abbreviation, not last
    (deny_floor.COPY_INTO, "Copy-Item -Path:{src}/NAME -Destination {root}"),
    (deny_floor.COPY_INTO, "Move-Item {src}/NAME {root}"),
    (deny_floor.COPY_INTO, "mi {src}/NAME {root}"),
    (deny_floor.COPY_INTO, "copy {src}/NAME {root} /Y"),
    (deny_floor.COPY_INTO, "xcopy {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "cd {root} && cp {src}/NAME ."),
    (deny_floor.COPY_INTO, "cp {src}/*.json {root}/"),
    (deny_floor.COPY_INTO, "cp {src}/* {root}/"),
    (deny_floor.COPY_INTO, "ln -s {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "cd {root} && ln -s {src}/NAME"),
    (deny_floor.COPY_INTO, "install -m 644 {src}/NAME {root}/"),
    # fix 1: GNU getopt's grammar, not a list of spellings
    (deny_floor.COPY_INTO, "cp --target={root} {src}/NAME"),         # a prefix of --target-directory
    (deny_floor.COPY_INTO, "cp --target {root} {src}/NAME"),
    (deny_floor.COPY_INTO, "cp --t={root} {src}/NAME"),
    (deny_floor.COPY_INTO, "mv --targ={root} {src}/NAME"),
    (deny_floor.COPY_INTO, "cp -t{root} {src}/NAME"),                # attached
    (deny_floor.COPY_INTO, "cp -vt{root} {src}/NAME"),               # in a cluster
    (deny_floor.COPY_INTO, "cp -vt {root} {src}/NAME"),
    (deny_floor.COPY_INTO, "ln -st {root} {src}/NAME"),
    (deny_floor.COPY_INTO, "install -t {root} -m 644 {src}/NAME"),
    (deny_floor.COPY_INTO, "cp {src}/NAME -t {root}"),               # an option after the operands
    (deny_floor.COPY_INTO, "cp -- {src}/NAME {root}/"),
    # fix 1: a wrapper head in front
    (deny_floor.COPY_INTO, "env cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "env -i A=1 cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "env -C {root} cp {src}/NAME ."),
    (deny_floor.COPY_INTO, "env -C{root} cp {src}/NAME ."),                # attached
    (deny_floor.COPY_INTO, "command cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "exec cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "nice -n 5 cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "timeout 20 cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "timeout -s KILL 20s cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "nohup cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "stdbuf -o0 cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "sudo -u someone cp {src}/NAME {root}/"),
    (deny_floor.COPY_INTO, "sudo -D {root} cp {src}/NAME ."),
    (deny_floor.COPY_INTO, "timeout 20 env cp {src}/NAME {root}/"),
    # fix 1: a PowerShell comma array, the name first
    (deny_floor.COPY_INTO, "Copy-Item {src}/NAME,{src}/x.json {root}"),
    (deny_floor.COPY_INTO, "cp -Destination {root} {src}/NAME"),            # PowerShell's cp alias
    (deny_floor.COPY_INTO, "Copy-Item -Path {src}/x.json,{src}/NAME -Destination {root}"),
    # G: a stream suffix on a FOLDER of the path word
    (deny_floor.DOLLAR_PATH, 'echo x > "{root}::$INDEX_ALLOCATION/NAME"'),
    (deny_floor.DOLLAR_PATH, 'rm "{root}:$I30:$INDEX_ALLOCATION/NAME"'),
    (deny_floor.DOLLAR_PATH, 'Set-Content "{root}::$INDEX_ALLOCATION/NAME" x'),
    (deny_floor.DOLLAR_PATH, 'cd "{root}::$INDEX_ALLOCATION" && rm NAME'),
    # J: a `key=value` word with no dash
    (deny_floor.KEY_VALUE, "dd if=/dev/null of={root}/NAME"),
    (deny_floor.KEY_VALUE, "dd of={root}/NAME conv=notrunc"),
    (deny_floor.KEY_VALUE, "somewriter out={root}/NAME"),
    # K: a linking copy of the file (a second name that is written through later)
    (deny_floor.LINK_COPY, "cp -l {root}/NAME {src}/alias"),
    (deny_floor.LINK_COPY, "cp --link {root}/NAME {src}/alias"),
    (deny_floor.LINK_COPY, "cp -fl {root}/NAME {src}/alias"),
    (deny_floor.LINK_COPY, "cp --li {root}/NAME {src}/alias"),        # fix 1: prefixes of --link
    (deny_floor.LINK_COPY, "cp --lin {root}/NAME {src}/alias"),    # E: the path inside a .NET call is its segment's head word
    (deny_floor.CALL_PATH, "[IO.File]::WriteAllText('{root}/NAME','x')"),
    (deny_floor.CALL_PATH, '[System.IO.File]::Delete("{root}/NAME")'),
    (deny_floor.CALL_PATH, "[IO.File]::WriteAllText( '{root}/NAME', 'x')"),
    (deny_floor.CALL_PATH, "$null = [IO.File]::WriteAllText('{root}/NAME','x')"),
    (deny_floor.CALL_PATH, "[IO.File]::Copy('evil.json','{root}/NAME',$true)"),
    (deny_floor.CALL_PATH, "[IO.File]::ReadAllText('{root}/NAME')"),   # over-block, stated
]
MORE_CONTROLS = [   # no class and no combination refuses these
    "cp {src}/other.json {root}/", "cp {src}/NAME.bak {root}/", "cp {src}/NAME {src}/sub/",
    "cp {src}/NAME {root}/renamed.json", "cp {root}/NAME {src}/", "mv {root}/other.json {src}/",
    "robocopy {src} {root} /XF NAME", "robocopy {src} {root}",
    "robocopy {src} {root} NAME", "rsync {src}/NAME {root}/",        # M forms left to S2 (fix 1)
    "ln -T {src}/NAME {root}", "install -d {src}/NAME {root}/",       # no target folder; dirs only
    "command -v cp {src}/NAME {root}/", "env -S 'cp {src}/NAME {root}/'",   # not a run; a string
    "cp -S .bak {src}/x.json {root}/", "cp --suffix=NAME {src}/x.json {root}/",   # option values
    "Copy-Item {src}/x.json,{src}/y.json {root}",
    'echo x > "{root}/$x/NAME"', 'echo x > "{src}::$INDEX_ALLOCATION/NAME"',
    "dd if={root}/NAME of={src}/copy", "dd if={root}/NAME",
    "cp {root}/NAME {src}/backup", "cp -r {root}/NAME {src}/backup",
    "Copy-Item -LiteralPath {root}/NAME -Destination {src}/backup",
    "Copy-Item -Path {root}/NAME -Filter x -Destination {src}/backup",
    "[IO.File]::WriteAllText('{src}/NAME','x')", "C:/tools/writer.exe --x",
    "(Get-Item {root}/NAME).Delete()",   # E, held: the path is a READER's argument (no `shell_commands` change)
]


def _more_command(template, root, src, name):
    return template.format(root=root.as_posix(), src=src.as_posix()).replace("NAME", name)


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
@pytest.mark.parametrize("cls, template", MORE_FORMS)
def test_more_adds_each_recognition_under_its_own_name_only(roots, cls, template, name):
    home, workspace, litetui, marked, plain = roots
    for root in (litetui, marked):
        command = _more_command(template, root, plain, name)
        assert _four_ways(command, workspace, home, cls, name) == [False, True, False, True], command
        target = _more_asked(command, workspace, home, name, more=frozenset({cls}))
        assert target == root / name or (isinstance(target, str) and "[jobs-file]" in target), command
        if "*" not in template:   # a glob may match both names, and does
            assert _more_asked(command, workspace, home, _other(name), more=deny_floor.MORE) is None, \
                "the recognition follows the name asked about"
    elsewhere = _more_command(template, plain / "x", plain, name)
    (plain / "x").mkdir(exist_ok=True)
    assert _more_asked(elsewhere, workspace, home, name, more=deny_floor.MORE) is None, "not a data root"


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
@pytest.mark.parametrize("template", MORE_CONTROLS)
def test_more_leaves_these_alone(roots, template, name):
    home, workspace, litetui, marked, plain = roots
    (plain / "sub").mkdir(exist_ok=True)
    for root in (litetui, marked):
        command = _more_command(template, root, plain, name)
        assert _more_asked(command, workspace, home, name, more=deny_floor.MORE) is None, command


def test_more_names_seven_recognitions_and_defaults_to_none(roots):
    assert deny_floor.MORE == {"copy-into", "dollar-path", "short-name", "key-value",
                               "link-copy", "pathspec", "call-path"}
    home, workspace, litetui, _, plain = roots
    command = f"cp {plain.as_posix()}/{SCHEDULE} {litetui.as_posix()}/"
    assert deny_floor.refusal(command, workspace, home) is None, "default: the copy into is not seen"
    assert "[jobs-file]" in deny_floor.refusal(command, workspace, home, more=deny_floor.MORE)


#: Fix round 2 (R7, R8): (the shell the caller names, command), each really
#: written in that shell on a temp root (the review's r3b/r3c rows).
FIX2_FORMS = [
    # R7: the grammar is the part's shell's, and every meaning a head has there
    ("bash", "pwsh -NoProfile -c 'Copy-Item {src}/NAME {root}'"),
    ("bash", "powershell -NoProfile -c 'Copy-Item {src}/NAME {root}'"),
    ("powershell", "bash -c 'cp -t {root} {src}/NAME'"),
    ("powershell", "cmd /d /c copy {src}\\NAME {root} /Y"),
    ("powershell", "xcopy {src}\\NAME {root}\\ /Y /Q"),               # System32's xcopy from pwsh
    ("powershell", "ln -f {src}/NAME {root}/"),                     # GNU's ln from pwsh
    ("powershell", "cp.exe {src}/NAME {root}/"),
    ("cmd", "cp -t {root} {src}/NAME"),                             # GNU's cp from cmd
    ("cmd", "mv {src}/NAME {root}/"),
    # R8: wrappers read as getopt clusters, and `time`
    ("bash", "time cp {src}/NAME {root}/"),
    ("bash", "time -p cp {src}/NAME {root}/"),
    ("bash", "env - /usr/bin/cp {src}/NAME {root}/"),
    ("bash", "env -iu X /usr/bin/cp {src}/NAME {root}/"),
    ("bash", "timeout -vk 5 20 cp {src}/NAME {root}/"),
    # R8: a wrapper's long option, by a prefix and with its value in the next word
    ("bash", "env --chdir {root} cp {src}/NAME ."),
    ("bash", "env --chd={root} cp {src}/NAME ."),
    ("bash", "timeout --signal KILL 20 cp {src}/NAME {root}/"),
    ("bash", "timeout --kill-after 5 20 cp {src}/NAME {root}/"),
    # R8: PowerShell parameters that take a value; cmd's concatenation and /-Y
    ("powershell", "Copy-Item {src}/* {root} -Exclude x.json"),
    ("powershell", "Copy-Item {src}/* {root} -Filter NAME"),
    ("powershell", "Copy-Item -Exclude x.json -Path {src}/NAME {root}"),
    ("powershell", "Copy-Item -ea Stop {src}/NAME {root}"),                 # an alias that takes a value
    ("powershell", "Copy-Item -pv p {src}/NAME {root}"),
    ("cmd", "cd /d {root} && copy {src}\\NAME"),                            # one word: into the current folder
    ("cmd", "copy /Y {src}\\NAME+{src}\\x.json {root}"),
    ("cmd", "copy {src}\\NAME {root} /-Y"),
]
#: A FOLDER source holding the file, copied so its files land in the root (R8).
FIX2_TREES = [
    ("bash", "cp -r {src}/. {root}/"), ("bash", "cp -rT {src} {root}"), ("bash", "cp -a {src}/. {root}"),
    ("cmd", "xcopy {src} {root} /E /Y /Q"), ("cmd", "copy {src} {root}"),
    ("powershell", "xcopy {src} {root} /E /Y /Q"),
]
FIX2_LEFT = [   # not written into the root, or not this file
    ("bash", "cp -r {src} {root}/"),            # lands in ROOT/src/, a folder of its own
    ("bash", "cp -rT {empty} {root}"),          # the folder does not hold the file
    ("bash", "cp {src}/. {root}/"),             # no -r: a folder is not copied
    ("cmd", "move {src} {root}"),               # cmd's move renames the folder; not followed
    ("powershell", "Copy-Item {src}/x.json {root} -Filter NAME"),
]


def _fix2(template, root, src, name, empty=None):
    return (template.replace("{root}", root.as_posix()).replace("{src}", src.as_posix())
            .replace("{empty}", (empty or src).as_posix()).replace("NAME", name))


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
@pytest.mark.parametrize("shell, template", FIX2_FORMS + FIX2_TREES)
def test_fix2_every_meaning_a_head_has_in_the_parts_shell(roots, shell, template, name):
    home, workspace, litetui, marked, plain = roots
    src = plain / "src"
    src.mkdir(exist_ok=True)
    (src / name).write_text("{}", encoding="utf-8")
    into = frozenset({deny_floor.COPY_INTO})
    for root in (litetui, marked):
        command = _fix2(template, root, src, name)
        for asked_as in (shell, None):
            assert deny_floor.jobs_write_target(command, workspace, home, shell=asked_as, name=name) is None
            target = deny_floor.jobs_write_target(command, workspace, home, shell=asked_as, name=name, more=into)
            assert target is not None, (asked_as, command)


def test_fix2_no_refusal_round_0_had_is_lost(roots):
    """The regression table's two shapes: after `cd $X` the base is unknown, and
    an absolute destination still answers; a NUL in one copy's words is no
    answer for that copy, not for the copy after it."""
    home, workspace, litetui, _, plain = roots
    root, src = litetui.as_posix(), plain.as_posix()
    into = frozenset({deny_floor.COPY_INTO})
    for shell, command in (("bash", f"cd ${{env:TEMP}}; rm -rf x | cp -t {root} {src}/{OTHER_NAME}"),
                           ("bash", f"cd $X && cp {src}/{OTHER_NAME} {root}/"),
                           ("cmd", f"cp a {root}/.\x00 && cp {src}/{OTHER_NAME} {root}/"),
                           ("bash", f"cp -r {src}/\x00x {root}/ ; cp {src}/{OTHER_NAME} {root}/")):
        assert deny_floor.jobs_write_target(command, workspace, home, shell=shell, name=OTHER_NAME) is None
        assert deny_floor.jobs_write_target(command, workspace, home, shell=shell, name=OTHER_NAME,
                                            more=into) == litetui / OTHER_NAME, command


@pytest.mark.parametrize("shell", ["bash", "powershell", "cmd"])
def test_fix2_a_linking_cp_is_asked_in_every_shell(roots, shell):
    """GNU's cp is on PATH from every shell here: `cp -l FILE alias` is a link in each."""
    home, workspace, litetui, _, plain = roots
    command = f"cp -l {litetui.as_posix()}/{OTHER_NAME} {plain.as_posix()}/alias"
    link = frozenset({deny_floor.LINK_COPY})
    assert deny_floor.jobs_write_target(command, workspace, home, shell=shell, name=OTHER_NAME) is None
    assert deny_floor.jobs_write_target(command, workspace, home, shell=shell, name=OTHER_NAME, more=link)


@pytest.mark.parametrize("shell, template", FIX2_LEFT)
def test_fix2_leaves_these_alone(roots, shell, template):
    home, workspace, litetui, marked, plain = roots
    src, empty = plain / "src", plain / "empty"
    src.mkdir(exist_ok=True)
    empty.mkdir(exist_ok=True)
    (src / OTHER_NAME).write_text("{}", encoding="utf-8")
    command = _fix2(template, litetui, src, OTHER_NAME, empty)
    assert deny_floor.jobs_write_target(command, workspace, home, shell=shell, name=OTHER_NAME,
                                        more=deny_floor.MORE) is None, command


#: Fix round 3 (R11, R12): rows the regression gate of fix round 2 had no template
#: for. (The shell the caller names, command); each really written by PowerShell 7,
#: refused by the rounds before, so each must be refused again under `more`.
FIX3_FORMS = [
    # R11: a PowerShell array written with a space after (or before) its comma is ONE argument
    ("powershell", "Copy-Item {src}/x.json, {src}/NAME {root}"),
    ("powershell", "Copy-Item {src}/NAME, {src}/x.json {root}"),            # the name first
    ("powershell", "Copy-Item {src}/x.json , {src}/NAME {root}"),
    ("powershell", "Copy-Item {src}/x.json ,{src}/NAME {root}"),
    ("powershell", "Copy-Item {src}/x.json, {src}/y.json, {src}/NAME {root}"),
    ("powershell", "Copy-Item 'x.json', '{src}/NAME' {root}"),
    ("powershell", 'Copy-Item "x.json", "{src}/NAME" {root}'),
    ("powershell", "Copy-Item -Path {src}/x.json, {src}/NAME -Destination {root}"),
    ("powershell", "Copy-Item -Destination {root} -Path {src}/x.json, {src}/NAME"),
    ("powershell", "Copy-Item -Path {src}/x.json , {src}/NAME -Destination {root}"),
    ("powershell", "Move-Item {src}/x.json, {src}/NAME {root} -Force"),
    ("powershell", "Move-Item -Path {src}/x.json, {src}/NAME -Destination {root} -Force"),
    ("powershell", "mi {src}/x.json, {src}/NAME {root} -Force"),
    ("powershell", "cpi {src}/x.json, {src}/NAME {root} -Force"),
    ("powershell", "cp {src}/x.json, {src}/NAME {root}"),
    ("powershell", "copy {src}/x.json, {src}/NAME {root}"),
    ("bash", "pwsh -NoProfile -c 'Copy-Item {src}/x.json, {src}/NAME {root}'"),
    # GNU cp reads -Destination as junk and takes the last operand, so only the PowerShell
    # reading of the nested part (its own shell, not the outer dialect) sees this one.
    ("bash", "pwsh -NoProfile -c 'cp -Destination {root} {src}/NAME'"),
    # Fix 4 (Q5 again): `cp`, `copy` are PowerShell's in every shell now, so the row above no longer tells the part's shell from the
    # outer dialect. A nested `cmd /c` body is a cmd part under any outer dialect, and cmd's `A+B` joins two sources where PowerShell
    # reads ONE name: only the cmd reading, chosen by the PART's shell, refuses this one.
    ("bash", 'cmd /c "copy {src}/NAME+{src}/x.json {root}"'),
    # R12: a PowerShell statement or block around the copy, asked with NO shell (what the hook and the
    # owner rule pass): the part the splitter cuts out has the shell that was asked, not PowerShell.
    # Asked as powershell the head of the statement is `if`, `{`; no round saw those.
    (None, "if ($true) { Copy-Item {src}/NAME {root} }"),
    (None, "if ($true) { Copy-Item {src}/NAME {root} } else { }"),
    (None, "if (Test-Path {src}/NAME) { Copy-Item {src}/NAME {root} }"),
    (None, "foreach ($i in 1..2) { Copy-Item {src}/NAME {root} }"),
    (None, "1 | ForEach-Object { Copy-Item {src}/NAME {root} }"),
    (None, "1 | % { Copy-Item {src}/NAME {root} }"),
    (None, "try { Copy-Item {src}/NAME {root} } catch { }"),
    (None, "try { } finally { Copy-Item {src}/NAME {root} }"),
    (None, "while ($true) { Copy-Item {src}/NAME {root}; break }"),
    (None, "switch (1) { 1 { Copy-Item {src}/NAME {root} } }"),
    (None, "function f { Copy-Item {src}/NAME {root} }; f"),
    (None, "$sb = { Copy-Item {src}/NAME {root} }; & $sb"),
    (None, "Invoke-Command -ScriptBlock { Copy-Item {src}/NAME {root} }"),
    (None, ". { Copy-Item {src}/NAME {root} }"),
    (None, "Measure-Command { Copy-Item {src}/NAME {root} } | Out-Null"),
    (None, "if ($true) { Move-Item {src}/NAME {root} -Force }"),
    (None, "try { Move-Item {src}/NAME {root} -Force } catch { }"),
    (None, "foreach ($i in 1) { mi {src}/NAME {root} -Force }"),
    (None, "if ($true) { cpi {src}/NAME {root} }"),
    (None, "while ($true) { copy {src}/NAME {root}; break }"),
    (None, "while ($true) { move {src}/NAME {root} -Force; break }"),
    (None, "if ($true) { Copy-Item {src}/x.json, {src}/NAME {root} }"),   # R11 inside R12
    ("bash", "Copy-Item {src}/NAME {root}"),     # the names PowerShell alone has, asked in every shell
    ("bash", "Move-Item {src}/NAME {root} -Force"),
    ("cmd", "Copy-Item {src}/NAME {root}"),
    ("cmd", "cpi {src}/NAME {root}"),
]
FIX3_LEFT = [   # R11/R12 controls: the same words that copy something else, or nothing of this name
    ("powershell", "Copy-Item {src}/x.json, {src}/y.json {root}"),
    ("powershell", "Copy-Item -Path {src}/x.json, {src}/y.json -Destination {root}"),
    ("powershell", "Copy-Item {src}/x.json, {src}/y.json, {src}/z.json {root}"),
    ("powershell", "if ($true) { Copy-Item {src}/x.json {root} }"),
    ("powershell", "if ($true) { Copy-Item {src}/NAME {src}/elsewhere }"),
    ("powershell", "Copy-Item {src}/NAME, {src}/x.json {src}/elsewhere"),
    ("bash", "copy {src}/x.json {root}"),
    ("bash", "if ($true) { Copy-Item {src}/x.json, {src}/y.json {root} }"),
]


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
@pytest.mark.parametrize("shell, template", FIX3_FORMS)
def test_fix3_arrays_with_a_space_and_copies_inside_a_statement(roots, shell, template, name):
    home, workspace, litetui, marked, plain = roots
    src = plain / "src"
    (src / "elsewhere").mkdir(parents=True, exist_ok=True)
    (src / name).write_text("{}", encoding="utf-8")
    into = frozenset({deny_floor.COPY_INTO})
    for root in (litetui, marked):
        command = _fix2(template, root, src, name)
        for asked_as in (shell, None):
            assert deny_floor.jobs_write_target(command, workspace, home, shell=asked_as, name=name) is None
            target = deny_floor.jobs_write_target(command, workspace, home, shell=asked_as, name=name, more=into)
            assert target is not None, (asked_as, command)


@pytest.mark.parametrize("shell, template", FIX3_LEFT)
def test_fix3_leaves_these_alone(roots, shell, template):
    home, workspace, litetui, _, plain = roots
    src = plain / "src"
    (src / "elsewhere").mkdir(parents=True, exist_ok=True)
    (src / OTHER_NAME).write_text("{}", encoding="utf-8")
    command = _fix2(template, litetui, src, OTHER_NAME)
    for asked_as in (shell, None):
        assert deny_floor.jobs_write_target(command, workspace, home, shell=asked_as, name=OTHER_NAME,
                                            more=deny_floor.MORE) is None, (asked_as, command)


@pytest.mark.parametrize("command", [
    "cp -r //no-such-host/share/dir {dest}/", "cp -a //no-such-host/share/dir {dest}/",
    "cp -rT //no-such-host/share/dir {dest}", "env cp -r //no-such-host/share/dir {dest}/",
    "xcopy \\\\no-such-host\\share\\dir {dest} /E", "copy \\\\no-such-host\\share\\dir {dest}",
    "cp -r \\\\no-such-host\\share\\dir {dest}/",
])
def test_fix3_a_folder_source_on_the_network_is_never_looked_at(roots, monkeypatch, command):
    """R13: a tree grammar tests each folder source on disk; a source that begins
    with two slashes may be a host that does not answer, so it is skipped (a stated
    limit) before anything is resolved. CONTROL: a local folder source is resolved."""
    home, workspace, litetui, _, plain = roots
    resolved = []
    real = deny_floor._resolve
    monkeypatch.setattr(deny_floor, "_resolve", lambda raw, *a, **k: resolved.append(raw) or real(raw, *a, **k))
    deny_floor.jobs_write_target(command.format(dest=plain.as_posix()), workspace, home, name=OTHER_NAME,
                                 more=deny_floor.MORE)
    assert not any(raw.startswith(("\\\\", "//")) for raw in resolved), resolved
    deny_floor.jobs_write_target(f"cp -r {plain.as_posix()} {litetui.as_posix()}/", workspace, home,
                                 name=OTHER_NAME, more=deny_floor.MORE)
    assert any(plain.as_posix() in raw for raw in resolved), "CONTROL: a local folder source is resolved"


#: Fix round 4 (R18): a copy written in PowerShell inside a block, behind a nested
#: `pwsh -c` or direct. The nested body is cut only in PowerShell's dialect (the head
#: is `{`, `if`, `$sb`), so the one part with the head `copy` is the cmd dialect's
#: and `cp`/`mv` get PowerShell's meaning only from a powershell part: every head
#: the PowerShell cmdlet is called by must be read as PowerShell reads it, in every
#: shell. Each cell really writes in PowerShell 7 and was refused by round 0.
FIX4_BLOCKS = ["if ($true) { CMD }", "foreach ($i in 1) { CMD }", "try { CMD } catch { }",
               "function f { CMD }; f", "$sb = { CMD }; & $sb"]
FIX4_WRAPS = {
    "direct": "{body}",
    "pwsh": "pwsh -NoProfile -c '{body}'",
    "powershell": "powershell -NoProfile -Command '{body}'",
}
FIX4_HEADS = ["Copy-Item", "Move-Item", "cpi", "mi", "copy", "move", "cp", "mv"]
FIX4_TRAILING = ["", "-Force", "-Verbose", "-Recurse", "-PassThru", "-ea Stop", "-Confirm:$false"]


@pytest.mark.parametrize("wrap", FIX4_WRAPS)
def test_fix4_r18_a_copy_in_a_block_is_read_as_powershell_in_every_shell(roots, wrap):
    home, workspace, litetui, marked, plain = roots
    src = plain / "src"
    src.mkdir(exist_ok=True)
    (src / OTHER_NAME).write_text("{}", encoding="utf-8")
    into = frozenset({deny_floor.COPY_INTO})
    open_ = []
    for block in FIX4_BLOCKS:
        for head in FIX4_HEADS:
            for trailing in FIX4_TRAILING:
                body = block.replace("CMD", f"{head} {src.as_posix()}/{OTHER_NAME} {litetui.as_posix()} {trailing}".rstrip())
                command = FIX4_WRAPS[wrap].replace("{body}", body)
                # Asked as bash, a quoted `pwsh -c` body is not entered: allowed at every round (a stated limit).
                for asked_as in (None, "cmd") + (("bash",) if wrap == "direct" else ()):
                    if not deny_floor.jobs_write_target(command, workspace, home, shell=asked_as,
                                                         name=OTHER_NAME, more=into):
                        open_.append((asked_as, command))
    assert not open_, (len(open_), open_[:6])


@pytest.mark.parametrize("command", [
    "pwsh -NoProfile -c 'if ($true) { move -Path {src}/NAME -Destination {root} -Force }'",
    "pwsh -NoProfile -c '1 | % { move {src}/NAME {root} -Force }'",
    "pwsh -NoProfile -c 'if ($true) { mv {src}/NAME {root} -Force -PassThru }'",
    "if ($true) { mv {src}/NAME {root} -Force -PassThru }",
    "try { cp {src}/NAME {root} -PassThru } catch { }",
])
def test_fix4_r18_rows_of_the_review(roots, command):
    home, workspace, litetui, _, plain = roots
    src = plain / "src"
    src.mkdir(exist_ok=True)
    (src / OTHER_NAME).write_text("{}", encoding="utf-8")
    into = frozenset({deny_floor.COPY_INTO})
    command = _fix2(command, litetui, src, OTHER_NAME)
    assert deny_floor.jobs_write_target(command, workspace, home, name=OTHER_NAME) is None
    assert deny_floor.jobs_write_target(command, workspace, home, name=OTHER_NAME, more=into), command


#: Fix round 4 (R20): a backtick-newline between the words of a copy leaves an EMPTY
#: word in PowerShell's dialect; it was bound as the destination and the folder fell
#: off. Each really writes in PowerShell 7; round 0 and fix 1 refused it.
FIX4_CONTINUED = [
    "Copy-Item {src}/NAME `\n {root}",
    "Copy-Item {src}/x.json `\n , {src}/NAME {root}",
    "Move-Item {src}/NAME `\n {root} -Force",
    "cpi {src}/NAME `\n {root}",
    "mi {src}/NAME `\n {root} -Force",
    "copy {src}/NAME `\n {root}",
    "move {src}/NAME `\n {root} -Force",
    "Copy-Item `\n {src}/NAME `\n {root}",
    "Copy-Item {src}/x.json, `\n {src}/NAME {root}",
    "Copy-Item -Path {src}/NAME -Destination `\n {root}",   # fell out of the same change
]
#: NOT closed (open at every round, stated limits): a CRLF (the splitter cuts the statement at
#: the newline), a bare newline after a comma, the continuation inside a block, and bash's
#: backslash-newline between `-t` and its value. No second mechanism was built for them.


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
@pytest.mark.parametrize("template", FIX4_CONTINUED)
def test_fix4_r20_a_backtick_newline_between_the_words_of_a_copy(roots, template, name):
    home, workspace, litetui, marked, plain = roots
    src = plain / "src"
    src.mkdir(exist_ok=True)
    (src / name).write_text("{}", encoding="utf-8")
    into = frozenset({deny_floor.COPY_INTO})
    for root in (litetui, marked):
        command = _fix2(template, root, src, name)
        for asked_as in ("powershell", None):
            assert deny_floor.jobs_write_target(command, workspace, home, shell=asked_as, name=name) is None
            assert deny_floor.jobs_write_target(command, workspace, home, shell=asked_as, name=name,
                                                more=into), (asked_as, command)


@pytest.mark.parametrize("source", [
    "\\/no-such-host/share/dir", "/\\no-such-host\\share\\dir", "'//no-such-host/share/dir'",
    "\"//no-such-host/share/dir\"", "'\\\\no-such-host\\share\\dir'", "//no-such-host/share/dir",
    "\\\\no-such-host\\share\\dir", "//?/UNC/no-such-host/share/dir", "\\/?/UNC/no-such-host/share/dir",
])
@pytest.mark.parametrize("head", ["cp -r {s} {dest}/", "cp -a {s} {dest}/", "cp -rT {s} {dest}",
                                  "env cp -r {s} {dest}/", "xcopy {s} {dest} /E", "copy {s} {dest}"])
def test_fix4_r21_any_spelling_windows_reads_as_a_network_folder_is_never_looked_at(roots, monkeypatch,
                                                                                     source, head):
    """R21: R13 skipped `//host/share` and `\\\\host\\share` only. Mixed separators and a quote left on
    the word (cmd keeps single quotes) name the same host; none may reach a stat. CONTROL: R13's
    control, a local folder source IS resolved."""
    home, workspace, litetui, _, plain = roots
    resolved = []
    real = deny_floor._resolve
    monkeypatch.setattr(deny_floor, "_resolve", lambda raw, *a, **k: resolved.append(raw) or real(raw, *a, **k))
    for asked_as in (None, "bash", "cmd", "powershell"):
        deny_floor.jobs_write_target(head.format(s=source, dest=plain.as_posix()), workspace, home,
                                     shell=asked_as, name=OTHER_NAME, more=deny_floor.MORE)
    assert not [raw for raw in resolved
                if raw.strip("'\"").replace("/", "\\").startswith("\\\\")], resolved
    deny_floor.jobs_write_target(f"cp -r {plain.as_posix()} {litetui.as_posix()}/", workspace, home,
                                 name=OTHER_NAME, more=deny_floor.MORE)
    assert any(plain.as_posix() in raw for raw in resolved), "CONTROL: a local folder source is resolved"


def test_the_gnu_grammar_alone_in_bash(roots):
    """Asked as bash only: `--` ends the options and the words after it are operands,
    even one that looks like `-t`. (Since fix 4 the PowerShell reading of `cp` is asked
    in bash too, so only a row it does not refuse tells the GNU reading apart.)"""
    home, workspace, litetui, _, plain = roots
    into = frozenset({deny_floor.COPY_INTO})
    for command, written in ((f"cp -- {plain.as_posix()}/{OTHER_NAME} {litetui.as_posix()}/", True),
                             (f"cp -v -- {plain.as_posix()}/{OTHER_NAME} {litetui.as_posix()}", True),
                             (f"cp -T {plain.as_posix()}/{OTHER_NAME} {litetui.as_posix()}", False),
                             # fix 4 (G5 again): PowerShell's reading of `cp` is asked in bash too and takes `-Destination` for
                             # its parameter; GNU, after `--`, takes it for a file name and the last operand for the folder.
                             (f"cp -- -Destination {plain.as_posix()}/{OTHER_NAME} {litetui.as_posix()}/", True)):
        target = deny_floor.jobs_write_target(command, workspace, home, shell="bash", name=OTHER_NAME, more=into)
        assert (target == litetui / OTHER_NAME) is written, command


def test_copy_into_looks_at_the_destination_only_when_a_source_has_the_name(roots, monkeypatch):
    """R5: a copy whose sources do not carry the name never resolves or stats its
    destination (a UNC folder on a host that does not answer can outlast the hook)."""
    home, workspace, litetui, _, plain = roots
    resolved = []
    real = deny_floor._resolve
    monkeypatch.setattr(deny_floor, "_resolve", lambda raw, *a, **k: resolved.append(raw) or real(raw, *a, **k))
    deny_floor.jobs_write_target("cp notes.md //no-such-host/share/", workspace, home, name=OTHER_NAME,
                                 more=deny_floor.MORE)
    assert not any("no-such-host" in raw for raw in resolved), resolved
    deny_floor.jobs_write_target(f"cp {plain.as_posix()}/{OTHER_NAME} {litetui.as_posix()}/", workspace, home,
                                 name=OTHER_NAME, more=deny_floor.MORE)
    assert any(litetui.as_posix() in raw for raw in resolved), "CONTROL: a match does resolve it"


def test_refusal_hands_more_to_both_jobs_questions(roots, monkeypatch):
    """refusal() asks the Git question and the shell question without `more`
    first, then (the default allowing) with it (each also asks a pathspec, so a
    verdict alone cannot show which did)."""
    home, workspace, _, _, _ = roots
    seen = []
    for fn in ("_jobs_git_refusal", "_jobs_write_target"):
        real = getattr(deny_floor, fn)
        monkeypatch.setattr(deny_floor, fn, lambda *a, _real=real, _fn=fn:
                            seen.append((_fn, a[-1])) or _real(*a))
    deny_floor.refusal("echo x", workspace, home, more=deny_floor.MORE)
    none = frozenset()
    assert seen == [("_jobs_git_refusal", none), ("_jobs_write_target", none),
                    ("_jobs_git_refusal", deny_floor.MORE), ("_jobs_write_target", deny_floor.MORE)]


NUL_MORE_ONLY = [   # a NUL in a word only an added recognition reads (fix 1, R3)
    "cp {src}/NAME {root}\x00/", "cp a b\x00", "Copy-Item {src}/NAME -Destination {root}\x00",
    'echo x > "{root}::$INDEX\x00/NAME"', 'cd "{root}::$INDEX_ALLOCATION" && echo x > a\x00/NAME',
"rm {root}/SETTIN~1\x00.JSO", "dd of={root}/x\x00/NAME",
    "git checkout HEAD -- :/x\x00/NAME", "[IO.File]::Delete('{root}/x\x00/NAME')",
]


@pytest.mark.parametrize("template", NUL_MORE_ONLY)
def test_a_nul_only_more_reads_is_no_answer_and_never_an_error(roots, template):
    """`more` may only add a refusal: where the default answers, `more` answers the
    same or refuses; it never raises instead (R3: `cp a b<NUL>; echo x > FILE`)."""
    home, workspace, litetui, _, plain = roots

    def answer(ask):
        try:
            return ("answer", bool(ask()))
        except ValueError:
            return ("raised", None)

    asked = 0
    for name in (SCHEDULE, OTHER_NAME):
        command = template.format(src=plain.as_posix(), root=litetui.as_posix()).replace("NAME", name)
        for text in (command, f"{command} ; echo x > {litetui.as_posix()}/{name}"):
            for ask in (lambda more: _more_asked(text, workspace, home, name, more=more),
                        lambda more: deny_floor.refusal(text, workspace, home, more=more)):
                default = answer(lambda: ask(frozenset()))
                if default[0] == "raised":
                    continue   # the base's own exception, unchanged
                asked += 1
                for more in (deny_floor.MORE, *(frozenset({cls}) for cls in deny_floor.MORE)):
                    with_more = answer(lambda: ask(more))
                    assert with_more[0] == "answer", (text, sorted(more), "raised where the default answered")
                    assert with_more[1] or not default[1], (text, sorted(more), "refuse -> allow")
    assert asked, "every form raised at the default: the row tests nothing"


@pytest.mark.parametrize("template", JOBS_WRITES)
def test_more_never_takes_a_refusal_away(roots, template):
    home, workspace, litetui, marked, plain = roots
    command = _jobs(template, litetui, marked, plain)
    assert "[jobs-file]" in (deny_floor.refusal(command, workspace, home, more=deny_floor.MORE) or "")


@pytest.mark.parametrize("template", JOBS_READS)
def test_more_still_lets_the_reads_pass(roots, template):
    home, workspace, litetui, marked, plain = roots
    command = _jobs(template, litetui, marked, plain)
    assert deny_floor.refusal(command, workspace, home, more=deny_floor.MORE) is None, command


def test_copy_into_a_folder_below_under(roots):
    """A, with `under`: the copy into a folder below `.agents` is judged there."""
    home, workspace, litetui, _, plain = roots
    seat = litetui / UNDER / "Seat"
    seat.mkdir(parents=True)
    for dest in (f"{seat.as_posix()}/", seat.as_posix()):
        command = f"cp {plain.as_posix()}/{OTHER_NAME} {dest}"
        assert _four_ways(command, workspace, home, deny_floor.COPY_INTO, OTHER_NAME, UNDER) == \
            [False, True, False, True], command
        assert _more_asked(command, workspace, home, OTHER_NAME, more=deny_floor.MORE) is None, \
            "without `under` a folder below `.agents` is not the data root"
    # A folder that does not exist yet is a folder when the word ends in a
    # separator (the copy makes it); without one the copy makes a FILE of that name.
    new = (litetui / UNDER / "NewSeat").as_posix()
    into = f"cp {plain.as_posix()}/{OTHER_NAME} {new}/"
    assert _four_ways(into, workspace, home, deny_floor.COPY_INTO, OTHER_NAME, UNDER) == [False, True, False, True]
    assert _more_asked(f"cp {plain.as_posix()}/{OTHER_NAME} {new}", workspace, home, OTHER_NAME, UNDER,
                       deny_floor.MORE) is None


def test_dollar_path_reaches_the_tool_path_and_the_delete_rules(roots):
    """G: the tool question, and the recursive-delete rules, which take the same keyword."""
    home, workspace, litetui, _, _ = roots
    spelled = f"{litetui.as_posix()}::$INDEX_ALLOCATION/{OTHER_NAME}"
    assert deny_floor.write_refusal(spelled, workspace, home, name=OTHER_NAME) is None
    assert deny_floor.write_refusal(spelled, workspace, home, name=OTHER_NAME,
                                    more=frozenset({deny_floor.DOLLAR_PATH}))
    git = f'git checkout HEAD -- "{spelled}"'   # the Git question hands `more` on
    assert deny_floor.jobs_git_refusal(git, workspace, home, name=OTHER_NAME) is None
    assert deny_floor.jobs_git_refusal(git, workspace, home, name=OTHER_NAME,
                                       more=frozenset({deny_floor.DOLLAR_PATH}))
    for command in (f'rm -rf "{home.as_posix()}::$INDEX_ALLOCATION"',
                    f'Remove-Item -Recurse "{home.as_posix()}:$I30:$INDEX_ALLOCATION"',
                    f'cd "{home.as_posix()}::$INDEX_ALLOCATION"; Remove-Item * -Recurse',
                    f'rm -rf "{home.as_posix()}/.claude::$INDEX_ALLOCATION"'):
        assert deny_floor.refusal(command, workspace, home) is None, command
        assert deny_floor.refusal(command, workspace, home, more=deny_floor.MORE - {deny_floor.DOLLAR_PATH}) is None
        reason = deny_floor.refusal(command, workspace, home, more=frozenset({deny_floor.DOLLAR_PATH}))
        assert reason and "[protected-root-delete]" in reason, command
    # A `$` left after the cut is still a variable: no answer, so no refusal.
    assert deny_floor.refusal(f'rm -rf "{home.as_posix()}/$x"', workspace, home, more=deny_floor.MORE) is None


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
def test_short_name_resolves_an_existing_file_for_the_writers(roots, name):
    """H: the file's 8.3 short name in an argument word, writer heads only, on disk.
    Skipped where the volume gives the file no short name."""
    if os.name != "nt":
        pytest.skip("short names are a Windows volume feature")
    import ctypes
    home, workspace, litetui, _, plain = roots
    (litetui / name).write_text("{}", encoding="utf-8")
    buffer = ctypes.create_unicode_buffer(600)
    ctypes.windll.kernel32.GetShortPathNameW(str(litetui / name), buffer, 600)
    short = Path(buffer.value).name
    if not short or short.lower() == name:
        pytest.skip("this volume gives the file no short name")
    spelled = f"{litetui.as_posix()}/{short}"
    for writer in (f"rm {spelled}", f"env rm {spelled}", f"Remove-Item {spelled}", f"mv {spelled} {plain.as_posix()}/x",
                   f"sed -i s/a/b/ {spelled}", f"Set-Content {spelled} x", f"cp x.json {spelled}"):
        assert _four_ways(writer, workspace, home, deny_floor.SHORT_NAME, name) == \
            [False, True, False, True], writer
    for left in (f"cat {spelled}", f"python tool.py {spelled}", f"cp {spelled} {plain.as_posix()}/x",
                 f"rm {litetui.as_posix()}/{short[:-5]}9{short[-4:]}"):
        assert _more_asked(left, workspace, home, name, more=deny_floor.MORE) is None, left


@pytest.mark.parametrize("name", [SCHEDULE, OTHER_NAME])
def test_pathspec_magic_names_the_file_from_the_repository_root(roots, monkeypatch, name):
    """N: `:/REL` and `:(top)REL` from a subfolder of the repository; other long
    magic is from the current folder; an exclude names nothing written."""
    home, _, litetui, _, plain = roots
    (litetui / ".git").mkdir()
    sub = litetui / "sub"
    sub.mkdir()
    for form in (":/NAME", "':(top)NAME'", "':(top,icase)NAME'", ":/:NAME"):
        command = f"git checkout HEAD -- {form}".replace("NAME", name)
        assert _four_ways(command, sub, home, deny_floor.PATHSPEC, name) == [False, True, False, True], command
        moved = f"cd {sub.as_posix()} && {command}"   # from another folder: the `cd` is followed
        assert _four_ways(moved, plain, home, deny_floor.PATHSPEC, name) == [False, True, False, True], moved
    assert _four_ways(f"git checkout HEAD -- ':(icase)sub/../{name}'", litetui, home,
                      deny_floor.PATHSPEC, name)[1], "other magic: from the current folder"
    for left in (f"git checkout HEAD -- ':(icase){name}'", f"git checkout HEAD -- ':!{name}'",
                 f"git checkout HEAD -- ':(exclude){name}'", f"git diff -- :/{name}"):
        assert _more_asked(left, sub, home, name, more=deny_floor.MORE) is None, left
    # An exclude names the file NOT written, even with top magic that would name
    # the protected file. (From inside the root the default already refuses
    # `':!NAME'`, an older over-block this keyword does not change.)
    for left in (f"git checkout HEAD -- ':/!{name}'", f"git checkout HEAD -- ':/^{name}'",
                 f"git checkout HEAD -- ':(top,exclude){name}'"):
        assert _more_asked(left, sub, home, name, more=deny_floor.MORE) is None, left
    # The Git question alone, as LiteTUI's rule and refusal() ask it first.
    command = f"git checkout HEAD -- :/{name}"
    assert deny_floor.jobs_git_refusal(command, sub, home, name=name) is None
    assert deny_floor.jobs_git_refusal(command, sub, home, name=name, more=frozenset({deny_floor.PATHSPEC}))


# ── the hook's COUNT-ONLY opt-in for the added recognitions (T0306-S) ──
#
# Off by default. On ("1"), a call the added recognitions would refuse and the
# default does not is logged, one line, and ALLOWED. Nothing is ever refused by it.

def _count_lines(harness):
    log = harness / "deny_gate.log"
    return [line for line in log.read_text(encoding="utf-8").splitlines()
            if "count-only" in line] if log.exists() else []


@pytest.fixture
def counted(roots, tmp_path, monkeypatch):
    harness = tmp_path / "harness-home"
    monkeypatch.setenv("LITEHARNESS_HOME", str(harness))
    monkeypatch.delenv(deny_gate.COUNT_ONLY_ENV, raising=False)
    return (*roots, harness)


def test_count_only_is_off_by_default(counted):
    home, workspace, litetui, _, plain, harness = counted
    command = f"cp {plain.as_posix()}/{SCHEDULE} {litetui.as_posix()}/"
    assert deny_gate.decide(_payload("Bash", command, workspace)) is None
    assert _count_lines(harness) == []


@pytest.mark.parametrize("value", ["0", "", "true", "yes"])
def test_count_only_is_on_for_one_value_only(counted, monkeypatch, value):
    home, workspace, litetui, _, plain, harness = counted
    monkeypatch.setenv(deny_gate.COUNT_ONLY_ENV, value)
    deny_gate.decide(_payload("Bash", f"cp {plain.as_posix()}/{SCHEDULE} {litetui.as_posix()}/", workspace))
    assert _count_lines(harness) == []


@pytest.mark.parametrize("template", [
    "cp {src}/jobs.json {root}/", "cp -t{root} {src}/jobs.json", "dd if=/dev/null of={root}/jobs.json",
    "cp -l {root}/jobs.json {src}/alias", "[IO.File]::WriteAllText('{root}/jobs.json','x')",
    'echo x > "{root}::$INDEX_ALLOCATION/jobs.json"', 'rm -rf "{home}::$INDEX_ALLOCATION"',
])
def test_count_only_logs_one_line_and_allows(counted, monkeypatch, template):
    home, workspace, litetui, _, plain, harness = counted
    monkeypatch.setenv(deny_gate.COUNT_ONLY_ENV, "1")
    command = template.format(src=plain.as_posix(), root=litetui.as_posix(), home=Path.home().as_posix())
    assert deny_gate.decide(_payload("Bash", command, workspace)) is None, "count-only refused"
    lines = _count_lines(harness)
    assert len(lines) == 1 and "would refuse" in lines[0] and "DENY FLOOR" in lines[0], lines


def test_count_only_logs_a_file_tool_write_too(counted, monkeypatch):
    home, workspace, litetui, _, _, harness = counted
    monkeypatch.setenv(deny_gate.COUNT_ONLY_ENV, "1")
    spelled = f"{litetui.as_posix()}::$INDEX_ALLOCATION/{SCHEDULE}"
    assert deny_gate.decide(_write("Write", spelled, workspace)) is None
    assert len(_count_lines(harness)) == 1


def test_count_only_leaves_a_refusal_and_an_ordinary_call_alone(counted, monkeypatch):
    home, workspace, litetui, _, _, harness = counted
    monkeypatch.setenv(deny_gate.COUNT_ONLY_ENV, "1")
    out = deny_gate.decide(_payload("Bash", f"echo x > {litetui.as_posix()}/{SCHEDULE}", workspace))
    assert out and "[jobs-file]" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert deny_gate.decide(_payload("Bash", "git status", workspace)) is None
    assert deny_gate.decide(_write("Write", litetui / SCHEDULE, workspace))
    assert _count_lines(harness) == [], "only a call the default allows is counted"


def test_count_only_cannot_change_a_verdict_when_it_breaks(counted, monkeypatch):
    """An error inside the count-only branch is swallowed: the call is allowed
    exactly as the default allows it."""
    home, workspace, litetui, _, plain, harness = counted
    monkeypatch.setenv(deny_gate.COUNT_ONLY_ENV, "1")
    real = deny_gate._load_floor

    def broken():
        floor = real()
        floor.MORE = 1   # `more=1` makes the added question raise (TypeError)
        return floor

    monkeypatch.setattr(deny_gate, "_load_floor", broken)
    command = f"cp {plain.as_posix()}/{SCHEDULE} {litetui.as_posix()}/"
    assert deny_gate.decide(_payload("Bash", command, workspace)) is None
    lines = _count_lines(harness)   # R4: the failure is said, so a quiet log means "nothing"
    assert len(lines) == 1 and "count-only: error TypeError" in lines[0], lines
    assert "would refuse" not in lines[0]
