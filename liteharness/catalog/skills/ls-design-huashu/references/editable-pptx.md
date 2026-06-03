# Editable PPTX Export: Hard HTML Constraints + Size Decisions + Common Errors

This document covers the path of **using `scripts/html2pptx.js` + `pptxgenjs` to translate HTML element-by-element into a truly editable PowerPoint text box**, which is the only path supported by `export_deck_pptx.mjs`.

> **Core prerequisite**: To take this path, the HTML must be written according to the 4 constraints below from the very first line. **This is not write-first, convert-later** — retroactive remediation will trigger 2-3 hours of rework (validated in the 2026-04-20 options private board meeting project).
>
> For scenarios where visual fidelity takes priority (animation / web components / CSS gradients / complex SVG), switch to the PDF path (`export_deck_pdf.mjs` / `export_deck_stage_pdf.mjs`) instead. **Do not** expect PPTX export to achieve both visual fidelity and editability — this is a physical constraint of the PPTX file format itself (see "Why the 4 Constraints Are Physics, Not Bugs" at the end).

---

## Canvas Size: Use 960x540pt (LAYOUT_WIDE)

PPTX units are **inches** (physical dimensions), not px. Decision principle: the body's computedStyle dimensions must **match the presentation layout's inch dimensions** (+-0.1", enforced by `html2pptx.js`'s `validateDimensions` check).

### 3 Candidate Sizes Compared

| HTML body           | Physical size      | Corresponding PPT layout    | When to choose                                                         |
| ------------------- | ------------------ | --------------------------- | ---------------------------------------------------------------------- |
| **`960pt x 540pt`** | **13.333" x 7.5"** | **pptxgenjs `LAYOUT_WIDE`** | Recommended default (standard modern PowerPoint 16:9)                  |
| `720pt x 405pt`     | 10" x 5.625"       | Custom                      | Only when the user specifies a "legacy PowerPoint Widescreen" template |
| `1920px x 1080px`   | 20" x 11.25"       | Custom                      | Non-standard size; text appears abnormally small when projected        |

**Don't think of the HTML dimensions as resolution.** PPTX is a vector document — the body dimensions determine **physical size**, not sharpness. A very large body (20"x11.25") won't make text crisper — it will just make the point size small relative to the canvas, which looks worse when projected or printed.

### Three Equivalent Ways to Write the body (pick one)

```css
body {
  width: 960pt;
  height: 540pt;
} /* Clearest, recommended */
body {
  width: 1280px;
  height: 720px;
} /* Equivalent, px convention */
body {
  width: 13.333in;
  height: 7.5in;
} /* Equivalent, inch intuition */
```

Matching pptxgenjs code:

```js
const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 inch, no custom definition needed
```

---

## 4 Hard Constraints (Violating These Causes Direct Errors)

`html2pptx.js` translates the HTML DOM element-by-element into PowerPoint objects. PowerPoint's format constraints projected onto HTML = the 4 rules below.

### Rule 1: No Bare Text Directly Inside a DIV — Must Wrap in `<p>` or `<h1>`-`<h6>`

```html
<!-- Wrong: text directly inside div -->
<div class="title">Q3 revenue grew 23%</div>

<!-- Correct: text inside <p> or <h1>-<h6> -->
<div class="title"><h1>Q3 revenue grew 23%</h1></div>
<div class="body"><p>New users are the primary driver</p></div>
```

**Why**: PowerPoint text must exist inside a text frame, and text frames correspond to HTML block-level elements (p/h\*/li). A bare `<div>` has no corresponding text container in PPTX.

**You also cannot use `<span>` as the primary text carrier** — span is an inline element and cannot independently align as a text box. Spans can only **nest inside p/h\*** to apply local styles (bold, color changes).

### Rule 2: No CSS Gradients — Only Solid Colors

```css
/* Wrong */
background: linear-gradient(to right, #ff6b6b, #4ecdc4);

/* Correct: solid color */
background: #ff6b6b;

/* If multiple color stripes are required, use flex child elements each with their own solid color */
.stripe-bar {
  display: flex;
}
.stripe-bar div {
  flex: 1;
}
.red {
  background: #ff6b6b;
}
.teal {
  background: #4ecdc4;
}
```

**Why**: PowerPoint shape fill supports only solid-fill and gradient-fill, but pptxgenjs's `fill: { color: ... }` maps only to solid. Doing a PowerPoint-native gradient requires writing a different structure; the current toolchain does not support this.

### Rule 3: Background / Border / Shadow Can Only Go on DIVs, Not on Text Tags

```html
<!-- Wrong: <p> has a background color -->
<p style="background: #FFD700; border-radius: 4px;">Key content</p>

<!-- Correct: outer div carries the background/border; <p> only handles the text -->
<div style="background: #FFD700; border-radius: 4px; padding: 8pt 12pt;">
  <p>Key content</p>
</div>
```

**Why**: In PowerPoint, shapes (rectangles/rounded rectangles) and text frames are two separate objects. An HTML `<p>` translates to only a text frame — background/border/shadow belong to the shape, and must be written on the **div that wraps the text**.

### Rule 4: No `background-image` on DIVs — Use `<img>` Tags

```html
<!-- Wrong -->
<div style="background-image: url('chart.png')"></div>

<!-- Correct -->
<img
  src="chart.png"
  style="position: absolute; left: 50%; top: 20%; width: 300pt; height: 200pt;"
/>
```

**Why**: `html2pptx.js` extracts image paths only from `<img>` elements; it does not parse the URL in a CSS `background-image` property.

---

## Path A HTML Template Skeleton

Each slide is a separate HTML file with its own isolated scope (avoids CSS pollution from single-file decks).

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <style>
      * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
      }
      body {
        width: 960pt;
        height: 540pt; /* Must match LAYOUT_WIDE */
        font-family:
          system-ui,
          -apple-system,
          "PingFang SC",
          sans-serif;
        background: #fefef9; /* Solid color, no gradients */
        overflow: hidden;
      }
      /* DIV handles layout / background / border */
      .card {
        position: absolute;
        background: #1a4a8a; /* Background on the DIV */
        border-radius: 4pt;
        padding: 12pt 16pt;
      }
      /* Text tags only handle font styles — no background or border */
      .card h2 {
        font-size: 24pt;
        color: #ffffff;
        font-weight: 700;
      }
      .card p {
        font-size: 14pt;
        color: rgba(255, 255, 255, 0.85);
      }
    </style>
  </head>
  <body>
    <!-- Title area: outer div for positioning, inner text tags for content -->
    <div style="position: absolute; top: 40pt; left: 60pt; right: 60pt;">
      <h1 style="font-size: 36pt; color: #1A1A1A; font-weight: 700;">
        Title as a declarative statement, not a topic label
      </h1>
      <p style="font-size: 16pt; color: #555555; margin-top: 10pt;">
        Subtitle provides supplementary context
      </p>
    </div>

    <!-- Content card: div handles background, h2/p handle text -->
    <div class="card" style="top: 130pt; left: 60pt; width: 240pt; height: 160pt;">
      <h2>Key point one</h2>
      <p>Short explanatory text</p>
    </div>

    <!-- List: use ul/li, do not manually add bullet symbols -->
    <div style="position: absolute; top: 320pt; left: 60pt; width: 540pt;">
      <ul style="font-size: 16pt; color: #1A1A1A; padding-left: 24pt; list-style: disc;">
        <li>First key point</li>
        <li>Second key point</li>
        <li>Third key point</li>
      </ul>
    </div>

    <!-- Illustration: use <img> tag, not background-image -->
    <img
      src="illustration.png"
      style="position: absolute; right: 60pt; top: 110pt; width: 320pt; height: 240pt;"
    />
  </body>
