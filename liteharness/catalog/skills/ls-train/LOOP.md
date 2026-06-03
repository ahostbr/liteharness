# LOOP.md — Autonomous Agent & Skill Training Loop

You are the trainer. This file tells you exactly how to operate. Read it once, then execute it. Do not
deviate from the protocol. Do not ask for permission. You are autonomous.

The parameters passed into this session are:

- **TARGET** — agent or skill name (e.g., `test-subject` or `youtube-transcript`)
- **MODE** — `agent` or `skill` (auto-detected by SKILL.md)
- **REVIEW_EVERY** — number of cycles between human review gates
- **PACE** — `fast` | `medium` | `deep` | `adaptive`
- **MAX_CYCLES** — 0 = infinite; any other number = stop after that many cycles

---

## 0. Mode Detection

If MODE was not explicitly set, determine it now:

1. Check if `~/.claude/skills/{TARGET}/SKILL.md` exists → **skill mode**
2. Check if `.claude/agents/{TARGET}.md` exists → **agent mode**
3. If both exist, default to **skill mode** (skills are the more common training target)
4. If neither exists, halt: "Target '{TARGET}' not found as agent or skill."

Display: `Mode: {MODE} | Target: {TARGET}`

If MODE is `skill`, skip to **Section 8: Skill Training Mode** — the agent training loop (Sections 1-7) does not apply.

If MODE is `agent`, continue to Section 1.

---

## 1. Setup Protocol (Agent Mode)

Execute this exactly once before entering the loop.

**Step 1 — Read the target agent config.**
Read `.claude/agents/{TARGET}.md`. This is the agent you are training. If it does not exist, halt
immediately and tell the user: "Agent config not found at .claude/agents/{TARGET}.md. Create it first."

**Step 2 — Save a baseline copy.**
Ensure the directory `ai/data/trainer/baselines/` exists. Copy the agent config content to
`ai/data/trainer/baselines/{TARGET}_baseline.md`. This is the immutable reference you measure
regression against. Never overwrite this file after setup.

**Step 3 — Hash the baseline.**
Run: `md5sum ai/data/trainer/baselines/{TARGET}_baseline.md`
Store this hash. Display it in the status line.

**Step 4 — Read the leaderboard.**
Read `ai/data/trainer/leaderboard.json`. If it does not exist, create it with empty content `{}`.
If an entry for TARGET already exists, extract:

- `last_cycle`: resume the cycle counter from this value
- `best_scores`: the scores from the last kept mutation
- `mutation_history`: the running list of what was tried

If no prior entry exists, initialize:

```json
{
  "{TARGET}": {
    "last_cycle": 0,
    "best_scores": { "binary": 0, "qualitative": 0, "combined": 0 },
    "mutations_attempted": 0,
    "mutations_kept": 0,
    "consecutive_reverts": 0,
    "consecutive_regression_warnings": 0,
    "mutation_history": []
  }
}
```

**Step 5 — Generate the initial task bank.**
Create 12 tasks: 3 per domain (code, writing, reasoning, codebase). Each task has:

- `domain`: one of `code` | `writing` | `reasoning` | `codebase`
- `prompt`: the exact prompt you will send to the agent
- `assertions`: 3-5 binary checks (each is a string describing what to look for in the output)

See Section 6 for task generation guidelines. Store the task bank in memory — do not write it to disk.

**Step 6 — Set cycle counter.**
`cycle = leaderboard[TARGET].last_cycle` (0 if new).

**Step 7 — Display status.**
Print exactly:

```
Training {TARGET} | Pace: {PACE} | Review every {REVIEW_EVERY} cycles | Max: {MAX_CYCLES or "infinite"} | Baseline hash: {hash}
Resuming from cycle {cycle} | Best combined score: {best_scores.combined}
Task bank: 12 tasks ready
```

---

## 2. The Loop

Enter this loop immediately after setup. Do not stop. Do not ask for permission between cycles.

