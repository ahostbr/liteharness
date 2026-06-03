---
name: ls-conversation-lookup
description: Find, search, and summarize Claude Code conversations. BM25 + semantic (vector) + hybrid search across indexed messages. Uses all-MiniLM-L6-v2 embeddings (sentence-transformers). USE PROACTIVELY when you need historical context — don't rg through JSONL files manually. Triggers on 'find conversation', 'search conversations', 'search convos', 'convo search', 'what conversation did we', 'which session did we', 'when did we build', 'find where we discussed', 'remember when we', or when a bare 8-char hex ID is given. Also use when the user references past work and you need to locate it. Use '--mode semantic' for conceptual queries, '--mode hybrid' for best results.
user_invocable: true
command: convo
---

# Conversation Lookup & Search

Find conversations by ID, or search across all conversations using BM25, semantic vectors, or hybrid mode.

## Prerequisites

- **Python** must be available on PATH (`python` or `python3`).
- **BM25 / basic search:** No extra packages required.
- **Semantic / hybrid search:** Requires `sentence-transformers`:
  ```bash
  pip install sentence-transformers
  ```
  The `all-MiniLM-L6-v2` model (~90 MB) is auto-downloaded on first use to the sentence-transformers default cache. Set `SENTENCE_TRANSFORMERS_HOME` to override the cache location.
- **`--summarize` mode:** Requires [LM Studio](https://lmstudio.ai) running locally on port 1234 with any chat model loaded (Devstral recommended for quality; any model works).

## Modes

| Flag                               | What it does                                           |
| ---------------------------------- | ------------------------------------------------------ |
| `<id>`                             | Lookup by ID prefix — return path, project, size, date |
| `<id> --summarize`                 | Lookup + extract + Devstral summary                    |
| `<id> --extract`                   | Lookup + extract condensed transcript (no LLM)         |
| `--search "query"`                 | BM25 keyword search (default)                          |
| `--search "query" --mode semantic` | Vector similarity search (conceptual)                  |
| `--search "query" --mode hybrid`   | BM25 + vector combined (best results)                  |
| `--index`                          | Build/update the FTS5 search index (incremental)       |
| `--index-embeddings`               | Build/update vector embeddings (incremental)           |
| `--stats`                          | Show index statistics                                  |

## Search Modes

### BM25 (default) — keyword matching

Best for: exact terms, function names, error messages, file paths.

```bash
python ${CLAUDE_SKILL_DIR}/find_conversation.py --search "navigator.clipboard"
```

### Semantic — vector similarity via all-MiniLM-L6-v2

Best for: conceptual queries ("how does the terminal work"), fuzzy matches, finding conversations about a topic even when exact words differ.

```bash
python ${CLAUDE_SKILL_DIR}/find_conversation.py --search "debugging PowerShell PATH issues" --mode semantic
```

### Hybrid — BM25 + vector combined (30% keyword / 70% vector)

Best overall results. Combines keyword precision with semantic understanding.

```bash
python ${CLAUDE_SKILL_DIR}/find_conversation.py --search "clipboard copy paste terminal" --mode hybrid
```

## Setup

### FTS5 index (required for BM25 and hybrid):

```bash
python ${CLAUDE_SKILL_DIR}/find_conversation.py --index
```

### Embeddings (required for semantic and hybrid):

```bash
python ${CLAUDE_SKILL_DIR}/find_conversation.py --index-embeddings
```

Both are incremental — re-running only processes new/modified files. Use `--force` for full rebuild.

## Common Options

```bash
-n 20              # Top 20 results (default 10)
--project LiteSuite # Filter by project
--type user         # Filter by message type (BM25/hybrid only)
--hours 48          # Only conversations from the past 48 hours
--date 2026-04-12   # Only conversations from a specific date
--force             # Force full rebuild (with --index or --index-embeddings)
```

## When to use which mode:

- **Exact terms** (function names, errors) → `--search "term"` (BM25 default)
- **Conceptual queries** ("when did we fix the auth bug") → `--mode semantic`
- **Best of both** → `--mode hybrid`
- **User says "use rag"** → use `--mode hybrid`
- **User gives an ID** → lookup mode (no --search)
- **"This happened yesterday/recently"** → add `--hours 24` or `--hours 72`
- **"I know it was the 12th"** → add `--date 2026-04-12`

## Lookup Mode (Original)

1. Extract the ID from the user's message (8+ hex characters)
2. Determine the mode:
   - If the user says "summarize", "summary", "what happened in" → use `--summarize`
   - If the user says "extract", "transcript" → use `--extract`
   - Otherwise → lookup only
3. Run:

```bash
python ${CLAUDE_SKILL_DIR}/find_conversation.py <id> [--summarize | --extract]
```

4. Report the results to the user.

**For `--summarize`:** Requires LM Studio running with Devstral loaded. Falls back to qwen3.5-0.8b if Devstral isn't available.

**For lookup only:** Do NOT read the file contents into context — just return the path.