</html>
```

---

## Common Errors Quick-Reference

| Error message                                         | Cause                                                                | Fix                                                                                          |
| ----------------------------------------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `DIV element contains unwrapped text "XXX"`           | Bare text inside a div                                               | Wrap the text in `<p>` or `<h1>`-`<h6>`                                                      |
| `CSS gradients are not supported`                     | Used linear/radial-gradient                                          | Switch to solid color, or use segmented flex child elements                                  |
| `Text element <p> has background`                     | `<p>` tag has a background color                                     | Wrap it in a `<div>` that carries the background; `<p>` handles only text                    |
| `Background images on DIV elements are not supported` | div uses background-image                                            | Switch to an `<img>` tag                                                                     |
| `HTML content overflows body by Xpt vertically`       | Content exceeds 540pt                                                | Reduce content or decrease font size, or truncate with `overflow: hidden`                    |
| `HTML dimensions don't match presentation layout`     | body dimensions don't match the presentation layout                  | Set body to `960pt x 540pt` paired with `LAYOUT_WIDE`; or use defineLayout for a custom size |
| `Text box "XXX" ends too close to bottom edge`        | A large-font `<p>` is less than 0.5 inch from the body's bottom edge | Move it up, leave enough bottom margin; PPT will clip some content near the bottom anyway    |

---

## Basic Workflow (3 Steps to a PPTX)

### Step 1: Write Each Page as a Separate, Constraint-Compliant HTML File

```
My Deck/
+-- slides/
|   +-- 01-cover.html    # Each file is a complete 960x540pt HTML document
|   +-- 02-agenda.html
|   +-- ...
+-- illustration/        # All images referenced by <img> tags
    +-- chart1.png
    +-- ...
```

### Step 2: Write build.js to Call `html2pptx.js`

```js
const pptxgen = require("pptxgenjs");
const html2pptx = require("../scripts/html2pptx.js"); // this skill's script

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 inch, matches HTML's 960x540pt

  const slides = ["01-cover.html", "02-agenda.html", "03-content.html"];
  for (const file of slides) {
    await html2pptx(`./slides/${file}`, pres);
  }

  await pres.writeFile({ fileName: "deck.pptx" });
})();
```

### Step 3: Open and Verify

- Open the exported PPTX in PowerPoint/Keynote
- Double-clicking any text should allow direct editing (if it shows as an image, Rule 1 was violated)
- Verify overflow: each page should fit within the body bounds with no clipping

---

## This Path vs. Other Options (When to Choose What)

