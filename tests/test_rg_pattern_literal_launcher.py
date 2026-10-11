"""Pattern text handed to a search command is data to the owner-launcher floor.

`rg -n 'subprocess\\.(run|Popen)' src` was refused as an owner-capable launch: the
`(` inside the single-quoted pattern was read as a statement boundary, which emptied
the command position, so `run` looked like argv0. Under a PROVEN PowerShell a
single-quoted literal that is an argument of a pattern-search head is verbatim text.
Everything that really launches stays refused, mixed or not, and an unproven shell
keeps the conservative reading.

Classifier inputs only: nothing here runs a command.
"""
import pytest

from liteharness import deny_floor

DATA = [
    # the stored report shape: Set-Location, then two searches, one with a quoted path
    "Set-Location 'C:/ExampleProjects/Tree'; rg -n '_require_armed|_action_drag|hold_ms|duration_ms' "
    "pkg/tests --glob '*example*'; rg -n 'subprocess\\.(run|Popen)|_run_ps' 'C:/ExampleHome/skills/tool.py'",
    "rg -n 'subprocess\\.(run|Popen)' src",
    "rg -n 'foo(run|bar)' src",
    "grep -n 'a;run' file.txt",
    "egrep 'x|run.bat' file.txt",
    "Select-String -Pattern 'x|run.bat' file.txt",
    "sls 'a&run' file.txt",
    "findstr 'a&run' file.txt",
    "rg 'it''s (run)' file.txt",
    "rg --glob \"*.py\" -n 'x(run)' src",
    "rg -e'x(run)' src",
]

LAUNCHES = [
    "rg 'x(y)' f; run.bat",
    "rg 'x' f; ./run.bat",
    "rg '(run|a)' f && run.bat",
    "rg 'a;b' f | run.bat",
    "rg 'x' f; & 'C:/ExampleProjects/Tree/run.bat'",
    "rg 'x' f | & 'a(run)'",
    "rg 'x' $(run.bat)",
    "rg 'x' (run.bat)",
    "rg 'a' f; rg 'b(' f; run.bat",
    "rg \"x\" 'a(run)'; run",
    "rg 'unterminated(run) f",
    # a quoted string that is CODE, not pattern text
    "iex 'a; run.bat'",
    "Invoke-Expression 'x(run.bat)'",
    "powershell -c 'a; run.bat'",
    "cmd /c 'x & run.bat'",
    "cmd.exe /c rg 'x(run)' f",
    "python -c 'import os; os.system(\"run.bat\")'",
    "Start-Process 'x(y)'; run.bat",
    "git log -S'a(run)'; run.bat",
    # rg runs the program an option names
    "rg --pre 'x;run.bat' pattern file",
    "rg --pre 'x(run)' pattern file",
    "rg --hostname-bin 'x(run)' pattern file",
    "rg --pre='x(run)' pattern file",
    # the same option word spelled with quotes: decoded before it is judged
    "rg \"--pre\" 'x(run)' pattern file",
    "rg '--pre' 'x(run)' pattern file",
    "rg --p\"re\" 'x(run)' pattern file",
    "rg --p're' 'x(run)' pattern file",
    "rg --\"pre\" 'x(run)' pattern file",
    "rg \"--hostname-bin\" 'x(run)' pattern file",
    "rg --hostname-'bin' 'x(run)' pattern file",
    "rg --pre=\"x\" 'a(run)' pattern file",
    # a head that needed quotes is a string expression, not a bare pattern head
    "\"rg\" 'x(run)' file",
    "'rg' 'x(run)' file",
    "r\"g\" 'x(run)' file",
    # syntax the small scan does not model restores the full scan
    "rg 'a' f `\n; run.bat",
    "rg 'x(run)' f # c\n; run.bat",
    "rg 'x(run)' \u2018y\u2019",
    "rg \"$x\" 'a(run)'",
]


@pytest.mark.parametrize("command", DATA)
def test_pattern_text_is_not_a_launch_under_proven_powershell(tmp_path, command):
    assert deny_floor.refusal(command, tmp_path, shell="powershell") is None, command


@pytest.mark.parametrize("command", [c for c in DATA if "(" in c or ";" in c or "&" in c])
@pytest.mark.parametrize("shell", [None, "bash"])
def test_an_unproven_shell_keeps_the_conservative_reading(tmp_path, command, shell):
    reason = deny_floor.refusal(command, tmp_path, shell=shell)
    assert reason and "[owner-launcher]" in reason, command


@pytest.mark.parametrize("command", LAUNCHES)
def test_real_launches_stay_refused_under_proven_powershell(tmp_path, command):
    reason = deny_floor.refusal(command, tmp_path, shell="powershell")
    assert reason and "[owner-launcher]" in reason, command


@pytest.mark.parametrize("command, spans", [
    ("rg 'a(b)' f", [(3, 9)]),
    ("rg -n 'a;b' 'c|d' f", [(6, 11), (12, 17)]),
    ("echo 'a(b)'", []),            # echo is a reader, not a pattern head
    ("awk 'BEGIN{x}' f", []),       # awk runs its quoted script
    ("'rg' 'a(b)'", []),            # a literal argv0 never spells a head
    ("rg --pre 'a(b)' f", []),
    ("rg \"--pre\" 'a(b)' f", []),
    ("rg --p\"re\" 'a(b)' f", []),
    ("rg '--hostname-bin' 'a(b)' f", []),
    ("\"rg\" 'a(b)' f", []),
    ("rg \"-n\" 'a(b)' f", [(8, 14)]),     # a quoted but harmless option is still fine
    ("rg -e 'a''(b)' f", [(6, 14)]),
    ("rg 'a' f; iex 'b(c)'", [(3, 6)]),
])
def test_only_pattern_head_arguments_are_excused(command, spans):
    assert deny_floor._pattern_literal_spans(command) == spans