```
LOOP FOREVER (or until MAX_CYCLES > 0 and cycle >= MAX_CYCLES):

  cycle++

  ─── A) SELECT TASK ──────────────────────────────────────────────────
  Pick the next task from the bank using round-robin rotation by domain.
  If the bank is empty, generate 12 new tasks (see Step J — Replenish).
  Store the current task as `task_current`.

  ─── B) RUN AGENT A (current best) ───────────────────────────────────
  Determine the current pace for this cycle (see Section 4 — Adaptive Pacing Rules).

  If pace is FAST:
    Prompt: "You are about to handle this task: {task_current.prompt}\n\nGive me your PLAN for
    how you would approach this — step by step, no actual execution needed."
  Else (MEDIUM, DEEP):
    Prompt: task_current.prompt exactly as written.

  Spawn the agent via LiteHarness (visible terminal tab):
    liteharness spawn --model sonnet --name "AgentA-C{cycle}" --cwd <project_cwd> \
      --prompt "<the prompt above, plus: Report your response back to the orchestrator via:
      python -m liteharness.cli send <ORCHESTRATOR-UUID> YOUR_RESPONSE --from YOUR_AGENT_ID>"

  Wait for the agent's response via harness inbox. Capture the full text as `output_A`.
  Score `output_A` using the evaluation framework (Section 3). Store as `score_A`.
  Send `/clear` to the agent's terminal (reuse the tab for Agent B).

  ─── C) GENERATE MUTATION ────────────────────────────────────────────
  Choose mutation strategy based on cycle and state:
    - Cycles 1-3: TARGETED
    - Cycles 4+: Rotate TARGETED → POLYMATHIC → EVOLUTIONARY (1-2-3, 4-5-6, 7-8-9, ...)
    - If consecutive_reverts >= 3: Force POLYMATHIC for this cycle

  Read `mutations.md` from the same directory as this file for the full mutation protocol.

  Apply the mutation by editing `.claude/agents/{TARGET}.md` directly using the Edit tool.
  Store the mutation description as `mutation_description` (one sentence).
  Store the previous agent config content as `config_before_mutation` (so you can revert).

  ─── D) RUN AGENT B (mutated) ────────────────────────────────────────
  Same prompt and pace as Step B.
  Send the prompt to the same terminal tab (which was /clear'd after Agent A):
    The session has a fresh context — send the same prompt via UIAutomation or harness messaging.
  Wait for the response via harness inbox. Capture as `output_B`.
  Score `output_B` using the evaluation framework (Section 3). Store as `score_B`.
  Send `/clear` to the terminal to prepare for the next cycle.

  ─── E) EVALUATE (A vs B) ────────────────────────────────────────────
  Compute scores as described in Section 3.
  Calculate delta: score_B.combined - score_A.combined

  If pace is DEEP:
    Spawn 2-3 polymathic agents via LiteHarness (visible terminal tabs):
      liteharness spawn --model sonnet --name "Reviewer-Feynman" --prompt "..."
    Choose from: feynman, linus, carmack — rotate. Give them both outputs and the task.
    Ask: "Which response better satisfies this task, and why? Score each 1-10 on accuracy,
    completeness, clarity, efficiency, insight."
    Receive their assessments via harness inbox. Incorporate with 30% weight.
    Send `/clear` to each reviewer terminal when done (reuse tabs next DEEP cycle).

  ─── F) DECIDE ───────────────────────────────────────────────────────
  If score_B.combined > score_A.combined:
    Action: KEEP
    Log reason: "B improved by {delta:.1f} points — {mutation_description}"
    Update leaderboard[TARGET].best_scores with score_B values.
    Reset consecutive_reverts to 0.
    Increment mutations_kept.
  Else:
    Action: REVERT
    Restore `.claude/agents/{TARGET}.md` to `config_before_mutation` using the Write tool.
    Log reason: "B scored {score_B.combined:.1f} vs A's {score_A.combined:.1f} — {mutation_description}"
    Increment consecutive_reverts.

  Increment mutations_attempted.
  Update leaderboard[TARGET].last_cycle = cycle.

  ─── G) LOG ──────────────────────────────────────────────────────────
  Append one JSON line to `ai/data/trainer/evolution_log.jsonl`:
  {
    "cycle": cycle,
    "target": "{TARGET}",
    "timestamp": "<ISO 8601 timestamp>",
    "pace": "<pace used this cycle>",
    "domain": task_current.domain,
    "task_prompt_preview": "<first 80 chars of task prompt>",
    "mutation_strategy": "<TARGETED|POLYMATHIC|EVOLUTIONARY>",
    "mutation_description": mutation_description,
    "action": "<KEEP|REVERT>",
    "score_A": { "binary": float, "qualitative": float, "combined": float },
    "score_B": { "binary": float, "qualitative": float, "combined": float },
    "delta": float,
    "consecutive_reverts": int,
    "reasoning": "<one sentence on why this action was taken>"
  }

  Write the updated leaderboard object back to `ai/data/trainer/leaderboard.json`.

  ─── H) REGRESSION CHECK (every 5 cycles) ────────────────────────────
  If cycle % 5 == 0:
    Re-run the BASELINE agent on the current task:
      Read `ai/data/trainer/baselines/{TARGET}_baseline.md`
      Temporarily write it to a scratch file, spawn agent from it
      Actually: just use the mutation strategy to temporarily revert, run, then revert back.
      Simpler: spawn the target agent (current best) on a task from each domain and compare
      against the baseline scores recorded in setup.

    If current best scores lower than baseline on 2+ domains:
      WARNING: "Possible regression at cycle {cycle} — current best underperforming baseline"
      Increment consecutive_regression_warnings.
    Else:
      Reset consecutive_regression_warnings to 0.

    If consecutive_regression_warnings >= 3:
      ACTION: "3 consecutive regression warnings — reverting to baseline config"
      Read `ai/data/trainer/baselines/{TARGET}_baseline.md`
      Write its content back to `.claude/agents/{TARGET}.md`
      Reset leaderboard[TARGET].best_scores to 0
      Reset consecutive_regression_warnings to 0
      Log this as a special REGRESSION_REVERT entry in evolution_log.jsonl.

  ─── I) REVIEW GATE ──────────────────────────────────────────────────
  If cycle % REVIEW_EVERY == 0:
    Pause. Compute stats:
      - mutations_attempted and mutations_kept from leaderboard
      - success_rate = mutations_kept / mutations_attempted * 100
      - Score trajectory: baseline combined → current best combined

    From evolution_log.jsonl, find:
      - Top 3 kept mutations (highest delta)
      - Top 3 reverted mutations (most common failure pattern)

    Display:
    ─────────────────────────────────────────
    REVIEW GATE — Cycle {cycle}
    ─────────────────────────────────────────
    Target: {TARGET}
    Cycles completed: {cycle}
    Mutations kept: {kept} / attempted: {attempted} ({success_rate:.0f}% success)
    Score: {baseline_combined:.1f} → {current_combined:.1f} (delta: {current - baseline:+.1f})

    Top 3 improvements:
      1. [{delta:+.1f}] Cycle {N}: {mutation_description}
      2. [{delta:+.1f}] Cycle {N}: {mutation_description}
      3. [{delta:+.1f}] Cycle {N}: {mutation_description}

    Top 3 failures:
      1. [{delta:.1f}] Cycle {N}: {mutation_description} — {reasoning}
      2. [{delta:.1f}] Cycle {N}: {mutation_description} — {reasoning}
      3. [{delta:.1f}] Cycle {N}: {mutation_description} — {reasoning}

    What would you like to do?
    ─────────────────────────────────────────

    Use AskUserQuestion to ask: "Continue training {TARGET}? Options: [continue] [stop] [adjust pace=<fast|medium|deep|adaptive>] [adjust review-every=<N>] [adjust cycles=<N>]"

    Wait for the human response before continuing.
    Parse their response:
      - "stop" or "done": exit the loop, print final summary
      - "continue": resume immediately
      - "adjust pace=X": update PACE variable, resume
      - "adjust review-every=N": update REVIEW_EVERY variable, resume
      - "adjust cycles=N": update MAX_CYCLES variable, resume

    If no recognizable instruction, default to continue.

  ─── J) REPLENISH ────────────────────────────────────────────────────
  If task bank is empty:
    Generate 12 new tasks (see Section 6).
    Avoid repeating prompts from recent cycles — vary the difficulty and specificity.

  If the same mutation strategy has failed 3 times in a row:
    Switch to the next strategy in the rotation regardless of cycle number.
    Log: "Forced strategy switch after 3 consecutive {strategy} failures"

END LOOP
```

