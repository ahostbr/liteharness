---
name: ls-self-improve
description: |
  Orchestrate the self-improvement meta loop — scan all skills, identify underperformers,
  train their descriptions via /train, and recompile via LiteCLI so the agent's own interface
  gets sharper every cycle. Triggers on 'self-improve', 'improve my skills', 'sharpen skills',
  'optimize descriptions', 'train all skills', 'meta improve', 'self improve loop',
  'improve skill descriptions', 'run self-improvement', 'make skills better'.
version: "1.0.0"
tags: [meta, self-improvement, training, litecli, automation]
---

# Self-Improve — Autonomous Skill Description Optimizer

Orchestrates the meta loop: scan skills, evaluate trigger accuracy, train underperformers via `/train`, recompile via LiteCLI, and report results. Claude Code (Opus 4.8 or later) is the brain — this skill cannot run on local models.

## What This Does

Your skills are only as good as their descriptions. A bad description means the wrong skill fires (or the right one doesn't). This skill closes the loop:

1. **Scan** all skills in `~/.claude/skills/`
2. **Evaluate** each skill's trigger accuracy using the built-in eval system
3. **Prioritize** by accuracy (worst first) or flag skills without evals
4. **Train** underperformers via `/train --target <skill> --mode skill`
5. **Recompile** any skill whose description changed via LiteCLI
6. **Report** what improved, new accuracy scores, what was recompiled

After this runs, your skill descriptions are tighter, your `--help` text is current, and agents pick the right tool more often.

## Variables

| Parameter        | Flag               | Default | Description                                           |
| ---------------- | ------------------ | ------- | ----------------------------------------------------- |
| Max skills       | `--max-skills <N>` | `3`     | How many skills to train this session (worst-first)   |
| Cycles per skill | `--cycles <N>`     | `5`     | Training cycles per skill (passed to /train)          |
| Skip compile     | `--skip-compile`   | `false` | Skip LiteCLI recompilation step                       |
| Target           | `--target <name>`  | (all)   | Train a single specific skill instead of scanning all |
| Generate evals   | `--gen-evals`      | `false` | Generate evals for skills that don't have them        |

## Instructions

### Phase 1: Scan

1. List all skill directories in `~/.claude/skills/`
2. For each skill directory containing `SKILL.md`:
   a. Read the YAML frontmatter — extract `name` and `description`
   b. Check if `evals/evals.json` exists in that skill directory
   c. Record: `{name, path, has_evals, description_length}`
3. Display a summary table:

```
Skill Inventory:
| Skill              | Has Evals | Description Length |
|--------------------|-----------|-------------------|
| youtube-transcript | Yes       | 312 chars         |
| train              | Yes       | 487 chars         |
| self-improve       | Yes       | 289 chars         |
| vault              | No        | 156 chars         |
...
Total: N skills | With evals: M | Without evals: K
```

### Phase 2: Evaluate

For each skill that has evals, run the eval system:

```bash
SKILL_CREATOR_DIR=$(find "~/.claude/plugins/cache" -name "run_eval.py" -path "*/skill-creator/*" 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname)
cd "$SKILL_CREATOR_DIR" && python -m scripts.run_eval \
  --eval-set "~/.claude/skills/<skill-name>/evals/evals.json" \
  --skill-path "~/.claude/skills/<skill-name>" \
  --runs-per-query 3 \
  --num-workers 5
```

**Finding and running the eval script:** The path changes with plugin cache versions. Find the skill-creator root, then run as a module:

```bash
SKILL_CREATOR_DIR=$(find "~/.claude/plugins/cache" -name "run_eval.py" -path "*/skill-creator/*" 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname)
cd "$SKILL_CREATOR_DIR" && python -m scripts.run_eval \
  --eval-set "~/.claude/skills/<skill-name>/evals/evals.json" \
  --skill-path "~/.claude/skills/<skill-name>" \
  --runs-per-query 3 \
  --num-workers 5
```

Capture accuracy for each skill. Update the summary table with accuracy scores.

If `--gen-evals` is set, also generate evals for skills that don't have them:

- Read the skill's SKILL.md to understand what it does
- Generate 20 queries: 10 should-trigger, 10 should-not-trigger
- Write to `~/.claude/skills/<skill-name>/evals/evals.json`
- Run the eval to get baseline accuracy

### Phase 3: Prioritize

Sort skills by accuracy (ascending — worst first).

If `--target <name>` was specified, skip sorting and just use that skill.

Otherwise, take the top `--max-skills` worst performers. If a skill is already at 100%, skip it.

Display:

```
Training Queue (worst-first):
1. vault — 65% accuracy (7 false negatives, 2 false positives)
2. mockup — 72% accuracy (4 false negatives, 1 false positive)
3. local-lens — 80% accuracy (2 false negatives, 3 false positives)
```

### Phase 4: Train

For each skill in the training queue:

1. Spawn a dedicated training agent via LiteHarness (visible terminal tab):
   ```bash
   liteharness spawn --model sonnet --name "Trainer-<skill-name>" \
     --prompt "/train --target <skill-name> --mode skill --cycles <cycles-per-skill>"
   ```
   The agent runs the `/train` mutation loop autonomously in its own visible terminal.
   Use `/clear` between skills to reuse the same terminal tab.
2. `/train` handles the mutation loop autonomously (see train/LOOP.md Section 8)
3. Monitor progress via harness inbox or UIAutomation (`liteharness read-output --headed <handle:pane>`)
4. After training completes, record:
   - Old description vs new description
   - Old accuracy vs new accuracy
   - Whether the description actually changed

### Phase 5: Recompile (unless --skip-compile)

For each skill whose description changed during training:

1. Run LiteCLI compile:

> **Requirement:** LiteCLI must be installed and on your PATH, or available in your LiteCore source tree. If you have the LiteCore source, run from its `resources/` directory. If you installed the `liteharness` PyPI package, the compiler may already be available as `litecli`.

```bash
# If LiteCore source is available at <your-litecore-dir>:
cd <your-litecore-dir>/resources && python -m litecli compile "~/.claude/skills/<skill-name>/SKILL.md"
# If litecli is on PATH:
litecli compile "~/.claude/skills/<skill-name>/SKILL.md"
```

2. Verify the compiled CLI exists:

```bash
ls ~/.litecli/compiled/<skill_name>.py
```

3. Extract the docstring from the compiled file to confirm the new description landed:

```bash
python -c "import ast; tree=ast.parse(open('PATH').read()); print(ast.get_docstring(tree).split(chr(10))[0][:80])"
```

**Note:** Not all skills are compilable (only API-based skills with URLs produce valid CLIs). If compilation fails with "too complex for automatic compilation", that's fine — the trained description still lives in SKILL.md and works for Claude Code's skill matching. Log it and move on.

### Phase 6: Report

Display the final summary:

```
Self-Improvement Report
=======================
Skills scanned: N
Skills evaluated: M
Skills trained: K

Results:
| Skill         | Before | After  | Delta  | Recompiled |
|---------------|--------|--------|--------|------------|
| vault         | 65%    | 90%    | +25%   | No (no URLs) |
| mockup        | 72%    | 85%    | +13%   | Yes        |
| local-lens    | 80%    | 95%    | +15%   | Yes        |

Description changes:
- vault: Added trigger phrases for "create a note", "daily note", "journal entry"
- mockup: Clarified disambiguation from canvas-design skill
- local-lens: Added "compress context" and "offload to local model" triggers

Compiled CLIs updated: 2
Next run: Consider training the next 3 worst performers
```

## Important Notes

### Claude Code (Opus 4.8 or Later) Is Required

This skill cannot run on local models. Training skill descriptions requires:

- Reading full SKILL.md files and understanding what each skill does
- Generating diverse eval queries that test edge cases
- Evaluating whether a description mutation improved trigger accuracy
- Understanding codebase context to write good descriptions

### LiteCLI Compile Path

> **Requirement:** LiteCLI must be installed. It is part of LiteCore's `resources/` directory or available via `pip install liteharness`. If you have the LiteCore source, point to its `resources/` directory.

```bash
# If LiteCore source is available:
cd <your-litecore-dir>/resources && python -m litecli compile <skill-path>
# If litecli is on PATH:
litecli compile <skill-path>
```

Compiled CLIs go to `~/.litecli/compiled/`. The compiler reads the `description` field from SKILL.md YAML frontmatter and uses it as the module docstring (which becomes `--help` text).

### What Gets Better

- **Skill descriptions** — tighter trigger phrases, fewer false positives/negatives
- **CLI help text** — compiled CLIs show the trained description as `--help`
- **Agent tool selection** — any LLM consuming these descriptions picks the right tool more often
- **The loop itself** — a better self-improve description means self-improve triggers more reliably

### Relationship to /train

This skill is an orchestrator that calls `/train` for each underperforming skill. `/train` does the actual mutation loop (LOOP.md Section 8). Self-improve adds the scanning, prioritization, recompilation, and reporting layers on top.
