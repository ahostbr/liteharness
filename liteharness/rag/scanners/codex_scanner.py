"""CodexScanner — indexes Codex conversation transcripts from ~/.codex/.

Format is TBD — discovered on first run. Returns empty if dir doesn't exist.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import logging

from .base import BaseScanner

logger = logging.getLogger(__name__)

_CODEX_DIR = Path.home() / ".codex"
_CHUNK_SIZE = 8
_MAX_CHARS = 2048


def _try_parse_jsonl(path: str) -> list[dict]:
    messages = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = obj.get("role") or obj.get("type", "")
                content = obj.get("content") or obj.get("text", "")
                if role not in ("user", "assistant"):
                    continue
                text = content if isinstance(content, str) else json.dumps(content)
                if len(text.strip()) < 10:
                    continue
                messages.append({"type": role, "text": text[:_MAX_CHARS]})
    except Exception as exc:
        logger.warning(f"{path}: {exc}")
    return messages


def _try_parse_json(path: str) -> list[dict]:
    messages = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        items = data if isinstance(data, list) else data.get("messages", [])
        for obj in items:
            if not isinstance(obj, dict):
                continue
            role = obj.get("role") or obj.get("type", "")
            content = obj.get("content") or obj.get("text", "")
            if role not in ("user", "assistant"):
                continue
            text = content if isinstance(content, str) else json.dumps(content)
            if len(text.strip()) < 10:
                continue
            messages.append({"type": role, "text": text[:_MAX_CHARS]})
    except Exception as exc:
        logger.warning(f"{path}: {exc}")
    return messages


class CodexScanner(BaseScanner):
    def scan_paths(self) -> list[str]:
        if not _CODEX_DIR.exists():
            return []
        paths = []
        for ext in ("*.jsonl", "*.json"):
            for f in _CODEX_DIR.rglob(ext):
                paths.append(str(f))
        return paths

    def parse_file(self, path: str) -> list[dict]:
        if path.endswith(".jsonl"):
            messages = _try_parse_jsonl(path)
        else:
            messages = _try_parse_json(path)

        if not messages:
            return []

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0

        project = Path(path).parent.name
        chunks = []

        for i in range(0, len(messages), _CHUNK_SIZE):
            group = messages[i : i + _CHUNK_SIZE]
            content = "\n".join(
                f"[{m['type'].upper()}]: {m['text']}" for m in group
            )[:_MAX_CHARS * 2]
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
                "source_type": "codex",
                "project": project,
                "mtime": mtime,
            })

        return chunks
