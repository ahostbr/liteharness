---
name: ls-vault
description: "Obsidian vault — notes, daily entries, search, import, guide. Triggers on '/vault', '/note', '/daily', '/vault-search', '/vault-import', 'create a note', 'daily note', 'add to today', 'journal entry', 'search my vault', 'find notes about', 'import to vault', 'how does my vault work', 'vault help', 'vault guide'."
allowed-tools: Read, Write, Edit, Bash, Glob
---

# Obsidian Vault

All-in-one skill for your Obsidian vault at `~\Documents\Obsidian Vault`.

> **Note:** The vault path `~\Documents\Obsidian Vault` is the default Obsidian location on Windows. If your vault lives elsewhere, update this skill's SKILL.md with your actual path.

Determine which section applies based on the user's intent:

- Creating/updating a named note → **Create/Update Notes**
- Viewing or adding to today's journal → **Daily Notes**
- Finding existing notes → **Search Vault**
- Importing an external file → **Import Files**
- Questions about vault structure/conventions → **Vault Guide**

---

## Create/Update Notes

Create or update a note in the Obsidian vault.

### Vault Structure

| Folder          | Purpose                                               | Frontmatter `type` |
| --------------- | ----------------------------------------------------- | ------------------ |
| **Projects/**   | Project notes, specs, architecture decisions          | `project`          |
| **Research/**   | Technical research, videos, articles, tutorials       | `research`         |
| **Ideas/**      | Brainstorms, concepts, "what if" notes                | `idea`             |
| **Chronicles/** | Origin stories, milestones, journey narrative         | `chronicle`        |
| **Daily/**      | Daily journal notes (use Daily Notes section instead) | `daily`            |
| **Sessions/**   | Auto-captured session summaries (auto-managed)        | `session`          |

### Known Projects

Add your own project names here, or leave empty to let the skill infer from the current working directory and conversation context.

### Vault Root

`~\Documents\Obsidian Vault`

### Steps

1. **Parse arguments from `$ARGUMENTS`**:
   - If it contains a `/`, treat as an explicit path (e.g. `Projects/LiteSuite`)
   - If it matches or references a known project name -> `Projects/<name>`
   - If it references the current working project (check cwd) -> `Projects/<project>`
   - Otherwise infer folder from keywords:
     - "research", "learn", "study", "article", "video" -> `Research/`
     - "idea", "brainstorm", "what if", "concept" -> `Ideas/`
     - Default -> `Ideas/`

2. **Check if note exists** using Read tool at `~\Documents\Obsidian Vault\<path>.md`:
   - If exists, read it and **append** the new content under the appropriate section (or ask user if unclear)
   - If not exists, create new from template structure below

3. **Generate frontmatter** (for new notes):

   ```yaml
   ---
   type: <from folder table above>
   title: "<title>"
   created: <current ISO datetime>
   updated: <current ISO datetime>
   status: active
   tags:
     - <type>
     - <additional inferred tags>
   ---
   ```

4. **Generate body**:
   - If user provided content after the title, write it under an appropriate heading
   - If no content, use the skeleton for that note type:
     - **Projects**: `## Overview`, `## Current Status`, `## Key Decisions`, `## Links`
     - **Research**: `## Summary`, `## Notes`, `## Sources`, `## Links`
     - **Ideas**: `## Concept`, `## Details`, `## Links`
   - Use `[[wikilinks]]` to connect to related notes (e.g. `[[My Project]]`, `[[Research Topic]]`)

5. **Write note** using Write tool to `~\Documents\Obsidian Vault\<path>.md`

6. **Confirm** with the full path and a brief summary of what was written

---

## Daily Notes

Manage today's daily note in the Obsidian vault.

### Steps

1. **Determine today's note path**: `~\Documents\Obsidian Vault\Daily\YYYY-MM-DD.md` (use current date)

2. **If no arguments** (`$ARGUMENTS` is empty):
   - Read the note with the Read tool and display it formatted nicely
   - If it doesn't exist, say so and offer to create it

3. **If arguments provided**:
   - Read current daily note (create skeleton if missing — see template below)
   - Append under "## Log" with timestamp: `- HH:MM — $ARGUMENTS`
   - Write back with Edit or Write tool

4. **Daily note skeleton** (if creating fresh):

   ```markdown
   ---
   type: daily
   date: YYYY-MM-DD
   tags: [daily]
   ---

   # YYYY-MM-DD

   ## Log

   ## Notes
   ```

5. **Show confirmation** of what was added or displayed

---

## Search Vault

Search the vault using Glob and Grep (built-in, always available).

> **Optional RAG upgrade:** If you have a BM25/vector index script at `~/.claude/vault_rag/vault_query.py`,
> call it instead of the Grep approach below for ranked results. If the script is missing, the Grep
> approach below works for all users without any setup.

### Steps

1. **Parse arguments from `$ARGUMENTS`**:
   - `--folder X` → scope search to `~\Documents\Obsidian Vault\X\`
   - `--reindex` → note that no index is maintained by default; this flag is a no-op unless you have installed a RAG script at `~/.claude/vault_rag/`
   - Plain text → full-text Grep search

2. **Run search** using the Grep tool:
   - Search pattern: the user's query terms
   - Path: `~\Documents\Obsidian Vault\` (or subfolder if `--folder` specified)
   - Output mode: `content` to show matching lines with context
   - File glob: `*.md`

3. **If Grep returns too many results**, also use the Glob tool to list matching filenames:
   - Pattern: `**/*<keyword>*.md`
   - Path: `~\Documents\Obsidian Vault\`

4. **Display results** — show file paths, matching lines, and brief context. Present them cleanly.

5. **Offer to read**: If the user wants to see a full note, use the Read tool on `~\Documents\Obsidian Vault\<path>`

---

## Import Files

Import external markdown files or content into the vault.

### Steps

1. **Read the source file** from `$ARGUMENTS` using the Read tool
   - The first argument is the file path to import

2. **Determine destination**:
   - If `--to` flag provided, use that vault path
   - Otherwise infer from content:
     - Looks like a transcript -> `Research/`
     - Looks like a story/chronicle -> `Chronicles/`
     - Looks like project docs -> `Projects/`
     - Default -> `Research/`

3. **Generate frontmatter**:

   ```yaml
   type: <inferred>
   title: "<extracted from first heading or filename>"
   created: <current ISO datetime>
   imported: <current ISO datetime>
   source: "<original file path>"
   tags: [imported, <inferred>]
   ```

4. **Write to vault** using Write tool to `~\Documents\Obsidian Vault\<destination>.md`

5. **Confirm** with the imported path and word count

---

## Vault Guide

Reference information about how the vault is organized and used.

### Vault Location

`~\Documents\Obsidian Vault`

### Folder Structure

| Folder          | Purpose                                                  |
| --------------- | -------------------------------------------------------- |
| **Chronicles/** | Origin stories, milestones, the journey narrative        |
| **Daily/**      | Daily journal notes (YYYY-MM-DD.md format)               |
| **Ideas/**      | Brainstorms, concepts, "what if" notes                   |
| **Projects/**   | Project notes, specs, architecture decisions             |
| **Research/**   | Technical research, videos, articles, tutorials          |
| **Sessions/**   | Auto-captured Claude Code session summaries              |
| **System/**     | Vault config, internal documentation                     |
| **Templates/**  | Note templates (daily, project, research, idea, session) |

### Key Commands

- `/vault note [title]` — Create or update a note
- `/vault daily [entry]` — Open or add to today's daily note
- `/vault search [query]` — Search the vault by content, tags, or links
- `/vault import [path]` — Import an external file into the vault

### Conventions

#### Frontmatter

Every note should have YAML frontmatter:

```yaml
---
type: project|daily|research|idea|session|chronicle
title: "Note Title"
created: YYYY-MM-DDTHH:mm:ss
updated: YYYY-MM-DDTHH:mm:ss
tags: [tag1, tag2]
---
```

#### Linking

- Use `[[wikilinks]]` to connect notes (e.g. `[[My Project]]`, `[[Research Topic]]`)
- Use `#tags` for categorization
- Cross-reference projects with `[[Project Name]]`

#### Templates

Available in Templates/ folder:

- `daily.md` — Daily journal note
- `project.md` — Project documentation
- `research.md` — Research/learning note
- `idea.md` — Brainstorm/concept note
- `session.md` — Session summary

### When to Use the Vault

- **Before starting work**: Search vault for existing context on the topic
- **During work**: Log important decisions to the daily note
- **After work**: Session summaries are auto-captured by the Stop hook
- **When researching**: Save findings to Research/ with proper tags
- **When brainstorming**: Capture ideas in Ideas/ before they're lost
