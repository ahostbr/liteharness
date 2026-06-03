---
description: Load latest checkpoint and restore session context
argument-hint: [checkpoint-name] [--list] [--last N]
allowed-tools: Bash, Read, Glob, Grep, lite__memory, AskUserQuestion
---

# /loadnow - Load Session Checkpoint

Load the most recent checkpoint (or a named one) from LCM to restore session context. Retrieves the structured checkpoint saved by `/savenow`.

## Input Parsing

Parse `$ARGUMENTS` for:

| Pattern    | Meaning                                      |
| ---------- | -------------------------------------------- |
| No args    | Load the most recent checkpoint              |
| Plain text | Load a checkpoint by name (partial match OK) |
| `--list`   | List recent checkpoints without loading      |
| `--last N` | List the last N checkpoints (default: 5)     |

**Examples:**

- `/loadnow` (loads most recent)
- `/loadnow auth-refactor-done` (loads by name)
- `/loadnow --list` (shows recent checkpoints)
- `/loadnow --last 10` (shows last 10)

## Step 1: Find Checkpoints

### If `--list` or `--last N`:

Search for all checkpoint messages:

```
lite__memory(
  action="grep",
  query="[CHECKPOINT:",
  conversation="checkpoints"
)
```

Display as a table:

```
## Recent Checkpoints

| # | Name | Date | Tags | Summary |
|---|------|------|------|---------|
| 1 | <name> | <date> | <tags> | <first line of summary> |
```

Stop here — don't load anything.

### If loading (no args or name provided):

Search for the target checkpoint:

```
lite__memory(
  action="grep",
  query="[CHECKPOINT: <name>]",
  conversation="checkpoints"
)
```

If no name given, get the most recent:

```
lite__memory(
  action="context",
  conversation="checkpoints"
)
```

## Step 2: Parse Checkpoint Data

Extract from the checkpoint message:

- **Name** — from `[CHECKPOINT: <name>]`
- **Timestamp** — from `Timestamp:` line
- **Tags** — from `Tags:` line
- **Plan** — from `Plan:` line
- **Summary** — from `## Summary` section
- **Files Modified** — from `## Files Modified` section
- **Recent Commits** — from `## Recent Commits` section
- **Active Tasks** — from `## Active Tasks` section
- **Changes** — from `## Changes` section

## Step 3: Verify Current State

Run in parallel:

1. **Git status** — Check if the repo has changed since the checkpoint
2. **Plan file** — If a plan was linked, read it (verify it still exists)
3. **Files check** — Verify key files from the checkpoint still exist

## Step 4: Display Context

```
## Checkpoint Loaded: <name>

**Saved**: <timestamp>
**Tags**: <tags>

### Summary
<summary content>

### Files Modified (at save time)
<file list>

### Recent Commits (at save time)
<commit list>

### Active Tasks (at save time)
<task list or "none">

### Plan
<plan content or "none linked">

---

### Current State
- **Git status**: <clean / N files modified since checkpoint>
- **Plan file**: <exists / missing>

Ready to continue.
```

## Step 5: Restore Context (if plan linked)

If the checkpoint references a plan file and it still exists:

1. **Read the plan** — Display the current plan state
2. **Highlight progress** — Compare checkpoint tasks vs current state
3. **Suggest next steps** — Based on plan + checkpoint data

## Rules

1. **CHECKPOINTS conversation** — Always search in `conversation="checkpoints"`
2. **PARTIAL MATCH** — Name matching should be substring/fuzzy — `auth` matches `auth-refactor-done`
3. **GRACEFUL MISSING** — If no checkpoints found, say so clearly and suggest `/savenow`
4. **VERIFY STATE** — Always check current git/file state against the checkpoint — things may have changed
5. **NON-BLOCKING** — If lite\_\_memory is unavailable, warn user but don't fail the session
