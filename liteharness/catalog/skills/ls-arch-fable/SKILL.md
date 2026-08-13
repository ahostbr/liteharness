---
name: ls-arch-fable
description: Lightweight, self-contained LiteSuite architecture map (doc index, port map, panel list) with no heavy file loads — kept model-stable for Fable/Mythos sessions. For the complete reference that opens every doc, use /ls-arch-opus. Triggers on 'arch', 'arch fable', 'architecture'.
---

# LiteSuite Architecture Map (Fable-safe)

## Overview

A self-contained navigation map for the LiteSuite architecture docs. It carries
the doc index, port map, and panel list **inline** so you can answer most
architecture questions without loading any other file — which keeps a
Fable/Mythos session model-stable. For a full read of every doc, use
`/ls-arch-opus`.

## Docs (open ONE only when needed)

Docs live in `C:/Projects/docs/architecture/`. For a specific question, open at
most one file.

| #   | File                         | Subject                                                        |
| --- | ---------------------------- | -------------------------------------------------------------- |
| 00  | 00-Ecosystem-Overview.md     | 3-app ecosystem, port map, directory layout                    |
| 01  | 01-LiteSuite-Architecture.md | Monorepo, Electron main, IPC handlers, services, preloads      |
| 02  | 02-Panel-System.md           | Pane types, sidebar morphing, canvas/zen, voice nav            |
| 03  | 03-Backend-Server.md         | Bun/Effect WebSocket, protocol, SQLite, orchestration          |
| 04  | 04-Voice-Pipeline.md         | STT/TTS, wake detection, voice commands, companion, emotion    |
| 05  | 05-LiteHarness.md            | 5-tier orchestration, git-as-memory, harness tools             |
| 06  | 06-MCP-Tools.md              | MCP tools, Agent Bridge (:7423), tool execution, generative UI |
| 07  | 07-LiteBench-Arena.md        | Gauntlet, arena matches, ELO, eval harness                     |
| 08  | 08-LiteMemory-LCM.md         | LCM DAG, Memory Graph 3D, vault integration                    |
| 09  | 09-LiteAgent.md              | Python CLI, identity, scheduler, evolution engine              |
| 10  | 10-LiteImage.md              | Standalone GPU studio: image + video generation, avatar cam    |
| 11  | 11-LiteDock.md               | Standalone Rust Stage Manager for Windows 11                   |
| 12  | 12-litesuite-dev.md          | SaaS site: Next.js 15, Cloudflare, Stripe, /harness explorer   |
| 13  | 13-AgentsOverflow.md         | Agent network, daily builds, voting                            |
| 14  | 14-Self-Improvement.md       | Self-improvement loop, LiteCLI compilation, /train             |

(Additional docs 15–17 — landscape notes, voice-model research, LiteModeler —
and the denser `INDEX.md` are available via `/ls-arch-opus`.)

## Protocol (Fable-safe)

1. Answer from the port map and panel list below, plus the doc table above,
   whenever you can — no file read needed for most questions.
2. If a question needs detail, read exactly ONE doc file from the directory above.
3. Do NOT read `INDEX.md` here — it is denser and this map replaces it for
   Fable. Docs 06, 12, and 15 are also denser; if you need them, switch to Opus
   and run `/ls-arch-opus`.

## Quick Port Reference

| Port | Service                           |
| ---- | --------------------------------- |
| 3773 | Backend server (HTTP + WebSocket) |
| 7423 | Agent Bridge (REST API)           |
| 7426 | LiteImage REST API                |
| 7438 | Voice API server                  |
| 5123 | TTS Server (Qwen3, on-demand)     |
| 8080 | Whisper STT (on-demand)           |

## Quick Panel Reference (21 types)

**WORKSPACE:** terminal, unified-editor, browser, files, git
**AI:** frontier-chat, claude, codex, liteagent-chat, claude-teams
**TOOLS:** benchmark, youtube, voice, memory-graph, modelHub
**SYSTEM:** dashboard, agent, settings, design-system, screens, style-test
