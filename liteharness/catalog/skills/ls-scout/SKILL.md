---
name: ls-scout
description: >
  PROACTIVE BEHAVIOR — not primarily user-triggered. Opus should automatically dispatch Sonnet 5
  sub-agents for research: reading files, listing directories, web searches, doc summaries,
  factual lookups, and light synthesis. Offloads grunt work to a near-Opus-capable model at a
  fraction of the cost (separate usage pool), saving Opus tokens for decisions. Use Sonnet 5 for
  research/facts/light-synthesis, Sonnet polymaths for framed reasoning, Opus for judgment calls.
  Triggers on 'scout', 'send a scout'. Proactively used without user asking.
---

# Scout — Sonnet 5 Research Agent

Dispatch a **Sonnet 5 sub-agent** for research and exploration tasks instead of doing them yourself. Sonnet 5 (`claude-sonnet-5`, released 2026-06-30) delivers near-Opus-4.8 research and synthesis quality at a fraction of the cost, drawing from a separate usage pool — offload grunt work and light analysis there, and keep Opus for the decisions.

## When to Use (Proactive)

Default to spawning a Scout instead of doing these yourself:

- Reading and summarizing documents, files, or web pages
- Exploring unfamiliar parts of a codebase
- Web searches for documentation, examples, or references
- Gathering context before making decisions
- Answering factual questions that require looking things up
- Scanning multiple files for patterns or information

## When NOT to Use

- Quick single-file reads where you already know the path
- Tasks requiring Opus-level reasoning or judgment calls
- When the user explicitly asks YOU to read something
- Writing code, editing files, or making changes (Scout is read-only)

## How to Dispatch

```
Agent(
  name: "scout",
  model: "claude-sonnet-5",
  subagent_type: "Explore",
  prompt: "<clear research question with enough context to act on>"
)
```

### Prompt Guidelines

- **Be specific.** "Find how playground files are loaded in apps/desktop" not "look at the desktop app"
- **State what you need back.** "Report: file paths, function names, and the loading mechanism"
- **Give the working directory.** Scout has no context from your conversation.
- **Multiple scouts in parallel.** If you have 3 independent questions, spawn 3 scouts simultaneously.

### Example Dispatches

**Codebase exploration:**

```
"In <your-project-dir>, find how the sidebar navigation is structured.
Read apps/desktop/src/renderer/components/Sidebar.tsx and report:
- What views are registered
- How nav groups are organized
- How new items get added
Report file paths and line numbers."
```

**Web search:**

```
"Search the web for the current pricing of Claude API models (Opus, Sonnet, Haiku).
Report input and output token costs for each model."
```

**Document summary:**

```
"Read <your-project-dir>/Docs/TECHNICAL_OVERVIEW.md
and summarize it in under 200 words. Focus on architecture and key components."
```

**Multi-scout parallel:**

```
// Spawn all 3 simultaneously:
Scout 1: "Find all React components in apps/desktop/src/renderer/components/ui/"
Scout 2: "Read package.json in apps/desktop and list all dependencies"
Scout 3: "Search web for Electron BrowserWindow sandbox options"
```

## Receiving Results

Scout reports back findings. You (Opus) then:

1. **Synthesize** — combine findings with your own reasoning
2. **Decide** — make the judgment call based on evidence
3. **Act** — write code, make plans, advise the user

Never delegate decision-making to Scout. It gathers; you think.
