"""Embedding providers for semantic search.

Default model: all-MiniLM-L6-v2 (384 dimensions, ~90MB)
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import numpy as np

from .storage import _get_model_cache_dir

logger = logging.getLogger(__name__)


def _get_embedding_model() -> str:
    return os.environ.get("LITEHARNESS_RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")


class LocalEmbedder:
    """Local embedding using sentence-transformers.

    Model: all-MiniLM-L6-v2 (384 dims, ~90MB, ~1000 texts/sec on CPU)
    """

    _instance: LocalEmbedder | None = None
    _model: Any = None
    _lock = threading.Lock()

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or _get_embedding_model()
        self._dim: int | None = None

    @classmethod
    def get(cls) -> LocalEmbedder:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def _load_model(self) -> Any:
        with LocalEmbedder._lock:
            if LocalEmbedder._model is not None:
                return LocalEmbedder._model
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"Loading embedding model: {self.model_name}")
                cache_dir = _get_model_cache_dir()
                cache_dir.mkdir(parents=True, exist_ok=True)
                LocalEmbedder._model = SentenceTransformer(
                    self.model_name, cache_folder=str(cache_dir)
                )
                self._dim = LocalEmbedder._model.get_sentence_embedding_dimension()
                logger.info(f"Model loaded: {self.model_name} ({self._dim} dims)")
                return LocalEmbedder._model
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )

    def embed_text(self, text: str) -> list[float]:
        model = self._load_model()
        max_len = 512 * 4
        if len(text) > max_len:
            text = text[:max_len]
        return model.encode(text, convert_to_numpy=True).tolist()

    def embed_batch(
        self,
        texts: list[str],
        show_progress: bool = False,
        batch_size: int = 32,
    ) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        max_len = 512 * 4
        texts = [t[:max_len] if len(t) > max_len else t for t in texts]
        return model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=show_progress,
            batch_size=batch_size,
        ).tolist()

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self._load_model()
        return self._dim or 384


def cosine_similarity(vec_a: np.ndarray | list[float], vec_b: np.ndarray | list[float]) -> float:
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def cosine_similarity_batch(query: list[float] | np.ndarray, candidates: dict[str, np.ndarray] | list) -> dict[str, float] | list[float]:
    """Vectorized batch cosine similarity.

    Accepts either:
    - dict[str, np.ndarray] -> returns dict[str, float] (keyed by chunk_id)
    - list[list[float]] -> returns list[float]
    """
    if isinstance(candidates, dict):
        if not candidates:
            return {}
        ids = list(candidates.keys())
        vecs = np.array([candidates[k] for k in ids], dtype=np.float32)
        q = np.asarray(query, dtype=np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-8)
        c_norms = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8)
        scores = np.dot(c_norms, q_norm)
        return dict(zip(ids, scores.tolist()))
    else:
        if not candidates:
            return []
        q = np.asarray(query, dtype=np.float32)
        c = np.array(candidates, dtype=np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-8)
        c_norms = c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-8)
        return np.dot(c_norms, q_norm).tolist()


def embed_text(text: str) -> list[float]:
    return LocalEmbedder.get().embed_text(text)


def embed_batch(texts: list[str], show_progress: bool = False) -> list[list[float]]:
    return LocalEmbedder.get().embed_batch(texts, show_progress=show_progress)
