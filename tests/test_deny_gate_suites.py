"""The opt-in "no full test suites" check inside deny_gate.py (T0427, T0053).

OFF unless LITEHARNESS_DENY_FULL_SUITES is truthy. deny_floor.py is not involved.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from liteharness import deny_gate

GATE = Path(deny_gate.__file__).resolve()
#: the real process reader, captured before the autouse fixture stubs it
READ_PROCESSES = getattr(deny_gate, "_live_runner_pids", None)
FLAG = "LITEHARNESS_DENY_FULL_SUITES"
ESCAPE = "LITEHARNESS_ALLOW_FULL_SUITE"
MARKER = "full test suite"

#: Whole suites: refused when the flag is on.
FULL_SUITES = [
    "python -m pytest -q",
    "pytest",
    "pytest -q",
    "pytest tests/",
    "python -m pytest -q tests/ -k foo",
    "bun run test",
    "bun run test:browser",
    "bun test",
    "npx vitest run",
    "vitest",
    "bunx vitest",
    "npx playwright test",
    "cargo test",
    "pnpm test",
    "npm run test",
    "cd x && pytest -q",
    "FOO=1 pytest -q 2>&1 | tail -5",
    "pytest -q > out.log 2>&1",
    "FILES=a.py; pytest -q tests/",
    "echo hi\npytest -q",
    "cat <<'EOF'\nhello\nEOF\npytest -q",
]

#: Named targets, expansions that resolve to a list, and the word as prose: never refused.
NOT_SUITES = [
    "python -m pytest -q tests/test_x.py",
    "python -m pytest -W error::pytest.PytestUnknownMarkWarning tests/test_x.py",
    "pytest tests/test_x.py::test_one",
    'pytest -k "a or b" tests/test_x.py',
    "bunx vitest run src/a.test.ts",
    "bun test src/a.test.ts",
    "cargo test my_filter",
    "npx playwright test e2e/a.spec.ts",
    'FILES="tests/test_a.py tests/test_b.py"; python -m pytest -q $FILES',
    'python -m pytest -q "$FILES"',
    "python -m pytest -q ${FILES}",
    'python -m pytest -q "$@"',
    "python -m pytest -q $(cat list.txt)",
    "python -m pytest -q `cat list.txt`",
    "python -m pytest -q $(git diff --name-only | grep '\\.py$')",
    "npx vitest run $FILES",
    "cargo test $FILTER",
    "git log --oneline | grep -E 'vitest\\|pytest\\|jest'",
    'git log --oneline | grep -E "vitest|pytest|jest"',
    "rg 'pytest|vitest' src",
    'rg -n "vitest|pytest" src',
    'findstr /R "pytest" a.txt',
    "grep -rn pytest src/",
    "echo pytest",
    'echo "run pytest next"',
    "printf '%s\\n' pytest vitest",
    "cat <<'EOF'\npytest -q\nbun run test\nEOF",
    "cat <<EOF > notes.md\nvitest\npytest\nEOF",
    "cat <<-EOF\n\tpytest\n\tEOF",
    "git commit -m 'fix: pytest no longer runs; bun run test unaffected'",
    "# pytest -q\nls",
    "ls  # pytest",
    "echo a; echo 'x;pytest'",
    "echo 'a && pytest'",
    "git status",
]


def _payload(command, tool="Bash", cwd="."):
    return {"hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"command": command}, "cwd": str(cwd)}


def _reason(command, **kw):
    out = deny_gate.decide(_payload(command, **kw))
    return out["hookSpecificOutput"]["permissionDecisionReason"] if out else None


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv(ESCAPE, raising=False)
    # never read the live machine's processes from a test
    monkeypatch.setattr(deny_gate, "_live_runner_pids", lambda: [], raising=False)


@pytest.mark.parametrize("command", FULL_SUITES + NOT_SUITES)
def test_flag_off_allows_everything(command, tmp_path):
    assert _reason(command, cwd=tmp_path) is None


@pytest.mark.parametrize("command", FULL_SUITES)
def test_flag_on_refuses_a_whole_suite(command, monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    reason = _reason(command, cwd=tmp_path)
    assert reason and MARKER in reason, command


@pytest.mark.parametrize("command", NOT_SUITES)
def test_flag_on_lets_a_named_target_or_prose_through(command, monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    assert _reason(command, cwd=tmp_path) is None, command


@pytest.mark.parametrize("value", ["0", "", "false", "no", "off"])
def test_a_falsy_flag_is_off(value, monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, value)
    assert _reason("pytest -q", cwd=tmp_path) is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_a_truthy_flag_is_on(value, monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, value)
    assert MARKER in _reason("pytest -q", cwd=tmp_path)


def test_the_escape_hatch_wins_over_the_flag(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(ESCAPE, "1")
    assert _reason("pytest -q", cwd=tmp_path) is None


def test_only_the_bash_tool_is_judged(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    assert _reason("pytest -q", tool="PowerShell", cwd=tmp_path) is None


def test_the_floor_still_decides_first(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    reason = _reason("rm -rf ~; pytest -q", cwd=tmp_path)
    assert reason and MARKER not in reason


def test_the_refusal_is_generic_and_says_what_to_run_instead(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    reason = _reason("pytest", cwd=tmp_path)
    assert FLAG in reason and ESCAPE in reason
    assert "Ryan" not in reason and "Rule 13" not in reason
    assert "path/to/test_file.py" in reason


@pytest.mark.parametrize("command,flag,denied", [
    ("python -m pytest -q", "1", True),
    ("python -m pytest -q", None, False),
    ("git status", "1", False),
])
def test_run_by_path_through_the_hook_protocol(command, flag, denied, tmp_path):
    env = {**os.environ, "LITEHARNESS_HOME": str(tmp_path / "h")}
    env.pop(FLAG, None)
    env.pop(ESCAPE, None)
    if flag:
        env[FLAG] = flag
    proc = subprocess.run([sys.executable, str(GATE)], input=json.dumps(_payload(command, cwd=tmp_path)),
                          capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    if denied:
        assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    else:
        assert proc.stdout.strip() == ""



# ── one runner at a time (the old hook's second half) ───────────────────────

SINGLE_FILE_RUNS = [
    "python -m pytest -q tests/test_x.py",
    "bunx vitest run src/a.test.ts",
    'FILES="tests/a.py"; python -m pytest -q $FILES',
    "cd x && npx playwright test e2e/a.spec.ts",
]
RUNNER_REFUSAL = "another test runner is alive"


def _fake_pids(monkeypatch, pids):
    calls = []
    monkeypatch.setattr(deny_gate, "_live_runner_pids", lambda: calls.append(1) or pids)
    return calls


@pytest.mark.parametrize("command", SINGLE_FILE_RUNS)
def test_a_live_runner_refuses_a_single_file_run(command, monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    _fake_pids(monkeypatch, ["111", "222"])
    reason = _reason(command, cwd=tmp_path)
    assert reason and RUNNER_REFUSAL in reason and "111,222" in reason


@pytest.mark.parametrize("command", SINGLE_FILE_RUNS)
def test_no_live_runner_allows_a_single_file_run(command, monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    _fake_pids(monkeypatch, [])
    assert _reason(command, cwd=tmp_path) is None


@pytest.mark.parametrize("command", ["git status", "ls -la", "echo pytest", "rg vitest src",
                                     "cat <<'EOF'\npytest\nEOF", "git commit -m 'run vitest'"])
def test_a_non_test_command_never_reads_processes(command, monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    calls = _fake_pids(monkeypatch, ["111"])
    assert _reason(command, cwd=tmp_path) is None
    assert not calls


def test_flag_off_and_escape_never_read_processes(monkeypatch, tmp_path):
    calls = _fake_pids(monkeypatch, ["111"])
    assert _reason("pytest tests/test_x.py", cwd=tmp_path) is None
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(ESCAPE, "1")
    assert _reason("pytest tests/test_x.py", cwd=tmp_path) is None
    assert not calls


def test_a_whole_suite_is_refused_as_a_suite_not_as_a_runner(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    _fake_pids(monkeypatch, ["111"])
    reason = _reason("pytest -q", cwd=tmp_path)
    assert MARKER in reason and RUNNER_REFUSAL not in reason


class _Done:
    def __init__(self, stdout):
        self.stdout = stdout


def test_the_process_read_parses_pids(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return _Done("4242\r\n77\r\n")
    monkeypatch.setattr(deny_gate, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(deny_gate.subprocess, "run", run, raising=False)
    assert READ_PROCESSES() == ["4242", "77"]
    assert "vitest|playwright" in seen["args"][-1] and "Get-CimInstance" in seen["args"][-1]
    assert 0 < seen["kw"]["timeout"] <= 10


@pytest.mark.parametrize("failure", [OSError("no powershell"),
                                     subprocess.TimeoutExpired("powershell", 8)])
def test_a_failed_process_read_allows(failure, monkeypatch):
    def run(*a, **kw):
        raise failure
    monkeypatch.setattr(deny_gate, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(deny_gate.subprocess, "run", run, raising=False)
    assert READ_PROCESSES() == []


def test_off_windows_nothing_is_read(monkeypatch):
    def run(*a, **kw):
        raise AssertionError("must not spawn")
    monkeypatch.setattr(deny_gate, "_IS_WINDOWS", False, raising=False)
    monkeypatch.setattr(deny_gate.subprocess, "run", run, raising=False)
    assert READ_PROCESSES() == []
