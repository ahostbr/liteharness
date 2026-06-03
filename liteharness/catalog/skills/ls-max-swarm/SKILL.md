---
name: ls-max-swarm
description: Use when spawning multiple coding agents to work on independent files or modules simultaneously. Triggers on 'max-swarm', 'swarm this', 'swarm it', 'spawn a swarm', 'coding swarm', 'one agent per file', 'throw agents at this'. NOT for structured decomposition (use /max-parallel).
allowed-tools: Bash, Read, Glob, Grep, Task
---

# /max-swarm - Parallel Coding Agent Swarm

Spawn multiple coding agents in parallel to work on a task. Each agent is launched as a background `claude` CLI process via the `Bash` tool.

## Flags

Parse the user's input for flags:

| Flag           | Effect                                                                         |
| -------------- | ------------------------------------------------------------------------------ |
| `--unattended` | Add `--dangerously-skip-permissions` for full autonomy (no permission prompts) |
| `--read-only`  | Restrict agents to read-only tools (exploration mode)                          |

**Examples:**

- `/max-swarm "document codebase"` → Default mode with `--allowedTools`
- `/max-swarm --unattended "refactor auth"` → Full autonomous mode
- `/max-swarm --read-only "explore the codebase"` → Safe exploration only

## When Invoked

The user provides a task description. You will:

1. Decompose it into 2-5 independent subtasks
2. Spawn a Claude agent for each subtask (in parallel)
3. Report the session IDs for monitoring

## Step 1: Task Decomposition

Analyze the task and decompose into **2-5 independent subtasks** that can be worked on in parallel.

Consider:

- What parts of the codebase need attention?
- What can be done independently without blocking?
- What's the optimal breakdown for parallel work?

**Examples:**

- "Document codebase" → Gateway docs, Desktop docs, MCP tools docs, AI harness docs
- "Review feature X" → Store review, Component review, Hook review, API review
- "Add tests" → Unit tests, Integration tests, E2E tests

## Step 2: Spawn Agents in Parallel

For each subtask, spawn a Claude agent using the `Bash` tool to launch a background `claude` CLI process.

**Prerequisites:** `claude` CLI must be installed and on PATH (`claude --version` to verify).

**Command templates by mode:**

**Default mode (restricted tools):**

```bash
claude -p "{subtask_prompt}" --allowedTools "Read,Glob,Grep,Write,Edit" &
```

**--unattended mode (full autonomy):**

```bash
claude -p "{subtask_prompt}" --dangerously-skip-permissions --allowedTools "Read,Glob,Grep,Write,Edit,Bash" &
```

**--read-only mode (exploration only):**

```bash
claude -p "{subtask_prompt}" --allowedTools "Read,Glob,Grep" &
```

**CRITICAL: Spawn ALL agents in a SINGLE message with parallel Bash tool calls!**

```
// DO THIS - all in one message (parallel Bash calls):
Bash("claude -p 'Task 1' --allowedTools '...' &")
Bash("claude -p 'Task 2' --allowedTools '...' &")
Bash("claude -p 'Task 3' --allowedTools '...' &")

// NOT THIS - sequential:
Bash(...)  // wait
Bash(...)  // wait
Bash(...)  // wait
```

## Step 3: Report Results

After spawning, provide a summary:

```
## Swarm Deployed

| Agent | Task | Status |
|-------|------|--------|
| Claude | Document API/Gateway | Running (background) |
| Claude | Document Desktop/UI | Running (background) |
| Claude | Document MCP/Tools | Running (background) |

**Monitor:** Check output files as agents complete
**Status:** All agents running in background
```

## Rules

1. **ALWAYS decompose** - Never spawn just 1 agent (use regular claude for that)
2. **ALWAYS parallel** - Spawn all agents in ONE message
3. **ALWAYS background** - Use `background: true, pty: true`
4. **Target 3-5 agents** - Sweet spot for most tasks
5. **Focused subtasks** - Each agent should have a clear, bounded scope

## Example Invocations

**User:** `/max-swarm "Write comprehensive documentation for <your-project>"`

**Response:**

```
Decomposing into parallel subtasks...

Spawning swarm of 4 Claude agents:

1. API / Gateway Documentation
2. Frontend / Desktop Documentation
3. MCP Tools / Integration Documentation
4. Configuration & Deployment Documentation

[Spawn 4 agents in parallel via Bash claude CLI calls...]

## Swarm Deployed

| Agent | Task | PID |
|-------|------|-----|
| Claude | API docs | 1001 |
| Claude | Frontend docs | 1002 |
| Claude | MCP docs | 1003 |
| Claude | Deployment docs | 1004 |

Monitor progress: check output files or tail agent logs
```

## Permission Flag Reference

| Scenario              | Use `--dangerously-skip-permissions` | Use `--allowedTools`          |
| --------------------- | ------------------------------------ | ----------------------------- |
| Read-only exploration | No                                   | `"Read,Glob,Grep"`            |
| Safe coding tasks     | No                                   | `"Read,Glob,Grep,Write,Edit"` |
| Unattended automation | **Yes** (via --unattended flag)      | Combine both                  |
| User-supervised       | No                                   | Yes                           |

**Key insight:**

- `--allowedTools` restricts WHICH tools are available (restrictive)
- `--dangerously-skip-permissions` skips permission PROMPTS (permissive)
- For full autonomy, combine both flags
