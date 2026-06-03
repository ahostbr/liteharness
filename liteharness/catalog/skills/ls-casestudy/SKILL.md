---
name: ls-casestudy
description: "Reverse-engineer any codebase, plugin, or repo into an evidence-first case study with file:line citations for every claim. Pattern extraction for future reference — not a bug hunt."
---

# /casestudy — Evidence-First Architecture Case Study

Generate a detailed, evidence-backed case study of a codebase, plugin, repo, or system. Every claim must cite a file path and line number. This is pattern extraction for future reference — not a bug hunt, not a review.

## Input

The user provides one or more of:

- A directory path to analyze
- A repo URL to clone and analyze
- A specific system/plugin/module to focus on
- An intent statement ("written as a pattern reference for X")

If no intent is stated, ask: "What are you building that this case study should inform?"

## Phase 1: Evidence Collection

Dispatch 2-3 **polymathic scouts** (Haiku, read-only) in parallel to gather raw evidence:

**Scout 1 — Structure Mapper:**

> Glob for all source files. Read build configs, manifests, plugin descriptors (.uplugin, package.json, pyproject.toml, Cargo.toml, etc.). Map the dependency graph. Report: file counts, module structure, entry points, dependency list. Every finding = file:line.

**Scout 2 — API Surface Scanner:**

> Grep for public interfaces, exported functions, class declarations, route handlers, IPC handlers, event emitters. Report: public API surface with file:line for each entry point. Identify the core patterns (subsystem, singleton, event bus, async action, etc.).

**Scout 3 — Flow Tracer (if the system has runtime behavior):**

> Trace the primary flow: startup → initialization → main loop → shutdown. Identify entry points, lifecycle hooks, configuration loading, error handling patterns. Report each step with file:line evidence.

## Phase 2: Case Study Generation

Synthesize scout reports into a structured case study document. Follow this format exactly:

```markdown
# Case Study: [System Name] Patterns for [Target Application]

Intent

- This is a case study of [system], written as a pattern reference for [future use].
- [Scope exclusions if any].

Scope and evidence sources

- [file:line] — [short note about what this line tells us]
- [file:line] — [short note]
- ... (every evidence source listed upfront)

Evidence glossary

- Each evidence line includes a short note about the referenced line and, when it starts a block, a brief description of what the following lines do.

---

## 1) [Category name] ([what it tells us])

- [Claim with plain language explanation]. Evidence: [file:line], [file:line]
- [Next claim]. Evidence: [file:line]

## 2) [Next category]

...

## N) [Target application] design notes (adaptation guidance)

These are recommendations based on the observed patterns above, not requirements.

- [Recommendation based on evidence]
- [Pattern takeaway for the target application]
```

## Evidence Rules (HARD)

1. **Every claim must have at least one file:line citation.** No exceptions. If you can't cite it, don't claim it.
2. **Evidence sources listed upfront.** The reader should be able to verify every claim by reading the listed files.
3. **File paths must be absolute.** Use the actual on-disk path, not relative or shortened paths.
4. **Line numbers must be accurate.** Read the file and confirm the line number before citing.
5. **Short notes per evidence line.** One sentence max explaining what that line tells us.
6. **No speculation.** If behavior is unclear from the code, say "unclear from evidence" — don't guess.
7. **Block descriptions.** When an evidence line starts a block (function, class, config section), briefly describe what the following lines do.

## Category Selection

Choose categories based on what the system actually does. Common categories:

| Category                 | When to use                                       |
| ------------------------ | ------------------------------------------------- |
| Plugin/module framing    | Always — what is this thing?                      |
| UX entry points          | If it has a UI surface                            |
| Core components          | Always — the main classes/modules and their roles |
| Data flow / lifecycle    | If it has runtime behavior                        |
| Configuration            | If it has settings/config files                   |
| API surface              | If it exposes APIs (REST, IPC, JS bridge, etc.)   |
| Error handling           | If it has a distinct error strategy               |
| Credential/auth model    | If it handles secrets or auth                     |
| Streaming/async patterns | If it does async work or streaming                |
| Provider/adapter pattern | If it supports multiple backends                  |
| Persistence              | If it stores data                                 |
| Testing patterns         | If it has tests worth studying                    |

Don't force categories that don't apply. 4-8 sections is typical.

## Phase 3: Adaptation Guidance

The final section maps observed patterns to the target application. These are recommendations, not requirements. Frame them as:

- "Pattern takeaway for [target]: [what to emulate and why]"
- Reference specific evidence that supports the recommendation

## Output

Save the case study to one of:

- `Docs/CaseStudies/[SystemName]_CaseStudy_[Target].md` (if inside a project)
- `<your-project-dir>/docs/case-studies/[slug].md` (if saving to a specific project)
- User-specified path

## Quality Checklist

Before delivering, verify:

- [ ] Every claim has file:line evidence
- [ ] Evidence sources section is complete (all cited files listed)
- [ ] No speculation — only code-verified claims
- [ ] Adaptation guidance references specific patterns from the study
- [ ] Intent statement is clear
- [ ] Categories match what the system actually does (no forced sections)
