---
name: ls-plan-w-quizmaster
description: Use when the user wants to plan something — load immediately when planning intent is expressed, don't ask first. Triggers on 'I want to plan', 'let's plan', 'plan something out', 'help me plan', 'plan it out', 'quiz me on the requirements', 'plan-w-quizmaster'. NOT a mandatory gate on implementation. For expert opinions use /consult-polymaths.
---

# Plan with Quizmaster

Plan with **Ultimate Quizzer** methodology - thoroughly understand requirements via structured questioning before generating any plan.

## Variables

| Variable    | Source      | Description                |
| ----------- | ----------- | -------------------------- |
| USER_PROMPT | $1          | The user's request to plan |
| PLAN_OUTPUT | Docs/Plans/ | Output directory           |

## STEP 1: Variant Selection (MANDATORY FIRST ACTION)

**Before doing ANYTHING else**, you MUST ask the user which quizmaster prompt variant to use. This is always the very first question — no exceptions.

Use `AskUserQuestion` with this exact question:

```
Question: "Which quizmaster prompt variant should we use for this planning session?"
Options:
  - "small" — Lightweight, faster questioning. Good for simple tasks.
  - "full" — Elaborated 10-domain context sweep. Thorough but verbose.
  - "v4" — Visual coverage maps, quality metrics, assumption gate, retrospective.
  - "v5" — Self-evaluating, ever-evolving meta planner. Learns and adapts.
  - "v6" — Adaptive quizmaster with fact dependencies, unified inversion, invisible ceremony. Built by the Polymathic Tribunal.
  - "v7" — v6 + master plan/sub-plan decomposition, workstream discovery, phased DAG orchestration. Enables parallel agent dispatch.
  - "v8" — v7 + specification enrichment, self-contained sub-plans, buildability gate, golden-path scenarios, banned ambiguity. Fixes the decision-to-specification gap that caused placeholder code in v7.
  - "v9 (Recommended)" — v8 + verification instrumentation, measurement conditions, negative-path proof, instrument-trust audit. Fixes the gap that lets a plan specify HOW to build but not how to PROVE it worked — the gap that shipped an app with a dead updater behind a fully green build log.
```

**Do NOT skip this step. Do NOT default to any variant. Always ask.**

## STEP 2: Load the Selected Prompt

After the user selects a variant, use the `Read` tool to load the corresponding prompt file from the skill directory:

| Variant   | File to Read                       |
| --------- | ---------------------------------- |
| **v9**    | `ULTIMATE_QUIZZER_PROMPT_v9.md`    |
| **v8**    | `ULTIMATE_QUIZZER_PROMPT_v8.md`    |
| **v7**    | `ULTIMATE_QUIZZER_PROMPT_v7.md`    |
| **v6**    | `ULTIMATE_QUIZZER_PROMPT_v6.md`    |
| **v5**    | `ULTIMATE_QUIZZER_PROMPT_v5.md`    |
| **v4**    | `ULTIMATE_QUIZZER_PROMPT_v4.md`    |
| **full**  | `ULTIMATE_QUIZZER PROMPT_full.md`  |
| **small** | `ULTIMATE_QUIZZER_PROMPT_small.md` |

Read the file from: `${CLAUDE_SKILL_DIR}/<filename>`

**Follow the loaded prompt's instructions exactly.** The prompt file defines the quizmaster's personality, questioning methodology, state tracking format, and all behavioral rules. It is the authority — this SKILL.md only handles variant selection and plan output.

## STEP 3: Quiz the User

Follow the loaded prompt file's methodology to interrogate the user's request. The prompt defines how to ask questions, track state, and determine when enough information has been gathered.

## STEP 4: Generate the Plan

When the user says "plan it" / "ok plan" / "enough" / "go ahead":

1. **Validate assumptions** - List all assumptions, confirm with user
2. **Summarize** - Known/Open/Assumptions in 5-10 bullets
3. **Generate plan** - Follow the Plan Format below
4. **Save** - Write to `Docs/Plans/<filename>.md`

