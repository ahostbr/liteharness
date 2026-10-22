"""Claude Code PreToolUse gate for the deny floor. RUN BY PATH, never as `-m`.

    python <liteharness-oss>/liteharness/deny_gate.py      (stdin: the hook JSON)

Why by path: `python -m liteharness.<anything>` imports the liteharness package
first, so a syntax error in it, a missing dependency or a bad editable install
exits 1 before any code here runs, and Claude Code treats exit 1 as
non-blocking: the floor would fail OPEN, the same silent-success shape as the
2026-09-26 16:45 command it exists to stop. So this file imports only the
standard library, removes its own folder from sys.path (a sibling module must
not shadow the stdlib), and loads deny_floor.py by file location INSIDE the try.

On any internal error it falls back to a dependency-free look at the raw text:
a recursive-delete-shaped command is BLOCKED (exit 2, reason on stderr), and
anything else passes (exit 0) with the crash logged. A broken gate cannot run
the dangerous class, and cannot brick every seat either. The same holds for the
file tools (T1085): a crash blocks only a Write/Edit/MultiEdit/NotebookEdit whose
file is named jobs.json; every other edit passes.

File tools (T1085): the matcher includes Write|Edit|MultiEdit|NotebookEdit, and
their path is judged by the jobs-file rule ALONE. A path not named jobs.json
returns before deny_floor.py is even loaded, so ordinary edits pay only the
process start and the JSON parse, and cannot reach the floor to crash in it.

Opt-in (T0427): with LITEHARNESS_DENY_FULL_SUITES=1 a Bash command that runs a whole
test suite is refused too, after the floor has had its say. Off by default; see the
"Opt-in" block below. It is inside the same try, so a crash in it falls to the fallback.

⚠️ INSTALL ORDER: if this FILE is missing, python exits 2, which Claude Code
treats as BLOCK -- every matched tool call on every Claude seat stops. With the
file tools in the matcher that is EVERY Write/Edit too (Dijkstra 0fd0f2d0). Copy
the files first, run one dry hook invocation with a Write payload (expect exit
0), and only then edit settings.json.
"""
import sys

if __name__ == "__main__" and sys.path:
    del sys.path[0]  # this folder: liteharness/*.py must not shadow the stdlib

import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import traceback  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

FLOOR = Path(__file__).resolve().with_name("deny_floor.py")

#: The fallback: recursive-delete SHAPED, judged on text alone. It over-blocks
#: on purpose (it only runs when the real floor could not).
_RECURSIVE_DELETE = re.compile(
    r"(?ix)"
    r"(?<![\w.$-])(?:remove-item|ri|rm|rd|rmdir|del|erase)(?:\.exe)?(?![\w.:\\-])[^;&|\n]*?"
    r"(?:\s-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?(?![\w-])"      # -r .. -recurse
    r"|\s-(?=[a-z]{1,3}(?![\w-]))[a-z]*r[a-z]*(?![\w-])"          # -rf, -fr, -Rf
    r"|\s--recursive\b|\s/s\b)"
    r"|\bshutil\.rmtree\b|\brimraf\b"
    r"|\bgit\s+clean\b[^;&|\n]*\s-[a-z]*[xd]"
    r"|\bgit\s+worktree\s+remove\b[^;&|\n]*(?:--force\b|\s-f\b)"
    r"|\bfind\b[^;&|\n]*\s-delete\b")
#: ...and ANY delete verb in text that also names a home variable, wherever the
#: recurse flag sits (`gci ~ -Recurse | Remove-Item -Force`), or with none at
#: all (`del /q %USERPROFILE%`).
_DELETE_VERB = re.compile(
    r"(?i)(?<![\w.$-])(?:remove-item|ri|rm|rd|rmdir|del|erase|rimraf)(?:\.exe)?(?![\w.:\\-])"
    r"|\bshutil\.rmtree\b|\s-delete\b")
_HOME_TOKEN = re.compile(
    r"(?i)(?<![\w/\\])~(?![\w])|\$\{?home\b|\$\{?env:(?:userprofile|home)\b|%userprofile%")


def _dangerous(text: str) -> bool:
    return bool(_RECURSIVE_DELETE.search(text)
                or (_DELETE_VERB.search(text) and _HOME_TOKEN.search(text)))


def _log_path() -> Path:
    root = os.environ.get("LITEHARNESS_HOME") or str(Path.home() / ".liteharness")
    return Path(root) / "deny_gate.log"


def _log(text: str) -> Path:
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {text}\n")
    except Exception:
        pass  # the log must never decide the outcome: this runs INSIDE the
        # crash handler, and an exception here would exit 1, which fails open
    return path


