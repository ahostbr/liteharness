# Slide Decks: HTML Presentation Production Standards

Slide decks are a high-frequency design task. This document explains how to produce great HTML slide decks — from architecture selection and per-slide design through PDF/PPTX export.

**What this skill covers**:

- **HTML presentation version (the base artifact — always the default, always required)** — each slide as an independent HTML file + `assets/deck_index.html` aggregator for keyboard navigation and fullscreen presenting in the browser
- HTML → PDF export → `scripts/export_deck_pdf.mjs` / `scripts/export_deck_stage_pdf.mjs`
- HTML → editable PPTX export → `references/editable-pptx.md` + `scripts/html2pptx.js` + `scripts/export_deck_pptx.mjs` (requires HTML to follow 4 hard constraints)

> **⚠️ HTML is the foundation; PDF/PPTX are derivatives.** Regardless of the final delivery format, you **must** always build the HTML aggregated presentation first (`index.html` + `slides/*.html`) — it is the canonical "source" of the deck. PDF/PPTX are snapshots exported from HTML with a single command.
>
> **Why HTML-first**:
>
> - Best for live presenting (projector / screen-share goes fullscreen directly, keyboard navigation, no dependency on Keynote/PPT software)
> - Each slide can be opened individually in the browser for quick validation during development — no need to re-run the export every time
> - The only upstream for PDF/PPTX export (eliminates the "I fixed the HTML after export and now have to re-export" death loop)
> - Deliverables can be "HTML + PDF" or "HTML + PPTX" — the recipient uses whichever they prefer
>
> 2026-04-22 moxt brochure field test: after finishing 13 slides + `index.html` aggregator, `export_deck_pdf.mjs` exported the PDF in one command, zero changes. The HTML version itself is a browser-ready presentation deliverable.

---

## 🛑 Confirm Delivery Format Before Starting (Hardest Checkpoint)

**This decision comes before "single-file vs. multi-file."** 2026-04-20 field test on a private equity board project: **failing to confirm delivery format before starting = 2–3 hours of rework.**

### Decision Tree (HTML-first Architecture)

Every delivery starts from the same HTML aggregated presentation (`index.html` + `slides/*.html`). The delivery format only determines **HTML writing constraints** and **the export command**:

```
[Always the default · Required] HTML aggregated presentation (index.html + slides/*.html)
   │
   ├── Browser-only presenting / local HTML archive   → Done here. HTML has full visual freedom.
   │
   ├── Also need PDF (printing / sharing / archiving) → Run export_deck_pdf.mjs — one command
   │                                                    HTML writing is unconstrained, no visual limits
   │
   └── Also need editable PPTX (teammates edit text)  → Follow 4 hard constraints from line 1 of HTML
                                                        Run export_deck_pptx.mjs — one command
                                                        Sacrifice gradients / web components / complex SVG
```

### Kickoff Script (Copy and Use)

> No matter the final delivery format — HTML, PDF, or PPTX — I'll always build a browser-navigable HTML aggregated presentation first (`index.html` with keyboard navigation). This is the permanent default base artifact. On top of that I'll ask whether you also want a PDF / PPTX snapshot.
>
> Which export format do you need?
>
> - **HTML only** (presenting/archiving) → full visual freedom
> - **Also PDF** → same as above, plus one export command
> - **Also editable PPTX** (teammates will edit text in PPT) → I must follow 4 hard constraints from the very first line of HTML, which sacrifices some visual capability (no gradients, no web components, no complex SVG).

### Why "Needing PPTX Means Writing Under 4 Hard Constraints from the Start"

Editable PPTX requires `html2pptx.js` to translate the DOM element-by-element into PowerPoint objects. This requires **4 hard constraints**:

1. Body fixed at 960pt × 540pt (matching `LAYOUT_WIDE`, 13.333″ × 7.5″ — not 1920×1080px)
2. All text wrapped in `<p>`/`<h1>`–`<h6>` (no text directly in divs; no `<span>` as primary text carrier)
3. `<p>`/`<h*>` elements themselves cannot have background/border/shadow (put those on the outer div)
4. `<div>` cannot use `background-image` (use `<img>` tags instead)
5. No CSS gradients, no web components, no complex decorative SVG

**This skill's default HTML has high visual freedom** — heavy use of spans, nested flex, complex SVG, web components (e.g. `<deck-stage>`), CSS gradients — **almost none of it naturally passes html2pptx's constraints** (field test: visual-driven HTML straight into html2pptx has a pass rate < 30%).

### Real-World Cost Comparison of Two Paths (2026-04-20 field incident)

