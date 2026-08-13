"""
LiteHarness Config — Agent identity and settings.
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

HARNESS_ROOT = Path.home() / ".liteharness"
CONFIG_PATH = HARNESS_ROOT / "config.json"

# Bounded retry for the rename in atomic_write_json — see its docstring.
# 8 attempts with linear backoff spans ~0.7s, which covers the observed
# contention window without stalling a hook noticeably.
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF_S = 0.02


def get_root() -> Path:
    return HARNESS_ROOT


def ensure_root() -> None:
    HARNESS_ROOT.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    """Load config from ~/.liteharness/config.json"""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save(config: dict) -> None:
    """Save config to ~/.liteharness/config.json"""
    ensure_root()
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write JSON to `path` via temp-file + os.replace.

    Multiple processes write the same presence file concurrently (SessionStart
    register + the long-lived `watch` heartbeat). A plain write_text is not
    atomic, so interleaved writes produced corrupt "Extra data" files that the
    desktop's strict JSON.parse silently dropped. Writing to a per-pid temp file
    and renaming makes each write all-or-nothing.

    The rename itself can still fail: on Windows os.replace raises
    PermissionError (WinError 5/32) while another process holds the destination
    open, which the concurrent writers above do routinely — observed as a
    SessionStart traceback on a fresh spawn, 2026-08-08. Contention clears in
    milliseconds, so retry a bounded number of times; the bound is what stops a
    genuine permissions failure from spinning forever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def merge_presence_fields(path: Path, updates: dict) -> bool:
    """Read-merge-write a presence file, touching ONLY the keys in `updates`.

    Non-owner writers (heartbeats, cwd updates, recap scans) must never clobber
    fields that register/spawn set — atomic_write_json makes each write
    all-or-nothing, but a writer that read the file earlier and writes a full
    stale dict back still resurrects old tier/model (last-write-wins). This
    helper re-reads at write time so the window shrinks to the rename itself,
    and merges only the caller's own fields.

    Returns False without writing when the file is missing or unreadable —
    a non-owner writer must not resurrect a purged/deregistered agent.
    """
    if not path.exists():
        return False
    try:
        base = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    base.update(updates)
    atomic_write_json(path, base)
    return True


def _find_claude_session_id() -> Optional[str]:
    """
    Find the current Claude Code session ID by scanning conversation JSONLs.

    Follows the conversation-lookup pattern:
    1. Scan ~/.claude/projects/*/*.jsonl
    2. Try to match CWD to project directory name (Claude encodes paths with dashes)
    3. Within matching project, find most recently modified JSONL
    4. Fall back to most recent JSONL globally if no CWD match
    5. Must be active within last 5 minutes
    """
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return None

    cwd = os.getcwd()
    # Claude encodes project paths: C:\Projects\MyApp -> C--Projects-MyApp
    # or D:\Work\benchmark -> D--Work-benchmark
    cwd_encoded = cwd.replace(":\\", "--").replace("\\", "-").replace("/", "-")

    best_match = None  # Best match within CWD's project dir
    best_match_mtime = 0.0
    best_global = None  # Best match across all projects
    best_global_mtime = 0.0

    for project_dir in claude_projects.iterdir():
        if not project_dir.is_dir():
            continue
        # Skip noisy dirs
        if "litegauntlet" in project_dir.name or "AppData-Local-Temp" in project_dir.name:
            continue

        is_cwd_match = project_dir.name == cwd_encoded

        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue

            if is_cwd_match and mtime > best_match_mtime:
                best_match_mtime = mtime
                best_match = jsonl

            if mtime > best_global_mtime:
                best_global_mtime = mtime
                best_global = jsonl

    # Prefer CWD-scoped match, fall back to global
    candidate = best_match or best_global
    candidate_mtime = best_match_mtime if best_match else best_global_mtime

    if candidate and (time.time() - candidate_mtime < 43200):  # Active in last 12 hours
        return candidate.stem  # UUID filename without .jsonl

    return None


def get_agent_id() -> str:
    """
    Get or create this agent's identity.

    Priority:
    1. LITEHARNESS_AGENT_ID env var (explicitly set by orchestrator)
    2. CLI-provided session IDs (CLAUDE_SESSION_ID, etc.)
    3. Auto-detect from most recent Claude Code conversation JSONL
    4. Fall back to config-stored ID
    """
    # Check for explicit override
    explicit = os.environ.get("LITEHARNESS_AGENT_ID")
    if explicit:
        return explicit

    # Check LiteSuite-specific agent ID
    litesuite_id = os.environ.get("LITESUITE_AGENT_ID")
    if litesuite_id:
        return litesuite_id

    # Check Claude Code session ID
    claude_code_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if claude_code_session:
        return claude_code_session

    # Check CLI-provided session IDs
    session_id = (
        os.environ.get("LITECODE_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or os.environ.get("GEMINI_SESSION_ID")
        or os.environ.get("CODEX_SESSION_ID")
    )
    if session_id:
        return session_id

    # Auto-detect from Claude Code conversation files
    claude_id = _find_claude_session_id()
    if claude_id:
        return claude_id

    # Fall back to config-stored ID
    cfg = load()
    if "agent_id" in cfg:
        return cfg["agent_id"]

    # Generate and persist
    agent_id = f"lh-{uuid.uuid4().hex[:12]}"
    cfg["agent_id"] = agent_id
    save(cfg)
    return agent_id


def get_model() -> str:
    """Get the current model name from env or config."""
    return (
        os.environ.get("LITEHARNESS_MODEL")  # Set by hook from stdin JSON (e.g. Claude Code model.display_name)
        or os.environ.get("LITECODE_MODEL")
        or os.environ.get("CLAUDE_MODEL")
        or os.environ.get("GEMINI_MODEL")
        or os.environ.get("CODEX_MODEL")
        or load().get("model", "unknown")
    )


def get_cli() -> str:
    """Detect which CLI is running."""
    explicit = os.environ.get("LITEHARNESS_CLI")
    if explicit:
        return explicit
    if os.environ.get("LITECODE_SESSION_ID"):
        return "litecode"
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_VERSION"):
        return "claude-code"
    if os.environ.get("GEMINI_SESSION_ID"):
        return "gemini-cli"
    if os.environ.get("CODEX_SESSION_ID"):
        return "codex-cli"
    if os.environ.get("COPILOT_SESSION_ID"):
        return "copilot-cli"
    if os.environ.get("OPENCODE_SESSION_ID"):
        return "opencode"
    return "unknown"


def get_memory_nudge() -> dict:
    """Memory-nudge settings, merged over defaults.

    On by default (fleet-wide, 2026-07-14): the UserPromptSubmit hook emits a
    tiny MEMORY.md index pointer every `cadence` turns (default 2 = every other
    turn). Turn it off per-agent via `liteharness memory-nudge --off` (or
    config). `cadence` is the every-Nth-turn interval.
    """
    defaults = {"enabled": True, "cadence": 2}
    stored = load().get("memory_nudge")
    if isinstance(stored, dict):
        defaults.update(stored)
    return defaults


def detect_installed_clis() -> list[str]:
    """Detect which AI coding CLIs are installed."""
    import shutil

    clis = []
    cli_binaries = {
        "claude-code": "claude",
        "gemini-cli": "gemini",
        "codex-cli": "codex",
        "cursor": "cursor",
        "opencode": "opencode",
        "kilocode": "kilocode",
        "kiro": "kiro",
        "copilot-cli": "gh",
        "litecode": "litecode",
    }
    for name, binary in cli_binaries.items():
        if shutil.which(binary):
            # For copilot-cli, gh must exist but that's just the base CLI
            # Check for the copilot extension or copilot-cli binary
            if name == "copilot-cli":
                copilot_bin = shutil.which("copilot")
                copilot_dir = Path.home() / ".github" / "copilot-cli"
                if copilot_bin or copilot_dir.exists():
                    clis.append(name)
            else:
                clis.append(name)

    # Also check by config directory existence
    config_dirs = {
        "claude-code": Path.home() / ".claude",
        "gemini-cli": Path.home() / ".gemini",
        "cursor": Path.home() / ".cursor",
        "codex-cli": Path.home() / ".codex",
        "kilocode": Path.home() / ".kilocode",
        "opencode": Path.home() / ".config" / "opencode",
        "litecode": Path.home() / ".litecode",
    }
    for name, path in config_dirs.items():
        if path.exists() and name not in clis:
            clis.append(name)

    return clis

def get_memory_nudge() -> dict:
    """Memory-nudge settings, merged over defaults.

    Ported from the LiteSuite tree 2026-08-09. The two liteharness source trees had
    diverged: this one carries the installer + catalog, that one carried memory-nudge and
    nothing had reconciled them. Shipped hook configs reference
    `python -m liteharness.hooks memory-nudge`, so every UserPromptSubmit printed
    "Unknown action: memory-nudge" against an install of THIS package.

    The UserPromptSubmit hook emits a tiny MEMORY.md index pointer every `cadence` turns.
    """
    defaults = {"enabled": True, "cadence": 2}
    stored = load().get("memory_nudge")
    if isinstance(stored, dict):
        defaults.update(stored)
    return defaults
