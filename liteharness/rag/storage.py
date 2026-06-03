"""SQLite-backed storage for LiteHarness RAG.

Tables:
- rag_chunks: chunk metadata + content
- rag_chunks_fts: FTS5 virtual table (porter + unicode61 tokenizer)
- rag_embeddings: 384-dim float32 embeddings as BLOBs
- rag_index_meta: key/value store for index metadata
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".litesuite" / "harness" / "rag.db"


def _get_db_path() -> Path:
    return Path(os.environ.get("LITEHARNESS_RAG_DB", str(_DB_PATH)))


def _get_model_cache_dir() -> Path:
    default = Path.home() / ".litesuite" / "llm" / "models"
    return Path(os.environ.get("LITEHARNESS_MODEL_CACHE_DIR", str(default)))


class RagStorage:
    """SQLite-backed storage for RAG chunks and embeddings."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or _get_db_path()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-65536")  # 64MB
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        self._create_schema(conn)
        self._conn = conn
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                chunk_id    TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                start_line  INTEGER NOT NULL,
                end_line    INTEGER NOT NULL,
                content     TEXT NOT NULL,
                language    TEXT,
                chunk_type  TEXT,
                source_type TEXT,
                project     TEXT,
                indexed_at  TEXT NOT NULL,
                mtime       REAL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_source ON rag_chunks(source_path);
            CREATE INDEX IF NOT EXISTS idx_chunks_type ON rag_chunks(chunk_type, source_type);

            CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
                content,
                chunk_id UNINDEXED,
                tokenize="porter unicode61"
            );

            CREATE TABLE IF NOT EXISTS rag_embeddings (
                chunk_id  TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                model     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rag_index_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "RagStorage":
        self._connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def begin(self) -> None:
        self._connect().execute("BEGIN")

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # Chunks
    # ------------------------------------------------------------------ #

    def upsert_chunk(
        self,
        chunk_id: str,
        source_path: str,
        start_line: int,
        end_line: int,
        content: str,
        language: str | None = None,
        chunk_type: str | None = None,
        source_type: str | None = None,
        project: str | None = None,
        mtime: float | None = None,
    ) -> None:
        conn = self._connect()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO rag_chunks
                (chunk_id, source_path, start_line, end_line, content,
                 language, chunk_type, source_type, project, indexed_at, mtime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                source_path = excluded.source_path,
                start_line  = excluded.start_line,
                end_line    = excluded.end_line,
                content     = excluded.content,
                language    = excluded.language,
                chunk_type  = excluded.chunk_type,
                source_type = excluded.source_type,
                project     = excluded.project,
                indexed_at  = excluded.indexed_at,
                mtime       = excluded.mtime
            """,
            (chunk_id, source_path, start_line, end_line, content,
             language, chunk_type, source_type, project, now, mtime),
        )
        conn.execute("DELETE FROM rag_chunks_fts WHERE chunk_id = ?", (chunk_id,))
        conn.execute(
            "INSERT INTO rag_chunks_fts(content, chunk_id) VALUES (?, ?)",
            (content, chunk_id),
        )

    def get_chunks_by_source(self, source_path: str) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT chunk_id, source_path, start_line, end_line, content, "
            "language, chunk_type, source_type, project, indexed_at, mtime "
            "FROM rag_chunks WHERE source_path = ?",
            (source_path,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_all_source_paths(self) -> set[str]:
        conn = self._connect()
        rows = conn.execute("SELECT DISTINCT source_path FROM rag_chunks").fetchall()
        return {r[0] for r in rows}

    def delete_chunks_by_source(self, source_path: str) -> int:
        conn = self._connect()
        conn.execute(
            "DELETE FROM rag_chunks_fts WHERE chunk_id IN "
            "(SELECT chunk_id FROM rag_chunks WHERE source_path = ?)",
            (source_path,),
        )
        conn.execute(
            "DELETE FROM rag_embeddings WHERE chunk_id IN "
            "(SELECT chunk_id FROM rag_chunks WHERE source_path = ?)",
            (source_path,),
        )
        result = conn.execute(
            "DELETE FROM rag_chunks WHERE source_path = ?", (source_path,)
        )
        return result.rowcount

    def get_all_chunks(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT chunk_id, source_path, start_line, end_line, content, "
            "language, chunk_type, source_type, project, indexed_at, mtime "
            "FROM rag_chunks"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def chunk_count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]

    # ------------------------------------------------------------------ #
    # FTS search
    # ------------------------------------------------------------------ #

    def search_fts(self, query: str, top_k: int = 20) -> list[dict[str, Any]]:
        """BM25 full-text search via FTS5. Returns chunk dicts with bm25_score."""
        conn = self._connect()
        safe_q = _fts5_phrase_query(query)
        if not safe_q:
            return []
        try:
            rows = conn.execute(
                """
                SELECT c.chunk_id, c.source_path, c.start_line, c.end_line, c.content,
                       c.language, c.chunk_type, c.source_type, c.project,
                       c.indexed_at, c.mtime,
                       -bm25(rag_chunks_fts) AS bm25_score
                FROM rag_chunks_fts
                JOIN rag_chunks c ON rag_chunks_fts.chunk_id = c.chunk_id
                WHERE rag_chunks_fts MATCH ?
                ORDER BY bm25_score DESC
                LIMIT ?
                """,
                (safe_q, top_k),
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS search failed: {e}")
            return []

        results = []
        for r in rows:
            d = self._row_to_dict(r[:11])
            d["bm25_score"] = r[11]
            results.append(d)
        return results

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #

    def upsert_embedding(self, chunk_id: str, embedding: list[float], model: str) -> None:
        conn = self._connect()
        blob = np.array(embedding, dtype=np.float32).tobytes()
        conn.execute(
            """
            INSERT INTO rag_embeddings(chunk_id, embedding, model)
            VALUES (?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET embedding = excluded.embedding, model = excluded.model
            """,
            (chunk_id, blob, model),
        )

    def get_all_embeddings(self) -> dict[str, np.ndarray]:
        conn = self._connect()
        rows = conn.execute("SELECT chunk_id, embedding FROM rag_embeddings").fetchall()
        return {r[0]: np.frombuffer(r[1], dtype=np.float32).copy() for r in rows}

    def embedding_count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM rag_embeddings").fetchone()[0]

    # ------------------------------------------------------------------ #
    # Metadata
    # ------------------------------------------------------------------ #

    def get_meta(self, key: str) -> str | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT value FROM rag_index_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO rag_index_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    _CHUNK_KEYS = (
        "chunk_id", "source_path", "start_line", "end_line", "content",
        "language", "chunk_type", "source_type", "project", "indexed_at", "mtime",
    )

    @staticmethod
    def _row_to_dict(row: tuple) -> dict[str, Any]:
        return dict(zip(RagStorage._CHUNK_KEYS, row))


def _fts5_phrase_query(raw: str) -> str:
    """Escape user input for safe FTS5 MATCH clause."""
    tokens = [t for t in raw.split() if t]
    if not tokens:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)
