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
    ("python -m pytest -q tests/test_x.py", "1", False),
    ('FILES="tests/a.py"; python -m pytest -q $FILES', "1", False),
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

