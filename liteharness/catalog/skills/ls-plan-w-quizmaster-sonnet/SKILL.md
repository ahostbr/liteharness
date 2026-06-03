---
name: ls-plan-w-quizmaster-sonnet
description: Triggers on 'autonomous plan', 'auto-plan', 'sonnet plan', or 'plan-w-quizmaster-sonnet' — also when user says they want a plan made while they're away or without their involvement. Opus runs the Quizmaster interrogation while a spawned Sonnet agent answers as the developer, grounded in codebase context. The user is an observer only — no questions asked of them. Use /plan-w-quizmaster (interactive) when the user wants to answer questions themselves.
---

# Autonomous Quizmaster Planning (Sonnet via LiteHarness)

Run the full Quizmaster interrogation autonomously: **Opus asks the questions, a Sonnet/Opus agent answers them** grounded in the actual codebase, project context, and memory. The agent is spawned via LiteHarness (visible terminal) and communicates via **two channels**: inbox messaging for codebase Q&A, and UIAutomation for answering AskUserQuestion prompts.

## How It Works

```
User: "sonnet plan: rebuild the auth system"
  |
  v
Opus (Quizmaster) ──harness msg──> Developer Agent (visible WT tab)
  ^                                     |
  |──────────harness msg────────────────|  ← inbox: codebase Q&A
  |                                     |
  |──── UIAutomation read-output ───────|  ← terminal buffer: detect AskUserQuestion
  |──── UIAutomation send-input ────────|  ← keyboard: answer selections
  |                                     |
  [repeats until coverage met]
  |
  v
Plan generated --> User reviews
```

## Two Communication Channels

### Channel 1: Inbox Messaging (Codebase Q&A)

When the Developer agent answers freeform questions — exploring the codebase, reading files, reporting findings — responses come through the LiteHarness inbox as usual.

```bash
# Send question
python -m liteharness.cli send <AGENT-UUID> "question" --from <YOUR-UUID>
# Answer arrives via inbox monitor notification
```

### Channel 2: UIAutomation (AskUserQuestion Prompts)

When the Developer agent runs a Quizmaster skill internally (e.g., `/plan-w-quizmaster`), it will present **AskUserQuestion** prompts in its terminal. These are interactive selection UIs that require keyboard input — NOT inbox messages. Sentinel must:

1. **Detect** when a question appears via terminal buffer reading
2. **Answer** by sending keyboard input to navigate and select options
3. **Re-arm** the monitor for the next question

This is the key flow: the orchestrator can puppeteer another agent's interactive terminal UI.

## Variables

