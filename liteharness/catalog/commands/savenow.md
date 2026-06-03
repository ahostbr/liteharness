---
description: Save session checkpoint to LCM memory
argument-hint: [descriptive-name] [--tags tag1,tag2] [--plan path/to/plan.md]
allowed-tools: Bash, Read, Glob, Grep, lite__memory, AskUserQuestion
---

# /savenow - Save Session Checkpoint

Save the current session state to the LCM (Lossless Context Management) DAG as a structured checkpoint message. This creates a recoverable restore point that `/loadnow` can retrieve.

## Input Parsing

Parse `$ARGUMENTS` for:

| Pattern                  | Meaning                                                 |
| ------------------------ | ------------------------------------------------------- |
| Plain text               | Checkpoint name (kebab-case, e.g. `auth-refactor-done`) |
| `--tags tag1,tag2`       | Comma-separated tags for searchability                  |
| `--plan path/to/plan.md` | Link a plan file to this checkpoint                     |
| `--summary "text"`       | Override auto-generated summary                         |

**Examples:**

- `/savenow terminal-spawn-fixes`
- `/savenow --tags refactor,api api-layer-cleanup`
- `/savenow --plan Docs/Plans/auth-rewrite.md auth-milestone-1`
- `/savenow` (auto-generates name from recent work)

## Step 1: Gather Session State

Run these in parallel to collect current state:

1. **Git status** — `git status --short` to find modified/untracked files
2. **Git diff stat** — `git diff --stat HEAD` to see what changed
3. **Git log** — `git log --oneline -5` to get recent commits
4. **Task list** — Check TaskList for any active tasks and their status

## Step 2: Build Checkpoint Data

Compose a structured checkpoint message from gathered state:

```
[CHECKPOINT: <name>]
Timestamp: <ISO 8601>
Tags: <comma-separated>
Plan: <path or "none">

## Summary
<2-3 sentence summary of what was accomplished — auto-generate from git diff/log if --summary not provided>

## Files Modified
<list from git status>

## Recent Commits
<from git log>

## Active Tasks
<from TaskList, if any>

## Changes
<key changes with brief descriptions>
```

## Step 3: Save to LCM

Execute these calls sequentially:

### 3a. Log the checkpoint message

```
lite__memory(
  action="log_message",
  role="system",
  content="<structured checkpoint content from Step 2>",
  conversation="checkpoints"
)
```

Use the `checkpoints` conversation to keep checkpoints separate from regular session messages, making them easy to find later.

### 3b. Take a snapshot

```
lite__memory(
  action="snapshot"
)
```

This builds a priority-tiered snapshot of current context, ensuring the checkpoint is captured at the appropriate DAG depth.

### 3c. Log the event

```
lite__memory(
  action="log_event",
  event_type="checkpoint_save",
  data="checkpoint:<name> tags:<tags> plan:<plan_path>"
)
```

## Step 4: Confirm

Display confirmation:

```
## Checkpoint Saved

**Name**: <name>
**Tags**: <tags>
**Plan**: <plan_path or "none">

**Files Modified**: <count>
**Recent Commits**: <count>

**Restore with**: `/loadnow` or `/loadnow <name>`

Saved to LCM conversation: checkpoints
```

## Rules

1. **ALWAYS gather state first** — Don't save empty checkpoints
2. **AUTO-NAME** — If no name provided, derive from recent git log or task subjects
3. **CHECKPOINTS conversation** — Always use `conversation="checkpoints"` for checkpoint messages
4. **NON-BLOCKING** — If lite\_\_memory is unavailable, warn user but don't fail the session
5. **NO SECRETS** — Never include .env contents, credentials, or tokens in checkpoint data
