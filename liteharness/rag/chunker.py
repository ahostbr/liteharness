"""Semantic and code-aware chunking for RAG.

Provides:
- SimpleChunker: fixed-size with overlap (fallback)
- CodeChunker: language-aware (splits at definitions)
- SemanticChunker: embedding-based topic breaks
- AutoChunker: auto-selects by file extension
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


LANGUAGE_PATTERNS: Dict[str, List[str]] = {
    ".py": [
        r"^class\s+\w+",
        r"^def\s+\w+",
        r"^async\s+def\s+\w+",
        r"^@\w+",
    ],
    ".ts": [
        r"^export\s+(default\s+)?(class|function|const|interface|type)",
        r"^(class|function|const|interface|type)\s+\w+",
        r"^async\s+function",
    ],
    ".tsx": [
        r"^export\s+(default\s+)?(class|function|const|interface|type)",
        r"^(class|function|const|interface|type)\s+\w+",
    ],
    ".js": [
        r"^(class|function|const)\s+\w+",
        r"^export\s+(default\s+)?(class|function|const)",
        r"^module\.exports",
    ],
    ".jsx": [
        r"^(class|function|const)\s+\w+",
        r"^export\s+(default\s+)?(class|function|const)",
    ],
    ".md": [
        r"^#{1,3}\s+",
        r"^---$",
    ],
    ".go": [
        r"^func\s+",
        r"^type\s+\w+\s+(struct|interface)",
    ],
    ".rs": [
        r"^(pub\s+)?(fn|struct|enum|impl|trait|mod)\s+",
    ],
    ".yaml": [r"^\w+:$"],
    ".yml": [r"^\w+:$"],
    ".toml": [r"^\[\w+"],
}


class SimpleChunker:
    def __init__(self, chunk_size: int = 100, overlap: int = 10):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, content: str, path: str) -> List[Chunk]:
        lines = content.splitlines()
        chunks: List[Chunk] = []
        i = 0
        while i < len(lines):
            end = min(i + self.chunk_size, len(lines))
            chunk_text = "\n".join(lines[i:end])
            chunks.append(Chunk(
                chunk_id=f"{path}:{i+1}-{end}",
                path=path,
                start_line=i + 1,
                end_line=end,
                text=chunk_text,
            ))
            i += self.chunk_size - self.overlap
            if i >= len(lines):
                break
        return chunks


class CodeChunker:
    def __init__(
        self,
        max_chunk_size: int = 150,
        min_chunk_size: int = 20,
        overlap: int = 5,
    ):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap = overlap
        self.simple_chunker = SimpleChunker(chunk_size=max_chunk_size, overlap=overlap)

    def _get_patterns(self, ext: str) -> List[re.Pattern]:
        patterns = LANGUAGE_PATTERNS.get(ext.lower(), [])
        return [re.compile(p, re.MULTILINE) for p in patterns]

    def _find_boundaries(self, lines: List[str], patterns: List[re.Pattern]) -> List[int]:
        boundaries = [0]
        for i, line in enumerate(lines):
            for pattern in patterns:
                if pattern.match(line):
                    if i > 0:
                        boundaries.append(i)
                    break
        boundaries.append(len(lines))
        return sorted(set(boundaries))

    def chunk(self, content: str, path: str) -> List[Chunk]:
        ext = Path(path).suffix.lower()
        patterns = self._get_patterns(ext)
        if not patterns:
            return self.simple_chunker.chunk(content, path)
        lines = content.splitlines()
        if not lines:
            return []
        boundaries = self._find_boundaries(lines, patterns)
        chunks: List[Chunk] = []
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            while start < end:
                chunk_end = min(start + self.max_chunk_size, end)
                chunk_text = "\n".join(lines[start:chunk_end])
                chunks.append(Chunk(
                    chunk_id=f"{path}:{start+1}-{chunk_end}",
                    path=path,
                    start_line=start + 1,
                    end_line=chunk_end,
                    text=chunk_text,
                    metadata={"language": ext.lstrip(".")},
                ))
                start = chunk_end
        return self._merge_small_chunks(chunks)

    def _merge_small_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        if len(chunks) <= 1:
            return chunks
        merged: List[Chunk] = []
        current: Optional[Chunk] = None
        for chunk in chunks:
            if current is None:
                current = chunk
                continue
            current_lines = current.end_line - current.start_line + 1
            chunk_lines = chunk.end_line - chunk.start_line + 1
            if current_lines < self.min_chunk_size:
                if current_lines + chunk_lines <= self.max_chunk_size:
                    current = Chunk(
                        chunk_id=f"{current.path}:{current.start_line}-{chunk.end_line}",
                        path=current.path,
                        start_line=current.start_line,
                        end_line=chunk.end_line,
                        text=current.text + "\n" + chunk.text,
                        metadata=current.metadata,
                    )
                    continue
            merged.append(current)
            current = chunk
        if current:
            merged.append(current)
        return merged


class SemanticChunker:
    def __init__(self, max_chunk_size: int = 500, similarity_threshold: float = 0.5):
        self.max_chunk_size = max_chunk_size
        self.similarity_threshold = similarity_threshold
        self._embedder = None

    def _get_embedder(self):
        if self._embedder is None:
            from .embeddings import LocalEmbedder
            self._embedder = LocalEmbedder.get()
        return self._embedder

    def chunk(self, content: str, path: str) -> List[Chunk]:
        paragraphs = self._split_paragraphs(content)
        if len(paragraphs) <= 1:
            return [Chunk(
                chunk_id=f"{path}:1-{len(content.splitlines())}",
                path=path,
                start_line=1,
                end_line=len(content.splitlines()),
                text=content,
            )]
        try:
            embedder = self._get_embedder()
            from .embeddings import cosine_similarity
            embeddings = embedder.embed_batch([p["text"] for p in paragraphs])
            boundaries = [0]
            for i in range(1, len(embeddings)):
                sim = cosine_similarity(embeddings[i - 1], embeddings[i])
                if sim < self.similarity_threshold:
                    boundaries.append(i)
            boundaries.append(len(paragraphs))
            chunks: List[Chunk] = []
            for i in range(len(boundaries) - 1):
                start_idx = boundaries[i]
                end_idx = boundaries[i + 1]
                chunk_paragraphs = paragraphs[start_idx:end_idx]
                chunk_text = "\n\n".join(p["text"] for p in chunk_paragraphs)
                start_line = chunk_paragraphs[0]["start_line"]
                end_line = chunk_paragraphs[-1]["end_line"]
                chunks.append(Chunk(
                    chunk_id=f"{path}:{start_line}-{end_line}",
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    text=chunk_text,
                ))
            return chunks
        except Exception as e:
            logger.warning(f"Semantic chunking failed, using simple chunking: {e}")
            return SimpleChunker().chunk(content, path)

    def _split_paragraphs(self, content: str) -> List[Dict[str, Any]]:
        paragraphs = []
        current_para: List[str] = []
        current_start = 1
        line_num = 0
        for line in content.splitlines():
            line_num += 1
            if not line.strip():
                if current_para:
                    paragraphs.append({
                        "text": "\n".join(current_para),
                        "start_line": current_start,
                        "end_line": line_num - 1,
                    })
                    current_para = []
                current_start = line_num + 1
            else:
                current_para.append(line)
        if current_para:
            paragraphs.append({
                "text": "\n".join(current_para),
                "start_line": current_start,
                "end_line": line_num,
            })
        return paragraphs


class AutoChunker:
    """Selects best chunking strategy by file extension."""

    def __init__(self):
        self.code_chunker = CodeChunker()
        self.simple_chunker = SimpleChunker()

    def chunk(self, content: str, path: str) -> List[Chunk]:
        ext = Path(path).suffix.lower()
        if ext in LANGUAGE_PATTERNS:
            return self.code_chunker.chunk(content, path)
        return self.simple_chunker.chunk(content, path)


def chunk_file(path: Path, content: Optional[str] = None) -> List[Chunk]:
    if content is None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return []
    rel_path = str(path).replace("\\", "/")
    return AutoChunker().chunk(content, rel_path)
