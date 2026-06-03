"""ClaudeScanner — indexes Claude Code JSONL conversation transcripts.

Scans ~/.claude/projects/*/*.jsonl (10K+ files).
Groups 8 messages per chunk, truncates each message to 2048 chars.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import logging

from .base import BaseScanner

logger = logging.getLogger(__name__)

_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_SKIP_PATTERNS = ["litegauntlet", "AppData-Local-Temp"]
_CHUNK_SIZE = 8          # messages per chunk
_MAX_CHARS = 2048        # per message


def _extract_text(content) -> str:
    """Extract searchable text from a message content field."""
    if isinstance(content, str):
        return content[:_MAX_CHARS]
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                name = block.get("name", "")
                if name:
                    parts.append(f"[tool:{name}]")
                inp = block.get("input", {})
                if isinstance(inp, dict):
                    for v in inp.values():
                        if isinstance(v, str) and len(v) < 1000:
                            parts.append(v)
            elif btype == "tool_result":
                result = block.get("content", "")
                if isinstance(result, str):
                    parts.append(result[:500])
                elif isinstance(result, list):
                    for sub in result:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", "")[:500])
        return "\n".join(parts)[:_MAX_CHARS]
    return str(content)[:_MAX_CHARS]


def _parse_messages(file_path: str) -> list[dict]:
    """Yield parsed messages from a JSONL file."""
    messages = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") not in ("user", "assistant"):
                    continue
                if obj.get("isMeta"):
                    continue
                message = obj.get("message", {})
                text = _extract_text(message.get("content", ""))
                if not text or len(text.strip()) < 10:
                    continue
                messages.append({
                    "uuid": obj.get("uuid", ""),
                    "timestamp": obj.get("timestamp", ""),
                    "type": obj.get("type"),
                    "text": text,
                })
    except Exception as exc:
        logger.warning(f"{file_path}: {exc}")
    return messages


def _project_from_path(path: str) -> str:
    """Extract project name from ~/.claude/projects/<project>/<file>.jsonl."""
    try:
        p = Path(path)
        return p.parent.name
    except Exception:
        return ""


class ClaudeScanner(BaseScanner):
    def scan_paths(self) -> list[str]:
        if not _PROJECTS_DIR.exists():
            return []
        paths = []
        for f in _PROJECTS_DIR.rglob("*.jsonl"):
            if any(skip in str(f) for skip in _SKIP_PATTERNS):
                continue
            paths.append(str(f))
        return paths

    def parse_file(self, path: str) -> list[dict]:
        messages = _parse_messages(path)
        if not messages:
            return []

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0

        project = _project_from_path(path)
        chunks = []

        for i in range(0, len(messages), _CHUNK_SIZE):
            group = messages[i : i + _CHUNK_SIZE]
            text_parts = []
            for m in group:
                prefix = "USER" if m["type"] == "user" else "ASSISTANT"
                text_parts.append(f"[{prefix}]: {m['text'][:_MAX_CHARS]}")
            content = "\n".join(text_parts)[:_MAX_CHARS * 2]

            chunk_index = i // _CHUNK_SIZE
            chunk_id = hashlib.sha1(f"{path}:{chunk_index}".encode()).hexdigest()

            chunks.append({
                "chunk_id": chunk_id,
                "source_path": path,
                "start_line": i,
                "end_line": min(i + _CHUNK_SIZE - 1, len(messages) - 1),
                "content": content,
                "language": "",
                "chunk_type": "conversation",
                "source_type": "claude",
                "project": project,
                "mtime": mtime,
            })

        return chunks
