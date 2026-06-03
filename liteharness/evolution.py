"""Per-agent evolution storage — append-only JSONL files.

Each polymathic agent has a file at resources/litesuite/evolution/{name}.jsonl.
Agents read their own file at runtime to recall past patterns, blind spots,
and strengths. /train and leader ratings write entries here.

Schema per line (JSON object):
  timestamp    — ISO-8601
  type         — evaluation | insight | blind_spot | strength
  source       — train | leader_rating | dream
  agent_name   — e.g. "feynman"
  session_id   — harness UUID
  task_context — what the agent was doing
  finding      — specific observation
  confidence   — HIGH | MED | LOW
  commit_refs  — list of SHAs (optional)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_EVOLUTION_DIR: Path | None = None


def _find_evolution_dir() -> Path:
    global _EVOLUTION_DIR
    if _EVOLUTION_DIR is not None:
        return _EVOLUTION_DIR

    # Walk up from this file to find resources/litesuite/evolution/
    anchor = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = anchor / "resources" / "litesuite" / "evolution"
        if candidate.is_dir():
            _EVOLUTION_DIR = candidate
            return candidate
        anchor = anchor.parent

    # Fallback: CWD-relative
    cwd_candidate = Path.cwd() / "resources" / "litesuite" / "evolution"
    if cwd_candidate.is_dir():
        _EVOLUTION_DIR = cwd_candidate
        return cwd_candidate

    raise FileNotFoundError("Cannot find resources/litesuite/evolution/ directory")


def _agent_file(agent_name: str) -> Path:
    return _find_evolution_dir() / f"{agent_name.lower()}.jsonl"


def append_evolution(agent_name: str, entry: dict) -> Path:
    """Append a single evolution entry to an agent's JSONL file."""
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    entry.setdefault("agent_name", agent_name.lower())

    fpath = _agent_file(agent_name)
    with fpath.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return fpath


def read_evolution(agent_name: str, last_n: int | None = None) -> list[dict]:
    """Read evolution entries for an agent. Returns newest-last order."""
    fpath = _agent_file(agent_name)
    if not fpath.exists():
        return []

    entries: list[dict] = []
    for line in fpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if last_n is not None:
        entries = entries[-last_n:]
    return entries


def list_agents() -> list[str]:
    """List all agents with evolution files."""
    evo_dir = _find_evolution_dir()
    return sorted(p.stem for p in evo_dir.glob("*.jsonl"))
