---
name: ls-consult-polymaths
description: Spawn expert polymathic thinking agents for deep analysis. Use proactively when the user wants expert opinions, multi-perspective review, or mentions specific thinkers by name. Triggers on 'consult polymaths', 'ask the polymaths', 'polymath review', 'expert opinions on', 'what would the polymaths say', 'get Feynman to review', 'ask Carmack', 'what would Jobs think', 'multi-perspective review', 'diverse expert perspectives', 'run it by the polymaths', 'task the polymaths', 'debate this architecture', 'expert analysis'. Also trigger when the user names ANY polymathic agent (Feynman, Carmack, Jobs, Munger, Shannon, Linus, Tesla, etc.). Internal thinking agents — different from /consult which queries external LLM APIs.
---

# Consult Polymaths

Spawn multiple polymathic thinking agents in parallel to analyze a task from different expert perspectives. Each polymath applies a historical figure's cognitive architecture as structural constraints on reasoning — they're read-only consultants that analyze and advise.

## Available Polymaths

### Code & Architecture

| Agent       | Lens                                                                                 |
| ----------- | ------------------------------------------------------------------------------------ |
| **carmack** | Performance, systems architecture, technical feasibility — finds the real constraint |
| **linus**   | Code review, taste, BS detection, maintainability — demands elegance                 |
| **tesla**   | Systems architecture, infrastructure, API design — complete mental model first       |
| **shannon** | API design, simplification, hidden structure — strips to the invariant skeleton      |

### Design Patterns (Gang of Four)

| Agent         | Lens                                                                                                    |
| ------------- | ------------------------------------------------------------------------------------------------------- |
| **gamma**     | Code architecture, API design — refactor TO patterns, composition over inheritance, Rule of Three       |
| **helm**      | Architectural evaluation, enterprise design — behavioral contracts, explicit tradeoff analysis          |
| **johnson**   | Framework design, library architecture — three concrete examples before abstracting                     |
| **vlissides** | Complex domain architecture, multi-pattern design — pattern constellations, bridge knowing-applying gap |

### Product & UX

| Agent       | Lens                                                                |
| ----------- | ------------------------------------------------------------------- |
| **jobs**    | Product vision, UX simplification, feature pruning — taste-first    |
| **rams**    | Product design, UI simplification — less but better                 |
| **vangogh** | UI/UX, color systems, emotional design — feeling before function    |
| **disney**  | Experience design, creative strategy — Dreamer/Realist/Critic triad |

### Strategy & Decisions

| Agent          | Lens                                                                     |
| -------------- | ------------------------------------------------------------------------ |
| **bezos**      | Customer-obsessed design, decision speed — press release first           |
| **thiel**      | Contrarian analysis, monopoly strategy — zero-to-one thinking            |
| **gates**      | Platform strategy, ecosystem design — decompose into atoms               |
| **andreessen** | Market timing, technology adoption — spot discontinuities                |
| **suntzu**     | Competitive strategy, positioning — win before fighting                  |
| **musk**       | Moonshot feasibility, aggressive simplification — delete before optimize |

### Thinking & Analysis

| Agent        | Lens                                                                                     |
| ------------ | ---------------------------------------------------------------------------------------- |
| **feynman**  | Debugging, first principles, cargo cult detection — freshman test                        |
| **holmes**   | Debugging, incident investigation, forensic evidence, negative signals — case file first |
| **moriarty** | Adversarial strategy, threat modeling, deception, hidden networks — move two             |
| **tao**      | Problem decomposition, cross-domain connections — toy models first                       |
| **munger**   | Decision frameworks, bias detection — invert every problem                               |
| **socrates** | Assumption testing, exposing hidden ignorance — elenctic questioning                     |
| **lovelace** | Technology visioning, system abstraction — operational patterns                          |
| **davinci**  | Cross-disciplinary synthesis, bio-inspired design — find the analog                      |
| **aurelius** | Decision-making under pressure, resilience — obstacle is the way                         |

### Marketing & Content

| Agent       | Lens                                                              |
| ----------- | ----------------------------------------------------------------- |
| **ogilvy**  | Copywriting, ad strategy — headline is 80% of the work            |
| **godin**   | Marketing strategy, audience building — smallest viable audience  |
| **graham**  | Startup strategy, product-market fit — do things that don't scale |
| **mrbeast** | Content strategy, attention engineering — retention curves        |

## Workflow

### Step 1: Gather the Task

If the user provided a clear task in their message, use it directly. Otherwise, use `AskUserQuestion` to ask:

- "What problem or task do you want the polymaths to analyze?"

### Step 2: Select Polymaths

Based on the task domain, select the most relevant polymaths. Consider:

- **Code problems** → carmack, linus, feynman, shannon, tesla
- **Product decisions** → jobs, rams, bezos, feynman, thiel
- **Architecture** → carmack, tesla, shannon, linus, gamma, helm
- **Design patterns** → gamma, helm, johnson, vlissides
- **Strategy/business** → bezos, thiel, gates, munger, suntzu
- **UX/design** → jobs, rams, vangogh, disney
- **Debugging** → holmes, feynman, moriarty, carmack, linus, socrates
- **Threat modeling / adversarial review** → moriarty, holmes, munger, dijkstra, shannon
- **Marketing** → ogilvy, godin, graham, mrbeast
- **Decision-making** → munger, aurelius, bezos, socrates, feynman
- **Innovation** → davinci, lovelace, thiel, musk, tao

Cross-domain tasks benefit from mixing categories (e.g., a product architecture question might get carmack + jobs + shannon).

### Step 3: Confirm with User

Use `AskUserQuestion` to present:

1. **How many polymaths?** — Offer 2, 3, 4, or 5 (suggest 3 as default for good coverage without overwhelming output). More than 5 gets noisy; fewer than 2 defeats the purpose.

2. **Does this selection look good?** — Show the selected polymaths with a one-line rationale for each. Let the user swap any out.

If the user wants to swap agents, adjust the selection and proceed. Don't re-confirm unless they explicitly ask.

### Step 4: Spawn in Parallel

Spawn all selected polymaths simultaneously using the `Agent` tool with:

- `subagent_type` matching the agent name (e.g., `polymathic-feynman`)
- The same task prompt for each agent
- Each agent gets the full task context

The prompt to each agent should be the user's task, verbatim. Don't paraphrase or filter — let each polymath's cognitive architecture shape their unique response.

### Step 5: Synthesize

After all agents return, provide a brief synthesis:

- **Consensus** — Where do the polymaths agree?
- **Tensions** — Where do they disagree, and why?
- **Surprise insights** — Anything unexpected that only one polymath surfaced?
- **Recommended action** — Based on the combined perspectives, what should the user do?

Keep the synthesis concise. The individual polymath responses carry the depth; the synthesis highlights patterns and conflicts.
