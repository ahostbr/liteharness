---
name: ls-train
description: |
  Autonomous agent and skill trainer — transforms the current Claude session into a self-improving
  loop inspired by Karpathy's autoresearch. Activates on: "train", "evolve agent", "improve agent",
  "run training loop", "agent trainer", "evolve this agent", "train this agent", "self-improving
  agent", "auto-improve", "training loop", "agent evolution", "autotrainer", "improve skill",
  "evolve skill", "optimize skill", "skill training", "improve my skills".
  Targets agents (.claude/agents/) OR skills (~/.claude/skills/) — auto-detects which.
  For agents: spawns A/B variants, evaluates output quality, mutates config.
  For skills: uses Claude Code's built-in eval system (run_eval.py) to measure trigger accuracy,
  then mutates the skill description. Use when you want an agent or skill to improve itself
  over N cycles without manual intervention.
---

# Train — Autonomous Agent & Skill Trainer

## What This Does

The `/train` skill turns Claude into an autonomous training loop for agents OR skills. It follows the
autoresearch pattern: evaluate → reflect → mutate → re-evaluate → keep/revert → repeat.

**Agent mode:** Spawns A/B agent variants via LiteHarness (visible terminal tabs), evaluates output
quality (binary + qualitative), mutates the agent config, keeps winners. Agents are visible so you
can watch them work in real-time.

**Skill mode:** Uses Claude Code's built-in eval system (run_eval.py) to measure trigger accuracy,
mutates the skill description, keeps improvements. Can also generate evals if none exist.

## Variables

| Parameter       | Flag                                    | Default        | Description                                         |
| --------------- | --------------------------------------- | -------------- | --------------------------------------------------- |
| Target          | `--target <name>`                       | `test-subject` | Agent name OR skill name to evolve                  |
| Mode            | `--mode <agent\|skill>`                 | auto-detect    | Force agent or skill mode (auto-detects by default) |
| Review interval | `--review-every <N>`                    | `10`           | Pause for human review every N cycles               |
| Pacing strategy | `--pace <fast\|medium\|deep\|adaptive>` | `adaptive`     | How deeply to evaluate per cycle                    |
| Max cycles      | `--cycles <N>`                          | `0` (infinite) | Stop after N cycles; 0 = run until stopped          |

## Target Resolution

The trainer auto-detects what you're pointing it at:

1. Check `~/.claude/skills/{TARGET}/SKILL.md` — if exists → **skill mode**
2. Check `.claude/agents/{TARGET}.md` — if exists → **agent mode**
3. If both exist, use `--mode` to disambiguate
4. If neither exists, halt with error

## Instructions

1. **Parse parameters** from the args string passed to this skill:
   - Extract `--target`, `--mode`, `--review-every`, `--pace`, and `--cycles` values
   - Fall back to defaults for any parameter not provided

2. **Resolve target** using the Target Resolution rules above.
   - Determine MODE: `agent` or `skill`
   - Display: "Target: {TARGET} | Mode: {MODE}"

3. **Read `LOOP.md`** from the same directory as this file:
   - Path: `${CLAUDE_SKILL_DIR}/LOOP.md`

4. **Follow `LOOP.md` instructions** using the parsed parameter values.
   - Pass all parameters into the loop as context, including MODE
   - LOOP.md handles both agent and skill training loops