| Variable    | Source      | Description                   |
| ----------- | ----------- | ----------------------------- |
| TOPIC       | $1          | What to plan (user's request) |
| VARIANT     | default: v8 | Quizmaster prompt variant     |
| PLAN_OUTPUT | Docs/Plans/ | Output directory              |

## STEP 1: Variant Selection

Default to v8 per standing orders. Skip asking.

## STEP 2: Load the Quizmaster Prompt

Read the v8 variant from `${CLAUDE_SKILL_DIR}/../ls-plan-w-quizmaster/ULTIMATE_QUIZZER_PROMPT_v8.md`.
Internalize the methodology. You ARE the Quizmaster now.

## STEP 3: Spawn the Developer Agent

Spawn a **headed** agent using `liteharness spawn`:

```bash
python -m liteharness.cli spawn --headed --model <opus|sonnet> --name "DevPlanner" --cwd <PROJECT_CWD> --permission-mode bypassPermissions --prompt "<DEVELOPER_PROMPT>"
```

**MUST be `--headed`** (not `--pty`) so UIAutomation can read/write the terminal buffer.

### After Spawn

1. Wait for the agent's inbox ping (it will message Sentinel when ready)
2. Note the agent ID from the inbox notification
3. **Find the WT pane handle** for UIAutomation:
   ```bash
   python -m liteharness.cli wt-list-panes --format json
   ```
   Search the output for a window title matching the agent's task. Note the `handle` number.
4. Verify you can read the buffer:
   ```bash
   python -m liteharness.cli read-output --headed <handle>:0 10
   ```

### WT Pane Handle Discovery

The `wt-list-panes` output is JSON. Parse it to find the window matching the agent:

```python
python -c "
import json
with open('wt-panes.json') as f:
    for win in json.load(f):
        if 'DevPlanner' in win.get('title', '') or 'task-name' in win.get('title', ''):
            print(f'Handle: {win[\"handle\"]}')
"
```

## STEP 4: Run the Interrogation Loop

### Phase A: Inbox-Based Q&A (Codebase Exploration)

Send questions via inbox. The Developer explores the codebase, reads files, greps for patterns, and responds with grounded answers via inbox.

```bash
python -m liteharness.cli send <AGENT-UUID> "QUIZMASTER ROUND N: <questions>" --from <YOUR-UUID>
```

Answers arrive via your inbox monitor notification. Track facts, send next round.

### Phase B: UIAutomation Terminal Puppeteering (AskUserQuestion Prompts)

When the Developer agent runs the Quizmaster skill internally, it presents interactive `AskUserQuestion` prompts. These are terminal selection UIs (arrow keys + Enter to select).

#### Step 1: Arm a Monitor to Detect Questions

```bash
Monitor({
  description: "DevPlanner quizmaster prompt",
  timeout_ms: 120000,
  persistent: false,
  command: "until python -m liteharness.cli read-output --headed <HANDLE>:0 5 2>/dev/null | grep -qE 'Enter to select|Submit|WRONG'; do sleep 3; done; echo 'QUESTION_READY'; python -m liteharness.cli read-output --headed <HANDLE>:0 50"
})
```

**Grep patterns to detect interactive prompts:**

- `Enter to select` — AskUserQuestion single-select
- `Enter to select · ↑/↓ to navigate` — navigation prompt
- `Tab/Arrow keys to navigate` — multi-question tabbed UI
- `Submit` — multi-select with submit button
- `WRONG` — inversion pass predictions

#### Step 2: Read the Question

When the monitor fires, read the terminal buffer to understand what's being asked:

```bash
python -m liteharness.cli read-output --headed <HANDLE>:0 50
```

Parse the output to identify:

- The question text
- The numbered options
- Which option is currently highlighted (marked with `❯`)
- Whether it's single-select or multi-select (checkboxes `[ ]` vs radio `❯`)

#### Step 3: Answer by Sending Keyboard Input

Navigate and select using `send-input --headed`:

```bash
# Select currently highlighted option (option 1)
python -m liteharness.cli send-input --headed <HANDLE>:0 "{ENTER}"

# Navigate down 2 options then select (option 3)
python -m liteharness.cli send-input --headed <HANDLE>:0 "{DOWN}{DOWN}{ENTER}"

# Toggle checkbox (multi-select) then submit
python -m liteharness.cli send-input --headed <HANDLE>:0 " "     # space toggles checkbox
python -m liteharness.cli send-input --headed <HANDLE>:0 "{ENTER}"  # submit

# Type freeform text (option "Type something")
python -m liteharness.cli send-input --headed <HANDLE>:0 "my answer text{ENTER}"
```

**Key mappings:**
| Key | Action |
|-----|--------|
| `{ENTER}` | Select highlighted option / Submit |
| `{DOWN}` | Move highlight down one option |
| `{UP}` | Move highlight up one option |
| `" "` (space) | Toggle checkbox in multi-select |
| `{TAB}` | Switch between questions in multi-question UI |
| `{ESC}` | Cancel / go back |

**Multi-question tabbed UI** (shows `← ☐ Question1 ☐ Question2 ✔ Submit →`):

- Answer first question with arrow+Enter
- Tab switches to next question automatically
- After last question, Tab goes to Submit

#### Step 4: Re-arm the Monitor

After answering, re-arm for the next question:

```bash
Monitor({
  description: "DevPlanner next prompt",
  timeout_ms: 120000,
  persistent: false,
  command: "until python -m liteharness.cli read-output --headed <HANDLE>:0 5 2>/dev/null | grep -qE 'Enter to select|Submit|WRONG|Plan Created|Wrote'; do sleep 3; done; echo 'NEXT_READY'; python -m liteharness.cli read-output --headed <HANDLE>:0 50"
})
```

Add `Plan Created|Wrote` to the grep to detect when the plan is finished being generated.

### Decision Logic for Answering

When answering AskUserQuestion prompts, apply these guidelines:

| Question Type    | How to Decide                                                        |
| ---------------- | -------------------------------------------------------------------- |
| Workstream count | Default: single workstream unless clearly multi-domain               |
| Prior Question   | Match to the actual problem from your reconnaissance                 |
| Architecture     | Prefer visualization layers over new systems; reuse existing infra   |
| Data source      | Prefer existing data channels (inbox, presence, PTY) over new ones   |
| Scope            | Include competitive differentiators in v1, defer polish to v2        |
| Inversion        | Validate against your own codebase knowledge; flag wrong predictions |

### Thinking Time Between Rounds

The Developer agent takes time to think ("Osmosing...", "Calculating...", "Boondoggling..."). The Monitor handles this automatically — the `until` loop polls every 3s and only fires when interactive content appears.

**Do NOT sleep or poll manually.** Arm the Monitor and continue other work while waiting.

## STEP 5: Generate the Plan

The Developer agent generates the plan after the Final Inversion. The Monitor will detect when files are written (grep for `Plan Created` or `Wrote`).

Read the generated plan:

```bash
python -m liteharness.cli read-output --headed <HANDLE>:0 100
```

Or if the plan is saved to disk, read the files directly.

## STEP 6: Present to User

```
**Autonomous Plan Complete**

Topic: <description>
Variant: v8
Rounds: <N>
Facts: <total> (HIGH: X, MED: Y, LOW: Z)
Communication: inbox Q&A + UIAutomation terminal puppeteering

**Files:**
- `Docs/Plans/<folder>/master.md`
- `Docs/Plans/<folder>/sub-<name>.md`

**Key Decisions Made:**
- <bullet list of 3-5 most important facts/decisions>

**Review the plan and let me know what to adjust.**
```

## STEP 7: Dismiss the Developer

```bash
python -m liteharness.cli send <AGENT-UUID> "Plan complete. Stand down." --from <YOUR-UUID>
```

## STEP 8: User Review

The user reviews the plan. They may:

- **Approve** — proceed to execution
- **Adjust** — modify specific sections
- **Re-run** — spawn a new agent to re-interrogate
- **Scrap** — start over

---

## Quick Reference: UIAutomation Commands

```bash
# List all WT windows and find pane handles
python -m liteharness.cli wt-list-panes --format json

# Focus a specific pane (bring to front)
python -m liteharness.cli wt-focus <handle> <pane-id>

# Read terminal buffer (last N lines)
python -m liteharness.cli read-output --headed <handle>:<pane> <lines>

# Send keyboard input
python -m liteharness.cli send-input --headed <handle>:<pane> "<keys>"

# Key escapes: {ENTER} {UP} {DOWN} {LEFT} {RIGHT} {TAB} {ESC} {HOME} {END}
```

## Self-Validation

1. A plan folder exists in `Docs/Plans/`
2. Contains `master.md` + at least one `sub-*.md`
3. Plan follows v8 format with fact provenance and golden-path scenarios
4. User has been presented the summary for review
5. All AskUserQuestion prompts were answered (no stuck prompts in agent terminal)
