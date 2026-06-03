# Content Guidelines: Anti-AI Slop, Content Standards, Scale Specs

The easiest traps to fall into when doing AI design. This is a "what NOT to do" list — more important than "what to do" — because AI slop is the default. If you do not actively avoid it, it will happen.

## Complete AI Slop Blacklist

### Visual Traps

**❌ Aggressive gradient backgrounds**

- Purple → pink → blue full-screen gradients (the signature look of AI-generated webpages)
- Rainbow gradients in any direction
- Mesh gradients covering the entire background
- ✅ If you do use gradients: subtle, monochromatic, intentional accenting (e.g., button hover states)

**❌ Rounded cards + left border accent color**

```css
/* This is the signature of AI-flavored cards */
.card {
  border-radius: 12px;
  border-left: 4px solid #3b82f6;
  padding: 16px;
}
```

This style of card is everywhere in AI-generated dashboards. Want to create emphasis? Use more design-intentional approaches: background color contrast, weight/size contrast, plain dividers, or simply do not use cards at all.

**❌ Emoji decoration**
Unless the brand itself uses emoji (like Notion, Slack), do not put emoji in the UI. **Especially avoid**:

- 🚀 ⚡️ ✨ 🎯 💡 before headings
- ✅ in feature lists
- → in CTA buttons (an arrow mark alone is fine; emoji arrows are not)

If you need icons, use a real icon library (Lucide / Heroicons / Phosphor), or use a placeholder.

**❌ SVG imagery**
Do not try to draw in SVG: people, scenes, devices, objects, or abstract art. AI-drawn SVG imagery is instantly recognizable, juvenile, and cheap-looking. **A grey rectangle with "+ Illustration placeholder 1200×800" text is 100x better than a clumsy SVG hero illustration**.

The only acceptable uses of SVG:

- Actual icons (16×16 to 32×32 scale)
- Geometric shapes as decorative elements
- Charts for data visualization

**❌ Excessive iconography**
Not every heading / feature / section needs an icon. Overusing icons makes the interface feel like a toy. Less is more.

**❌ "Data slop"**
Made-up stats for decoration:

- "10,000+ happy customers" (you do not even know if that is true)
- "99.9% uptime" (do not write it without real data)
- Decorative "metric cards" made of icon + number + phrase
- Fake data dressed up in mock tables

If you do not have real data, leave a placeholder or ask the user.

**❌ "Quote slop"**
Made-up user testimonials or celebrity quotes decorating the page. Leave a placeholder and ask the user for a real quote.

### Typography Traps

**❌ Avoid these overused fonts**:

