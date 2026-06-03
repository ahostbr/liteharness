---
name: ls-dream-consolidate
description: Memory consolidation — reflective pass over memory files to synthesize, prune, and organize durable memories. Triggers on 'dream', 'consolidate memory', 'clean up memory', 'organize memories', 'memory maintenance'.
---

# Dream: Memory Consolidation

You are performing a dream — a reflective pass over your memory files. Synthesize what you've learned recently into durable, well-organized memories so that future sessions can orient quickly.

Memory directory: `~\.claude\projects\<project-slug>\memory\` — where `<project-slug>` is your current project's path with `:`, `/`, and `\` replaced by `-` (e.g. `C:\Projects` → `C--Projects`). Create the `memory\` folder if it doesn't exist yet, then write to it with the Write tool.

Session transcripts: `~\.claude\projects\<project-slug>\` (large JSONL files — grep narrowly, don't read whole files)

---

## Phase 1 — Orient

- `ls` the memory directory to see what already exists
- Read `MEMORY.md` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates
- If `logs/` or `sessions/` subdirectories exist, review recent entries there

## Phase 2 — Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Daily logs** if present — these are the append-only stream
2. **Existing memories that drifted** — facts that contradict something you see in the codebase now
3. **Transcript search** — if you need specific context, grep the JSONL transcripts for narrow terms:
   `grep -rn "<narrow term>" "~\.claude\projects\<project-slug>\" --include="*.jsonl" | tail -50`

Don't exhaustively read transcripts. Look only for things you already suspect matter.

## Phase 3 — Consolidate

For each thing worth remembering, write or update a memory file at the top level of the memory directory. Use the memory file format and type conventions from your system prompt's auto-memory section — it's the source of truth for what to save, how to structure it, and what NOT to save.

Focus on:

- Merging new signal into existing topic files rather than creating near-duplicates
- Converting relative dates ("yesterday", "last week") to absolute dates so they remain interpretable after time passes
- Deleting contradicted facts — if today's investigation disproves an old memory, fix it at the source

## Phase 4 — Prune and index

Update `MEMORY.md` so it stays under 200 lines. It's an **index**, not a dump — link to memory files with one-line descriptions. Never write memory content directly into it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Demote verbose entries: keep the gist in the index, move the detail into the topic file
- Add pointers to newly important memories
- Resolve contradictions — if two files disagree, fix the wrong one

---

Return a brief summary of what you consolidated, updated, or pruned. If nothing changed (memories are already tight), say so.
