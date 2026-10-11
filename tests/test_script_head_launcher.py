"""awk/sed run their quoted program; a pattern head's excuse depends on its file.

1. The launcher name inside the quoted script of awk/gawk/mawk/nawk/sed/gsed is a
   launch (`system("run")`, `print | "run"`, sed `e run`), never data. A bare operand
   stays a file name. A `;` or `|` inside the quotes must not hide the head.
2. The excuse for pattern text handed to rg/grep/egrep/findstr holds only when the
   head resolves to a native program: Windows `.exe`/`.com`; POSIX any executable.
   A `.cmd`/`.bat`/`.ps1` (cmd re-parses the text), an unresolvable name or any
   resolution error withdraws it, and the original scan runs.

Classifier inputs only: stand-in files in tmp_path are never executed.
"""
import os

import pytest

from liteharness import deny_floor

SHELLS = ["powershell", None, "bash"]


def _refused(command, tmp_path, shell):
    reason = deny_floor.refusal(command, tmp_path, shell=shell)
    return bool(reason) and "[owner-launcher]" in reason


def _path(tmp_path, monkeypatch, *folders, windows, pathext=".COM;.EXE;.BAT;.CMD"):
    made = []
    for n, files in enumerate(folders):
        folder = tmp_path / f"bin{n}"
        folder.mkdir()
        for name, mode in files.items():
            (folder / name).write_bytes(b"#!/bin/sh\n" if mode == "shebang" else b"stand-in")
            (folder / name).chmod(0o755 if mode in ("x", "shebang") else 0o644)
        made.append(str(folder))
    monkeypatch.setenv("PATH", os.pathsep.join(made))
    monkeypatch.setenv("PATHEXT", pathext)
    monkeypatch.setattr(deny_floor, "_is_windows", lambda: windows)


SCRIPT_LAUNCHES = [
    "awk 'BEGIN{system(\"run\")}' f",
    "awk 'BEGIN{system(\"run.bat\")}' f",
    "awk 'BEGIN{system(\"./run.bat\")}' f",
    "awk 'BEGIN { system(\"run\") }' f",
    "awk \"BEGIN{system(\"\"run\"\")}\" f",   # PowerShell's `""` escape: unmodelled, original scan
    "gawk 'BEGIN{system(\"run\")}' f",
    "mawk 'BEGIN{system(\"run\")}' f",
    "nawk 'BEGIN{system(\"run\")}' f",
    "awk 'BEGIN{print | \"run\"}' f",
    "awk 'BEGIN{\"run\" | getline x}' f",
    "sed -e 'e run' f",
    "sed -n '1e run' f",
    "gsed 's/a/b/e;e run' f",
    # a `;` or `|` inside the quoted script must not hide the head
    "awk 'BEGIN{x=1; system(\"run\")}' f",
    "awk 'BEGIN{print 1 | \"cat\"; system(\"run.bat\")}' f",
    "sed -e 's/a/b/;e run' f",
    "awk 'a|b; system(\"run\")' f",
    # head spelled with a path / .exe / behind a wrapper / as a payload
    "C:/tools/awk.exe 'BEGIN{system(\"run\")}' f",
    "awk.exe 'BEGIN{system(\"run\")}' f",
    "env awk 'BEGIN{system(\"run\")}' f",
    "nohup awk 'BEGIN{system(\"run\")}' f",
    "command sed -e 'e run' f",
    "bash -c 'awk \"BEGIN{system(\"\"run\"\")}\"'",
    # a real launch after a script head stays refused as before
    "awk '1' f; run.bat",
    "awk '1' f | run.bat",
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", SCRIPT_LAUNCHES)
def test_the_launcher_name_in_an_awk_or_sed_program_is_a_launch(tmp_path, command, shell):
    assert _refused(command, tmp_path, shell), (shell, command)


#: bash's backslash-escaped quote right after the name: the _LAUNCH word end never sees it.
ESCAPED_QUOTE = [
    "awk \"BEGIN{system(\\\"run\\\")}\" f",
    "awk \"BEGIN{system(\\\"run.bat\\\")}\" f",
    "gawk \"BEGIN{system(\\\"./run.bat\\\")}\" f",
    "awk \"BEGIN{x=1; system(\\\"run\\\")}\" f",
    "awk \"BEGIN{print 1 | \\\"cat\\\"; system(\\\"run\\\")}\" f",
    "env awk \"BEGIN{system(\\\"run\\\")}\" f",
    "C:/tools/awk.exe \"BEGIN{system(\\\"run\\\")}\" f",
    "sed -e \"e run\\\"\" f",
]


@pytest.mark.parametrize("shell", [None, "bash"])
@pytest.mark.parametrize("command", ESCAPED_QUOTE)
def test_an_escaped_quote_after_the_name_does_not_hide_the_launch(tmp_path, command, shell):
    assert _refused(command, tmp_path, shell), (shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", [
    # a bash program carried as ONE PowerShell word, or inside another shell's -c
    "bash -c 'awk \"BEGIN{system(\\\"run\\\")}\"'",
    "sh -c 'gawk \"BEGIN{x=1; system(\\\"run.bat\\\")}\" f'",
    "env bash -c 'sed -e \"e run\\\"\" f'",
])
def test_a_wrapped_bash_program_with_escaped_quotes_is_a_launch(tmp_path, command, shell):
    assert _refused(command, tmp_path, shell), (shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", [
    # another name, not the launcher: the exact-name guard runs before the program rule
    "awk 'BEGIN{system(\"rerun\")}' f",
    "awk 'BEGIN{system(\"myrun.bat\")}' f",
    "awk 'BEGIN{system(\"prerun\")}' f",
    "awk 'BEGIN{system(\"run2\")}' f",
    "awk 'BEGIN{system(\"x_run.bat\")}' f",
    "sed -e 'e rerun' f",
    "gawk 'BEGIN{print \"rerun.bat\"}' f",
    "env awk 'BEGIN{system(\"myrun\")}' f",
])
def test_a_neighbouring_name_in_a_program_is_not_the_launcher(tmp_path, command, shell):
    assert not _refused(command, tmp_path, shell), (shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", [
    # `run` + backslash + anything but a quote ends no token: a directory component, not the launcher
    "awk \"BEGIN{print \\\"C:/x/run\\\\sub\\\"}\" f",
    "awk \"BEGIN{print \\\"C:\\\\tools\\\\run\\\\bin\\\\x.exe\\\"}\" f",
    "awk 'BEGIN{print \"C:/x/run.bat\\\\\"}' f",
    "gawk \"BEGIN{x=\\\"run\\\\n\\\"}\" f",
    "sed -e \"s/a/run\\\\1/\" f",
])
def test_run_before_a_backslash_that_is_not_an_escaped_quote_is_not_a_launch(tmp_path, command, shell):
    assert not _refused(command, tmp_path, shell), (shell, command)