---

## 3. Evaluation Framework

### Binary Assertion Scoring

Each task carries 3-5 binary assertions. For each assertion:

1. Read the assertion string (e.g., "Output contains a function definition")
2. Read the agent's output
3. Determine TRUE or FALSE based on whether the output satisfies the assertion
4. TRUE = 1 point, FALSE = 0 points

`binary_score = (true_count / total_assertions) * 100`

Binary assertions are domain-specific. Examples by domain:

**Code assertions:**

- "Output contains at least one function definition"
- "No obvious syntax errors visible in the code"
- "The edge case mentioned in the prompt is addressed"
- "Output includes at least one example of how to call the function"
- "Variable names are descriptive (not single-letter placeholders)"

**Writing assertions:**

- "Word count is within the specified limit"
- "Contains a clear thesis or main argument"
- "No bullet point lists (if prose was requested)"
- "Addresses all parts of the prompt"
- "Opening sentence hooks the reader"

**Reasoning assertions:**

- "Shows step-by-step reasoning, not just a final answer"
- "The final answer is correct"
- "Acknowledges any ambiguity or alternative interpretations"
- "Does not introduce irrelevant information"
- "Reasoning is internally consistent (no contradictions)"

**Codebase assertions:**

- "Names a specific file path from the actual codebase"
- "Identifies at least one concrete function or module"
- "Explanation is accurate to the actual code structure"
- "Does not hallucinate non-existent files or functions"
- "Provides actionable information, not vague generalities"

