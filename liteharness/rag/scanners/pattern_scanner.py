"""PatternScanner — indexes .liteharness/patterns.jsonl (project-local).

Each entry becomes one chunk. Agent tier comes from explicit record metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import logging

from .base import BaseScanner

logger = logging.getLogger(__name__)

_PATTERNS_FILE = Path(".liteharness") / "patterns.jsonl"


class PatternScanner(BaseScanner):
    def __init__(self, repo_path: str | None = None):
        self.repo_path = repo_path or str(Path.cwd())

    def _patterns_path(self) -> Path:
        return Path(self.repo_path) / _PATTERNS_FILE

    def scan_paths(self) -> list[str]:
        p = self._patterns_path()
        if not p.exists():
            return []
        return [str(p)]

    def parse_file(self, path: str) -> list[dict]:
        chunks = []
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for idx, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue

                    name = entry.get("name") or entry.get("session") or ""
                    description = entry.get("description") or entry.get("content") or ""
                    tier = entry.get("tier") or "worker"
                    project = entry.get("project") or ""

                    content_parts = []
                    if name:
                        content_parts.append(f"Pattern: {name}")
                    if description:
                        content_parts.append(description)
                    if tier:
                        content_parts.append(f"Tier: {tier}")
                    content = "\n".join(content_parts)[:4096]

                    chunk_id = hashlib.sha1(f"{path}:{idx}".encode()).hexdigest()
                    chunks.append({
                        "chunk_id": chunk_id,
                        "source_path": path,
                        "start_line": idx,
                        "end_line": idx,
                        "content": content,
                        "language": "",
                        "chunk_type": "pattern",
                        "source_type": "pattern",
                        "project": project,
                        "mtime": mtime,
                    })
        except Exception as exc:
            logger.warning(f"{path}: {exc}")

        return chunks