@pytest.mark.parametrize("shell", [None, "bash"])
@pytest.mark.parametrize("command", [
    "awk \"BEGIN{system(\\\"run\\\")}\" f",        # \" ends the token
    "awk $'BEGIN{system(\\'run\\')}' f",           # \' ends it too (ANSI-C quoting)
    "awk \"BEGIN{system(\\\"run.bat\\\")}\" f",
])
def test_only_an_escaped_quote_ends_the_escaped_token(tmp_path, command, shell):
    assert _refused(command, tmp_path, shell), (shell, command)


@pytest.mark.parametrize("command", [
    "awk \"BEGIN{system(\\\"rerun\\\")}\" f",
    "awk \"BEGIN{system(\\\"myrun.bat\\\")}\" f",
    "bash -c 'awk \"BEGIN{system(\\\"rerun\\\")}\"'",
])
@pytest.mark.parametrize("shell", SHELLS)
def test_a_neighbouring_name_before_an_escaped_quote_is_not_the_launcher(tmp_path, command, shell):
    assert not _refused(command, tmp_path, shell), (shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", [
    "awk -f run.bat f", "sed -f run.bat f", "sed -n 1p run.bat", "awk 1 run.bat",
])
def test_a_bare_operand_is_a_file_name(tmp_path, command, shell):
    assert not _refused(command, tmp_path, shell), (shell, command)


@pytest.mark.parametrize("command, powershell, unproven", [
    # proven PowerShell reads the quotes exactly; an unproven shell cannot, so it refuses
    ("awk '{print}' run.bat", False, True),
    ("sed -e 's/a/b/' 'C:/ExampleProjects/Tree/run.bat'", True, True),
    ("rg 'sed' 'x' run.bat", False, True),
])
def test_quoted_operand_and_script_ambiguity(tmp_path, command, powershell, unproven):
    assert _refused(command, tmp_path, "powershell") is powershell, command
    assert _refused(command, tmp_path, None) is unproven, command
    assert _refused(command, tmp_path, "bash") is unproven, command


PATTERN = "rg -n 'subprocess\\.(run|Popen)' src"


def test_a_native_exe_head_keeps_the_excuse_on_windows(tmp_path, monkeypatch):
    _path(tmp_path, monkeypatch, {"rg.exe": "f"}, windows=True)
    assert not _refused(PATTERN, tmp_path, "powershell")
    assert _refused(PATTERN, tmp_path, None)   # unproven shell: original scan


@pytest.mark.parametrize("folders", [
    [{"rg.cmd": "f"}],
    [{"rg.bat": "f"}],
    [{"rg.ps1": "f"}],
    [{"rg.exe": "f", "rg.cmd": "f"}],            # a shim beside the exe: cmd may win
    [{"rg.cmd": "f"}, {"rg.exe": "f"}],          # the shim is earlier on PATH
    [{"other.exe": "f"}],                        # unresolved
    [],                                          # empty PATH
], ids=["cmd", "bat", "ps1", "exe+cmd", "cmd-first", "unresolved", "empty"])
def test_a_batch_shim_or_unresolved_head_withdraws_the_excuse_on_windows(tmp_path, monkeypatch, folders):
    _path(tmp_path, monkeypatch, *folders, windows=True)
    assert _refused(PATTERN, tmp_path, "powershell")