| Path                                           | Approach                                                        | Result                                                                                                                                                      | Cost                                                                                                                                      |
| ---------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| ❌ **Write free HTML first, patch PPTX later** | Single-file deck-stage + lots of SVG/span decoration            | Only two options left for editable PPTX:<br>A. Hand-write hundreds of lines of hardcoded pptxgenjs coordinates<br>B. Rewrite all 17 slides in Path A format | 2–3 hours of rework, and the hand-written version has **perpetual maintenance debt** (change one word in HTML → manually sync PPTX again) |
| ✅ **Follow Path A constraints from step 1**   | Each slide as independent HTML + 4 hard constraints + 960×540pt | One command exports 100% editable PPTX, and HTML can also be presented fullscreen in the browser (Path A HTML is standard browsable HTML)                   | 5 extra minutes thinking "how do I wrap this text in `<p>`" while writing HTML, zero rework                                               |

### Handling Mixed Delivery

User says "I want HTML presenting **and** editable PPTX" — **this is not mixed**, it's the PPTX requirement covering the HTML requirement. HTML written under Path A constraints can be presented fullscreen in the browser (just add a `deck_index.html` aggregator). **No extra cost.**

User says "I want PPTX **and** animations / web components" — **this is a real conflict**. Tell the user: editable PPTX requires sacrificing those visual capabilities. Let them make the call — don't quietly build a hand-written pptxgenjs solution (that becomes perpetual maintenance debt).

### What to Do If PPTX Is Needed After the Fact (Emergency Fallback)

In rare cases: HTML is already written and then you find out PPTX is needed. Recommended **fallback workflow** (full details in `references/editable-pptx.md` at the end — "Fallback: Visual draft already exists but user insists on editable PPTX"):

1. **First choice: produce PDF** (100% visual fidelity, cross-platform, recipients can view and print) — if what the recipient actually needs is "presenting/archiving", PDF is the best deliverable
2. **Second choice: AI rewrites an editable-HTML version based on the visual draft** → exports editable PPTX — preserves color/layout/copy decisions, sacrifices gradients, web components, complex SVG
3. **Not recommended: hand-write pptxgenjs from scratch** — positions, fonts, alignment all need manual tuning, high maintenance cost, and every future HTML change requires another manual sync

Always present the options to the user and let them decide. **Never make hand-writing pptxgenjs your first instinct** — that is the last-resort fallback.

---

## 🛑 Before Mass Production: Build a 2-Slide Showcase to Lock the Grammar

**For any deck ≥ 5 slides, never write straight from slide 1 to the last slide.** Correct order validated in the 2026-04-22 moxt brochure field build:

1. Pick **2 slide types with the most visual contrast** and build those as a showcase first (e.g., "cover" + "mood/quote slide", or "cover" + "product feature slide")
2. Screenshot them and get the user to confirm the grammar (masthead / typeface / color / spacing / structure / bilingual balance)
3. Once the direction is approved, batch-produce the remaining N–2 slides, each reusing the established grammar
4. Assemble all slides into the HTML aggregator + PDF/PPTX derivatives at the end

**Why**: Writing all 13 slides straight to the end → user says "wrong direction" = 13 rounds of rework. Building 2 showcase slides → wrong direction = 2 rounds of rework. Once visual grammar is established, decisions for the remaining N slides are dramatically constrained — only "how does the content fit in."

**Showcase slide selection principle**: Choose the two slides with the most structurally different visual layouts. If those two pass, everything in between will pass too.

| Deck Type                     | Recommended Showcase Pair                                 |
| ----------------------------- | --------------------------------------------------------- |
| B2B brochure / product launch | Cover + content slide (philosophy/emotional page)         |
| Brand launch                  | Cover + product feature slide                             |
| Data report                   | Full-page data visualization + analysis conclusions slide |
| Tutorial / course             | Chapter title slide + specific knowledge point slide      |

---

## 📐 Publication Grammar Template (Reusable from moxt Field Test)

Suitable for B2B brochures / product launches / long-form report decks. Reuse this structure on every slide = 13 slides with complete visual consistency, zero rework.

### Per-Slide Skeleton

