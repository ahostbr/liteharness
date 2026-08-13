---
name: ls-taste
description: Premium web design skill — applies high-end agency-level design principles to new or existing websites. Audits for AI design cliches, enforces elite typography, color calibration, layout diversification, motion choreography, and performance guardrails. Use when building or redesigning websites, landing pages, dashboards, or any frontend to look like a $150K agency build instead of generic AI output. Triggers on 'taste', 'apply taste skill', 'redesign this site', 'make it premium', 'high-end design', 'anti-slop', 'upgrade the design', 'make it look expensive', 'agency quality', 'awwwards tier'.
---

# Taste — Premium Web Design System

4 modules loaded from `${CLAUDE_SKILL_DIR}/`:

## Protocol

### Step 1: Detect Mode

- **New build** — user wants a new page/component from scratch → load `taste-core.md` + `soft-core.md`
- **Redesign** — user wants to upgrade an existing site → load `redesign-core.md` first (audit), then apply `taste-core.md` + `soft-core.md` fixes
- **Always** load `output-core.md` to enforce complete code output (no placeholders, no "...rest of code")

### Step 2: Load Modules

Read the relevant module files from this skill directory:

```
${CLAUDE_SKILL_DIR}/taste-core.md    — Design engineering directives, anti-slop rules, motion engine
${CLAUDE_SKILL_DIR}/soft-core.md     — $150K agency aesthetics, haptic micro-design, fluid motion
${CLAUDE_SKILL_DIR}/redesign-core.md — Audit checklist for existing projects, fix priority order
${CLAUDE_SKILL_DIR}/output-core.md   — Full output enforcement, no truncation, no placeholders
```

Use the Read tool to load each file's content, then follow their instructions as design constraints.

### Step 3: Execute

**For redesigns:**

1. Read the existing codebase (framework, styling, current patterns)
2. Run the redesign audit (Section "Design Audit" from redesign-core.md)
3. List every violation found
4. Apply fixes in priority order: font swap → color cleanup → hover states → layout/spacing → components → states → typography polish

**For new builds:**

1. Roll the Creative Variance Engine (taste-core.md Section 3) — pick Vibe + Layout archetype
2. Apply all design engineering directives (Sections 3-9)
3. Apply soft-core.md haptic micro-aesthetics (Double-Bezel, Nested CTAs, Spatial Rhythm)
4. Apply motion choreography (soft-core.md Section 5)
5. Run pre-flight checklist before outputting

### Key Rules (Quick Reference)

| Rule                                 | Enforcement                                   |
| ------------------------------------ | --------------------------------------------- |
| No Inter font                        | Use Geist, Satoshi, Cabinet Grotesk, Outfit   |
| No emoji icons                       | Use Phosphor or Radix icons                   |
| No 3-column card grid                | Bento, masonry, zig-zag, or horizontal scroll |
| No centered hero (variance > 4)      | Split screen, left-aligned, asymmetric        |
| No purple/blue AI gradient           | Neutral bases + single considered accent      |
| No neon/outer glows                  | Inner borders or subtle tinted shadows        |
| No h-screen                          | Always min-h-[100dvh]                         |
| No linear easing                     | Spring physics or custom cubic-bezier         |
| No gradient text headers (excessive) | Use sparingly, one per page max               |
| Cards only when elevation needed     | Prefer spacing, border-t, divide-y            |
| Full code output only                | No "...", no "rest of code", no placeholders  |
