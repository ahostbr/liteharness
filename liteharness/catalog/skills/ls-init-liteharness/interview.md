# Interview Guide — Cognitive Architecture Discovery

This document is the agent's reference for conducting the identity interview. It is NOT shown to the user. The agent reads this to calibrate question depth, follow-up strategy, and synthesis patterns.

## Question Bank

### Core Questions (always ask)

| #   | Question                                                                       | What it reveals                                     | Follow-up triggers                                                                                                            |
| --- | ------------------------------------------------------------------------------ | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| 1   | Tell me about yourself as a developer.                                         | Background, experience level, domains               | If self-taught → ask about learning style. If CS degree → ask what school missed.                                             |
| 2   | When you face a hard problem, what's your first instinct?                      | Cognitive style: planner vs diver, visual vs verbal | If "I Google it" → push for what happens AFTER that. If "I draw it" → spatial thinker.                                        |
| 3   | Is there a principle that governs how you decompose problems?                  | The kernel — their universal pattern                | If blank → synthesize from Q1+Q2 answers. Offer candidates: "Sounds like you [X]?"                                            |
| 4   | Why do you write code? The real reason.                                        | The trunk — their purpose                           | If "money" → "What would you build if money wasn't the issue?" If vague → "What's the ONE thing your code has to accomplish?" |
| 5   | Solo or team? Review preferences?                                              | Work style, trust model                             | If solo → higher HITL tolerance likely. If team → ask about delegation comfort.                                               |
| 6   | Would you let an AI merge a bug fix without asking? What about a DB migration? | Risk tolerance spectrum                             | Map to HITL: both yes = OFF, both no = ON, mixed = per-task override                                                          |
| 7   | Which polymathic style resonates?                                              | Agent affinity, default dispatch                    | Let them pick 2-3 or describe their own. Map to agent_pools config.                                                           |
| 8   | What are your blind spots?                                                     | Anti-patterns, review focus areas                   | If "none" → ask "What do code reviewers catch in your PRs?"                                                                   |

### Follow-up Question Bank (use as needed)

- "You mentioned [X]. How does that show up in your code?"
- "What's the last thing you built that you're proud of? Why that one?"
- "If you could only keep one tool/language/framework, what stays?"
- "What's the most expensive mistake you've made in code? What did you learn?"
- "Do you think in types or in data flow?"
- "When you read someone else's code, what's the first thing you look for?"
- "How do you know when something is done?"

### Adaptive Strategies

**Verbose user:** Let them talk. Their digressions often contain the most revealing data. Gently steer back after 2+ minutes on a tangent.

**Terse user:** Switch to binary or multiple-choice: "More like Feynman (first principles) or more like Tesla (complete model first)?" Follow up with "Why?"

**Skeptical user:** Lead with value: "This interview shapes how the AI agents think when working for you. A bad profile means generic agents. A good one means they think like you do."

**Expert user:** Skip basics. "I see 20 years of Go in your git history. Let's skip the intro — what's your decomposition principle?"

**New developer:** Be encouraging. Don't assume knowledge of patterns or cognitive frameworks. Discover their natural style through concrete examples rather than abstract questions.

## Synthesis Rules

After the interview, synthesize answers into these profile fields:

### Kernel Discovery

The kernel is their universal decomposition principle. Common patterns:

| Pattern            | Signal               | Example                                          |
| ------------------ | -------------------- | ------------------------------------------------ |
| Input/Work/Output  | Functional thinker   | "Everything is a pipeline"                       |
| Divide and Conquer | Recursive thinker    | "Break it into smaller problems"                 |
| State Machine      | Systems thinker      | "What are the states? What are the transitions?" |
| Contract First     | Interface thinker    | "Define the API, then implement"                 |
| Data Flow          | Pipeline thinker     | "Follow the data"                                |
| Constraint First   | Optimization thinker | "What's the bottleneck?"                         |
| Story First        | Product thinker      | "What's the user trying to do?"                  |
| Test First         | Safety thinker       | "How will I know it works?"                      |

If no clear kernel emerges, offer 2-3 candidates based on their answers and let them choose.

### Trunk Discovery

The trunk is their non-negotiable purpose. Categories:

| Category      | Example                    | How it shapes the harness                            |
| ------------- | -------------------------- | ---------------------------------------------------- |
| Family/legacy | "For my kids"              | Ship quality, long-term thinking, no shortcuts       |
| Craft         | "To write beautiful code"  | High review standards, aesthetic agents (Rams, Jobs) |
| Impact        | "To change how people [X]" | User-obsessed, Bezos/Jobs agents, fast shipping      |
| Freedom       | "To never have a boss"     | Autonomous mode, aggressive HITL OFF                 |
| Learning      | "To understand everything" | Feynman/Euler agents, exploration-first              |
| Survival      | "To pay the bills"         | Pragmatic, ship fast, minimal ceremony               |

If the user doesn't want to share a trunk, respect that. Use "to be discovered" — never fabricate.

### HITL Mapping

| Risk tolerance answers        | HITL default | Per-task behavior                        |
| ----------------------------- | ------------ | ---------------------------------------- |
| Both "yes, auto-merge"        | OFF          | Override to ON for security/migrations   |
| Bug fix = yes, migration = no | Per-task     | Reversibility test decides               |
| Both "no, always ask me"      | ON           | Only override to OFF for trivial changes |

### Agent Pool Configuration

Map their polymathic affinities to config.agent_pools:

```yaml
agent_pools:
  code_architecture: [their picks from systems/code thinkers]
  strategy_reasoning: [their picks from strategy/analysis thinkers]
  review: [default: linus + gamma + their strongest pick]
  patterns: [gamma, helm, johnson, vlissides — unless they specifically resonate with others]
```

## Quality Checklist

Before generating the orchestrator prompt, verify:

- [ ] Kernel identified or "to be discovered" explicitly
- [ ] Trunk captured authentically (their words, not yours)
- [ ] At least 2 polymathic affinities mapped
- [ ] HITL preference clear
- [ ] Anti-patterns list has at least 1 entry
- [ ] Work style (solo/team) captured
- [ ] Cognitive style (planner/diver, visual/verbal) captured