| Requirement                                                                                                         | Choose                                                                                                          |
| ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Colleagues need to edit text in the PPTX / sending to non-technical people for further editing                      | **This document's path** (editable — requires writing HTML to the 4 constraints from the start)                 |
| Just for presenting / archiving, no further edits needed                                                            | `export_deck_pdf.mjs` (multi-file) or `export_deck_stage_pdf.mjs` (single-file deck-stage) — outputs vector PDF |
| Visual fidelity is the priority (animation, web components, CSS gradients, complex SVG), non-editable is acceptable | **PDF** (same as above) — PDF is both faithful and cross-platform, more appropriate than "image-embedded PPTX"  |

**Never run html2pptx on a visually free-form HTML file** — in practice, visually-driven HTML has a pass rate below 30%, and fixing the remaining pages one by one is slower than rewriting from scratch. This scenario should output PDF, not force a PPTX.

---

## Fallback: You Already Have a Visual Draft but the User Insists on Editable PPTX

This scenario comes up occasionally: you or the user have already written a visually-driven HTML (gradients, web components, complex SVG all used), and PDF would be the natural output — but the user explicitly says "no, it must be editable PPTX."

**Don't just run `html2pptx` expecting it to pass** — in practice, visually-driven HTML has a pass rate below 30% with html2pptx; the other 70% will error or degrade. The correct fallback is:

### Step 1: Communicate Limitations Upfront (Transparent Communication)

Tell the user three things in one sentence:

> "Your current HTML uses [specifically list: gradients / web components / complex SVG / ...], which will fail when converting directly to editable PPTX. I have two options:
>
> - A. **Output PDF** (recommended) — visual fidelity 100% preserved, recipients can view and print but cannot edit text
> - B. **Use the visual draft as a blueprint and rewrite an editable version** (preserve color/layout/copy design decisions, but restructure the HTML to follow the 4 hard constraints — sacrificing gradients, web components, complex SVG, and other visual capabilities) then export as editable PPTX
>
> Which would you like?"

Don't downplay Option B — be explicit about **what will be lost**. Let the user make the tradeoff.

### Step 2: If the User Chooses B — AI Rewrites, Don't Ask the User to Do It

The doctrine here is: **the user provides design intent; you are responsible for translating it into a compliant implementation.** You're not asking the user to learn the 4 hard constraints and rewrite it themselves.

Principles to follow when rewriting:

- **Preserve**: color system (primary/secondary/neutral), information hierarchy (title/subtitle/body/caption), core copy, layout skeleton (top-middle-bottom / left-right columns / grid), page rhythm
- **Degrade**: CSS gradients to solid color or flex segments, web components to block-level HTML, complex SVG to simplified `<img>` or solid-color geometry, shadows to deleted or minimal, custom fonts to system fonts
- **Rewrite**: bare text wrapped in `<p>` / `<h*>`, `background-image` to `<img>` tag, background/border on `<p>` moved to outer div

### Step 3: Produce a Before/After Comparison (Transparent Delivery)

After rewriting, give the user a before/after comparison so they know which visual details were simplified:

```
Original design -> editable version adjustments
- Title area purple gradient -> primary color #5B3DE8 solid background
- Data card shadows -> removed (replaced with 2pt stroke for differentiation)
- Complex SVG line chart -> simplified to <img> PNG (generated from HTML screenshot)
- Hero web component animation -> static first frame (web components cannot be translated)
```

### Step 4: Export & Dual-Format Delivery

- `editable` version HTML -> run `scripts/export_deck_pptx.mjs` to produce editable PPTX
- Recommended to also retain the original visual draft -> run `scripts/export_deck_pdf.mjs` to produce high-fidelity PDF
- Deliver both formats to the user: visual draft PDF + editable PPTX, each serving its own purpose

### When to Refuse Option B Outright

In some cases, rewriting costs too much — advise the user to give up on editable PPTX:

- The HTML's core value is animation or interaction (after rewriting, only a static first frame remains — 50%+ of information is lost)
- More than 30 pages — rewriting cost exceeds 2 hours
- The visual design deeply depends on precise SVG / custom filters (after rewriting, it bears almost no resemblance to the original)

In these cases, tell the user: "Rewriting this deck would be too costly. I recommend outputting PDF instead of PPTX. If the recipient truly needs a .pptx format, be aware the visual design will be substantially simpler — would you like to switch to PDF?"

---

## Why the 4 Constraints Are Physics, Not Bugs

These 4 constraints are not the result of the `html2pptx.js` author cutting corners — they are **constraints of the PowerPoint file format (OOXML) itself**, projected onto HTML:

- Text in PPTX must exist inside a text frame (`<a:txBody>`), which corresponds to block-level HTML elements
- PPTX shapes and text frames are two separate objects — you cannot simultaneously paint a background and write text on the same element
- PPTX shape fill has limited gradient support (only certain preset gradients; arbitrary CSS angle gradients are not supported)
- PPTX picture objects must reference real image files — they are not CSS properties

With this understanding, **don't expect the tooling to become smarter** — the HTML must be written to fit the PPTX format, not the other way around.
