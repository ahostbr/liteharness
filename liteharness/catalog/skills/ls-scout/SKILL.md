---
name: ls-scout
description: >
  PROACTIVE BEHAVIOR — not primarily user-triggered. Opus should automatically dispatch Haiku
  sub-agents for simple research: reading files, listing directories, web searches, doc summaries,
  factual lookups. Saves Opus tokens by offloading grunt work to the cheapest model with its own
  separate usage pool. Use Haiku for facts, Sonnet polymaths for reasoning, Opus for decisions.
  Triggers on 'scout', 'send a scout'. Proactively used without user asking.
---

# Scout — Haiku Research Agent

Dispatch a **Haiku sub-agent** for research and exploration tasks instead of doing them yourself. Haiku draws from a separate, nearly untouched usage pool — offload all grunt work there.

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
  model: "haiku",
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
