"""Cross-encoder re-ranking for two-stage retrieval.

Model: ms-marco-MiniLM-L-6-v2 (default, ~50MB)
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from .storage import _get_model_cache_dir

logger = logging.getLogger(__name__)


def _get_reranker_model() -> str:
    return os.environ.get(
        "LITEHARNESS_RAG_RERANKER_MODEL",
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
    )


class Reranker:
    """Cross-encoder re-ranker for two-stage retrieval."""

    _instance: Reranker | None = None
    _model: Any = None
    _lock = threading.Lock()

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or _get_reranker_model()

    @classmethod
    def get(cls) -> Reranker:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def _load_model(self) -> Any:
        with Reranker._lock:
            if Reranker._model is not None:
                return Reranker._model
            try:
                from sentence_transformers import CrossEncoder
                logger.info(f"Loading reranker model: {self.model_name}")
                cache_dir = _get_model_cache_dir()
                cache_dir.mkdir(parents=True, exist_ok=True)
                Reranker._model = CrossEncoder(
                    self.model_name,
                    max_length=512,
                )
                logger.info(f"Reranker model loaded: {self.model_name}")
                return Reranker._model
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int = 5,
        text_key: str = "text",
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        model = self._load_model()
        pairs = []
        valid = []
        for c in candidates:
            text = c.get(text_key, "")
            if text:
                pairs.append([query, text])
                valid.append(c)
        if not pairs:
            return []
        scores = model.predict(pairs)
        reranked = sorted(zip(valid, scores), key=lambda x: x[1], reverse=True)
        results = []
        for candidate, score in reranked[:top_k]:
            r = dict(candidate)
            r["score"] = float(score)
            r["match_kind"] = "reranked"
            results.append(r)
        return results

    def score_pair(self, query: str, document: str) -> float:
        model = self._load_model()
        return float(model.predict([[query, document]])[0])


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int = 5,
    text_key: str = "text",
) -> list[dict[str, Any]]:
    return Reranker.get().rerank(query, candidates, top_k, text_key)
