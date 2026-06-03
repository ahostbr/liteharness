"""MCP tool registration stub for litesuite_rag."""

from __future__ import annotations

from typing import Any, Dict


def register_rag_tool(registry: Any) -> None:
    """Register litesuite_rag as an MCP tool with the given registry."""
    from .engine import litesuite_rag, ACTION_HANDLERS

    registry.register(
        name="litesuite_rag",
        description=(
            "Multi-strategy code search for LiteSuite. "
            "Actions: help, status, index, index_semantic, query, query_semantic, "
            "query_hybrid, query_reranked, query_multi, query_reflective, "
            "query_agentic, query_interactive, query_by_tier."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTION_HANDLERS.keys()),
                    "description": "Action to perform.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for query actions)",
                },
                "top_k": {
                    "type": "integer",
                    "default": 8,
                    "description": "Max results to return",
                },
                "exts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File extensions to search",
                },
                "root": {
                    "type": "string",
                    "description": "Root directory to search/index",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "default": False,
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force full rebuild (for index actions)",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["auto", "keyword", "semantic", "hybrid", "reranked", "multi", "reflective"],
                    "default": "auto",
                },
                "bm25_weight": {
                    "type": "number",
                    "default": 0.3,
                    "description": "BM25 weight for hybrid search (0-1)",
                },
                "variations": {
                    "type": "integer",
                    "default": 3,
                    "description": "Number of query variations for query_multi",
                },
                "scope": {
                    "type": "string",
                    "enum": ["project", "all", "reference", "knowledge"],
                    "default": "project",
                },
                "tier": {
                    "type": "string",
                    "enum": ["orchestrator", "leader", "worker", "thinker", "reviewer"],
                    "default": "worker",
                    "description": "Agent tier for query_by_tier",
                },
                "source": {
                    "type": "string",
                    "enum": ["claude", "codex", "copilot", "litecode", "git", "patterns", "all"],
                    "default": "all",
                    "description": "Source filter for index action",
                },
            },
            "required": ["action"],
        },
        handler=litesuite_rag,
    )
