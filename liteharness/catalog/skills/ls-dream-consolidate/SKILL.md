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

## Phase 3 — Consolidate (typed, tool-driven)

Do NOT free-form delete or wholesale-overwrite memories in this phase. Route every consolidation decision through the deterministic tool, which acquires a single-writer lock, embeds the items, and emits typed proposals. The tool is the destructive floor; you apply its typed verdicts as additive edits.

**Step 1 — Run the router.** From the repo, run:

```
python -m liteharness.memory_consolidate propose --dir <memory-dir>
```

`<memory-dir>` is the Phase 1 memory directory. The tool acquires the write lock (`<state-dir>/.write.lock`; a concurrent run aborts with "another consolidation is in progress"), embeds each item, and prints JSON proposals with an `action` per pair. The bare `propose` walk over existing items can only ever emit `MERGE_UPDATE` / `SKIP` / `KEEP_SEPARATE` — it is structurally incapable of proposing a destructive `REPLACE`. To surface a genuine contradiction as a candidate `REPLACE`, pass a candidates file: `--candidates <file.json>` (a list of `{id, text, contradiction: true}`); even then it is only ever _proposed_, never applied.

**Step 2 — Apply the typed verdicts as ADDITIVE edits.** For each proposal:

- **`MERGE_UPDATE`** — fold the new signal into the existing topic file. Add/enrich; do not delete the old wording wholesale. Convert relative dates ("yesterday", "last week") to absolute dates so they stay interpretable.
- **`KEEP_SEPARATE`** — the items are distinct. Leave both; optionally write the new one as its own file.
- **`SKIP`** — pure duplicate/restatement. Do nothing.
- **`REPLACE`** (destructive, proposal-only) — never execute it yourself and never hand-delete the contradicted memory. It stays a proposal in the ledger. Only after you have confirmed the replacement is correct, execute it through the tool:

  ```
  python -m liteharness.memory_consolidate apply-replace <mutation-id>
  ```

  This is the ONLY destructive path. It re-checks the cosine ≥ 0.9 floor, pre-images the live bytes (recoverable via `undo <mutation-id>`), then overwrites — all under the same write lock. A REPLACE below the floor is refused. If in doubt, leave it as a proposal.

Use the memory file format and type conventions from your system prompt's auto-memory section for any file you write or enrich — it's the source of truth for what to save, how to structure it, and what NOT to save.

## Phase 4 — Prune and index

Update `MEMORY.md` so it stays under 200 lines. It's an **index**, not a dump — link to memory files with one-line descriptions. Never write memory content directly into it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Demote verbose entries: keep the gist in the index, move the detail into the topic file
- Add pointers to newly important memories
- Resolve index contradictions — if two MEMORY.md pointers describe the same topic or conflict, merge/update the INDEX entries (one-line summaries only). Do NOT hand-edit topic-file content here; content contradictions go through the tool's typed REPLACE proposal path (Phase 3)

---

Return a brief summary of what you consolidated, updated, or pruned. If nothing changed (memories are already tight), say so.