#: Keystroke streams a tool types into a live shell, judged WHENEVER they are
#: strings: a PTY submits on "\r" as well as "\n", and terminal action=submit
#: presses Enter itself (review a93de8a9 R3), so "holds a newline" missed both.
#: A stopgap that over-blocks is cheap. STOPGAP (review 2be2a62c): the real fix
#: is LiteSuite's PTY write door, its own card.
_KEYSTROKE_KEYS = ("text", "input", "data")
#: Claude Code's file tools: judged by their path, under the jobs-file rule only.
_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _written_path(payload) -> str | None:
    """The file a Write/Edit/MultiEdit/NotebookEdit call writes, or None."""
    if not isinstance(payload, dict) or payload.get("tool_name") not in _FILE_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return path if isinstance(path, str) and path else None


def _names_jobs_file(path: str) -> bool:
    """Could this path be jobs.json, in ANY Windows alias (`::$DATA`, `:x`, a
    trailing dot or space: Dijkstra 69c7c209 A1)? A superset test, on purpose:
    the gate may not load the floor before this short-circuit (binding a), nor
    trust it after a crash, so it asks for the substring. deny_floor's
    canonical_name returns a PREFIX of the lowered last segment, so every name it
    calls jobs.json contains "jobs.json" here; the floor makes the real decision."""
    return "jobs.json" in path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _command(payload) -> str | None:
    """What the tool will execute: `command`, else a submitted keystroke stream."""
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if isinstance(command, str):
        return command
    typed = [v for k in _KEYSTROKE_KEYS if isinstance(v := tool_input.get(k), str)]
    return "\n".join(typed) or None


#: ── Opt-in: refuse a whole test suite (T0427, folded from a per-machine hook) ──
#: OFF unless LITEHARNESS_DENY_FULL_SUITES is truthy ("1", "true", "yes", "on").
#: An env var, not a config key: this file is stdlib-only and runs on every call,
#: the harness config is YAML (no stdlib parser), and a flag read from a file that
#: can be missing or malformed is one more way for the gate to misjudge. The
#: harness that launches the seat sets it; LITEHARNESS_ALLOW_FULL_SUITE=1 in the
#: same environment lifts it for the one deliberate, serialized run.
#:
#: A whole suite is a runner (pytest, vitest, playwright test, bun test, cargo test,
#: bun run test*, npm|pnpm|yarn [run] test*) in COMMAND position with no target.
#: A file, or a word carrying a shell expansion ($FILES, "$@", $(cat list)), is a
#: target. Judged on shell words, not on text: `grep -E 'vitest\|pytest'` and a
#: heredoc body are prose. Bash only. deny_floor.py is not consulted or changed.
#: Same flag, same escape: a test run that names its target is still refused while
#: another vitest/playwright process is alive (one runner at a time). Only a test
#: run reads the process list; every other Bash call pays nothing.
_SUITES_FLAG = "LITEHARNESS_DENY_FULL_SUITES"
_SUITES_ESCAPE = "LITEHARNESS_ALLOW_FULL_SUITE"
_ENV_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_SRC_FILE = re.compile(r"\.(ts|tsx|js|mjs)$")
_PY_TARGET = re.compile(r"\.py(::|$)")
_TEST_SCRIPT = re.compile(r"test(\W|$)")
_VAR_START = "_@*#?!-"
_SUITES_REFUSAL = (
    f"Refused: this runs a full test suite ({_SUITES_FLAG} is on). Run only the test files you "
    "touched, e.g. `python -m pytest path/to/test_file.py` or `npx vitest run path/to/file.test.ts`, "
    f"plus the typecheck. To run the full suite once on purpose, set {_SUITES_ESCAPE}=1 in the "
    "environment of that session.")
_RUNNER_REFUSAL = ("Refused: another test runner is alive on this machine (pids %s). One runner at a "
                   "time: wait for it to finish, then re-run your single file.")
_IS_WINDOWS = sys.platform == "win32"


