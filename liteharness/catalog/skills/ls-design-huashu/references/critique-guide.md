# Design Review Deep Guide

> Detailed reference for Phase 7. Provides scoring criteria, scenario-specific emphasis, and a common issues checklist.

---

## Scoring Criteria Explained

### 1. Philosophy Alignment

| Score | Criteria                                                                                                        |
| ----- | --------------------------------------------------------------------------------------------------------------- |
| 9-10  | The design perfectly embodies the core spirit of the chosen philosophy — every detail has a philosophical basis |
| 7-8   | Overall direction is correct, core characteristics are in place, isolated details deviate                       |
| 5-6   | The intent is visible, but execution has mixed in other stylistic elements — not pure enough                    |
| 3-4   | Surface-level imitation only — the philosophical core was not understood                                        |
| 1-2   | Essentially unrelated to the chosen philosophy                                                                  |

**Review points**:

- Does it use the signature techniques of this designer / institution?
- Do the color, typography, and layout conform to this philosophical system?
- Are there any "self-contradicting" elements? (e.g., chose Kenya Hara but packed the space full of content)

### 2. Visual Hierarchy

| Score | Criteria                                                                                                   |
| ----- | ---------------------------------------------------------------------------------------------------------- |
| 9-10  | The user's eye flows naturally along the designer's intended path — zero friction in information retrieval |
| 7-8   | Primary/secondary relationships are clear, with 1-2 instances of hierarchy ambiguity                       |
| 5-6   | Headlines and body text can be distinguished, but mid-level hierarchy is confused                          |
| 3-4   | Information is flat — no clear visual entry point                                                          |
| 1-2   | Chaotic — the user does not know where to look first                                                       |

**Review points**:

- Is the size contrast between headline and body sufficient? (at least 2.5x)
- Do color / weight / size establish 3-4 clear hierarchy levels?
- Is negative space guiding the eye?
- "Squint test": squint and look — is the hierarchy still clear?

### 3. Craft Quality

| Score | Criteria                                                              |
| ----- | --------------------------------------------------------------------- |
| 9-10  | Pixel-perfect — zero flaws in alignment, spacing, or color            |
| 7-8   | Overall polished, with 1-2 minor alignment/spacing issues             |
| 5-6   | Roughly aligned, but spacing inconsistent, color usage not systematic |
| 3-4   | Obvious alignment errors, chaotic spacing, too many colors            |
| 1-2   | Rough — looks like a draft                                            |

**Review points**:

- Is a consistent spacing system used (e.g., 8pt grid)?
- Is the spacing between equivalent elements consistent?
- Is the number of colors controlled? (typically no more than 3-4)
- Is the font family unified? (typically no more than 2)
- Is edge alignment precise?

### 4. Functionality

| Score | Criteria                                                                          |
| ----- | --------------------------------------------------------------------------------- |
| 9-10  | Every design element serves the objective — zero redundancy                       |
| 7-8   | Functional orientation is clear, with a small amount of removable decoration      |
| 5-6   | Basically usable, but there are obvious decorative elements distracting attention |
| 3-4   | Form over function — users must work to find information                          |
| 1-2   | Completely buried in decoration — lost the ability to communicate information     |

**Review points**:

- If any single element were removed, would the design get worse? (If not, it should be removed)
- Is the CTA / key information in the most prominent position?
- Are there elements added "because they look nice"?
- Does the information density match the medium? (PPT should not be too dense; PDF can be denser)

### 5. Originality

| Score | Criteria                                                                   |
| ----- | -------------------------------------------------------------------------- |
| 9-10  | Refreshing — found a unique expression within this philosophical framework |
| 7-8   | Has its own ideas — not simply template-filling                            |
| 5-6   | Competent but generic — looks like a template                              |
| 3-4   | Heavy use of clichés (e.g., gradient spheres to represent AI)              |
| 1-2   | Entirely a template or asset collage                                       |

**Review points**:

- Are common clichés avoided? (see "Common Issues Checklist" below)
- Is there personal expression while adhering to the design philosophy?
- Are there "unexpected yet clearly right" design decisions?

---

## Scenario Review Emphasis

Different output types have different review priorities:

