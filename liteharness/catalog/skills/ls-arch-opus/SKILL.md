---
name: ls-arch-opus
description: Complete LiteSuite architecture reference — full quick-lookup index into every architecture doc (panels, services, tools, voice, harness, arena, web, and more), reading the canonical docs on demand. Use on Opus/xhigh sessions. On Fable, prefer /ls-arch-fable to stay model-stable. Triggers on 'full arch', 'arch opus'.
---

# Lite Suite Architecture Reference

## Overview

Quick-lookup index into the **canonical** architecture docs for the consolidated LiteSuite mega-app (post-2026-04-09 rewrite).

## Files

- **Index:** `C:/Projects/docs/architecture/INDEX.md`
- **Docs directory:** `C:/Projects/docs/architecture/`
- **Backup of pre-consolidation docs:** `C:/Projects/docs/architecture-backup-2026-04-09/`

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

1. Read `INDEX.md` for the full table of contents + section lookup table
2. If the user's question maps to a specific topic, use the Section Lookup to find the right doc and section
3. Read that specific doc section — don't load all 15 docs
4. If unclear which section applies, show the index and ask

## Quick Port Reference

| Port | Service                           |
| ---- | --------------------------------- |
| 3773 | Backend server (HTTP + WebSocket) |
| 7423 | Agent Bridge (REST, bearer token) |
| 7426 | LiteImage REST API                |
| 7438 | Voice API server                  |
| 5123 | TTS Server (Qwen3, on-demand)     |
| 8080 | Whisper STT (on-demand)           |

## Quick Panel Reference (21 types)

**WORKSPACE:** terminal, unified-editor, browser, files, git
**AI:** frontier-chat, claude, codex, liteagent-chat, claude-teams
**TOOLS:** benchmark, youtube, voice, memory-graph, modelHub
**SYSTEM:** dashboard, agent, settings, design-system, screens, style-test