## Plan Format

After quizzing is complete, generate the plan. The plan format depends on the variant used:

- **v9:** Produces a **folder** containing BOTH `master.md` + `sub-<workstream>.md` (the builder-facing source of truth) AND `master.html` + `sub-<workstream>.html` (the styled render opened in the LiteSuite browser pane). The HTML is generated from the markdown so they cannot drift.
- **v7/v8:** Produce a **folder** with per-workstream master and sub-plan files. ⚠️ Note: the v7 and v8 PROMPT files instruct HTML output while this SKILL.md historically documented `.md` — a real contradiction in the shipped plugin. v9 resolves it by emitting both. If you run v7 or v8, expect HTML and convert if a builder agent needs the markdown.
- **v6 and earlier:** Produces a single plan file using the format below.

### Single-File Plan Format (v6 and earlier)

```markdown
# Plan: <descriptive task name>

## Task Description

<describe what will be accomplished>

## Objective

<clearly state the goal and success criteria>

## Problem Statement

<define the problem being solved>

## Solution Approach

<describe the technical approach>

## Relevant Files

<list files to be modified/created>

## Team Orchestration

You operate as the team lead and orchestrate the team to execute this plan.
You NEVER write code directly - you use Task and Task\* tools to deploy team members.

### Team Members

<list builders and validators>

## Step by Step Tasks

<structured task list with dependencies>

## Acceptance Criteria

<measurable criteria from quizzing>

## Validation Commands

<specific commands to verify completion>

## Assumptions Made

<list assumptions from quizzing session>

## Notes

<optional additional context>
```

## Enforced Best Practices

When generating the plan, **always include these engineering disciplines** as part of the execution strategy. These are non-negotiable when going through quizmaster planning — the whole point of rigorous planning is rigorous execution.

| Practice                           | Skill                          | When                                                                   |
| ---------------------------------- | ------------------------------ | ---------------------------------------------------------------------- |
| **Isolated workspace**             | git worktrees                  | Create a worktree before touching code. Keep main clean.               |
| **Test-driven development**        | test-driven development        | Write tests before implementation for each task in the plan.           |
| **Structured implementation plan** | a structured execution doc     | The quizmaster plan feeds directly into a writing-plans execution doc. |
| **Systematic debugging**           | systematic debugging           | When tests fail, follow the debugging skill — don't guess.             |
| **Verification before completion** | verification before completion | Every task must pass verification commands before claiming done.       |
| **Code review**                    | polymathic code review         | Request review before merging back.                                    |
| **Branch completion**              | structured merge/PR/cleanup    | Follow the structured merge/PR/cleanup flow at the end.                |

Include a **"Execution Workflow"** section in every generated plan that applies these disciplines in order:

1. Create worktree → 2. Write tests → 3. Implement → 4. Debug failures → 5. Verify → 6. Review → 7. Finish branch

## Report

After saving the plan:

**v7 (folder output):**

```
Plan Created: Docs/Plans/<folder-name>/

Files:
- master.md
- sub-<workstream-1>.md
- sub-<workstream-2>.md
- ...

Topic: <brief description>

Quizzing Summary:
- Variant used: v7
- Workstreams identified: N
- Domains covered: X/10
- Questions asked: N
- Assumptions validated: Y

To execute:
# ultracode: open master.md and run it — the Workflow tool fans the sub-plans out across agents
```

**v6 and earlier (single file):**

```
Plan Created: Docs/Plans/<filename>.md

Topic: <brief description>

Quizzing Summary:
- Variant used: <variant>
- Domains covered: X/10
- Questions asked: N
- Assumptions validated: Y

To execute:
# ultracode: open the plan and run it — the Workflow tool fans the sub-plans out across agents
```

## Self-Validation

The Stop hook validates:

1. A new .md file exists in Docs/Plans/
2. File was created within last 10 minutes

---

**You are now in QUIZMASTER PLAN MODE. Start with STEP 1: ask which variant to use.**
