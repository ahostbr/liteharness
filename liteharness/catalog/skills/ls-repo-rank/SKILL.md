---
name: ls-repo-rank
description: >
  Targeting machine — before pointing an expensive model (Opus/Fable) at a codebase, sweep every
  candidate file with CHEAP models (Haiku/Sonnet scouts) and score each Impact × Opportunity (1–5
  each, scored INDEPENDENTLY, then multiplied). Discard the 1s and 2s; aim premium tokens only at
  the top-ranked targets. Produces a ranked worklist for bugs, security, tech-debt, dead code, perf,
  or any task. Triggers on 'repo-rank', 'rank the codebase', 'where should I point <model>',
  'targeting sweep', 'find the highest-impact work'. Use BEFORE any large premium-model sweep.
---

# Repo-Rank — Aim the Expensive Model

The premium model is the scarcest resource in the harness. Pointing one at _"the whole repo, go
fix bugs"_ burns context and money and rarely converges — it fixes leaves, misses the root, and
runs out of window. **Repo-rank decides WHERE the premium model works** by sweeping the codebase
with cheap models first and ranking targets by **Impact × Opportunity**.

This is the **pre-build bookend**. Its partner is [`ls-eval-gate`](../ls-eval-gate/SKILL.md), the
**post-build bookend** that scores what the premium model produced. Together they bracket the
expensive model: _aim it well, then verify it._

## The Formula

```
score = Impact × Opportunity      (each scored 1–5, independently, then multiplied)
```

- **Impact** — how far-reaching is this code? Blast radius. Is it imported by many files, on a
  hot path, a core route? Proxies: **git churn**, fan-in, request/traffic volume.
- **Opportunity** — how much is actually here to fix or improve? Proxies: **complexity**, bug
  density, age, TODO/FIXME density, lint debt.

Only `high × high` earns premium tokens:

|                 | Low opportunity                       | High opportunity           |
| --------------- | ------------------------------------- | -------------------------- |
| **High impact** | leave it — it's load-bearing and fine | **← point the model HERE** |
| **Low impact**  | ignore                                | nobody cares yet           |

### Task-specific proxies

| Task                 | Impact proxy                                | Opportunity proxy           |
| -------------------- | ------------------------------------------- | --------------------------- |
| Tech debt            | git churn                                   | cyclomatic complexity       |
| Bugs                 | fan-in / traffic                            | recent churn × test gaps    |
| Dead code            | (inverse) import count                      | size × unused-confidence    |
| Security             | exposure (network-reachable, auth boundary) | input-handling complexity   |
| Performance          | call frequency / hot path                   | allocation churn, N+1 smell |
| Conversion (product) | traffic                                     | drop-off rate               |

## How to Run It — dispatch cheap scouts

Score the two axes with **separate, independent** cheap-model passes, then multiply. Never let one
model eyeball a single combined gut-score — independence is what makes the ranking trustworthy.
Use [`ls-scout`](../ls-scout/SKILL.md) (Haiku, separate usage pool) for the sweep.

```
// Two independent passes, run in parallel:
Agent(name: "scout-impact",      model: "haiku",  subagent_type: "Explore",
  prompt: "In <repo>, for each file under <paths>, score IMPACT 1–5 using git churn + fan-in
           (how many files import it, is it on a hot path). Report: path, impact, one-line why.")

Agent(name: "scout-opportunity", model: "haiku",  subagent_type: "Explore",
  prompt: "In <repo>, for each file under <paths>, score OPPORTUNITY 1–5 using complexity +
           TODO/FIXME density + test gaps. Report: path, opportunity, one-line why.")
```

For a large tree, fan out by subtree (one scout pair per package) and merge. Heavy signal
(real churn/complexity numbers) beats a model's guess — pull `git log --format=` churn, a
complexity tool, coverage gaps where you have them, and hand those numbers to the scout.

Then **you** (Opus) join the two passes:

1. `score = impact × opportunity` per file.
2. **Discard everything scoring 1 or 2** — it is not worth premium attention.
3. Sort descending. Emit the worklist (below).
4. Optionally seed `lst run tasks` with the top N as claimable tasks.

## The root-cause follow-up

When you then send the premium model at a top-ranked target, **do not accept a point-fix.** Append:

> "Is there a meta-level pattern or deeper root cause here? Prefer the architectural fix that closes
> the whole _class_ of this problem over patching this one symptom."

Models default to the lazy leaf-fix unless told otherwise. This is the same signal
`ls-eval-gate` scores after the fact as **Root-Cause Depth** — repo-rank prompts for it, the gate
verifies it.

## Output — the ranked worklist

```
rank · path · impact · opportunity · score · why
  1    apps/server/src/wsServer.ts      5 × 4 = 20   core route, high churn, low coverage
  2    apps/web/src/state/session.ts    4 × 4 = 16   imported everywhere, complex reducer
  …
(discarded: 37 files scored ≤ 2)
```

Always `log()`/report how many were discarded — silent truncation reads as "covered everything."
**Re-rank after each batch is fixed** — scores shift as the top targets resolve.

## When NOT to use

- **Small repos** — skip the sweep, just point the model at it. The ranking overhead isn't worth it.
- **Garbage signal in → garbage ranking out.** If churn/complexity data is noise, the scores lie;
  step in manually.
- **Human attention is still the bottleneck.** Ranking tells you WHERE, not WHAT the fix is —
  reviewing the architectural change still costs your time.

---

_Provenance: the Impact×Opportunity targeting pattern follows Nicholas Carlini's 1–5 file-rating
technique (rate every file, discard the 1s and 2s, point the expensive model at the rest);
popularized for agentic coding by Ray Amjad. Method adopted; not the surrounding hype._