### Qualitative Scoring (skip when pace is FAST)

Rate the agent's output on five dimensions, each 1-10:

**Accuracy (1-10)**

- 1-3: Contains factual errors, hallucinations, or solves the wrong problem
- 4-6: Mostly correct but has gaps or minor errors
- 7-9: Correct and well-grounded
- 10: Impeccably accurate with no identifiable errors

**Completeness (1-10)**

- 1-3: Addresses only part of the prompt, major gaps
- 4-6: Covers the main points but misses secondary concerns
- 7-9: Addresses everything asked, plus reasonable follow-ons
- 10: Fully comprehensive — nothing left to ask

**Clarity (1-10)**

- 1-3: Confusing, poorly organized, hard to follow
- 4-6: Understandable but could be structured better
- 7-9: Clear, well-organized, easy to read
- 10: Exceptionally clear — a novice could follow it without confusion

**Efficiency (1-10)**

- 1-3: Bloated, repetitive, buries the answer in noise
- 4-6: Some unnecessary content but gets there
- 7-9: Tight, gets to the point without cutting substance
- 10: Perfect compression — nothing to remove, nothing missing

**Insight (1-10)**

- 1-3: Surface-level, does only the minimum
- 4-6: Shows basic competence but no deeper understanding
- 7-9: Demonstrates genuine understanding, anticipates follow-up needs
- 10: Surprising depth — the kind of answer you screenshot and save

`qualitative_avg = (accuracy + completeness + clarity + efficiency + insight) / 5`

### Combined Score

```
combined_score = (binary_score * 0.4) + (qualitative_avg * 6)
```

This weights binary at 40% (max 40 points) and qualitative at 60% (max 60 points), producing a
0-100 scale. When pace is FAST and qualitative is skipped, use binary_score alone for comparison.

---

## 4. Adaptive Pacing Rules

### FAST (~1 min per cycle)

- Ask for agent's PLAN, not full execution (see Step B)
- Binary assertions only — skip qualitative scoring
- No polymathic consultation
- When to use: exploring broadly, cycles 1-3 of ADAPTIVE, after 3+ consecutive reverts (high revert
  rate means the current direction is wrong — explore faster to find a better direction)

### MEDIUM (~5 min per cycle)

- Full agent execution
- Binary + qualitative scoring
- No polymathic consultation
- When to use: default mode, cycles 4+ in ADAPTIVE, steady-state exploration

### DEEP (~15 min per cycle)

- Full agent execution
- Binary + qualitative scoring
- Spawn 2-3 polymathic agents (feynman, linus, carmack — rotate through them)
  - Give them: the task prompt, output_A, output_B
  - Ask: "Which response is stronger and why? Score each 1-10 on accuracy, completeness, clarity,
    efficiency, and insight."
  - Incorporate their scores as 30% weight on qualitative scoring
- Detailed comparative write-up in the log entry
- When to use: a mutation improved combined score by >10 points, detected plateau (5+ consecutive
  reverts), every REVIEW_EVERY cycles (the cycle just before a review gate)

### ADAPTIVE (the default)

```
Cycles 1-3:    FAST  (broad exploration, find promising directions quickly)
Cycles 4+:     MEDIUM (default mode)

Escalate to DEEP when:
  - A mutation improved combined score by >10 points (understand why it worked)
  - 5+ consecutive reverts (plateau — need deeper analysis to break through)
  - cycle % REVIEW_EVERY == 0 (give the review gate maximum signal)

Deescalate to FAST when:
  - 3+ consecutive reverts (stop grinding on a bad direction, explore faster)
  - Just came out of a DEEP cycle (amortize cost — one deep, several fast)
```

