---
name: ls-arch-gen
description: Generate or update architecture documentation for any project. Dispatches scout agents to explore the codebase, then writes structured architecture docs with an INDEX. Triggers on 'generate arch docs', 'create architecture docs', 'update arch docs', 'document this project', 'arch-gen', 'generate documentation'.
---

# Architecture Documentation Generator

## Overview

Generates comprehensive architecture documentation for any project by scouting the codebase and writing structured Markdown docs. This is the meta-evolution — the same process used to create LiteSuite's own architecture docs, packaged as a reusable skill.

## What It Produces

```
{project}/docs/architecture/
├── INDEX.md              # Table of contents + section lookup
├── 00-Overview.md        # Project overview, tech stack, directory layout
├── 01-{Module}.md        # One doc per major module/subsystem
├── 02-{Module}.md        # ...
└── NN-{Module}.md        # As many as needed
```

## Workflow

### Phase 1: Scout

Dispatch multiple parallel Haiku scout agents to explore the codebase:

1. **Structure Scout** — Top-level directory structure, package.json/Cargo.toml/pyproject.toml, workspace config, build system
2. **Entry Point Scout** — Main entry points, server creation, app initialization, routing
3. **API/Protocol Scout** — HTTP routes, WebSocket channels, IPC handlers, CLI commands, MCP tools
4. **Data Scout** — Database schemas, migrations, state management, persistence
5. **Feature Scout** — Major features, subsystems, plugins, extensions

For each scout, use the Agent tool with `subagent_type: "Explore"` and `model: "haiku"`:

```
Agent({
  subagent_type: "Explore",
  model: "haiku",
  prompt: "Explore {project_path} and report on {aspect}. Be thorough — read key files, don't just list them."
})
```

### Phase 2: Analyze

From scout results, identify:

- **Major subsystems** — Each becomes its own doc (01, 02, ...)
- **Shared patterns** — Tech stack, state management, communication protocols
- **Port map** (if applicable) — All services and their ports
- **External integrations** — APIs, databases, third-party services

### Phase 3: Write

For each identified subsystem, write a Markdown doc following this template:

```markdown
# {Subsystem Name}

## Overview

One paragraph: what it does, where it lives, key tech.

## Architecture

Tables, code blocks, diagrams showing structure.

## Key Components

| Component | File | Purpose |
| --------- | ---- | ------- |

## API / IPC / Protocol

Routes, channels, message formats.

## Configuration

Settings, env vars, defaults.

## Key Files Reference

| File | Purpose |
| ---- | ------- |
```

### Phase 4: Index

Write `INDEX.md` with:

1. Document table (number, filename, subject, approximate lines)
2. Section Lookup table (topic → doc → section) for quick navigation

### Phase 5: Skill (Optional)

If the project uses Claude Code or LiteSuite harness, create an `/arch` skill pointing to the generated docs.

## Update Mode

When updating existing docs (not generating from scratch):

1. Read existing `INDEX.md` to understand current doc structure
2. Back up existing docs to `{project}/docs/architecture-backup-{date}/`
3. Scout for changes since last update (check git log, new files, changed structure)
4. Rewrite docs that are stale, add new docs for new subsystems, remove docs for deleted subsystems
5. Update INDEX.md

## Guidelines

- **One doc per major subsystem** — Don't cram everything into one file
- **Tables over prose** — Architecture docs should be scannable, not novels
- **Include file paths** — Every component should reference its actual file location
- **Port maps are critical** — If the project has services, document every port
- **Section Lookup is the killer feature** — Topic → doc → section mapping enables instant navigation
- **Don't document code patterns** — The code itself is the reference. Document architecture, not implementation details
- **Keep docs under 300 lines** — If a doc is getting long, split it
