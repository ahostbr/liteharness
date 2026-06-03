---
name: ls-stitch-pipeline
description: End-to-end design-to-production pipeline using Google Stitch + taste enforcement. Pulls AI-generated UI designs from Stitch via MCP, applies $150K agency-level design polish, and outputs production-ready Next.js/React code. Triggers on 'stitch pipeline', 'stitch to code', 'stitch build', 'design to production', 'stitch it', 'pull from stitch', 'use stitch', 'stitch design', 'vibe design', 'stitch website', 'design pipeline'.
---

# Stitch Pipeline — Design to Divine Production Code

End-to-end workflow: **Stitch design** -> **Taste enforcement** -> **Production-ready website**

## Prerequisites

- Stitch MCP server configured (`stitch` in `.mcp.json`)
- Google Stitch project with screens at [stitch.withgoogle.com](https://stitch.withgoogle.com)
- If MCP is not connected, fall back to manual mode (user pastes Stitch code)

## Workflow

### Phase 0: Input Resolution

Determine the input mode. Ask the user ONE question if needed:

| User says                                 | Mode                    | Action                                                                  |
| ----------------------------------------- | ----------------------- | ----------------------------------------------------------------------- |
| A Stitch project ID or "pull from stitch" | **MCP mode**            | Use Stitch MCP tools to pull designs                                    |
| Pastes HTML/code from Stitch              | **Paste mode**          | Parse the pasted Stitch output directly                                 |
| Describes what they want (no Stitch yet)  | **Generate-first mode** | Direct them to create in Stitch first, OR build from scratch with taste |
| Provides a screenshot or URL as inspo     | **Inspo mode**          | Use as reference, build with taste from scratch                         |

### Phase 1: Pull Design from Stitch (MCP Mode)

Use these Stitch MCP tools in sequence:

```
1. mcp__stitch__get_screen_code    — Pull the HTML/CSS for each screen
2. mcp__stitch__get_screen_image   — Pull screenshot for visual reference
3. mcp__stitch__build_site         — Map screens to routes, get full site HTML
```

**build_site example:**

```json
{
  "projectId": "<stitch-project-id>",
  "routes": [
    { "screenId": "<screen-id>", "route": "/" },
    { "screenId": "<screen-id>", "route": "/about" },
    { "screenId": "<screen-id>", "route": "/dashboard" }
  ]
}
```

If MCP tools aren't available (server not running), tell the user:

> "Stitch MCP isn't connected. Either run `npx @_davideast/stitch-mcp init` to set it up, or copy your Stitch code and paste it here."

### Phase 2: Taste Audit & Enhancement

**MANDATORY.** Every Stitch design goes through the taste filter before production output.

If the `ls-taste` skill is installed in this plugin, load its taste modules:

1. **`${CLAUDE_SKILL_DIR}/../ls-taste/taste-core.md`** — Design engineering directives, anti-slop rules, variance engine
2. **`${CLAUDE_SKILL_DIR}/../ls-taste/soft-core.md`** — Agency-level aesthetics, haptic micro-design, fluid motion
3. **`${CLAUDE_SKILL_DIR}/../ls-taste/output-core.md`** — Full code output enforcement, no placeholders

Read each file with the Read tool if it exists, then apply as constraints. If the files
are not present, apply the design standards inline from the audit checklist below.

**Audit the Stitch output for these common issues:**

| Problem                           | Fix                                                  |
| --------------------------------- | ---------------------------------------------------- |
| Inter/Roboto font                 | Swap to Geist, Satoshi, Cabinet Grotesk, or Outfit   |
| Generic 3-column card grid        | Bento grid, masonry, zig-zag, or horizontal scroll   |
| Purple/blue AI gradient           | Neutral base + single considered accent              |
| Emoji icons                       | Replace with Phosphor or Radix icons                 |
| Linear/ease-in-out transitions    | Spring physics or custom cubic-bezier                |
| Generic shadow-md                 | Soft ambient shadows or inner borders                |
| h-screen                          | min-h-[100dvh]                                       |
| Centered hero (if variance > 4)   | Split screen, left-aligned, asymmetric               |
| Missing hover/focus/active states | Add micro-interactions on every interactive element  |
| No dark mode consideration        | Add dark mode variant or ensure chosen palette works |

**Enhancement pass:**

- Add scroll-triggered animations (IntersectionObserver, not scroll listeners)
- Add hover micro-interactions on cards and buttons
- Ensure typography hierarchy (display -> heading -> subheading -> body -> caption)
- Apply spatial rhythm (consistent spacing system, not random padding)
- Add loading states and skeleton screens where appropriate

### Phase 3: Production Code Output

Transform the taste-audited design into production-ready code.

**Default stack** (unless user specifies otherwise):

- **Framework:** Next.js 15 (App Router) + TypeScript
- **Styling:** Tailwind CSS v4
- **Icons:** @phosphor-icons/react
- **Fonts:** Next/Font with premium typefaces
- **Motion:** CSS animations + IntersectionObserver (no heavy deps unless requested)
- **Package manager:** pnpm

**Output structure:**

```
project/
  src/
    app/
      layout.tsx          — Root layout with fonts, metadata, theme
      page.tsx            — Home page (mapped from Stitch "/" screen)
      [route]/page.tsx    — Additional pages from Stitch screens
      globals.css         — Tailwind imports + custom properties
    components/
      ui/                 — Reusable primitives (Button, Card, Badge, etc.)
      sections/           — Page sections (Hero, Features, Pricing, etc.)
      layout/             — Nav, Footer, Sidebar
    lib/
      utils.ts            — cn() helper, animation utilities
  tailwind.config.ts      — Custom theme (colors, fonts, spacing)
  package.json
  tsconfig.json
```

**Code quality rules:**

- Full files only — no `// ...rest`, no placeholders, no skeletons
- Server Components by default, `'use client'` only for interactive leaves
- Responsive: mobile-first with sm/md/lg/xl breakpoints
- Accessible: semantic HTML, ARIA labels, keyboard navigation, focus rings
- Performance: lazy load below-fold images, font-display: swap, no layout shift

### Phase 4: Verification

Before declaring done:

1. List all files created with a brief description
2. Provide the commands to run it:
   ```bash
   pnpm install
   pnpm dev
   ```
3. Note any TODO items (API connections, backend logic, auth) that need the user's input
4. If the user has a project already, integrate into their existing structure instead of scaffolding new

## Quick Reference — Stitch MCP Tools

| Tool               | Purpose                                       |
| ------------------ | --------------------------------------------- |
| `get_screen_code`  | Get HTML/CSS for a specific screen            |
| `get_screen_image` | Get base64 screenshot of a screen             |
| `build_site`       | Map multiple screens to routes, get full HTML |

## Modes Summary

```
/stitch-pipeline                    → Interactive: asks what you want to build
/stitch-pipeline <project-id>       → MCP mode: pulls from Stitch project
/stitch-pipeline (with pasted code) → Paste mode: enhances pasted Stitch output
```