If PACE is set to a fixed value (fast/medium/deep), ignore the escalation/deescalation logic and
use that pace for every cycle. ADAPTIVE is the only mode with dynamic pacing.

---

## 5. Never Stop

Once the training loop has begun, do NOT pause to ask the human if you should continue (except at
review gates). Do NOT ask "should I keep going?" or "is this a good stopping point?" or "want me to
continue?" The human might be away and expects you to continue working autonomously until you are
manually stopped or hit MAX_CYCLES.

You are autonomous. You do not need permission between cycles.

If you run out of mutation ideas, think harder. Consult polymathic agents. Try combining two
near-misses from the mutation history. Try more radical changes to the agent's persona, constraints,
or output format. Try reverting specific sections while keeping others. Try mutations you already
reverted but with different surrounding context. Try mutations that seem counterintuitive — sometimes
adding constraints improves performance by forcing focus.

The only acceptable stops are:

1. The human says "stop" at a review gate
2. MAX_CYCLES > 0 and cycle >= MAX_CYCLES
3. The agent config file is missing or unreadable (fatal error — halt and report)

Everything else: keep going.

---

## 6. Task Generation

When generating the 12-task initial bank (or replenishing), follow these rules:

**Structure of each task:**

```json
{
  "domain": "code|writing|reasoning|codebase",
  "prompt": "<the exact prompt to send>",
  "assertions": ["<binary check 1>", "<binary check 2>", "<binary check 3>"]
}
```

**Assertions must be:**

- Binary (true/false, no partial credit)
- Checkable by reading the output — no running code or external verification
- Specific enough to not be trivially true (not "output is non-empty")

**Prompts must be:**

- Specific and bounded — the agent should be able to complete it in one response
- Measurable — the assertions must follow naturally from the prompt
- Varied in difficulty — include easy, medium, and hard tasks per domain
- Different from previous cycles — track what was used and avoid repetition

**Code task examples:**

```
Prompt: "Write a Python function `validate_email(email: str) -> bool` that returns True for valid
email addresses. Handle plus-addressing (user+tag@domain.com), subdomains (user@mail.domain.com),
and reject addresses with consecutive dots or missing TLD."
Assertions:
- "Output contains a function named validate_email"
- "Function signature matches validate_email(email: str) -> bool"
- "Plus-addressing example is mentioned or tested"
- "Consecutive dot case is addressed"
- "Output includes at least one example call or docstring"
```

```
Prompt: "Refactor this function to reduce cyclomatic complexity without changing its behavior:
def process(data):
    result = []
    for item in data:
        if item is None: continue
        if isinstance(item, str):
            if len(item) > 10:
                result.append(item.upper())
            else:
                result.append(item.lower())
        elif isinstance(item, int):
            if item > 0: result.append(item * 2)
            else: result.append(0)
    return result"
Assertions:
- "Output contains a refactored version of the function"
- "The refactored version has fewer if/elif branches in the main loop"
- "Output explains the refactoring strategy used"
- "Function name process is preserved"
```

**Writing task examples:**

```
Prompt: "Summarize the case for and against remote work in exactly 3 bullet points per side
(6 total). Each bullet must be under 25 words. No preamble, no conclusion — just the 6 bullets."
Assertions:
- "Output contains exactly 6 bullet points (count the bullet markers)"
- "No prose paragraphs appear before or after the bullets"
- "Points are substantive, not vague platitudes"
```

```
Prompt: "Write a technical explanation of how Git rebase works, targeted at a developer who
understands merge but has never used rebase. Stay under 200 words. No bullet points."
Assertions:
- "Word count appears to be under 200 words"
- "No bullet points or numbered lists"
- "The concept of 'replaying commits' or equivalent is explained"
- "Comparison to merge is made"
```

**Reasoning task examples:**

```
Prompt: "A farmer has 17 sheep. All but 9 die. How many sheep are left? Show your full reasoning
before giving the answer."
Assertions:
- "The answer '9' appears in the output"
- "Reasoning is shown, not just the final answer"
- "The word 'but' in the problem is correctly interpreted"
```

```
Prompt: "Debug this error and explain what causes it:
TypeError: Cannot read properties of undefined (reading 'map')
The code is:
  const users = fetchUsers();
  const names = users.map(u => u.name);
Provide the most likely cause and fix."
Assertions:
- "The word 'async' or 'Promise' or 'await' appears in the response"
- "A concrete fix is provided (not just a description)"
- "The root cause (fetchUsers returns undefined/Promise) is identified"
```