def test_an_exe_earlier_on_path_than_a_shim_keeps_it(tmp_path, monkeypatch):
    _path(tmp_path, monkeypatch, {"rg.exe": "f"}, {"rg.cmd": "f"}, windows=True)
    assert not _refused(PATTERN, tmp_path, "powershell")


@pytest.mark.parametrize("pathext, folders, refused", [
    # only an extension PATHEXT lists is a command name; anything unsure withdraws
    (".CMD", [{"rg.exe": "f"}, {"rg.cmd": "f"}], True),         # the exe is not runnable by name; a later .cmd is
    (".CMD;.BAT", [{"rg.exe": "f"}], True),                      # native extension omitted, nothing else resolves
    (".COM;.CMD", [{"rg.exe": "f"}], True),                      # .exe omitted, no rg.com
    (".EXE;.CMD", [{"rg.cmd": "f"}, {"rg.exe": "f"}], True),     # a listed shim earlier on PATH
    (".EXE;.CMD", [{"rg.exe": "f"}, {"rg.cmd": "f"}], False),    # the listed exe is earlier
    (".EXE", [{"rg.exe": "f"}], False),
    (".COM;.EXE;.BAT;.CMD", [{"rg.com": "f"}], False),
    (".exe;.cmd", [{"rg.exe": "f"}], False),                     # case of PATHEXT does not matter
    (" .EXE ;.CMD", [{"rg.exe": "f"}], False),
    ("", [{"rg.exe": "f"}], True),                               # empty PATHEXT: nothing listed
    (";;", [{"rg.exe": "f"}], True),
], ids=["omit-exe-later-cmd", "omit-native", "omit-exe-no-com", "listed-cmd-first", "listed-exe-first",
        "exe-only", "com", "case", "padded", "empty", "separators"])
def test_only_an_extension_pathext_lists_counts(tmp_path, monkeypatch, pathext, folders, refused):
    _path(tmp_path, monkeypatch, *folders, windows=True, pathext=pathext)
    assert _refused(PATTERN, tmp_path, "powershell") is refused


def test_an_unset_pathext_withdraws_it_and_invents_no_default(tmp_path, monkeypatch):
    _path(tmp_path, monkeypatch, {"rg.exe": "f"}, windows=True)
    monkeypatch.delenv("PATHEXT")
    assert deny_floor._native_head("rg") is False
    assert _refused(PATTERN, tmp_path, "powershell")


@pytest.mark.parametrize("pathext", [".COM;.EXE;.BAT;.CMD", ".EXE"])
def test_a_set_pathext_still_resolves_a_native_exe(tmp_path, monkeypatch, pathext):
    _path(tmp_path, monkeypatch, {"rg.exe": "f"}, windows=True, pathext=pathext)
    assert deny_floor._native_head("rg") is True


@pytest.mark.parametrize("name", ["sls", "select-string"])
def test_cmdlets_are_named_not_resolved(tmp_path, monkeypatch, name):
    _path(tmp_path, monkeypatch, windows=True)
    assert not _refused(f"{name} -Pattern 'x(run)' file.txt", tmp_path, "powershell")


@pytest.mark.parametrize("mode", ["x", "shebang"])
def test_any_resolved_posix_executable_keeps_the_excuse(tmp_path, monkeypatch, mode):
    _path(tmp_path, monkeypatch, {"rg": mode}, windows=False)
    assert not _refused(PATTERN, tmp_path, "powershell")


@pytest.mark.parametrize("folders", [
    pytest.param([{"rg": "f"}], id="not-executable",   # a Windows host reports every file executable
                 marks=pytest.mark.skipif(os.name == "nt", reason="os.access(X_OK) is true for any file on Windows")),
    pytest.param([{"other": "x"}], id="unresolved"),
    pytest.param([], id="empty"),
])
def test_an_unresolved_or_non_executable_posix_head_withdraws_it(tmp_path, monkeypatch, folders):
    _path(tmp_path, monkeypatch, *folders, windows=False)
    assert _refused(PATTERN, tmp_path, "powershell")


@pytest.mark.parametrize("windows", [True, False])
def test_a_resolution_error_withdraws_to_the_original_scan(tmp_path, monkeypatch, windows):
    _path(tmp_path, monkeypatch, {"rg.exe" if windows else "rg": "x"}, windows=windows)

    def boom(*_a, **_k):
        raise OSError("unreadable")

    monkeypatch.setattr(deny_floor.Path, "is_file", boom)
    assert _refused(PATTERN, tmp_path, "powershell")


def test_the_path_variable_missing_withdraws_it(tmp_path, monkeypatch):
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setattr(deny_floor, "_is_windows", lambda: True)
    assert _refused(PATTERN, tmp_path, "powershell")


def test_resolution_answers_from_the_path_only(tmp_path, monkeypatch):
    _path(tmp_path, monkeypatch, {"rg.exe": "f"}, windows=True)
    assert deny_floor._native_head("rg") is True
    assert deny_floor._native_head("grep") is False
