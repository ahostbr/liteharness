"""LiteHarness RAG engine — 13 action handlers backed by SQLite.

Actions: help, status, index, index_semantic, query, query_semantic,
         query_hybrid, query_reranked, query_multi, query_reflective,
         query_agentic, query_interactive, query_by_tier
"""

from __future__ import annotations

import fnmatch
import functools
import logging
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .storage import RagStorage

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

def _get_project_root() -> Path:
    return Path(os.environ.get("LITEHARNESS_PROJECT_ROOT", os.getcwd())).resolve()


_db_instance: RagStorage | None = None


def _get_db() -> RagStorage:
    global _db_instance
    if _db_instance is None:
        _db_instance = RagStorage()
    return _db_instance


def _get_max_file_bytes() -> int:
    return int(os.environ.get("LITEHARNESS_RAG_MAX_FILE_BYTES", "1500000"))


def _get_default_exts() -> set[str]:
    default = ".py,.md,.txt,.json,.yml,.yaml,.ts,.tsx,.js,.jsx,.html,.css,.log,.jsonl,.ps1,.sh,.bat,.go,.rs,.toml"
    exts_str = os.environ.get("LITEHARNESS_RAG_DEFAULT_EXTS", default)
    return {
        (e_stripped.lower() if e_stripped.startswith(".") else f".{e_stripped.lower()}")
        for e in exts_str.split(",")
        if (e_stripped := e.strip())
    }


def _use_ripgrep() -> bool:
    return os.environ.get("LITEHARNESS_RAG_USE_RG", "1").strip() in ("1", "true", "yes")


def _get_bm25_weight() -> float:
    return float(os.environ.get("LITEHARNESS_RAG_BM25_WEIGHT", "0.3"))


# ============================================================================
# LiteSuite-specific scope configuration
# ============================================================================

SKIP_DIRS: set[str] = {
    ".git", ".hg", ".svn",
    "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".worktrees", "test-results",
    ".litesuite", ".liteharness",
}

ALLOWED_DOT_DIRS: set[str] = {
    ".agents", ".github", ".vscode", ".claude",
}

REFERENCE_DIRS: set[str] = {
    "Docs/Plans",
    "designs",
    "e2e/test-results",
}

KNOWLEDGE_PATTERNS: list[str] = [
    "Docs/**/*.md",
    "packages/*/README.md",
    "apps/*/README.md",
    "AGENTS.md",
    "CLAUDE.md",
]

TIER_ROOTS: dict[str, list[str]] = {
    "orchestrator": ["Docs", "CLAUDE.md", "AGENTS.md"],
    "leader": ["packages", "apps/server"],
    "worker": ["apps/web/src", "apps/desktop/src/litesuite", "packages"],
    "thinker": ["packages/contracts", "packages/shared"],
    "reviewer": ["apps/web/src", "apps/desktop/src/litesuite"],
}

TIER_TOKEN_BUDGETS: dict[str, int] = {
    "orchestrator": 4000,
    "leader": 6000,
    "worker": 8000,
    "thinker": 5000,
    "reviewer": 6000,
}


def _should_skip_dir(name: str) -> bool:
    if name in SKIP_DIRS:
        return True
    if name.startswith(".") and name not in ALLOWED_DOT_DIRS:
        return True
    return False


def _is_reference_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    for ref_dir in REFERENCE_DIRS:
        if normalized.startswith(ref_dir + "/") or normalized == ref_dir:
            return True
    return False