- Inter (the default for AI-generated web pages)
- Roboto
- Arial / Helvetica
- Pure system font stack
- Fraunces (AI discovered it and ran it into the ground)
- Space Grotesk (AI's current favorite)

**✅ Use distinctive display + body pairings**. Inspiration directions:

- Serif display + sans-serif body (editorial feel)
- Mono display + sans body (technical feel)
- Heavy display + light body (contrast)
- Variable font for hero weight animations

Font resources:

- Underused gems on Google Fonts (Instrument Serif, Cormorant, Bricolage Grotesque, JetBrains Mono)
- Open source font sites (sibling fonts of Fraunces, Adobe Fonts)
- Never invent font names out of thin air

### Color Traps

**❌ Inventing colors from scratch**
Do not design an entire unfamiliar color palette from nothing. It usually does not harmonize.

**✅ Strategy**:

1. Have brand colors → use them, fill missing color tokens with oklch interpolation
2. No brand colors but have a reference → sample colors from a reference product screenshot
3. Starting completely from zero → choose a known color system (Radix Colors / Tailwind default palette / Anthropic brand), do not mix your own

**Defining color with oklch** is the most modern approach:

```css
:root {
  --primary: oklch(0.65 0.18 25); /* warm terracotta */
  --primary-light: oklch(0.85 0.08 25); /* lighter shade, same hue */
  --primary-dark: oklch(0.45 0.2 25); /* darker shade, same hue */
}
```

oklch guarantees the hue will not drift when adjusting lightness — more reliable than hsl.

**❌ Throwing on dark mode as an afterthought by inverting colors**
It is not a simple color invert. Good dark mode requires re-tuning saturation, contrast, and accent colors. If you do not want to do dark mode properly, do not do it.

### Layout Traps

**❌ Bento grid overuse**
Every AI-generated landing page wants a bento. Unless your information structure genuinely suits bento, use a different layout.

**❌ Large hero + 3-column features + testimonials + CTA**
This landing page template has been beaten to death. If you want to innovate, actually innovate.

**❌ Every card in a card grid looks identical**
Asymmetric, different-sized cards — some with images, some text-only, some spanning columns — that is what a real designer makes.

## Content Standards

### 1. Don't add filler content

Every element must earn its place. Empty space is a design problem — solve it with **composition** (contrast, rhythm, negative space), **not** by stuffing content in.

**Questions to identify filler**:

- If this content were removed, would the design get worse? If the answer is "no," remove it.
- What real problem does this element solve? If it is "to make the page feel less empty," delete it.
- Does this stat/quote/feature have real data behind it? If not, do not write it from thin air.

"One thousand no's for every yes."

### 2. Ask before adding material

Think adding another section / page / block would improve it? Ask the user first — do not unilaterally add it.

Why:

- The user knows their audience better than you do
- Adding content has a cost, and the user may not want it
- Unilaterally adding content violates the "junior designer reporting to a lead" relationship

### 3. Create a system up front

After exploring the design context, **verbally describe the system you intend to use** and let the user confirm:

```markdown
My design system:

- Color: #1A1A1A primary + #F0EEE6 background + #D97757 accent (from your brand)
- Typography: Instrument Serif for display + Geist Sans for body
- Rhythm: section titles use full-bleed colored background + white text; regular sections use white background
- Imagery: hero uses full-bleed photo, feature sections use placeholders until you provide assets
- Maximum 2 background colors, avoid visual clutter

Confirm this direction and I'll start building.
```

Get the user's confirmation before acting. This check-in prevents "finished half the work and realized the direction was wrong."

## Scale Specs

### Slides (1920×1080)

- Body text minimum **24px**, ideal 28-36px
- Headlines 60-120px
- Section titles 80-160px
- Hero headlines can go 180-240px
- Never use <24px text on slides

### Print Documents

- Body text minimum **10pt** (approximately 13.3px), ideal 11-12pt
- Headlines 18-36pt
- Captions 8-9pt

### Web and Mobile

- Body text minimum **14px** (use 16px for accessibility-friendly)
- Mobile body text **16px** (avoids iOS auto-zoom)
- Hit targets (clickable elements) minimum **44×44px**
- Line height 1.5-1.7 (Chinese text: 1.7-1.8)

### Contrast

- Body text vs background **at least 4.5:1** (WCAG AA)
- Large text vs background **at least 3:1**
- Check with Chrome DevTools accessibility tool

## CSS Power Moves

**Advanced CSS features** are a designer's best friends — use them boldly:

### Typography

```css
/* Makes headline line breaks more natural, prevents a single orphaned word on the last line */
h1,
h2,
h3 {
  text-wrap: balance;
}

/* Body text line breaks, avoids widows and orphans */
p {
  text-wrap: pretty;
}

/* Chinese typography power move: punctuation compression, line-start/end control */
p {
  text-spacing-trim: space-all;
  hanging-punctuation: first;
}
```

### Layout

```css
/* CSS Grid + named areas = outstanding readability */
.layout {
  display: grid;
  grid-template-areas:
    "header header"
    "sidebar main"
    "footer footer";
  grid-template-columns: 240px 1fr;
  grid-template-rows: auto 1fr auto;
}

/* Subgrid for aligning card content */
.card {
  display: grid;
  grid-template-rows: subgrid;
}
```

### Visual Effects

```css
/* Styled scrollbars */
* {
  scrollbar-width: thin;
  scrollbar-color: #666 transparent;
}

/* Glassmorphism (use sparingly) */
.glass {
  backdrop-filter: blur(20px) saturate(150%);
  background: color-mix(in oklch, white 70%, transparent);
}

/* View Transitions API for smooth page changes */
@view-transition {
  navigation: auto;
}
```

### Interaction

```css
/* :has() selector makes conditional styles easy */
.card:has(img) { padding-top: 0; } /* Cards with images get no top padding */

/* container queries make components truly responsive */
@container (min-width: 500px) { ... }

/* New color-mix function */
.button:hover {
  background: color-mix(in oklch, var(--primary) 85%, black);
}
```

## Decision Quick Reference: When in Doubt

- Want to add a gradient? → Probably do not
- Want to add an emoji? → Do not
- Want to give a card border-radius + border-left accent? → Do not, find another approach
- Want to draw a hero illustration in SVG? → Do not, use a placeholder
- Want to add a quote for decoration? → Ask the user if they have a real quote first
- Want to add a row of icon features? → Ask first whether icons are needed — they may not be
- Using Inter? → Switch to something with more character
- Using a purple gradient? → Switch to a color choice with a real rationale

**When you feel "adding this would look better" — that is usually the sign of AI slop.** Do the simplest version first, and only add when the user asks for it.
