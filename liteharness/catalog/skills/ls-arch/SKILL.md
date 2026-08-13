---
name: ls-arch
description: Canonical architecture reference for LiteSuite mega-app and ecosystem. Quick-lookup index into 15 architecture docs covering panels, services, MCP tools, voice, harness, arena, and more. Triggers on 'arch', 'architecture', 'show architecture', 'arch docs'.
---

# LiteSuite Architecture Reference

## Overview

Quick-lookup index into the **canonical** architecture docs for the consolidated LiteSuite mega-app.

> **Note:** This skill reads architecture docs from your project's `docs/architecture/` directory.
> If you are working on LiteSuite, these docs live in your LiteSuite project root.
> For other projects, use `/arch-gen` to generate architecture docs first.

## Docs

| #   | File                         | Subject                                                            |
| --- | ---------------------------- | ------------------------------------------------------------------ |
| 00  | 00-Ecosystem-Overview.md     | 3-app ecosystem, port map, consolidation history, directory layout |
| 01  | 01-LiteSuite-Architecture.md | Monorepo, Electron main, IPC (44 handlers), services, preloads     |
| 02  | 02-Panel-System.md           | 21+ pane types, sidebar morphing, canvas/zen, voice nav (48 cmds)  |
| 03  | 03-Backend-Server.md         | Bun/Effect WebSocket, protocol, SQLite, orchestration              |
| 04  | 04-Voice-Pipeline.md         | STT/TTS, wake detection, voice commands, companion, emotion        |
| 05  | 05-LiteHarness.md            | 5-tier orchestration, git-as-memory, harness MCP tools             |
| 06  | 06-MCP-Tools.md              | 20 MCP tools, Agent Bridge (:7423), just-bash/lite\_\_shell        |
| 07  | 07-LiteBench-Arena.md        | Gauntlet, arena battles, ELO, eval harness (10 benchmarks)         |
| 08  | 08-LiteMemory-LCM.md         | LCM DAG, Memory Graph 3D, vault integration                        |
| 09  | 09-LiteAgent.md              | Python CLI, identity, scheduler, evolution engine                  |
| 10  | 10-LiteImage.md              | Standalone GPU: sd.cpp, face swap, avatar cam, video gen           |
| 11  | 11-LiteDock.md               | Standalone Rust Stage Manager for Windows 11                       |
| 12  | 12-litesuite-dev.md          | SaaS: Next.js 15, Cloudflare, Stripe, /harness explorer            |
| 13  | 13-AgentsOverflow.md         | Worldwide agent network, daily builds, voting                      |
| 14  | 14-Self-Improvement.md       | Self-improvement loop, LiteCLI compilation, /train                 |

## Protocol

1. Locate the architecture docs directory relative to your current working directory:
   - Look for `docs/architecture/INDEX.md` in the project root (search up from cwd if needed)
   - If not found, inform the user and suggest running `/arch-gen` to generate docs first
2. Read `INDEX.md` for the full table of contents + section lookup table
3. If the user's question maps to a specific topic, use the Section Lookup to find the right doc and section
4. Read that specific doc section — don't load all docs at once
5. If unclear which section applies, show the index and ask

## Quick Port Reference

> **LiteSuite-specific ports** (only relevant when LiteSuite is running locally):

| Port | Service                                                        |
| ---- | -------------------------------------------------------------- |
| 3773 | Backend server (HTTP + WebSocket)                              |
| 7423 | Agent Bridge (REST, bearer token) — requires LiteSuite running |
| 7426 | LiteImage REST API — requires LiteSuite running                |
| 7438 | Voice API server — requires LiteSuite running                  |
| 5123 | TTS Server (Qwen3, on-demand) — requires LiteSuite running     |
| 8080 | Whisper STT (on-demand) — requires LiteSuite running           |

## Quick Panel Reference (21 types)

**WORKSPACE:** terminal, unified-editor, browser, files, git
**AI:** frontier-chat, claude, codex, liteagent-chat, claude-teams
**TOOLS:** benchmark, youtube, voice, memory-graph, modelHub
**SYSTEM:** dashboard, agent, settings, design-system, screens, style-test
