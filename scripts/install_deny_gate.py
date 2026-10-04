"""Install the deny-floor gate for Claude Code (the orchestrator runs this; seats do not).

    python scripts/install_deny_gate.py [target-folder]

Copies liteharness/deny_gate.py and liteharness/deny_floor.py, byte for byte,
into ~/.claude/hooks/deny-floor/ (or the folder given), verifies both copies,
and prints the settings.json entry to add and the one to remove it again.

Why a folder outside this repo: this checkout is a working tree people switch
branches in, and a missing gate file makes python exit 2, which Claude Code
treats as BLOCK for every matched call on every seat. The installed pair
cannot vanish on a branch switch; tests/test_deny_floor.py fails when it
drifts from the repo copy.
"""
import json
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "liteharness"
FILES = ("deny_gate.py", "deny_floor.py")
DEFAULT_TARGET = Path.home() / ".claude" / "hooks" / "deny-floor"
MATCHER = "Bash|PowerShell|mcp__.*|Write|Edit|MultiEdit|NotebookEdit"


def install(target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(SOURCE / name, target / name)
        if (target / name).read_bytes() != (SOURCE / name).read_bytes():
            raise SystemExit(f"copy of {name} does not match its source")
    command = f"{Path(sys.executable).as_posix()} {(target / 'deny_gate.py').as_posix()}"
    return {"matcher": MATCHER,
            "hooks": [{"type": "command", "command": command, "timeout": 10}]}


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    entry = install(target)
    print(f"installed {', '.join(FILES)} -> {target}")
    print("ADD to hooks.PreToolUse in ~/.claude/settings.json (synchronous):")
    print(json.dumps(entry, indent=2))
    print("REMOVE: delete that one PreToolUse entry (matcher "
          f"{MATCHER!r}); the files in {target} are then inert.")
