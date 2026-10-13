"""GitScanner — indexes git commit history as searchable chunks.

Each commit becomes one chunk: subject + body + trailer metadata.
Extracts Agent-Name, Agent-ID, Agent-Role (or the older Agent-Tier), Task-id trailers.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path

from ... import roles
from .base import BaseScanner

logger = logging.getLogger(__name__)

_TRAILER_KEYS = ["Agent-Name", "Agent-ID", "Agent-Role", "Agent-Tier", "Task-id"]
_VIRTUAL_PATH = "__git_log__"


class GitScanner(BaseScanner):
    def __init__(self, repo_path: str | None = None):
        self.repo_path = repo_path or str(Path.cwd())

    def scan_paths(self) -> list[str]:
        # Git log is a single virtual source — always needs reindex check via mtime
        return [_VIRTUAL_PATH]

    def needs_reindex(self, path: str, storage) -> bool:
        # Re-index if no chunks exist or if it's been more than 5 minutes
        existing = storage.get_chunks_by_source(_VIRTUAL_PATH)
        if not existing:
            return True
        stored_mtime = existing[0].get("mtime", 0)
        return (time.time() - stored_mtime) > 300

    def parse_file(self, path: str) -> list[dict]:
        try:
            result = subprocess.run(
                [
                    "git", "log",
                    "--format=COMMIT:%H%n%(trailers:key=Agent-Name,key=Agent-ID,key=Agent-Role,key=Agent-Tier,key=Task-id,separator=%x00)%nEND_COMMIT",
                    "--stat",
                    "--max-count=500",
                    "--all",
                ],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            logger.warning(f"git log failed: {exc}")
            return []

        if result.returncode != 0:
            return []

        chunks = []
        now = time.time()
        current_commit: dict = {}
        body_lines: list[str] = []
        in_trailers = False

        for line in result.stdout.splitlines():
            if line.startswith("COMMIT:"):
                if current_commit:
                    chunks.append(_build_chunk(current_commit, body_lines, now))
                current_commit = {"sha": line[7:]}
                body_lines = []
                in_trailers = True
            elif line == "END_COMMIT":
                in_trailers = False
            elif in_trailers:
                # Git separates trailers, but emits no separator for a single trailer.
                for trailer in line.split("\x00"):
                    trailer = trailer.strip()
                    if ":" in trailer:
                        k, _, v = trailer.partition(":")
                        k = k.strip()
                        v = v.strip()
                        if k in _TRAILER_KEYS:
                            current_commit[k.lower().replace("-", "_")] = v
            else:
                body_lines.append(line)

        if current_commit:
            chunks.append(_build_chunk(current_commit, body_lines, now))

        return chunks


def _build_chunk(commit: dict, body_lines: list[str], mtime: float) -> dict:
    sha = commit.get("sha", "unknown")
    body = "\n".join(body_lines).strip()
    agent_name = commit.get("agent_name", "")
    agent_role = roles.canonical(commit.get("agent_role") or commit.get("agent_tier"), "")
    task_id = commit.get("task_id", "")

    meta_parts = []
    if agent_name:
        meta_parts.append(f"Agent: {agent_name}")
    if agent_role:
        meta_parts.append(f"Role: {agent_role}")
    if task_id:
        meta_parts.append(f"Task: {task_id}")

    content_parts = [f"Commit: {sha}"]
    if body:
        content_parts.append(body)
    if meta_parts:
        content_parts.append(" | ".join(meta_parts))

    content = "\n".join(content_parts)[:4096]
    chunk_id = hashlib.sha1(sha.encode()).hexdigest()

    return {
        "chunk_id": chunk_id,
        "source_path": _VIRTUAL_PATH,
        "start_line": 0,
        "end_line": 0,
        "content": content,
        "language": "",
        "chunk_type": "pattern",
        "source_type": "git",
        "project": "",
        "mtime": mtime,
    }