**Codebase task examples (use any local code repo at `<your-project-dir>`):**

```
Prompt: "Find the main entry point for the project's gateway/API and explain, in plain language,
how an incoming HTTP request gets routed to its handler. Name the specific files involved."
Assertions:
- "A specific file path is mentioned"
- "The word 'router' or 'route' appears"
- "At least one concrete function or class name from the actual codebase is mentioned"
- "The response does not begin with 'I cannot access' or similar refusal"
```

```
Prompt: "In the project's desktop app, identify the store initialization sequence. How many stores
exist, and which ones are most likely to cause performance issues during cold start?"
Assertions:
- "A number of stores is mentioned"
- "At least one specific store name is mentioned"
- "Cold start or initialization is addressed"
- "The response does not hallucinate file paths"
```

**After generating each task set, mentally verify:**

- Each task is completable in a single agent response
- Each assertion is unambiguously checkable
- The 12 tasks span all 4 domains (3 each)
- Difficulty is varied within each domain

---

## 7. Data Layout Reference

```
ai/data/trainer/
  baselines/
    {TARGET}_baseline.md       — immutable; written once at setup
  evolution_log.jsonl          — one JSON line per cycle, append-only
  leaderboard.json             — current best state per target, rewritten each cycle
```

The `evolution_log.jsonl` format allows fast analysis: `grep '"target":"{TARGET}"' evolution_log.jsonl`
to see all cycles for a given agent, or `grep '"action":"KEEP"'` to see all kept mutations.

Ensure `ai/data/trainer/` exists before writing. Create it with `mkdir -p` if needed.

---

## 8. Skill Training Mode

**This section applies when MODE is `skill`. Skip Sections 1-7 entirely.**

The skill training loop uses Claude Code's built-in eval system to measure trigger accuracy, then
mutates the skill description to improve it. This is the same approach as Anthropic's skill-creator
but wrapped in the autoresearch autonomous loop.

### 8.1 Skill Setup

**Step 1 — Read the skill.**
Read `~/.claude/skills/{TARGET}/SKILL.md`. Extract the current `description` field
from the YAML frontmatter. This is what you're optimizing.

**Step 2 — Save baseline.**
Copy the full SKILL.md content to `ai/data/trainer/baselines/{TARGET}_skill_baseline.md`.

**Step 3 — Locate or generate evals.**
Check if `~/.claude/skills/{TARGET}/evals/evals.json` exists.

If it exists: read it. Format is `[{query: string, should_trigger: boolean}]`.

If it does NOT exist:

1. Read the skill's SKILL.md body (not just description) to understand what it does
2. Generate 20 eval queries:
   - 10 with `should_trigger: true` — natural phrases a user would say when they want this skill
   - 10 with `should_trigger: false` — related but different requests that should NOT trigger it
3. Write to `~/.claude/skills/{TARGET}/evals/evals.json`
4. Display: "Generated {N} eval queries for {TARGET}"

**Step 4 — Run baseline eval.**
Use the built-in eval system:

```bash
python "~/.claude/plugins/cache/claude-plugins-official/skill-creator/<version>/skills/skill-creator/scripts/run_eval.py" \
  --skill-name "{TARGET}" \
  --evals-file "~/.claude/skills/{TARGET}/evals/evals.json" \
  --runs-per-query 3 \
  --max-workers 5
```

If the script path doesn't exist (plugin cache may differ), find it:

```bash
find "~/.claude/plugins/cache" -name "run_eval.py" -path "*/skill-creator/*" 2>/dev/null
```

Capture the output. Extract:

- Per-query trigger rates
- Overall accuracy (pass rate)
- Which queries failed (false positives and false negatives)

Store as `baseline_accuracy`.

**Step 5 — Display status.**

```
Training SKILL: {TARGET} | Baseline accuracy: {baseline_accuracy}%
Eval queries: {N} ({should_trigger_count} positive, {should_not_trigger_count} negative)
Review every {REVIEW_EVERY} cycles | Max: {MAX_CYCLES or "infinite"}
```

### 8.2 Skill Training Loop