def _is_knowledge_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    for pattern in KNOWLEDGE_PATTERNS:
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def _filter_by_scope(matches: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    if scope == "all":
        return matches
    filtered = []
    for m in matches:
        path = m.get("path", "")
        if scope == "reference":
            if _is_reference_path(path):
                filtered.append(m)
        elif scope == "knowledge":
            if _is_knowledge_path(path):
                filtered.append(m)
        else:
            if not _is_reference_path(path):
                filtered.append(m)
    return filtered


# ============================================================================
# Ripgrep integration
# ============================================================================

@functools.lru_cache(maxsize=1)
def _find_ripgrep() -> str | None:
    if not _use_ripgrep():
        return None
    rg_path = shutil.which("rg")
    if rg_path:
        return rg_path
    for p in [
        r"C:\Program Files\ripgrep\rg.exe",
        r"C:\scoop\shims\rg.exe",
        os.path.expanduser(r"~\scoop\shims\rg.exe"),
    ]:
        if os.path.isfile(p):
            return p
    return None


def _search_with_ripgrep(
    query: str,
    root: Path,
    exts: set[str],
    case_sensitive: bool,
    top_k: int,
    context_lines: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rg_path = _find_ripgrep()
    if not rg_path:
        raise RuntimeError("ripgrep not available")

    stats: dict[str, int] = {"files_scanned": 0, "files_skipped": 0}
    cmd = [rg_path, "--line-number", "--no-heading", "--with-filename"]
    if not case_sensitive:
        cmd.append("--smart-case")
    else:
        cmd.append("--case-sensitive")
    for ext in exts:
        cmd.extend(["-g", f"*.{ext.lstrip('.')}"])
    for skip in SKIP_DIRS:
        cmd.extend(["--glob", f"!{skip}/**"])
    cmd.extend(["--max-filesize", str(_get_max_file_bytes())])
    cmd.append(query)
    cmd.append(".")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, cwd=str(root)
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("ripgrep timed out")
    except Exception as e:
        raise RuntimeError(f"ripgrep failed: {e}")

    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or f"ripgrep code {result.returncode}")

    file_matches: dict[str, list[tuple[int, str]]] = {}
    for line in result.stdout.splitlines():
        m = re.match(r"^(.*):(\d+):(.*)$", line)
        if not m:
            continue
        file_path, line_num_str, _ = m.groups()
        line_num = int(line_num_str)
        try:
            abs_path = (root / file_path).resolve()
            rel_path = str(abs_path.relative_to(root)).replace("\\", "/")
        except Exception:
            rel_path = file_path.replace("\\", "/")
        file_matches.setdefault(rel_path, []).append((line_num, ""))

    stats["files_scanned"] = len(file_matches)
    ranked = sorted(
        [(p, min(h[0] for h in hits), len(hits)) for p, hits in file_matches.items()],
        key=lambda x: (-x[2], x[1]),
    )
    rank_map = {r[0]: i for i, r in enumerate(ranked)}

    matches: list[dict[str, Any]] = []
    for path, _, match_count in ranked[: top_k * 2]:
        try:
            content = (root / path).read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            best_line = file_matches[path][0][0]
            start = max(1, best_line - context_lines)
            end = min(len(lines), best_line + context_lines)
            snippet = "\n".join(lines[start - 1 : end])
            rank_idx = rank_map[path]
            score = min(1.0, match_count / 10) * 0.7 + (1.0 / (1 + rank_idx)) * 0.3
            matches.append({
                "path": path,
                "start_line": start,
                "end_line": end,
                "snippet": snippet[:1000],
                "score": round(score, 4),
                "match_kind": "keyword",
            })
            if len(matches) >= top_k:
                break
        except Exception:
            continue

    return matches, stats


def _search_python_fallback(
    query: str,
    root: Path,
    exts: set[str],
    case_sensitive: bool,
    top_k: int,
    context_lines: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats: dict[str, int] = {"files_scanned": 0, "files_skipped": 0}
    max_bytes = _get_max_file_bytes()
    query_lower = query.lower()
    query_words = [w for w in re.split(r"\s+", query_lower) if len(w) >= 2] or [query_lower]

    file_hits: list[Any] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            stats["files_skipped"] += 1
            continue
        skip = False
        try:
            for p in path.relative_to(root).parts[:-1]:
                if _should_skip_dir(p):
                    skip = True
                    break
        except ValueError:
            skip = True
        if skip:
            stats["files_skipped"] += 1
            continue
        if exts and path.suffix.lower() not in exts:
            stats["files_skipped"] += 1
            continue
        try:
            if path.stat().st_size > max_bytes:
                stats["files_skipped"] += 1
                continue
        except Exception:
            stats["files_skipped"] += 1
            continue

        stats["files_scanned"] += 1
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            content_lower = content.lower()
            if not any(w in content_lower for w in query_words):
                continue
            lines = content.splitlines()
            match_lines = [
                i + 1 for i, line in enumerate(lines)
                if any(w in line.lower() for w in query_words)
            ]
            if match_lines:
                rel_path = str(path.relative_to(root)).replace("\\", "/")
                words_matched = sum(1 for w in query_words if w in content_lower)
                first_line = match_lines[0]
                start = max(1, first_line - context_lines)
                end = min(len(lines), first_line + context_lines)
                snippet = "\n".join(lines[start - 1 : end])
                file_hits.append((rel_path, first_line, len(match_lines), words_matched, snippet))
        except Exception:
            continue

    file_hits.sort(key=lambda x: (-x[3], -x[2], x[1]))
    rank_map = {h[0]: i for i, h in enumerate(file_hits)}

    matches: list[dict[str, Any]] = []
    for path, first_line, match_count, words_matched, snippet in file_hits[:top_k]:
        rank = rank_map[path]
        word_cov = words_matched / len(query_words) if query_words else 0
        score = word_cov * 0.5 + min(1.0, match_count / 10) * 0.3 + (1.0 / (1 + rank)) * 0.2
        start = max(1, first_line - context_lines)
        end = start + snippet.count("\n")
        matches.append({
            "path": path,
            "start_line": start,
            "end_line": end,
            "snippet": snippet[:1000],
            "score": round(score, 4),
            "match_kind": "keyword",
        })
    return matches, stats


# ============================================================================
# BM25 tokenizer
# ============================================================================

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_PATTERN.findall(text)]


# ============================================================================
# Action: help
# ============================================================================

def _action_help(**kwargs: Any) -> dict[str, Any]:
    rg_path = _find_ripgrep()
    return {
        "ok": True,
        "data": {
            "tool": "litesuite_rag",
            "description": "Multi-strategy code search — BM25 FTS5 + semantic vector search",
            "llm_guidance": (
                "Use 'query' for exact matches (function names, class names, errors). "
                "Use 'query_agentic' when unsure — it auto-selects the best strategy. "
                "Use 'query_semantic' for conceptual questions. "
                "Use 'query_by_tier' to search within your tier's domain. "
                "Always check 'status' first if results seem stale."
            ),
            "actions": {
                "help": "Show this help",
                "status": "Check RAG index health",
                "index": "Build/rebuild BM25+FTS5 index",
                "index_semantic": "Build vector embeddings (run after index)",
                "query": "Keyword search (ripgrep + Python fallback)",
                "query_semantic": "Pure vector similarity search",
                "query_hybrid": "BM25 + vector (30% BM25 / 70% vector)",
                "query_reranked": "Hybrid + cross-encoder re-ranking",
                "query_multi": "Multiple query variations, deduplicated",
                "query_reflective": "Self-correcting search with query refinement",
                "query_agentic": "Auto-select strategy based on query analysis",
                "query_interactive": "Run query, return result summary for human filtering",
                "query_by_tier": "Tier-specific search with token budget",
            },
            "project_root": str(_get_project_root()),
            "ripgrep_available": rg_path is not None,
        },
        "error": None,
    }


# ============================================================================
# Action: status
# ============================================================================

def _action_status(**kwargs: Any) -> dict[str, Any]:
    try:
        db = _get_db()
        rg_path = _find_ripgrep()
        with db:
            chunk_count = db.chunk_count()
            embedding_count = db.embedding_count()
            last_indexed = db.get_meta("last_indexed")
            index_root = db.get_meta("index_root")
        return {
            "ok": True,
            "indexed": chunk_count > 0,
            "chunk_count": chunk_count,
            "embedding_count": embedding_count,
            "last_indexed": last_indexed,
            "index_root": index_root,
            "db_path": str(db.db_path),
            "project_root": str(_get_project_root()),
            "ripgrep_available": rg_path is not None,
            "ripgrep_path": rg_path,
            "max_file_bytes": _get_max_file_bytes(),
            "default_exts": sorted(_get_default_exts()),
        }
    except Exception as e:
        return {"ok": False, "error_code": "STATUS_FAILED", "message": str(e)}


# ============================================================================
# Action: index
# ============================================================================

def _action_index(
    root: str | None = None,
    force: bool = False,
    source: str = "all",
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    try:
        search_root = Path(root).resolve() if root else _get_project_root()
        allowed_exts = _get_default_exts()
        max_bytes = _get_max_file_bytes()

        from .chunker import AutoChunker
        chunker = AutoChunker()

        db = _get_db()
        files_processed = 0
        files_skipped = 0
        chunks_indexed = 0

        with db:
            if force:
                db._connect().execute("DELETE FROM rag_chunks")
                db._connect().execute("DELETE FROM rag_chunks_fts")
                db._connect().execute("DELETE FROM rag_embeddings")

            db.begin()
            try:
                for path in search_root.rglob("*"):
                    if not path.is_file() or path.is_symlink():
                        files_skipped += 1
                        continue
                    skip = False
                    try:
                        for p in path.relative_to(search_root).parts[:-1]:
                            if _should_skip_dir(p):
                                skip = True
                                break
                    except ValueError:
                        skip = True
                    if skip:
                        files_skipped += 1
                        continue
                    if path.suffix.lower() not in allowed_exts:
                        files_skipped += 1
                        continue
                    try:
                        if path.stat().st_size > max_bytes:
                            files_skipped += 1
                            continue
                    except Exception:
                        files_skipped += 1
                        continue

                    try:
                        content = path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        files_skipped += 1
                        continue

                    rel_path = str(path.relative_to(search_root)).replace("\\", "/")
                    mtime = path.stat().st_mtime
                    ext = path.suffix.lower()

                    chunks = chunker.chunk(content, rel_path)
                    if chunks:
                        db.delete_chunks_by_source(rel_path)
                        for c in chunks:
                            db.upsert_chunk(
                                chunk_id=c.chunk_id,
                                source_path=rel_path,
                                start_line=c.start_line,
                                end_line=c.end_line,
                                content=c.text,
                                language=c.metadata.get("language", ext.lstrip(".")),
                                chunk_type="code" if ext in {".py", ".ts", ".tsx", ".js", ".go", ".rs"} else "doc",
                                source_type=source if source != "all" else None,
                                mtime=mtime,
                            )
                        files_processed += 1
                        chunks_indexed += len(chunks)

                db.set_meta("last_indexed", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                db.set_meta("index_root", str(search_root))
                db.commit()
            except Exception:
                db.commit()
                raise

        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "ok": True,
            "chunks_indexed": chunks_indexed,
            "files_processed": files_processed,
            "files_skipped": files_skipped,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as e:
        return {"ok": False, "error_code": "INDEX_FAILED", "message": str(e)}


# ============================================================================
# Action: index_semantic
# ============================================================================

def _action_index_semantic(
    root: str | None = None,
    force: bool = False,
    batch_size: int = 32,
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    try:
        from .embeddings import LocalEmbedder

        db = _get_db()
        with db:
            chunks = db.get_all_chunks()
            if not chunks:
                index_result = _action_index(root=root, force=force)
                if not index_result.get("ok"):
                    return index_result
                chunks = db.get_all_chunks()

            existing_embs = {} if force else db.get_all_embeddings()
            to_embed = [
                (c["chunk_id"], c["content"])
                for c in chunks
                if c["chunk_id"] not in existing_embs
            ]

            if not to_embed:
                return {
                    "ok": True,
                    "message": "All chunks already have embeddings",
                    "chunks_total": len(chunks),
                    "chunks_embedded": 0,
                }

            embedder = LocalEmbedder.get()
            model_name = embedder.model_name
            embedded = 0

            db.begin()
            for i in range(0, len(to_embed), batch_size):
                batch = to_embed[i : i + batch_size]
                texts = [t for _, t in batch]
                ids = [cid for cid, _ in batch]
                vecs = embedder.embed_batch(texts)
                for cid, vec in zip(ids, vecs):
                    db.upsert_embedding(cid, vec, model_name)
                embedded += len(batch)
            db.commit()

        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "ok": True,
            "chunks_total": len(chunks),
            "chunks_embedded": embedded,
            "embedding_dimension": embedder.dimension,
            "elapsed_ms": elapsed_ms,
        }
    except ImportError:
        return {
            "ok": False,
            "error_code": "MISSING_DEPS",
            "message": "sentence-transformers not installed. Install with: pip install sentence-transformers",
        }
    except Exception as e:
        return {"ok": False, "error_code": "INDEX_FAILED", "message": str(e)}


# ============================================================================
# Action: query (keyword)
# ============================================================================

def _action_query(
    query: str = "",
    top_k: int = 8,
    exts: list[str] | None = None,
    root: str | None = None,
    case_sensitive: bool = False,
    scope: str = "project",
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    scope = (scope or "project").lower()
    if scope not in ("project", "all", "reference", "knowledge"):
        scope = "project"

    fetch_k = min(75, top_k * 3 if scope != "all" else top_k)
    search_root = Path(root).resolve() if root else _get_project_root()

    ext_filter = (
        {e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts if e}
        if exts else _get_default_exts()
    )

    warnings: list[str] = []
    try:
        rg_path = _find_ripgrep()
        if rg_path:
            try:
                matches, stats = _search_with_ripgrep(query, search_root, ext_filter, case_sensitive, fetch_k)
                rag_mode = "keyword_rg"
            except Exception as e:
                warnings.append(f"ripgrep failed ({e}), using Python fallback")
                matches, stats = _search_python_fallback(query, search_root, ext_filter, case_sensitive, fetch_k)
                rag_mode = "keyword_fallback"
        else:
            matches, stats = _search_python_fallback(query, search_root, ext_filter, case_sensitive, fetch_k)
            rag_mode = "keyword_fallback"

        matches = _filter_by_scope(matches, scope)[:top_k]
        stats["elapsed_ms"] = int((time.time() - start_time) * 1000)
        result: dict[str, Any] = {
            "ok": True,
            "rag_mode": rag_mode,
            "query": query,
            "root": str(search_root),
            "scope": scope,
            "matches": matches,
            "stats": stats,
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"ok": False, "error_code": "QUERY_FAILED", "message": str(e)}


# ============================================================================
# Action: query_semantic
# ============================================================================

def _action_query_semantic(
    query: str = "",
    top_k: int = 8,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    top_k = max(1, min(25, top_k))

    try:
        from .embeddings import LocalEmbedder, cosine_similarity_batch

        embedder = LocalEmbedder.get()
        query_vec = embedder.embed_text(query)

        db = _get_db()
        with db:
            chunks = {c["chunk_id"]: c for c in db.get_all_chunks()}
            embeddings = db.get_all_embeddings()

        if not embeddings:
            return {
                "ok": False,
                "error_code": "NO_EMBEDDINGS",
                "message": "No embeddings found. Run action='index_semantic' first.",
            }

        scores = cosine_similarity_batch(query_vec, embeddings)

        results = []
        for chunk_id, sim in scores.items():
            chunk = chunks.get(chunk_id)
            if not chunk:
                continue
            results.append({
                "path": chunk["source_path"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "snippet": chunk["content"][:1000],
                "score": round(float(sim), 4),
                "match_kind": "semantic",
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:top_k]
        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "ok": True,
            "rag_mode": "semantic",
            "query": query,
            "matches": results,
            "stats": {"elapsed_ms": elapsed_ms, "chunks_searched": len(embeddings)},
        }
    except ImportError:
        return {"ok": False, "error_code": "MISSING_DEPS", "message": "sentence-transformers not installed"}
    except Exception as e:
        return {"ok": False, "error_code": "QUERY_FAILED", "message": str(e)}


# ============================================================================
# Action: query_hybrid
# ============================================================================

def _action_query_hybrid(
    query: str = "",
    top_k: int = 8,
    bm25_weight: float | None = None,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    top_k = max(1, min(25, top_k))
    bm25_weight = bm25_weight if bm25_weight is not None else _get_bm25_weight()

    try:
        from .embeddings import LocalEmbedder, cosine_similarity_batch

        embedder = LocalEmbedder.get()
        query_vec = embedder.embed_text(query)

        db = _get_db()
        with db:
            chunks = {c["chunk_id"]: c for c in db.get_all_chunks()}
            bm25_hits = db.search_fts(query, top_k=min(top_k * 5, 200))
            embeddings = db.get_all_embeddings()

        if not chunks:
            return {"ok": False, "error_code": "NO_INDEX", "message": "No index found. Run action='index' first."}

        bm25_scores = {h["chunk_id"]: h["bm25_score"] for h in bm25_hits}
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
        bm25_norm = {k: v / max_bm25 for k, v in bm25_scores.items()} if max_bm25 > 0 else {}

        vec_scores = cosine_similarity_batch(query_vec, embeddings) if embeddings else {}

        results = []
        for chunk_id, chunk in chunks.items():
            bm25_score = bm25_norm.get(chunk_id, 0.0)
            vec_score = float(vec_scores.get(chunk_id, 0.0)) if vec_scores else 0.0
            combined = bm25_score * bm25_weight + vec_score * (1 - bm25_weight)
            if combined > 0:
                results.append({
                    "path": chunk["source_path"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "snippet": chunk["content"][:1000],
                    "score": round(combined, 4),
                    "bm25_score": round(bm25_score, 4),
                    "vec_score": round(vec_score, 4),
                    "match_kind": "hybrid",
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:top_k]
        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "ok": True,
            "rag_mode": "hybrid",
            "query": query,
            "bm25_weight": bm25_weight,
            "matches": results,
            "stats": {"elapsed_ms": elapsed_ms, "chunks_searched": len(chunks)},
        }
    except ImportError:
        return _action_query(query=query, top_k=top_k, root=root, **kwargs)
    except Exception as e:
        return {"ok": False, "error_code": "QUERY_FAILED", "message": str(e)}


# ============================================================================
# Action: query_reranked
# ============================================================================

def _action_query_reranked(
    query: str = "",
    top_k: int = 5,
    candidate_multiplier: int = 4,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    top_k = max(1, min(15, top_k))
    candidate_limit = min(top_k * candidate_multiplier, 30)

    try:
        from .reranker import Reranker

        hybrid_result = _action_query_hybrid(query=query, top_k=candidate_limit, root=root, **kwargs)
        if not hybrid_result.get("ok"):
            return hybrid_result

        candidates = hybrid_result.get("matches", [])
        if not candidates:
            return {"ok": True, "rag_mode": "reranked", "query": query, "matches": [], "stats": {}}

        reranker = Reranker.get()
        candidate_dicts = [{"text": c["snippet"], **c} for c in candidates]
        reranked = reranker.rerank(query, candidate_dicts, top_k=top_k)

        results = []
        for r in reranked:
            results.append({
                "path": r.get("path", ""),
                "start_line": r.get("start_line", 1),
                "end_line": r.get("end_line", 1),
                "snippet": r.get("snippet", r.get("text", ""))[:1000],
                "score": round(r.get("score", 0), 4),
                "match_kind": "reranked",
            })

        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "ok": True,
            "rag_mode": "reranked",
            "query": query,
            "matches": results,
            "stats": {
                "elapsed_ms": elapsed_ms,
                "candidates_retrieved": len(candidates),
                "results_returned": len(results),
            },
        }
    except ImportError:
        return _action_query_hybrid(query=query, top_k=top_k, root=root, **kwargs)
    except Exception as e:
        return {"ok": False, "error_code": "QUERY_FAILED", "message": str(e)}


# ============================================================================
# Action: query_multi
# ============================================================================

def _generate_query_variations(query: str, n: int = 3) -> list[str]:
    variations = []
    if '"' not in query:
        variations.append(f'"{query}"')
    tokens = query.split()
    if len(tokens) >= 2:
        # camelCase/snake → split form
        expanded = []
        for t in tokens:
            parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).replace("_", " ").split()
            expanded.extend(parts)
        joined = " ".join(expanded)
        if joined != query:
            variations.append(joined)
    return variations[:n]


def _action_query_multi(
    query: str = "",
    top_k: int = 8,
    variations: int = 3,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    top_k = max(1, min(25, top_k))
    all_queries = [query] + _generate_query_variations(query, variations)

    all_results: dict[str, dict[str, Any]] = {}
    for q in all_queries:
        result = _action_query_hybrid(query=q, top_k=top_k * 2, root=root, **kwargs)
        if result.get("ok"):
            for match in result.get("matches", []):
                key = f"{match['path']}:{match['start_line']}"
                if key not in all_results or match["score"] > all_results[key]["score"]:
                    all_results[key] = match

    results = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)[:top_k]
    elapsed_ms = int((time.time() - start_time) * 1000)
    return {
        "ok": True,
        "rag_mode": "multi",
        "query": query,
        "variations_used": all_queries,
        "matches": results,
        "stats": {"elapsed_ms": elapsed_ms, "queries_executed": len(all_queries), "unique_results": len(all_results)},
    }


# ============================================================================
# Action: query_reflective
# ============================================================================

_STOPWORDS = {"the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
              "her", "was", "one", "our", "out", "has", "have", "from", "this", "that",
              "with", "they", "been", "said", "each", "which", "their", "will", "other",
              "about", "them", "then", "than", "into", "could", "would", "make", "like",
              "just", "over", "such", "also", "back", "after", "use", "two", "how", "its",
              "let", "made", "may", "new", "now", "way", "who", "did", "get", "these",
              "def", "self", "return", "import", "class", "function", "const", "true", "false", "none"}


def _score_result_quality(query: str, results: list[dict]) -> float:
    if not results:
        return 0.0
    avg_score = sum(r.get("score", 0) for r in results) / len(results)
    query_terms = set(_tokenize(query))
    covered = sum(1 for r in results if query_terms & set(_tokenize(r.get("snippet", ""))))
    coverage = covered / len(results)
    return avg_score * 0.6 + coverage * 0.4


def _refine_query(original: str, results: list[dict]) -> str:
    all_text = " ".join(r.get("snippet", "")[:500] for r in results[:3])
    freq = Counter(_tokenize(all_text))
    original_tokens = set(_tokenize(original))
    new_terms = [
        t for t, _ in freq.most_common(20)
        if t not in original_tokens and len(t) > 2 and t not in _STOPWORDS
    ][:2]
    return f"{original} {' '.join(new_terms)}" if new_terms else original


def _action_query_reflective(
    query: str = "",
    top_k: int = 8,
    max_iterations: int = 2,
    quality_threshold: float = 0.5,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    start_time = time.time()
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    top_k = max(1, min(25, top_k))
    current_query = query
    all_attempts = []
    final_results: list[dict] = []

    for iteration in range(max_iterations):
        result = _action_query_hybrid(query=current_query, top_k=top_k, root=root, **kwargs)
        if not result.get("ok"):
            break
        matches = result.get("matches", [])
        quality = _score_result_quality(current_query, matches)
        all_attempts.append({"query": current_query, "quality": round(quality, 3), "result_count": len(matches)})
        final_results = matches
        if quality >= quality_threshold or not matches:
            break
        current_query = _refine_query(query, matches)

    elapsed_ms = int((time.time() - start_time) * 1000)
    return {
        "ok": True,
        "rag_mode": "reflective",
        "query": query,
        "final_query": current_query,
        "iterations": len(all_attempts),
        "attempts": all_attempts,
        "matches": final_results,
        "stats": {"elapsed_ms": elapsed_ms},
    }


# ============================================================================
# Action: query_agentic
# ============================================================================

def _auto_select_strategy(query: str) -> str:
    words = query.split()
    if any(re.match(r"[A-Z][a-z]+[A-Z]", w) or "_" in w for w in words):
        return "keyword"
    if '"' in query or "exact" in query.lower():
        return "keyword"
    if len(words) <= 2:
        return "keyword"
    question_words = {"how", "what", "why", "where", "when", "which"}
    if words[0].lower() in question_words:
        return "semantic"
    return "hybrid"


def _action_query_agentic(
    query: str = "",
    strategy: str = "auto",
    top_k: int = 8,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    if strategy == "auto":
        strategy = _auto_select_strategy(query)

    handlers = {
        "keyword": _action_query,
        "semantic": _action_query_semantic,
        "hybrid": _action_query_hybrid,
        "reranked": _action_query_reranked,
        "multi": _action_query_multi,
        "reflective": _action_query_reflective,
    }
    handler = handlers.get(strategy, _action_query_hybrid)
    result = handler(query=query, top_k=top_k, root=root, **kwargs)
    result["strategy_used"] = strategy
    return result


# ============================================================================
# Action: query_interactive
# ============================================================================

def _action_query_interactive(
    query: str = "",
    strategy: str = "hybrid",
    top_k: int = 10,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    top_k = max(1, min(25, top_k))

    result = _action_query_agentic(query=query, strategy=strategy, top_k=top_k, root=root, **kwargs)
    if not result.get("ok"):
        return result

    matches = result.get("matches", [])
    if not matches:
        return {"ok": True, "rag_mode": "interactive", "query": query, "matches": [], "filtered": False, "message": "No results to filter"}

    summary_lines = []
    for i, m in enumerate(matches):
        path = m.get("path", "?")
        start_line = m.get("start_line", "?")
        score = m.get("score", 0)
        preview = (m.get("snippet", "") or "")[:80].replace("\n", " ").strip()
        summary_lines.append(f"[{i+1}] {path}:{start_line} (score: {score:.2f})\n    {preview}...")

    result["rag_mode"] = "interactive"
    result["filtered"] = False
    result["result_summary"] = "\n".join(summary_lines)
    result["note"] = "Use AskUserQuestion or manual filtering to select results"
    return result


# ============================================================================
# Action: query_by_tier (NEW)
# ============================================================================

def _action_query_by_tier(
    query: str = "",
    tier: str = "worker",
    top_k: int = 8,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Tier-specific search with token budget enforcement."""
    if not query or not query.strip():
        return {"ok": False, "error_code": "MISSING_PARAM", "message": "query is required"}

    query = query.strip()
    tier = tier.lower()

    if tier not in TIER_ROOTS:
        tier = "worker"

    project_root = Path(root).resolve() if root else _get_project_root()
    token_budget = TIER_TOKEN_BUDGETS.get(tier, 6000)
    tier_dirs = TIER_ROOTS.get(tier, [])

    all_results: dict[str, dict[str, Any]] = {}
    for tier_dir in tier_dirs:
        search_root = project_root / tier_dir
        if not search_root.exists():
            continue
        result = _action_query_hybrid(query=query, top_k=top_k, root=str(search_root), **kwargs)
        if result.get("ok"):
            for match in result.get("matches", []):
                abs_path = search_root / match["path"]
                try:
                    rel_path = str(abs_path.relative_to(project_root)).replace("\\", "/")
                except ValueError:
                    rel_path = match["path"]
                match["path"] = rel_path
                key = f"{rel_path}:{match['start_line']}"
                if key not in all_results or match["score"] > all_results[key]["score"]:
                    all_results[key] = match

    results = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    chars_per_token = 4
    budget_chars = token_budget * chars_per_token
    trimmed = []
    used = 0
    for r in results:
        snippet_len = len(r.get("snippet", ""))
        if used + snippet_len > budget_chars and trimmed:
            break
        trimmed.append(r)
        used += snippet_len

    return {
        "ok": True,
        "rag_mode": "by_tier",
        "query": query,
        "tier": tier,
        "tier_roots": tier_dirs,
        "token_budget": token_budget,
        "matches": trimmed,
        "stats": {"total_candidates": len(all_results), "results_returned": len(trimmed)},
    }


# ============================================================================
# Router
# ============================================================================

ACTION_HANDLERS: dict[str, Any] = {
    "help": _action_help,
    "status": _action_status,
    "index": _action_index,
    "index_semantic": _action_index_semantic,
    "query": _action_query,
    "query_semantic": _action_query_semantic,
    "query_hybrid": _action_query_hybrid,
    "query_reranked": _action_query_reranked,
    "query_multi": _action_query_multi,
    "query_reflective": _action_query_reflective,
    "query_agentic": _action_query_agentic,
    "query_interactive": _action_query_interactive,
    "query_by_tier": _action_query_by_tier,
}


def litesuite_rag(
    action: str,
    query: str = "",
    top_k: int = 8,
    exts: list[str] | None = None,
    root: str | None = None,
    case_sensitive: bool = False,
    force: bool = False,
    strategy: str = "auto",
    bm25_weight: float | None = None,
    variations: int = 3,
    scope: str = "project",
    tier: str = "worker",
    source: str = "all",
    **kwargs: Any,
) -> dict[str, Any]:
    """LiteHarness RAG — multi-strategy code search."""
    act = (action or "").strip().lower()
    if not act:
        return {
            "ok": False,
            "error_code": "MISSING_PARAM",
            "message": "action is required. Use action='help' for available actions.",
            "available_actions": list(ACTION_HANDLERS.keys()),
        }

    handler = ACTION_HANDLERS.get(act)
    if not handler:
        return {
            "ok": False,
            "error_code": "UNKNOWN_ACTION",
            "message": f"Unknown action: {act}",
            "available_actions": list(ACTION_HANDLERS.keys()),
        }

    return handler(
        query=query,
        top_k=top_k,
        exts=exts,
        root=root,
        case_sensitive=case_sensitive,
        force=force,
        strategy=strategy,
        bm25_weight=bm25_weight,
        variations=variations,
        scope=scope,
        tier=tier,
        source=source,
        **kwargs,
    )