def _balanced(cmd: str, i: int, open_c: str, close_c: str) -> int:
    """Index just past the group whose opener is at cmd[i]; quotes inside are skipped."""
    depth, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            j = cmd.find(c, i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _heredoc_delim(cmd: str, i: int):
    """After `<<`: returns (delimiter, strip_tabs, index past the delimiter word)."""
    n = len(cmd)
    strip = cmd[i:i + 1] == "-"
    if strip:
        i += 1
    while i < n and cmd[i] in " \t":
        i += 1
    word = []
    while i < n and cmd[i] not in " \t\n;&|()<>":
        c = cmd[i]
        if c in "'\"":
            j = cmd.find(c, i + 1)
            j = n if j < 0 else j
            word.append(cmd[i + 1:j])
            i = j + 1
            continue
        if c == "\\" and i + 1 < n:
            i += 1
            c = cmd[i]
        word.append(c)
        i += 1
    return "".join(word), strip, i


def _skip_heredocs(cmd: str, i: int, pending) -> int:
    """cmd[i] is the first char after a newline; skip each pending heredoc body."""
    n = len(cmd)
    for delim, strip in pending:
        while i < n:
            j = cmd.find("\n", i)
            line = cmd[i:n if j < 0 else j]
            i = n if j < 0 else j + 1
            if (line.lstrip("\t") if strip else line) == delim:
                break
    return i


def _is_var(cmd: str, i: int) -> bool:
    """cmd[i] is a `$` that starts a plain variable reference ($x, $1, $@ ...)."""
    nxt = cmd[i + 1:i + 2]
    return bool(nxt) and (nxt.isalnum() or nxt in _VAR_START)


def _shell_segments(cmd: str):
    """Shell-ish tokenizer. Returns segments, each a list of (text, dynamic) words; `dynamic`
    marks a word carrying an expansion ($x, ${x}, $(..), `..`) outside single quotes. Segments
    split on UNQUOTED newline ; & | ( ). Heredoc bodies and comments are dropped."""
    segs, words = [], []
    cur, has, dyn = [], False, False
    pending = []
    i, n = 0, len(cmd)

    def end_word():
        nonlocal cur, has, dyn
        if has:
            words.append(("".join(cur), dyn))
        cur, has, dyn = [], False, False

    def end_seg():
        nonlocal words
        end_word()
        if words:
            segs.append(words)
        words = []

    def expansion(j):
        """cmd[j] is `$(`, `${` or a backtick: the index past it (j itself when it is none)."""
        if cmd[j] == "`":
            k = cmd.find("`", j + 1)
            return n if k < 0 else k + 1
        if cmd[j:j + 2] == "$(":
            return _balanced(cmd, j + 1, "(", ")")
        if cmd[j:j + 2] == "${":
            return _balanced(cmd, j + 1, "{", "}")
        return j

    while i < n:
        c = cmd[i]
        if c == "\n":
            end_seg()
            i += 1
            if pending:
                i = _skip_heredocs(cmd, i, pending)
                pending = []
        elif c in " \t\r":
            end_word()
            i += 1
        elif c == "#" and not has:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        elif c in ";|()":
            end_seg()
            i += 1
        elif c == "&":
            if (cur and cur[-1] in "<>") or cmd[i + 1:i + 2] == ">":   # 2>&1, &>file
                cur.append(c)
                has = True
            else:
                end_seg()
            i += 1
        elif c == "<" and cmd[i:i + 2] == "<<" and cmd[i:i + 3] != "<<<":
            delim, strip, i = _heredoc_delim(cmd, i + 2)
            if delim:
                pending.append((delim, strip))
        elif c == "'":
            j = cmd.find("'", i + 1)
            j = n if j < 0 else j
            cur.append(cmd[i + 1:j])
            has = True
            i = j + 1
        elif c == '"':
            has = True
            i += 1
            while i < n and cmd[i] != '"':
                d = cmd[i]
                if d == "\\" and cmd[i + 1:i + 2] and cmd[i + 1] in '"\\$`':
                    cur.append(cmd[i + 1])
                    i += 2
                elif d in "$`" and expansion(i) != i:
                    j = expansion(i)
                    cur.append(cmd[i:j])
                    dyn = True
                    i = j
                else:
                    if d == "$" and _is_var(cmd, i):
                        dyn = True
                    cur.append(d)
                    i += 1
            i += 1
        elif c == "\\" and i + 1 < n:
            cur.append(cmd[i + 1])
            has = True
            i += 2
        elif c in "$`" and expansion(i) != i:
            j = expansion(i)
            cur.append(cmd[i:j])
            has = dyn = True
            i = j
        else:
            if c == "$" and _is_var(cmd, i):
                dyn = True
            cur.append(c)
            has = True
            i += 1
    end_seg()
    return segs


def _is_full_suite(seg):
    """None when the segment is not a test run; else True if the run is a whole suite."""
    k = 0
    while k < len(seg) and _ENV_ASSIGN.match(seg[k][0]):
        k += 1
    if k < len(seg) and seg[k][0] in ("bunx", "npx"):
        k += 1
    elif k + 1 < len(seg) and seg[k][0] == "python" and seg[k + 1][0] == "-m":
        k += 2
    if k >= len(seg):
        return None
    name, rest = seg[k][0], seg[k + 1:]
    texts = [t for t, _ in rest]
    expands = any(d for _, d in rest)
    has_src = expands or any(_SRC_FILE.search(t) for t in texts)
    if name == "vitest":
        return not has_src
    if name == "playwright":
        return None if texts[:1] != ["test"] else not has_src
    if name == "pytest":
        return not (expands or any(_PY_TARGET.search(t) for t in texts))
    if name == "cargo":
        if texts[:1] != ["test"]:
            return None
        return not (expands or any(re.match(r"\w", t) for t in texts[1:]))
    if name == "bun":
        if texts[:1] == ["run"] and len(texts) > 1 and _TEST_SCRIPT.match(texts[1]):
            return True
        return not has_src if texts[:1] == ["test"] else None
    if name in ("npm", "pnpm", "yarn"):
        script = texts[1:] if texts[:1] == ["run"] else texts
        return True if script and _TEST_SCRIPT.match(script[0]) else None
    return None


def _live_runner_pids() -> list[str]:
    """Pids of live vitest/playwright runners (bun.exe or node.exe) on this machine.

    A coordination guard, not a safety floor: where it cannot look (not Windows, no
    PowerShell, a read that fails or times out) it returns [] and the call is allowed."""
    if not _IS_WINDOWS:
        return []
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='bun.exe' OR Name='node.exe'\" | "
             "Where-Object { $_.CommandLine -match 'vitest|playwright' } | "
             "ForEach-Object { $_.ProcessId }"],
            capture_output=True, text=True, timeout=8,
        ).stdout.split()
    except Exception:
        return []