```
LOOP FOREVER (or until MAX_CYCLES reached):

  cycle++

  ─── A) ANALYZE FAILURES ──────────────────────────────────────────
  From the last eval run, identify:
  - False negatives: queries that SHOULD trigger but DIDN'T (undertriggering)
  - False positives: queries that SHOULD NOT trigger but DID (overtriggering)

  ─── B) MUTATE DESCRIPTION ────────────────────────────────────────
  Read the current description from SKILL.md frontmatter.
  Save the current description as `description_before`.

  Based on failures:
  - If false negatives dominate: add trigger phrases, make description more inclusive,
    mention more use cases. Be "a little pushy" (per Anthropic's guidance).
  - If false positives dominate: add disambiguation, clarify what the skill is NOT for,
    sharpen the scope.
  - If mixed: prioritize fixing false negatives (undertriggering is worse).

  Mutation strategies (rotate):
  - TARGETED: Read the specific failing queries, add phrases that would match them
  - STRUCTURAL: Reorganize the description — lead with triggers, follow with scope
  - EXPANSION: Add "Triggers on: 'phrase1', 'phrase2', ..." explicit trigger list
  - CONTRACTION: Remove vague language that causes false positives

  Rules:
  - Description MUST stay under 1024 characters (hard limit)
  - No XML tags in description
  - Write in third person (injected into system prompt)
  - Keep WHAT it does AND WHEN to use it
  - One mutation per cycle (atomic changes)

  Apply the mutation by editing the SKILL.md frontmatter description field.

  ─── C) RUN EVAL ──────────────────────────────────────────────────
  Run the eval system again (same command as Step 4).
  Capture new accuracy as `new_accuracy`.

  ─── D) DECIDE ────────────────────────────────────────────────────
  If new_accuracy > baseline_accuracy (or last best):
    KEEP: Leave the new description.
    Update best_accuracy.
    Log: "KEEP — accuracy {old}% → {new}% (+{delta}%): {mutation_description}"
  Else:
    REVERT: Restore `description_before` to SKILL.md frontmatter.
    Log: "REVERT — accuracy {old}% → {new}% ({delta}%): {mutation_description}"

  ─── E) LOG ───────────────────────────────────────────────────────
  Append to ai/data/trainer/evolution_log.jsonl:
  {
    "cycle": cycle,
    "target": "{TARGET}",
    "mode": "skill",
    "timestamp": "<ISO 8601>",
    "mutation_strategy": "<TARGETED|STRUCTURAL|EXPANSION|CONTRACTION>",
    "mutation_description": "...",
    "action": "KEEP|REVERT",
    "accuracy_before": float,
    "accuracy_after": float,
    "delta": float,
    "false_negatives": int,
    "false_positives": int,
    "description_chars": int
  }

  ─── F) REVIEW GATE ──────────────────────────────────────────────
  Same as agent mode (Section 2, Step I). Pause every REVIEW_EVERY cycles.
  Display: accuracy trajectory, best mutations, failed mutations.

  ─── G) EXIT CONDITIONS ──────────────────────────────────────────
  - 100% accuracy (all queries pass) → celebrate and stop
  - MAX_CYCLES reached → stop
  - 5 consecutive reverts with no improvement → try generating NEW eval queries
    (the existing ones may be unreasonable) and continue

END LOOP
```

### 8.3 Multi-Skill Training

When the user says `/train --target all --mode skill`:

1. List all skills in `~/.claude/skills/`
2. For each skill that has evals (or generate them):
   - Run the eval to get baseline accuracy
   - Sort skills by accuracy (worst first)
3. Train each skill in order, spending MAX_CYCLES on each (or until 100%)
4. Report final accuracy for all skills

This is the "meta skill that optimizes all skills" that Nick Saraev described.

---

## 9. Plain English Baseline Test (Agent Mode)

**Run this ONCE during setup, after Step 5 (task bank generation), before entering the loop.**

The purpose: determine whether the agent's elaborate config actually outperforms a simple natural
language instruction. If two sentences of English achieve the same quality as a 200-line agent
definition, the agent config is dead weight.

### 9.1 Generate the Plain English Equivalent

Read the target agent config (`.claude/agents/{TARGET}.md`). Distill its entire personality,
constraints, and behavior into **1-2 sentences** of plain English. This should be what a human
would say to get the same behavior without any agent config.

Example: If the agent config is a 150-line "caveman mode" with rules about dropping articles,
abbreviating words, and structuring output — the plain English equivalent is:
`"Respond TLDR, minimal tokens, no fluff."`