| Scenario                    | Most Important Dimension        | Second Priority      | Can Relax                                    |
| --------------------------- | ------------------------------- | -------------------- | -------------------------------------------- |
| WeChat article cover/images | Originality, Visual Hierarchy   | Philosophy Alignment | Functionality (single image, no interaction) |
| Infographics                | Functionality, Visual Hierarchy | Craft Quality        | Originality (accuracy first)                 |
| PPT/Keynote                 | Visual Hierarchy, Functionality | Craft Quality        | Originality (clarity first)                  |
| PDF/White Paper             | Craft Quality, Functionality    | Visual Hierarchy     | Originality (professionalism first)          |
| Landing page/website        | Functionality, Visual Hierarchy | Originality          | — (comprehensive requirements)               |
| App UI                      | Functionality, Craft Quality    | Visual Hierarchy     | Philosophy Alignment (usability first)       |
| Xiaohongshu images          | Originality, Visual Hierarchy   | Philosophy Alignment | Craft Quality (atmosphere first)             |

---

## Top 10 Common Design Issues

### 1. AI Tech Clichés

**Problem**: Gradient spheres, digital rain, blue circuit boards, robot faces
**Why it is a problem**: Users are visually fatigued by these — they cannot distinguish you from anyone else
**Fix**: Replace literal symbols with abstract metaphors (e.g., use the metaphor of "conversation" rather than a chat bubble icon)

### 2. Insufficient Type Scale Hierarchy

**Problem**: Gap between headline and body is too small (<2.5x)
**Why it is a problem**: Users cannot quickly locate key information
**Fix**: Headlines should be at least 3x the body size (e.g., body 16px → headline 48-64px)

### 3. Too Many Colors

**Problem**: 5 or more colors with no clear primary/secondary structure
**Why it is a problem**: Visual chaos, weak brand identity
**Fix**: Limit to 1 primary + 1 secondary + 1 accent + grayscale

### 4. Inconsistent Spacing

**Problem**: Spacing between elements is arbitrary, no system
**Why it is a problem**: Looks unprofessional, visual rhythm is broken
**Fix**: Establish an 8pt grid system (spacing only uses 8/16/24/32/48/64px)

### 5. Insufficient Negative Space

**Problem**: Every space is filled with content
**Why it is a problem**: Cramped information causes reading fatigue, which actually reduces communication efficiency
**Fix**: Negative space should occupy at least 40% of total area (minimalist styles: 60%+)

### 6. Too Many Fonts

**Problem**: 3 or more typefaces in use
**Why it is a problem**: Visual noise, undermines cohesion
**Fix**: Maximum 2 fonts (1 for headlines + 1 for body), create variation through weight and size

### 7. Inconsistent Alignment

**Problem**: Some elements left-aligned, some centered, some right-aligned
**Why it is a problem**: Destroys the sense of visual order
**Fix**: Choose one alignment (left alignment recommended) and apply it globally

### 8. Decoration Overpowering Content

**Problem**: Background patterns / gradients / shadows steal attention from the primary content
**Why it is a problem**: Putting the cart before the horse — users came for information, not decoration
**Fix**: "If this decoration were removed, would the design get worse?" If not, remove it

### 9. Cyberpunk Neon Overuse

**Problem**: Dark blue background (#0D1117) + neon glowing effects
**Why it is a problem**: Default aesthetic no-go zone (the taste baseline of this skill), and it has become one of the biggest clichés — users can override with their own brand
**Fix**: Choose a more distinctive color scheme (reference the 20-style color systems)

### 10. Information Density Mismatched to Medium

**Problem**: A full page of text in a PPT / 10 elements crammed into a cover image
**Why it is a problem**: Different mediums have different optimal information densities
**Fix**:

- PPT: 1 core idea per page
- Cover image: 1 visual focal point
- Infographic: layered presentation
- PDF: can be denser, but needs clear navigation

---

## Review Output Template

```
## Design Review Report

**Overall Score**: X.X/10 [Excellent (8+) / Good (6-7.9) / Needs Improvement (4-5.9) / Failing (<4)]

**Dimension Scores**:
- Philosophy Alignment: X/10 [one-line explanation]
- Visual Hierarchy: X/10 [one-line explanation]
- Craft Quality: X/10 [one-line explanation]
- Functionality: X/10 [one-line explanation]
- Originality: X/10 [one-line explanation]

### Strengths (Keep)
- [Specifically identify what was done well, described in design language]

### Issues (Fix)
[Sorted by severity]

**1. [Issue Name]** — ⚠️ Critical / ⚡ Important / 💡 Optimization
- Current state: [describe what exists]
- Problem: [why this is an issue]
- Fix: [specific action, including numerical values]

### Quick Wins
If you only have 5 minutes, prioritize these 3 things:
- [ ] [Most impactful fix]
- [ ] [Second most important fix]
- [ ] [Third most important fix]
```

---

**Version**: v1.0
**Updated**: 2026-02-13