def _full_suite_refusal(payload) -> str | None:
    """The refusal text when the opt-in flag is on and a Bash command is a test run that is
    either a whole suite or starts while another seat's runner is alive. A command that is
    not a test run is judged on text alone: no process is read."""
    if os.environ.get(_SUITES_FLAG, "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    if os.environ.get(_SUITES_ESCAPE) == "1" or payload.get("tool_name") != "Bash":
        return None
    command = _command(payload)
    runs = [r for s in _shell_segments(command or "") if (r := _is_full_suite(s)) is not None]
    if not runs:
        return None
    if any(runs):
        return _SUITES_REFUSAL
    pids = _live_runner_pids()
    return _RUNNER_REFUSAL % ",".join(pids[:6]) if pids else None


def _load_floor():
    spec = importlib.util.spec_from_file_location("liteharness_deny_floor", FLOOR)
    floor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(floor)
    return floor


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def decide(payload: dict) -> dict | None:
    """The Claude Code decision for one hook payload, from the real floor."""
    written = _written_path(payload)
    if written is not None:
        # BINDING (Dijkstra 0fd0f2d0 a): an edit of any file not named jobs.json
        # returns HERE, before the floor is loaded.
        if not _names_jobs_file(written):
            return None
        reason = _load_floor().write_refusal(written, payload.get("cwd") or os.getcwd())
        return _deny(reason) if reason else None
    command = _command(payload)
    if not command:
        return None
    floor = _load_floor()
    # The session's cwd AND the tool's own (the MCP shells take a `cwd`): the
    # command is refused if it is refused against either.
    session = payload.get("cwd") or os.getcwd()
    tool_cwd = payload["tool_input"].get("cwd")
    bases = [session] + ([os.path.join(session, tool_cwd)] if isinstance(tool_cwd, str)
                         and tool_cwd else [])
    reason = next(filter(None, (floor.refusal(command, base) for base in bases)), None)
    reason = reason or _full_suite_refusal(payload)
    return _deny(reason) if reason else None


def main() -> int:
    raw = ""
    try:
        # BYTES, decoded here: sys.stdin.read() uses the locale codepage, and
        # under cp1252 a byte like 0x9D raised BEFORE `raw` was assigned, so the
        # fallback saw "" and allowed the call.
        raw = "" if sys.stdin.isatty() else sys.stdin.buffer.read().decode("utf-8", "replace")
        decision = decide(json.loads(raw or "{}"))
        if decision:
            print(json.dumps(decision))
        return 0
    except Exception:
        # A payload that PARSES is judged on what it executes, and nothing else:
        # an inbox send that merely MENTIONS `rm -rf ~` is not a delete (G3).
        # Only an unparseable payload is judged as raw text.
        try:
            parsed = json.loads(raw)
        except Exception:
            text = raw
        else:
            written = _written_path(parsed)
            if written is not None and _names_jobs_file(written):
                path = _log(f"deny-floor hook failed (BLOCKED jobs.json write): {written!r}\n"
                            f"{traceback.format_exc()}")
                print(f"deny-floor hook failed, refusing a write to {written}; see {path}",
                      file=sys.stderr)
                return 2
            text = _command(parsed) or ""
        dangerous = _dangerous(text)
        path = _log(f"deny-floor hook failed ({'BLOCKED' if dangerous else 'allowed'}): "
                    f"{text[:300]!r}\n{traceback.format_exc()}")
        if dangerous:
            print(f"deny-floor hook failed, refusing a recursive delete; see {path}",
                  file=sys.stderr)
            return 2
        return 0


if __name__ == "__main__":
    sys.exit(main())
