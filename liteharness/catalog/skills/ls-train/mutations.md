# Mutation Strategy Reference

This file is read by the trainer during a live `/train` session. It defines how to generate, apply, and log mutations to agent config files.

---

## 1. Mutation Philosophy

Mutations are targeted edits to a target agent's `.claude/agents/{TARGET}.md` config file. The goal is to make the agent produce better outputs on a specific task or class of tasks.

All mutations must be:

- **Atomic**: One change at a time. If you change two things and the score improves, you don't know which change caused it. If the score drops, you can't pinpoint the regression.
- **Reversible**: Always save the pre-mutation config before applying. If a mutation causes a revert, restore the saved version exactly.
- **Logged**: Every mutation gets a description, strategy type, and target section. Logs live in `ai/data/trainer/mutations/cycle_{N}.json`.

---

## 2. Strategy: TARGETED (Trainer Judgment)

The trainer reads Agent A's output, identifies the single weakest aspect, and writes a specific improvement to the config.

**Protocol:**

1. Read Agent A's output carefully
2. Identify the single biggest weakness — not multiple. One at a time.
3. Determine which section of the agent config is responsible for that weakness
4. Write a specific instruction addition or change to address it
5. Apply the edit to the agent config

**Example weaknesses and corresponding mutations:**

| Observed Weakness                              | Mutation                                                                                                                                        |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Output was too verbose                         | Add to `## Output Format`: "Be concise. Lead with the answer, then explain only if needed."                                                     |
| Missed edge case in code                       | Add to `## Code Generation`: "Always consider: empty input, null values, boundary conditions."                                                  |
| Reasoning was jumbled                          | Add to `## Reasoning`: "Structure your thinking: (1) restate the problem, (2) identify constraints, (3) work through step by step, (4) verify." |
| Didn't reference actual code files             | Add to `## Codebase Analysis`: "Always cite specific file paths and line numbers."                                                              |
| Gave wrong answer without flagging uncertainty | Add to `## Output Format`: "When confidence is below ~80%, state what you're unsure about before giving your answer."                           |
| Skipped tool use that would have helped        | Add to `## Tool Usage`: "If answering from memory when a file or search would confirm, use the tool."                                           |

---

## 3. Strategy: POLYMATHIC (Expert Consultation)

Spawn 2-3 polymathic agents to analyze the output and suggest mutations from their domain perspective.

**Protocol:**

1. Pick 2-3 relevant polymathic agents based on the domain:

   | Domain               | Agents to Use                                                              |
   | -------------------- | -------------------------------------------------------------------------- |
   | Code quality         | polymathic-carmack (bottleneck finding), polymathic-linus (taste/elegance) |
   | Writing / docs       | polymathic-ogilvy (persuasion), polymathic-rams (simplicity)               |
   | Reasoning / analysis | polymathic-feynman (first principles), polymathic-tao (decomposition)      |
   | Codebase navigation  | polymathic-carmack (systems), polymathic-gamma (patterns)                  |
   | Strategy / decisions | polymathic-munger (bias detection), polymathic-feynman (first principles)  |

2. Spawn them in background (parallel) with prompt:

   > "Analyze this agent output for task [X]. What is the single most impactful improvement to the agent's instructions that would produce better output? Be specific — suggest exact wording to add or change."

3. Collect all suggestions
4. Pick the most actionable and specific suggestion
5. Translate it into a concrete config edit (section + text change)

---

## 4. Strategy: EVOLUTIONARY (Random Parameter Sweeps)

Systematic exploration of predefined mutation dimensions. Pick ONE dimension and ONE direction per mutation cycle.

**Dimensions:**

| Dimension      | Axis                      | Mutation Examples                                                                              |
| -------------- | ------------------------- | ---------------------------------------------------------------------------------------------- |
| **Tone**       | formal ↔ casual           | "Use professional technical language" ↔ "Write like you're explaining to a friend"             |
| **Detail**     | terse ↔ verbose           | "Maximum 3 sentences per response" ↔ "Explain thoroughly with examples"                        |
| **Reasoning**  | direct ↔ chain-of-thought | "Give the answer immediately" ↔ "Think step by step, show your work"                           |
| **Tool Usage** | conservative ↔ aggressive | "Only use tools when necessary" ↔ "Proactively search, read, and verify before answering"      |
| **Structure**  | rigid ↔ flexible          | "Always use headers and bullet points" ↔ "Use whatever format fits the content"                |
| **Confidence** | hedged ↔ assertive        | "Qualify uncertain statements" ↔ "State conclusions directly, note caveats only when critical" |
| **Scope**      | narrow ↔ broad            | "Answer only what was asked" ↔ "Anticipate follow-up questions and address them"               |

**Protocol:**

1. Pick a random dimension from the table above
2. Pick a random position on the axis — not just the extremes, try midpoints too
3. Find the relevant section in the agent config
4. Add or modify an instruction along that dimension
5. Log which dimension and direction was tried (required for the `dimension` field in the log)

---

## 5. Strategy Selection Rules

```
Cycles 1-3:        TARGETED  (establish baseline behavior understanding)
Cycle 4+:          Rotate: TARGETED → EVOLUTIONARY → TARGETED → POLYMATHIC → repeat
After 3 consecutive reverts:   Force POLYMATHIC  (fresh perspective needed)
After 5 consecutive reverts:   Force EVOLUTIONARY with opposite direction of last attempt
After a KEEP:      Next cycle uses same strategy type  (momentum — keep pushing what works)
```

---

## 6. Mutation Logging Format

Every mutation must be logged. Save to: `ai/data/trainer/mutations/cycle_{N}.json`

```json
{
  "cycle": 1,
  "strategy": "targeted|polymathic|evolutionary",
  "target_section": "## Section Name",
  "description": "What was changed and why",
  "diff_summary": "Added: '...' | Removed: '...' | Changed: '...'",
  "dimension": "tone|detail|reasoning|tool_usage|structure|confidence|scope",
  "polymathic_agents": ["carmack", "feynman"],
  "pre_hash": "abc123",
  "post_hash": "def456"
}
```

**Field notes:**

- `dimension` is only required when `strategy` is `"evolutionary"`
- `polymathic_agents` is only required when `strategy` is `"polymathic"`
- `pre_hash` and `post_hash` are the first 7 chars of the MD5 of the agent config before and after the mutation — used to verify the revert was applied correctly
- `diff_summary` should be human-readable, not a raw diff

---

## 7. Anti-Patterns (What NOT to Mutate)

These changes are off-limits regardless of strategy:

- **Don't change tool access** — tools listed in the frontmatter are fixed per training session
- **Don't change the model** — model in the frontmatter is fixed
- **Don't add more than 5 lines in a single mutation** — keep changes small and attributable
- **Don't remove section headers** — `## Header` structure must be preserved
- **Don't introduce contradictory instructions** — before applying, scan the config for existing instructions that conflict with the new one
- **Don't duplicate existing instructions** — if the config already says something equivalent, strengthen it rather than repeat it
