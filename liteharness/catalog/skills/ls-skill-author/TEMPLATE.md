---
name: { { SKILL_NAME } }
description: >
  {{ONE_OR_TWO_SENTENCE_DESCRIPTION — what the skill does and, critically, WHEN
  it should trigger. List concrete trigger phrases the user is likely to say.
  Disambiguate from any adjacent skill it could be confused with.}}
  Triggers on {{'phrase one'}}, {{'phrase two'}}, {{'phrase three'}}.
# allowed-tools: LEAST PRIVILEGE. Start EMPTY (inherit nothing extra). Only add
# the specific, scoped tools this skill genuinely needs, e.g.:
#   allowed-tools: Read, Grep
#   allowed-tools: Bash(git status:*), Bash(git diff:*)
# NEVER use a wildcard (*, Bash(*), all) — the security scanner hard-fails it.
allowed-tools: []
version: "0.1.0"
tags: [{ { tag } }]
---

# {{Skill Title}} — {{one-line tagline}}

## What This Does

{{One short paragraph: the job this skill performs and the outcome the user gets.}}

## When To Use

- {{Concrete situation 1}}
- {{Concrete situation 2}}

Do NOT use this when {{the adjacent skill / different intent}} — use `/{{other-skill}}` instead.

## Instructions

1. {{Step}} → verify: {{check}}
2. {{Step}} → verify: {{check}}
3. {{Step}} → verify: {{check}}

## Notes

- {{Edge cases, constraints, or gotchas.}}