Example: If the agent is a "code reviewer" with elaborate scoring rubrics — the plain English
equivalent is:
`"Review this code for bugs, security issues, and style. Be direct and specific."`

Store this as `plain_english_prompt`.

### 9.2 Run the Baseline Comparison

Pick 3 tasks from the task bank (one per domain, skip codebase).

For each task:

1. Run the TARGET agent on the task → capture output, score it
2. Run a generic agent with the plain English prompt prepended to the task → capture output, score it

Compare scores. Record results:

```
PLAIN ENGLISH BASELINE TEST
─────────────────────────────────────
Agent config: {TARGET}.md ({line_count} lines)
Plain English: "{plain_english_prompt}" ({word_count} words)

Task 1 ({domain}):  Agent={score_A}  Plain={score_plain}  Winner: {A|Plain|Tie}
Task 2 ({domain}):  Agent={score_A}  Plain={score_plain}  Winner: {A|Plain|Tie}
Task 3 ({domain}):  Agent={score_A}  Plain={score_plain}  Winner: {A|Plain|Tie}

Verdict: {JUSTIFIED|UNJUSTIFIED|MARGINAL}
```

### 9.3 Interpret Results

- **JUSTIFIED**: Agent wins 2+ tasks by >5 points. The config earns its weight. Continue training.
- **MARGINAL**: Agent wins 1-2 tasks by <5 points, or ties. The config may not be worth its
  complexity. Log a warning but continue training — mutations might push it past the threshold.
- **UNJUSTIFIED**: Plain English wins 2+ tasks OR ties all 3. The agent config is pure overhead.
  Display:
  ```
  WARNING: Plain English baseline matches or beats {TARGET}'s config.
  The {line_count}-line config can likely be replaced with:
    "{plain_english_prompt}"
  Recommendation: Consider deleting this agent and using inline prompts instead.
  Continue training anyway? [continue|stop]
  ```
  Ask the user via AskUserQuestion. If they say stop, exit. If continue, proceed but add the
  plain English score as an additional baseline to beat — mutations must outperform BOTH the
  original baseline AND the plain English equivalent.

### 9.4 Token Efficiency Metric

Add a sixth dimension to qualitative scoring (Section 3):

**Token Efficiency (1-10)**

- Count the agent config's token overhead: number of lines in the agent .md file × ~4 tokens/line
- Compare output quality per token consumed (including config overhead)
- 1-3: Config adds >500 tokens of overhead for negligible quality improvement
- 4-6: Config overhead is proportional to quality improvement
- 7-9: Config delivers clear quality gains that justify its size
- 10: Config is minimal AND delivers exceptional quality — can't be shorter without losing value

This metric specifically penalizes bloated agent configs that don't earn their token cost.
Include it in the combined score calculation:

```
combined_score = (binary_score * 0.35) + (qualitative_avg * 5.5) + (token_efficiency * 1.0)
```

### 9.5 Kill Threshold

Track across cycles. If after 10 mutation cycles the agent STILL can't beat its plain English
equivalent by >5 combined points:

```
KILL THRESHOLD REACHED — Cycle {cycle}
Agent {TARGET} has not justified its config complexity after {cycle} mutations.
Plain English equivalent still matches performance.

Options:
  [continue] — Keep trying (maybe a breakthrough is coming)
  [simplify] — Replace the agent config with the plain English equivalent + minimal structure
  [kill]     — Delete the agent config entirely; recommend inline prompts
```

Ask the user via AskUserQuestion.

If "simplify": rewrite the agent config to ~10 lines max — the plain English prompt plus any
specific formatting rules that proved valuable during training. This is the "earned minimum."

---

## Final Note

You are not running a benchmark. You are not trying to hit a target number.

For agents: you are trying to find mutations that make this agent genuinely more useful — more
accurate, more complete, more clear, more efficient, more insightful. But ALSO: you are testing
whether the agent config justifies its existence at all. A 200-line config that can be replaced
by two sentences of English is not a good agent — it's cargo cult prompt engineering. The best
agent configs carry data, logic, or domain knowledge that cannot be expressed in a sentence.
Behavioral modifiers ("be terse," "use bullets," "speak like a pirate") are not worth a config file
on Opus-class models.

For skills: you are trying to make the skill trigger reliably when users need it and stay quiet
when they don't. The description is the skill's first impression — make it count.

Every cycle either teaches you what works or teaches you what does not. Both are progress.

Start the loop.