```
┌─ masthead (top strip + rule) ─────────────────────────────┐
│  [logo 22-28px] · A Product Brochure          Issue · Date · URL │
├───────────────────────────────────────────────────────────┤
│                                                           │
│  ── kicker (green rule + uppercase label)                 │
│  CHAPTER XX · SECTION NAME                                │
│                                                           │
│  H1 (Chinese Noto Serif SC 900)                           │
│  Key words in brand primary color                         │
│                                                           │
│  English subtitle (Lora italic, secondary heading)        │
│  ─────────────── divider ──────────────                   │
│                                                           │
│  [Specific content: 60/40 two-col / 2×2 grid / list]     │
│                                                           │
├───────────────────────────────────────────────────────────┤
│ section name                                   XX / total │
└───────────────────────────────────────────────────────────┘
```

### Style Conventions (Copy Directly)

- **H1**: Chinese Noto Serif SC 900, 80–140px depending on content volume, key words in brand primary color (don't color the entire line)
- **English subtitle**: Lora italic 26–46px, brand signature words (e.g., "AI team") in bold + primary color italic
- **Body text**: Noto Serif SC 17–21px, line-height 1.75–1.85
- **Accent highlights**: Bold key words in primary color in body text, no more than 3 per slide (more than that loses anchoring effect)
- **Background**: Warm off-white #FAFAFA + very subtle radial-gradient noise (`rgba(33,33,33,0.015)`) for paper texture

### Differentiate the Visual Lead on Each Slide

If all 13 slides are "text + one screenshot" it becomes monotonous. **Rotate the type of visual lead on each slide**:

| Visual Type                                             | Suitable Section                                |
| ------------------------------------------------------- | ----------------------------------------------- |
| Cover typography (large type + masthead + pillar)       | First slide / chapter opener                    |
| Single character portrait (oversized single momo, etc.) | Introducing a single concept/character          |
| Group portrait / avatar card row                        | Team / user case studies                        |
| Timeline card progression                               | Showing "long-term relationship" or "evolution" |
| Knowledge graph / connected node diagram                | Showing "collaboration" or "flow"               |
| Before/After comparison card + arrow                    | Showing "change" or "contrast"                  |
| Product UI screenshot + outlined device frame           | Specific feature showcase                       |
| Big-quote (half-page large type)                        | Mood / question / quotation slide               |
| Real person avatar + quote card (2×2 or 1×4)            | User testimonials / usage scenarios             |
| Large-type back cover + URL oval button                 | CTA / closing                                   |

---

## ⚠️ Common Pitfalls (moxt Field Test Summary)

### 1. Emoji Fails to Render in Chromium / Playwright Export

Chromium doesn't ship with a color emoji font by default; `page.pdf()` or `page.screenshot()` shows emoji as empty boxes.

**Fix**: Use Unicode text symbols (`✦` `✓` `✕` `→` `·` `—`) instead, or replace with plain text ("Email · 23" instead of "📧 23 emails").

### 2. `export_deck_pdf.mjs` Throws `Cannot find package 'playwright'`

Cause: ESM module resolution searches upward from the script's location for `node_modules`. The script lives inside this skill's `scripts/` directory, which has no dependencies there.

**Fix**: Copy the script to the deck project directory (e.g., `brochure/build-pdf.mjs`), run `npm install playwright pdf-lib` in the project root, then `node build-pdf.mjs --slides slides --out output/deck.pdf`.

### 3. Google Fonts Not Fully Loaded Before Screenshot → Chinese Displays in System Default Font

Add at least `wait-for-timeout=3500` before Playwright screenshot/PDF to allow webfonts to download and paint. Or self-host fonts to `shared/fonts/` to reduce network dependency.

### 4. Information Density Imbalance: Too Much Content on One Slide

moxt philosophy slide first version had 2×2 = 4 paragraphs + 3 bottom principles = 7 content blocks — cramped and repetitive. After trimming to 1×3 = 3 paragraphs, breathing room returned immediately.

**Fix**: Limit each slide to "1 core message + 3–4 supporting points + 1 visual lead." If it exceeds that, split to a new slide. **Less is more** — audience spends 10 seconds on a slide; giving them 1 memorable point is more effective than 4.

---

## 🛑 Choose Architecture First: Single-File or Multi-File?

**This is the very first decision when building a slide deck. Getting it wrong leads to repeated pitfalls. Read this section fully before starting.**

### Architecture Comparison

| Dimension              | Single-file + `deck_stage.js`                            | **Multi-file + `deck_index.html` aggregator**                           |
| ---------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------- |
| Code structure         | One HTML, all slides are `<section>` elements            | Each slide is independent HTML; `index.html` aggregates via iframes     |
| CSS scope              | ❌ Global — one slide's styles can bleed into all others | ✅ Natural isolation — each iframe is its own world                     |
| Validation granularity | ❌ Requires JS goTo to navigate to a specific slide      | ✅ Double-click a single HTML file to view it in the browser            |
| Parallel development   | ❌ One file — multiple agents editing causes conflicts   | ✅ Agents can work on different slides in parallel, zero-conflict merge |
| Debug difficulty       | ❌ One CSS error crashes the whole deck                  | ✅ One slide's error affects only itself                                |
| In-deck interactivity  | ✅ Cross-slide shared state is simple                    | 🟡 Cross-iframe requires postMessage                                    |
| Print to PDF           | ✅ Built in                                              | ✅ Aggregator iterates iframes in beforeprint                           |
| Keyboard navigation    | ✅ Built in                                              | ✅ Built into the aggregator                                            |

### Which to Choose? (Decision Tree)

```
│ Question: How many slides is the deck likely to have?
├── ≤10 slides, needs in-deck animations or cross-slide interactivity, pitch deck → single-file
└── ≥10 slides, academic lecture, course material, long deck, multi-agent parallel build → multi-file (recommended)
```

**Default to multi-file**. It is not a "fallback" — it is **the primary path for long decks and team collaboration**. Every advantage of single-file (keyboard navigation, printing, scaling) exists in multi-file too, while multi-file's CSS scope isolation and per-slide validatability cannot be retrofitted into single-file.

### Why Is This Rule So Hard? (Real Incident Record)

Single-file architecture hit four pitfalls in a row during an AI Psychology lecture deck build:

1. **CSS specificity override**: `.emotion-slide { display: grid }` (specificity 10) beat `deck-stage > section { display: none }` (specificity 2), causing all slides to render stacked simultaneously.
2. **Shadow DOM slot rules overridden by outer CSS**: `::slotted(section) { display: none }` couldn't block the outer rule's override — sections refused to hide.
3. **localStorage + hash navigation race condition**: After refresh, the page landed at the localStorage-saved old position instead of the hash target.
4. **High validation cost**: Had to call `page.evaluate(d => d.goTo(n))` to navigate to a slide for screenshot, twice as slow as `goto(file://.../slides/05-X.html)`, and it frequently errored.

All root causes trace back to **a single global namespace** — multi-file architecture eliminates these issues at the physical layer.

---

## Path A (Default): Multi-File Architecture

### Directory Structure

```
MyDeck/
├── index.html              # Copied from assets/deck_index.html, update MANIFEST
├── shared/
│   ├── tokens.css          # Shared design tokens (color palette / type scale / common chrome)
│   └── fonts.html          # <link> imports for Google Fonts (included on each slide)
└── slides/
    ├── 01-cover.html       # Each file is a complete 1920×1080 HTML
    ├── 02-agenda.html
    ├── 03-problem.html
    └── ...
```

### Per-Slide Template Skeleton

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <title>P05 · Chapter Title</title>
    <link href="https://fonts.googleapis.com/css2?family=..." rel="stylesheet" />
    <link rel="stylesheet" href="../shared/tokens.css" />
    <style>
      /* Styles unique to this slide. Any class names here won't pollute other slides. */
      body { padding: 120px; }
      .my-thing { ... }
    </style>
  </head>
  <body>
    <!-- 1920×1080 content (body width/height locked in tokens.css) -->
    <div class="page-header">...</div>
    <div>...</div>
    <div class="page-footer">...</div>
  </body>
</html>
```

**Key constraints**:

- `<body>` is the canvas — lay out directly on it. Do not wrap in `<section>` or other containers.
- `width: 1920px; height: 1080px` is locked by the `body` rule in `shared/tokens.css`.
- Import `shared/tokens.css` for shared design tokens (color palette, type scale, page-header/footer, etc.).
- Font `<link>` tags go on each slide individually (font imports are cheap and ensure each slide can be opened standalone).

### Aggregator: `deck_index.html`

**Copy directly from `assets/deck_index.html`**. The only thing you need to change is the `window.DECK_MANIFEST` array — list all slide filenames and human-readable labels in order:

```js
window.DECK_MANIFEST = [
  { file: "slides/01-cover.html", label: "Cover" },
  { file: "slides/02-agenda.html", label: "Agenda" },
  { file: "slides/03-problem.html", label: "Problem Statement" },
  // ...
];
```

The aggregator has built-in: keyboard navigation (←/→/Home/End/number keys/P to print), scale + letterbox, bottom-right counter, localStorage memory, hash-based navigation, and print mode (iterates iframes for per-page PDF output).

### Per-Slide Validation (This Is Multi-File's Killer Advantage)

Each slide is an independent HTML file. **After finishing a slide, double-click it in the browser to check**:

```bash
open slides/05-personas.html
```

Playwright screenshots also go straight to `goto(file://.../slides/05-personas.html)` — no JS navigation required, no interference from another slide's CSS. This brings the cost of "change a little, check a little" close to zero.

### Parallel Development

Assign each slide's task to a different agent and run them simultaneously — HTML files are fully independent, no merge conflicts. Long decks built this way compress production time to 1/N.

### What Goes in `shared/tokens.css`

Only things **truly shared across slides**:

- CSS variables (color palette, type scale, spacing scale)
- `body { width: 1920px; height: 1080px; }` canvas lock
- `.page-header` / `.page-footer` chrome used identically on every slide

**Do not** put single-slide layout classes in here — that degrades back into the global-pollution problem of single-file architecture.

---

## Path B (Small Deck): Single-File + `deck_stage.js`

Use when: ≤10 slides, cross-slide shared state is needed (e.g., a React tweaks panel that controls all slides), or an extremely compact pitch deck demo.

### Basic Usage

1. Read the content from `assets/deck_stage.js` and embed in the HTML `<script>` tag (or `<script src="deck_stage.js">`)
2. Wrap slides in `<deck-stage>` in the body
3. 🛑 **Script tag must come after `</deck-stage>`** (see hard constraint below)

```html
<body>
  <deck-stage>
    <section>
      <h1>Slide 1</h1>
    </section>
    <section>
      <h1>Slide 2</h1>
    </section>
  </deck-stage>

  <!-- ✅ Correct: script comes after deck-stage -->
  <script src="deck_stage.js"></script>
</body>
```

### 🛑 Script Position Hard Constraint (2026-04-20 Real Pitfall)

**Do not put `<script src="deck_stage.js">` in `<head>`.** Even if it defines `customElements` there, when the parser reaches the opening `<deck-stage>` tag it fires `connectedCallback` — at that point the child `<section>` elements haven't been parsed yet, `_collectSlides()` gets an empty array, the counter shows `1 / 0`, and all slides render stacked simultaneously.

**Three compliant approaches** (pick one):

```html
<!-- ✅ Most recommended: script after </deck-stage> -->
</deck-stage>
<script src="deck_stage.js"></script>

<!-- ✅ Also fine: script in head with defer -->
<head><script src="deck_stage.js" defer></script></head>

<!-- ✅ Also fine: module scripts are naturally deferred -->
<head><script src="deck_stage.js" type="module"></script></head>
```

`deck_stage.js` already has a built-in `DOMContentLoaded` deferred-collection defense, so placing the script in `<head>` won't cause a complete crash — but `defer` or placing it at the bottom of `<body>` is still the cleaner approach, avoiding reliance on the defense branch.

### ⚠️ Single-File CSS Trap (Required Reading)

The most common pitfall in single-file architecture — **the `display` property getting hijacked by per-slide styles**.

Common mistake 1 (writing display: flex directly on section):

```css
/* ❌ Outer CSS specificity 2 overrides shadow DOM ::slotted(section){display:none} (also 2) */
deck-stage > section {
  display: flex;            /* All slides will render stacked simultaneously! */
  flex-direction: column;
  padding: 80px;
  ...
}
```

Common mistake 2 (section has a higher-specificity class):

```css
.emotion-slide {
  display: grid;
} /* Specificity: 10, even worse */
```

Both cause **all slides to render stacked simultaneously** — the counter might show `1 / 10` pretending everything is fine, but visually slide 1 is on top of slide 2 on top of slide 3.

### ✅ Starter CSS (Copy to Avoid Pitfalls)

**The section element itself** only manages "visible/hidden"; **layout (flex/grid etc.) goes on `.active`**:

```css
/* section only defines non-display common styles */
deck-stage > section {
  background: var(--paper);
  padding: 80px 120px;
  overflow: hidden;
  position: relative;
  /* ⚠️ Do not write display here! */
}

/* Lock "non-active = hidden" — specificity + !important double defense */
deck-stage > section:not(.active) {
  display: none !important;
}

/* Active slide gets the display + layout it needs */
deck-stage > section.active {
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* Print mode: all slides must show — override :not(.active) */
@media print {
  deck-stage > section {
    display: flex !important;
  }
  deck-stage > section:not(.active) {
    display: flex !important;
  }
}
```

Alternative: **put single-slide flex/grid on an inner wrapper `<div>`**, while the section itself is always just a "display: block/none" toggle. This is the cleanest approach:

```html
<deck-stage>
  <section>
    <div class="slide-content flex-layout">...</div>
  </section>
</deck-stage>
```

### Custom Dimensions

```html
<deck-stage width="1080" height="1920">
  <!-- 9:16 portrait -->
</deck-stage>
```

---

## Slide Labels

Both deck_stage and deck_index label each slide (counter display). Give them **more meaningful** labels:

**Multi-file**: in `MANIFEST`, write `{ file, label: "04 Problem Statement" }`
**Single-file**: add `<section data-screen-label="04 Problem Statement">` to the section

**Key: Slide numbers start at 1, not 0.**

When a user says "slide 5," they mean the 5th slide — never array index `[4]`. Humans don't speak in zero-indexed terms.

---

## Speaker Notes

**Off by default** — only add when the user explicitly requests them.

With speaker notes, you can reduce on-slide text to a minimum and focus on impactful visuals — the notes carry the full script.

### Format

**Multi-file**: in `index.html`'s `<head>`:

```html
<script type="application/json" id="speaker-notes">
  ["Script for slide 1...", "Script for slide 2...", "..."]
</script>
```

**Single-file**: same location.

### Notes Writing Guidelines

- **Complete**: not an outline — these are the actual words to be spoken
- **Conversational**: the way you'd naturally talk, not formal written prose
- **Aligned**: array index N corresponds to slide N
- **Length**: 200–400 words is ideal
- **Emotional arc**: mark emphasis, pauses, and stress points

---

## Slide Design Patterns

### 1. Establish a System First (Required)

After exploring the design context, **state the system you'll use in plain language first**:

```markdown
Deck system:

- Backgrounds: at most 2 (90% white + 10% dark section dividers)
- Typefaces: Instrument Serif for display, Geist Sans for body
- Rhythm: section dividers use full-bleed color + white type; regular slides use white background
- Imagery: hero slides use full-bleed photography; data slides use charts

I'll build to this system. Tell me if anything looks off.
```

Get user confirmation before proceeding.

### 2. Common Slide Layouts

- **Title slide**: solid background + huge heading + subtitle + author/date
- **Section divider**: colored background + chapter number + chapter title
- **Content slide**: white background + heading + 1–3 bullet points
- **Data slide**: heading + large chart/number + brief annotation
- **Image slide**: full-bleed photo + small caption at the bottom
- **Quote slide**: whitespace + large quote + attribution
- **Two-column**: left/right comparison (vs / before–after / problem–solution)

Use at most 4–5 layouts in one deck.

### 3. Scale (Worth Repeating)

- Body text minimum **24px**, ideal 28–36px
- Headings **60–120px**
- Hero text **180–240px**
- Slides are viewed from 10 meters away — type needs to be large enough

### 4. Visual Rhythm

Decks need **intentional variety**:

- Color rhythm: mostly white backgrounds + occasional colored section dividers + occasional dark segments
- Density rhythm: a few text-heavy slides + a few image-heavy slides + a few sparse quote slides
- Type scale rhythm: regular headings + occasional massive hero text

**Don't make every slide look the same** — that's a PPT template, not design.

### 5. Breathing Room (Required Reading for Data-Dense Slides)

**The most common beginner pitfall**: trying to pack every possible piece of information onto one slide.

Information density ≠ effective information delivery. Academic/lecture decks especially need restraint:

- List/matrix slides: don't draw all N items at the same size. Use **hierarchy** — enlarge the 5 items being discussed today as leads, shrink the remaining 16 as background hints.
- Big-number slides: the number itself is the visual lead. Keep surrounding caption to 3 lines or fewer — more than that causes the audience's eyes to jump around.
- Quote slides: leave whitespace between the quotation and the attribution — don't crowd them together.

Self-audit against "Is the data the lead?" and "Is the text squeezed?" — revise until the whitespace feels slightly uncomfortable.

---

## Printing to PDF

**Multi-file**: `deck_index.html` already handles the `beforeprint` event and outputs each slide as its own PDF page.

**Single-file**: `deck_stage.js` handles the same.

Print styles are already written — no additional `@media print` CSS is needed.

---

## Exporting to PPTX / PDF (Self-Service Scripts)

HTML-first is the primary format. But users frequently need PPTX/PDF deliverables. Two general-purpose scripts are provided — **usable with any multi-file deck** — located in `scripts/`:

### `export_deck_pdf.mjs` — Export Vector PDF (Multi-File Architecture)

```bash
node scripts/export_deck_pdf.mjs --slides <slides-dir> --out deck.pdf
```

**Characteristics**:

- Text remains **vector** (copyable, searchable)
- 100% visual fidelity (Playwright's embedded Chromium renders and prints)
- **No changes to the HTML source at all**
- Each slide runs its own `page.pdf()`, then `pdf-lib` merges them

**Dependencies**: `npm install playwright pdf-lib`

**Limitation**: PDF text cannot be edited — to change content, go back to the HTML source.

### `export_deck_stage_pdf.mjs` — Single-File deck-stage Architecture Only ⚠️

**When to use**: The deck is a single HTML file + `<deck-stage>` web component wrapping N `<section>` elements (Path B architecture). The `export_deck_pdf.mjs` approach of "one `page.pdf()` per HTML file" doesn't apply — use this dedicated script instead.

```bash
node scripts/export_deck_stage_pdf.mjs --html deck.html --out deck.pdf
```

**Why `export_deck_pdf.mjs` cannot be reused** (2026-04-20 real incident):

1. **Shadow DOM beats `!important`**: deck-stage's shadow CSS has `::slotted(section) { display: none }` (only the active slide gets `display: block`). Even with `@media print { deck-stage > section { display: block !important } }` in the light DOM, it can't override — after `page.pdf()` triggers print media, Chromium's final render only shows the active slide, resulting in **a PDF with only 1 page** (the current active slide repeated).

2. **Looping goto per slide still produces only 1 page**: The intuitive fix — "navigate to each `#slide-N` once, then `page.pdf({pageRanges:'1'})`" — also fails, because after the print CSS overrides the `deck-stage > section { display: block }` rule in the outer scope, the final render is always the first section in the list (not the one you navigated to). Result: 17 loops produce 17 copies of slide 1 (the cover).

3. **Absolutely-positioned children overflow to the next page**: Even if all sections render successfully, if section itself is `position: static`, absolutely-positioned `cover-footer`/`slide-footer` elements position relative to the initial containing block — when print forces a section to 1080px height, the absolute footer may be pushed to the next page (resulting in a PDF with one more page than sections, the extra page containing only an orphaned footer).

**Fix strategy** (already implemented in the script):

```js
// After opening the HTML, use page.evaluate to extract sections from the deck-stage slot,
// attach them directly to body in a plain div, and inline styles to ensure position:relative + fixed dimensions
await page.evaluate(() => {
  const stage = document.querySelector("deck-stage");
  const sections = Array.from(stage.querySelectorAll(":scope > section"));
  document.head.appendChild(
    Object.assign(document.createElement("style"), {
      textContent: `
      @page { size: 1920px 1080px; margin: 0; }
      html, body { margin: 0 !important; padding: 0 !important; }
      deck-stage { display: none !important; }
    `,
    }),
  );
  const container = document.createElement("div");
  sections.forEach((s) => {
    s.style.cssText =
      "width:1920px!important;height:1080px!important;display:block!important;position:relative!important;overflow:hidden!important;page-break-after:always!important;break-after:page!important;background:#F7F4EF;margin:0!important;padding:0!important;";
    container.appendChild(s);
  });
  // Last slide: no page break to avoid trailing blank page
  sections[sections.length - 1].style.pageBreakAfter = "auto";
  sections[sections.length - 1].style.breakAfter = "auto";
  document.body.appendChild(container);
});

await page.pdf({
  width: "1920px",
  height: "1080px",
  printBackground: true,
  preferCSSPageSize: true,
});
```

**Why this works**:

- Pulls sections out of the shadow DOM slot into a plain light-DOM div — completely bypasses the `::slotted(section) { display: none }` rule
- Inline `position: relative` ensures absolutely-positioned children position relative to their section, not overflowing it
- `page-break-after: always` tells the browser's print engine to give each section its own page
- Last section has no page break to avoid a trailing blank page

**Note when validating with `mdls -name kMDItemNumberOfPages`**: macOS's Spotlight metadata caches. After overwriting a PDF, run `mdimport file.pdf` to force a refresh — otherwise the old page count is shown. Use `pdfinfo` or count `pdftoppm` output files for the true page count.

---

### `export_deck_pptx.mjs` — Export Editable PPTX

```bash
# Only mode: native editable text boxes (fonts fall back to system fonts)
node scripts/export_deck_pptx.mjs --slides <dir> --out deck.pptx
```

How it works: `html2pptx` reads the computed style of each element and translates the DOM into PowerPoint objects (text frames / shapes / pictures). Text becomes real text boxes — double-click in PPT to edit.

**Hard constraints** (HTML must satisfy these, otherwise the slide is skipped — full details in `references/editable-pptx.md`):

- All text must be inside `<p>`/`<h1>`–`<h6>`/`<ul>`/`<ol>` (no bare text in divs)
- `<p>`/`<h*>` tags themselves cannot have background/border/shadow (put those on an outer div)
- Do not use `::before`/`::after` for decorative text (pseudo-elements can't be extracted)
- Inline elements (`span`/`em`/`strong`) cannot have margin
- No CSS gradients (not renderable)
- `div` cannot use `background-image` (use `<img>` instead)

The script has a built-in **auto-preprocessor** — it automatically wraps "bare text in leaf divs" in `<p>` tags (preserving class). This solves the most common violation (bare text). Other violations (border on `<p>`, margin on `<span>`, etc.) still require fixing in the HTML source.

**Font fallback caveat**:

- Playwright measures text-box dimensions using webfonts; PowerPoint/Keynote renders using local fonts
- When they differ, **overflow or misalignment** occurs — inspect every slide visually
- Recommended: install the fonts used in the HTML on the target machine, or fall back to `system-ui`

**For visual-priority decks, don't go down this path** → use `export_deck_pdf.mjs` for PDF instead. PDF has 100% visual fidelity, is vector, cross-platform, and has searchable text — it is the true destination for visual-priority decks, not some "uneditable compromise."

### Write HTML Export-Friendly from the Start

The most robust deck: **follow the 4 editable hard constraints when writing HTML from the beginning**. Then `export_deck_pptx.mjs` will pass all slides. The extra effort is minimal:

```html
<!-- ❌ Not good -->
<div class="title">Key Finding</div>

<!-- ✅ Good (wrapped in p, class preserved) -->
<p class="title">Key Finding</p>

<!-- ❌ Not good (border on p) -->
<p class="stat" style="border-left: 3px solid red;">41%</p>

<!-- ✅ Good (border on outer div) -->
<div class="stat-wrap" style="border-left: 3px solid red;">
  <p class="stat">41%</p>
</div>
```

### When to Use Which

| Scenario                                      | Recommendation                                        |
| --------------------------------------------- | ----------------------------------------------------- |
| Submitting to organizer / archiving           | **PDF** (universal, high fidelity, text searchable)   |
| Sending to collaborators for minor text edits | **Editable PPTX** (accept font fallback)              |
| Live presenting, no edits needed              | **PDF** (vector fidelity, cross-platform)             |
| HTML is the primary presentation medium       | Play in the browser directly; export is just a backup |

## Deep Path for Editable PPTX (Long-Term Projects Only)

If your deck will be maintained long-term, revised repeatedly, or collaborated on by a team — it's worth **writing the HTML under html2pptx constraints from the very beginning**, so `export_deck_pptx.mjs` passes every slide. See `references/editable-pptx.md` for details (4 hard constraints + HTML template + common error quick reference + fallback workflow for existing visual drafts).

---

## FAQ

**Multi-file: iframe content won't load / white screen**
→ Check that `MANIFEST`'s `file` paths are correct relative to `index.html`. Use browser DevTools to see if the iframe `src` is directly accessible.

**Multi-file: one slide's styles are conflicting with another slide's**
→ Impossible (iframe isolation). If it looks like a conflict, it's a cache issue — hard-refresh with Cmd+Shift+R.

**Single-file: multiple slides rendering stacked simultaneously**
→ CSS specificity issue. See the "Single-File CSS Trap" section above.

**Single-file: scaling looks wrong**
→ Check that all slides are directly under `<deck-stage>` as `<section>` elements. No `<div>` wrapper in between.

**Single-file: jump to a specific slide**
→ Add hash to URL: `index.html#slide-5` jumps to slide 5.

**Both architectures: text position inconsistent across screens**
→ Use fixed dimensions (1920×1080) and `px` units — no `vw`/`vh` or `%`. Scaling is handled uniformly.

---

## Validation Checklist (Required After Completing Any Deck)

1. [ ] Open `index.html` (or main HTML) directly in the browser — verify no broken images and fonts are loaded on the first slide
2. [ ] Press → to advance through every slide — no blank slides, no layout breaks
3. [ ] Press P for print preview — each slide occupies exactly one A4 page (or 1920×1080) with no clipping
4. [ ] Hard-refresh 3 random slides with Cmd+Shift+R — confirm localStorage memory is working correctly
5. [ ] Playwright batch screenshot (multi-file: iterate `slides/*.html`; single-file: use goTo to navigate), visually scan all slides by eye
6. [ ] Search for `TODO` / `placeholder` remnants — confirm all have been removed
