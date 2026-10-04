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
