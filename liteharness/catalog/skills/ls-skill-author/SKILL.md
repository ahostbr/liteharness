---
name: ls-skill-author
description: |
  Author a brand-new Claude Code skill from a seed (a description, a workflow you just ran, or a
  transcript) and put it through a mandatory, fail-closed security gate before it can ever become
  discoverable. Triggers on: "author a skill", "make a skill from this", "create a new skill",
  "scaffold a skill", "turn this into a skill", "generate a SKILL.md", "build me a skill",
  "write a skill that triggers on ...", "skill self-creation".
  This CREATES a new skill. It is NOT /train (which mutates an EXISTING skill's description to
  improve trigger accuracy) and NOT /self-improve (which scans and retrains the whole skill set).
  If the user wants to improve a skill that already exists, use /train or /self-improve instead.
version: "1.0.0"
tags: [meta, skill-creation, security, gate, authoring]
---

# Skill Author — Generate a Skill, Then Prove It's Safe Before It Can Load

Claude can write a new skill in seconds. The danger is that a freshly generated `SKILL.md` — from an
untrusted seed, a pasted transcript, or a subtly poisoned instruction — becomes **immediately
discoverable** and runs with your tools. This skill closes that hole: **every** authored skill is
staged in an isolated quarantine, scanned deterministically, reviewed by an LLM security pass, and
gated behind a human approval **before** a single byte lands in a scanned skills root.

> The hard invariant: an unscanned `SKILL.md` is **structurally unable** to become discoverable.
> Drafts live OUTSIDE every scanned root, and the only path in (`promote`) refuses unless a Layer-1
> pass, a Layer-2 GO, and a human approval all exist. Any exception or malformed input => refuse.

## Companion tooling (in this skill's directory)

| File            | Role                                                                             |
| --------------- | -------------------------------------------------------------------------------- |
| `scan_skill.py` | Deterministic security tool. Subcommands: `draft`, `scan`, `promote`.            |
| `denylist.yaml` | Versioned regex+severity ruleset (any HIGH finding = hard fail).                 |
| `TEMPLATE.md`   | SKILL.md scaffold — least-privilege, empty `allowed-tools`.                      |
| `evals/`        | 10 should-trigger / 10 should-not queries for this skill's own trigger accuracy. |

Paths (env-overridable for testing):

- **Quarantine (drafts):** `~/.liteharness/skill-quarantine/<name>/` — OUTSIDE all scanned roots.
- **Promote target:** `~/.claude/skills/<name>/` — the only discoverable destination.

## Pipeline

Run these steps in order. Do **not** skip the gate. Do **not** hand-copy files into `~/.claude/skills`.

### 1. Resolve the seed

Establish, with the user, the minimum spec:

- **name** — lowercase, hyphenated, unique (`^[a-z0-9][a-z0-9_-]*$`). Check it doesn't already exist
  in `~/.claude/skills/` or the plugin skill set.
- **what it does** — one paragraph.
- **when it triggers** — concrete phrases; how it's disambiguated from adjacent skills.
- **least-privilege tools** — the _specific_ scoped tools it needs. Default to none.

If the seed is "make a skill from what we just did", summarize the workflow into that spec first.

### 2. Fill the template

Read `${CLAUDE_SKILL_DIR}/TEMPLATE.md` and produce the concrete `SKILL.md` (and any companion
files). Keep `allowed-tools` **minimal** — never a wildcard. Write the generated files to a scratch
staging dir (e.g. the session scratchpad), NOT into any skills root.

### 3. Draft into quarantine (isolation)

```bash
python "${CLAUDE_SKILL_DIR}/scan_skill.py" draft --name <name> \
  --skill-md <staging>/SKILL.md \
  --companion helper.py=<staging>/helper.py    # repeatable; omit if none
# or stage a whole directory:
python "${CLAUDE_SKILL_DIR}/scan_skill.py" draft --name <name> --from <staging-dir>
```

`draft` HARD-ASSERTS the resolved quarantine path is outside `~/.claude/skills`, `~/.claude/agents`,
`~/.claude/commands`, and the plugin cache. If that assert ever fails, it refuses (exit 3). The draft
is now staged but **cannot be discovered**.

### 4. Layer-1 — deterministic scan

```bash
python "${CLAUDE_SKILL_DIR}/scan_skill.py" scan --name <name>
```

Reads `denylist.yaml` and scans frontmatter + body + companions. Emits a JSON verdict
(`{verdict, findings[], counts}`) and writes it to `<quarantine>/<name>/.scan-verdict.json`. **Any
HIGH finding => `verdict: "fail"` and exit 1.** On a fail: show the user the findings, fix the
generated skill, re-`draft`, re-`scan`. Do not proceed until Layer-1 passes. Review MEDIUM/LOW
findings too — they inform the next step.

### 5. Layer-2 — LLM security review

Only after Layer-1 passes. Run the built-in **`security-review`** skill (or `/consult-polymaths`
with a security lens) over the quarantined skill. It reads the whole skill as an adversary would and
looks for what static regex can't: subtle intent, social-engineering of the operator, capability
creep, staged multi-step attacks. It must return a clear **GO** or **NO-GO**.

On GO, record the decision as the Layer-2 token:

```bash
printf '{"decision":"GO","reviewer":"security-review","ts":"%s"}' "$(date -u +%FT%TZ)" \
  > "$HOME/.liteharness/skill-quarantine/<name>/.layer2-go"
```

On NO-GO: stop, surface the reasoning, iterate. Never fabricate the token.

### 6. Gate on both + HITL

Present a concise summary to the human: name, what it does, `allowed-tools`, the Layer-1 verdict, and
the Layer-2 rationale. Ask for explicit approval to install. Only on a real human "yes":

```bash
printf '{"decision":"APPROVE","approver":"<user>","ts":"%s"}' "$(date -u +%FT%TZ)" \
  > "$HOME/.liteharness/skill-quarantine/<name>/.hitl-approve"
```

### 7. Promote (the only discoverability path)

```bash
python "${CLAUDE_SKILL_DIR}/scan_skill.py" promote --name <name>
```

`promote` is fail-closed. It **re-runs Layer-1 fresh** (catching any post-scan tampering) and refuses
(exit 2) unless the stored passing verdict, the Layer-2 GO token, and the HITL approve token all
exist and are valid. Only then does it copy the skill into `~/.claude/skills/<name>/` (control tokens
are stripped). A malformed frontmatter or any exception at this stage => refuse.

### 8. Refresh + record

- Refresh discovery so the new skill is visible: `/library refresh` (or `/library list`).
- Record the outcome as a collective pattern:

```bash
lst run pattern action=record outcome=success \
  tags="skill-author,security-gate" \
  summary="Authored + gated skill <name> (Layer-1 pass, Layer-2 GO, HITL approve)"
```

## Failure handling

- **Layer-1 fail (HIGH):** fix the skill, re-draft, re-scan. Never edit the denylist to make a real
  finding disappear — if a rule is a false positive, bump `denylist.yaml` `version` deliberately and
  say why.
- **Layer-2 NO-GO:** treat as authoritative. Iterate or abandon.
- **No human approval:** the skill stays in quarantine indefinitely. That is the safe default.
- **promote refuses:** something is missing or was tampered with. Read the `reason`, re-run the
  pipeline from the failed step. Do not hand-move files.

## Why this shape

`draft` guarantees isolation, `scan` guarantees a deterministic floor no LLM can be talked out of,
`security-review` catches intent static analysis misses, and HITL keeps a human in the loop for the
irreversible step. Remove any one and an unscanned skill could load. That's why `promote` requires
**all three** and fails closed on anything unexpected.
