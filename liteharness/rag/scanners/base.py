"""BaseScanner ABC — all transcript scanners implement this interface."""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from liteharness.rag.storage import RagStorage

logger = logging.getLogger(__name__)


class BaseScanner(ABC):
    @abstractmethod
    def scan_paths(self) -> list[str]:
        """Return all file paths this scanner can index."""
        ...

    @abstractmethod
    def parse_file(self, path: str) -> list[dict]:
        """Parse a single transcript file into a list of chunk dicts.

        Each chunk must contain:
          chunk_id, source_path, start_line, end_line, content,
          language, chunk_type, source_type, project, mtime
        """
        ...

    def needs_reindex(self, path: str, storage: "RagStorage") -> bool:
        """Return True if the file is new or has been modified since last index."""
        try:
            current_mtime = os.path.getmtime(path)
        except OSError:
            return False
        existing = storage.get_chunks_by_source(path)
        if not existing:
            return True
        stored_mtime = existing[0].get("mtime", 0)
        return current_mtime > stored_mtime

    def index_all(self, storage: "RagStorage", chunker=None) -> dict:
        """Incrementally index all paths returned by scan_paths().

        Returns a summary dict: {indexed, skipped, deleted, errors}.
        """
        paths = self.scan_paths()
        indexed = 0
        skipped = 0
        errors = 0
        deleted = 0

        all_known = storage.get_all_source_paths()
        path_set = set(paths)
        for stale in all_known - path_set:
            storage.delete_chunks_by_source(stale)
            deleted += 1

        for path in paths:
            if not self.needs_reindex(path, storage):
                skipped += 1
                continue
            try:
                chunks = self.parse_file(path)
                storage.delete_chunks_by_source(path)
                for chunk in chunks:
                    storage.upsert_chunk(
                        chunk["chunk_id"],
                        chunk["source_path"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["content"],
                        language=chunk.get("language", ""),
                        chunk_type=chunk.get("chunk_type", "conversation"),
                        source_type=chunk.get("source_type", "unknown"),
                        project=chunk.get("project", ""),
                        mtime=chunk.get("mtime", 0.0),
                    )
                indexed += 1
            except Exception as exc:
                logger.warning(f"Error indexing {path}: {exc}")
                errors += 1

        return {"indexed": indexed, "skipped": skipped, "deleted": deleted, "errors": errors}
