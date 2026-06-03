# Gallery Ripple + Multi-Focus: Scene Choreography Philosophy

> A **reusable visual choreography structure** distilled from the huashu-design hero animation v9 (25 seconds, 8 scenes).
> This is not an animation production pipeline — it is about **when this kind of choreography is the "right" choice**.
> Practical reference: [demos/hero-animation-v9.mp4](../demos/hero-animation-v9.mp4) · [https://www.huasheng.ai/huashu-design-hero/](https://www.huasheng.ai/huashu-design-hero/)

## The One-Sentence Summary

> **When you have 20+ visually homogeneous assets and the scene needs to "express scale and depth," reach for Gallery Ripple + Multi-Focus choreography first — not a heap of more complex layouts.**

Generic SaaS feature animations, product launches, skill showcases, series portfolio presentations — as long as you have enough assets and they share a consistent style, this structure almost always produces results.

---

## What This Technique Is Actually Expressing

This is not "showing off assets" — it tells a story through **two rhythmic shifts**:

**First beat - Ripple Expansion (~1.5s)**: 48 cards spread outward from the center. The viewer is stunned by the **quantity** — "Oh, this thing has produced that much."

**Second beat - Multi-Focus (~8s, 4 cycles)**: As the camera slowly pans, 4 times the background dims + desaturates and a single card is magnified to the center of the screen — the viewer shifts from "impact of quantity" to "quality in close-up," each cycle lasting a steady 1.7s.

**Core narrative structure**: **Scale (Ripple) -> Scrutiny (Focus x 4) -> Fade Out (Walloff)**. These three beats together express "Breadth x Depth" — not just that it can produce a lot, but that each one is worth stopping to look at.

Compare to the counterexamples:

| Approach                                  | Viewer Perception                                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| 48 cards in a static grid (no Ripple)     | Looks nice but no narrative — like a grid screenshot                                                   |
| Fast cuts one by one (no Gallery context) | Feels like a slideshow — loses the sense of "scale"                                                    |
| Ripple only, no Focus                     | Stunned but doesn't remember any particular card                                                       |
| **Ripple + Focus x 4 (this formula)**     | **First stunned by quantity, then lingering on quality, finally calm fade — a complete emotional arc** |

---

## Prerequisites (All Four Must Be Met)

This choreography is **not a universal fit**. All 4 of the following conditions are required:

1. **Asset count >= 20, ideally 30+**
   Fewer than 20 cards will make the Ripple feel "sparse" — density requires every cell to be in motion. v9 used 48 cells x 32 images (looped to fill).

2. **Assets share a visually consistent style**
   All 16:9 slide previews / all app screenshots / all cover designs — aspect ratio, color tone, and layout must feel like "a set." Mixing styles makes the Gallery look like a clipboard.

3. **Assets remain readable when zoomed in individually**
   Focus magnifies a card to 960px wide. If the source image becomes blurry or information-sparse at that size, the Focus beat fails. Reverse-verify: can you pick out 4 cards from the 48 as "most representative"? If not, asset quality is uneven.

4. **The scene is landscape or square, not portrait**
   The Gallery's 3D tilt (`rotateX(14deg) rotateY(-10deg)`) needs horizontal extension. Portrait orientation will make the tilt look narrow and awkward.

**Fallback paths when conditions are not met**:

| Missing condition         | Degrade to                                             |
| ------------------------- | ------------------------------------------------------ |
| Fewer than 20 assets      | "3-5 cards side by side statically + individual focus" |
| Inconsistent styles       | "Cover + 3 chapter hero images" keynote-style          |
| Information-sparse assets | "Data-driven dashboard" or "big quote + large type"    |
| Portrait scene            | "Vertical scroll + sticky cards"                       |

---

## Technical Recipe (v9 Production Parameters)

### 4-Layer Structure

```
viewport (1920x1080, perspective: 2400px)
  +-- canvas (4320x2520, oversized overflow) -> 3D tilt + pan
      +-- 8x6 grid = 48 cards (gap 40px, padding 60px)
          +-- img (16:9, border-radius 9px)
      +-- focus-overlay (absolute center, z-index 40)
          +-- img (matches selected slide)
```

**Key**: The canvas is 2.25x larger than the viewport — this gives the pan a "peeking into a larger world" feeling.

### Ripple Expansion (Distance-Delay Algorithm)

```js
// Each card's entry time = distance from center x 0.8s delay
const col = i % 8,
  row = Math.floor(i / 8);
const dc = col - 3.5,
  dr = row - 2.5; // offset from center
const dist = Math.hypot(dc, dr);
const maxDist = Math.hypot(3.5, 2.5);
const delay = (dist / maxDist) * 0.8; // 0 -> 0.8s
const localT = Math.max(0, (t - rippleStart - delay) / 0.7);
const opacity = expoOut(Math.min(1, localT));
```

**Core parameters**:

- Total duration 1.7s (`T.s3_ripple: [8.3, 10.0]`)
- Maximum delay 0.8s (center appears first, corners appear last)
- Each card's entry duration 0.7s
- Easing: `expoOut` (burst feel, not smooth)

**Simultaneous action**: canvas scale goes from 1.25 -> 0.94 (zoom out to reveal) — creates a synchronized push-away feeling as cards appear.

### Multi-Focus (4-Cycle Rhythm)

```js
T.focuses = [
  { start: 11.0, end: 12.7, idx: 2 }, // 1.7s
  { start: 13.3, end: 15.0, idx: 3 }, // 1.7s
  { start: 15.6, end: 17.3, idx: 10 }, // 1.7s
  { start: 17.9, end: 19.6, idx: 16 }, // 1.7s
];
```

**Rhythm pattern**: Each focus lasts 1.7s, with 0.6s breathing gaps. Total 8s (11.0-19.6s).

**Inside each focus cycle**:

- In ramp: 0.4s (`expoOut`)
- Hold: middle 0.9s (`focusIntensity = 1`)
- Out ramp: 0.4s (`easeOut`)

**Background changes (this is the key part)**:

```js
if (focusIntensity > 0) {
  const dimOp = entryOp * (1 - 0.6 * focusIntensity); // dim to 40%
  const brt = 1 - 0.32 * focusIntensity; // brightness 68%
  const sat = 1 - 0.35 * focusIntensity; // saturate 65%
  card.style.filter = `brightness(${brt}) saturate(${sat})`;
}
```

**Not just opacity — simultaneously desaturate + darken**. This makes the foreground overlay's colors "pop out" instead of simply "getting brighter."

**Focus overlay size animation**:

- From 400x225 (entry) -> 960x540 (held state)
- Surrounded by 3 layers of shadow + 3px accent-color outline ring, creating a "framed" feeling

### Pan (Sustained Motion Prevents Static Boredom)

```js
const panT = Math.max(0, t - 8.6);
const panX = Math.sin(panT * 0.12) * 220 - panT * 8;
const panY = Math.cos(panT * 0.09) * 120 - panT * 5;
```

- Sine wave + linear drift dual-layer motion — not a pure loop; position is unique at every moment
- X/Y have different frequencies (0.12 vs 0.09) to avoid the viewer detecting "a repeating cycle"
- Clamped to +-900/500px to prevent drifting out of frame

**Why not use a pure linear pan**: With pure linear, the viewer "predicts" where the next second will be. Sine + drift makes every second fresh — under 3D tilt, this produces a "slight sea-sway feeling" (the good kind) that holds attention.

---

## 5 Reusable Patterns (Distilled from v6->v9 Iterations)

### 1. expoOut as the Primary Easing — Not cubicOut

`easeOut = 1 - (1-t)^3` (smooth) vs `expoOut = 1 - 2^(-10t)` (burst then rapidly converge).

**Why**: expoOut reaches ~90% within the first 30% of its duration — more like physical damping, matching the intuition of "a heavy object landing." Especially suited for:

- Card entry (sense of weight)
- Ripple diffusion (shockwave)
- Brand float-up (settling feel)

**When to still use cubicOut**: focus out ramp, symmetric micro-animations.

### 2. Paper-Tone Background + Terracotta Orange Accent (Anthropic Lineage)

```css
--bg: #f7f4ee; /* warm paper */
--ink: #1d1d1f; /* near-black */
--accent: #d97757; /* terracotta orange */
--hairline: #e4ded2; /* warm rule line */
```

**Why**: A warm background retains a "breathing quality" even after GIF compression — unlike pure white, which reads as "screen glow." Terracotta orange, as the sole accent, runs through terminal prompt, dir-card selection, cursor, brand hyphen, and focus ring — all visual anchors are strung together by this one color.

**v5 lesson**: Added a noise overlay to simulate "paper grain," which completely destroyed GIF frame compression (every frame was different). v6 switched to "base color + warm shadow only" — paper feel retained at 90%, GIF file size reduced by 60%.

### 3. Two-Tier Shadows Simulate Depth — No Real 3D

```css
.gallery-card.depth-near {
  box-shadow:
    0 32px 80px -22px rgba(60, 40, 20, 0.22),
    ...;
}
.gallery-card.depth-far {
  box-shadow:
    0 14px 40px -16px rgba(60, 40, 20, 0.1),
    ...;
}
```

A deterministic `sin(i * 1.7) + cos(i * 0.73)` algorithm assigns each card to one of three shadow tiers (near/mid/far) — **visually creates a "three-dimensional stacking" effect, but frame transforms are completely unchanging and GPU cost is 0**.

**The cost of real 3D**: Each card gets its own `translateZ`, and the GPU computes 48 transforms + shadow blur every frame. Tried this in v4 — Playwright recording struggled to maintain 25fps. v6's two-tier shadow approach has less than 5% visible difference, at 10x lower cost.

### 4. Font-Weight Variation (font-variation-settings) Is More Cinematic Than Size Variation

```js
const wght = 100 + (700 - 100) * morphP; // 100 -> 700 over 0.9s
wordmark.style.fontVariationSettings = `"wght" ${wght.toFixed(0)}`;
```

Brand wordmark transitions from Thin -> Bold over 0.9s, with a subtle letter-spacing adjustment (-0.045 -> -0.048em).

**Why this beats scaling up and down**:

- Viewers have seen scaling too many times — expectations are fixed
- Weight change is "internal expansion," like a balloon being inflated — not "being pushed closer"
- Variable fonts became mainstream in 2020+ — viewers subconsciously perceive them as "modern"

**Limitation**: Requires a variable font (Inter/Roboto Flex/Recursive, etc.). Static fonts can only fake it (switching between a few fixed weights produces visible jumps).

### 5. Corner Brand Signature — Low-Intensity Persistent Presence

During the Gallery phase, a small `HUASHU · DESIGN` identifier appears in the top-left corner — 16% opacity, 12px font size, wide letter spacing.

**Why add this**:

- After the Ripple burst, viewers easily "lose focus" and forget what they're watching — the subtle corner label helps anchor them
- More sophisticated than a full-screen large logo — people who do branding know that a brand signature doesn't need to shout
- Leaves an attribution signal when the GIF is screenshot and shared

**Rule**: Appears only during the middle section (when the frame is busy); hidden during the opening (don't obstruct the terminal) and the ending (brand reveal is the star).

---

## Counterexamples: When NOT to Use This Choreography

**Product demos (where you need to show features)**: Gallery makes every card flash by — the viewer won't remember any specific feature. Switch to "single-screen focus + tooltip annotations."

**Data-driven content**: Viewers need to read numbers; Gallery's fast pace doesn't give them time. Switch to "data charts + progressive reveal."

**Story-based narrative**: Gallery is a "parallel" structure — stories need "cause and effect." Switch to keynote chapter transitions.

**Only 3-5 assets**: Ripple density is insufficient — it looks like a "patch." Switch to "static layout + individual card highlight."

**Portrait orientation (9:16)**: 3D tilt needs horizontal extension — portrait makes the tilt feel "crooked" rather than "spreading open."

---

## How to Determine Whether Your Task Suits This Choreography

Three quick checks:

**Step 1 - Asset count**: Count how many similar visual assets you have. < 15 -> stop; 15-25 -> it's marginal; 25+ -> go for it.

**Step 2 - Consistency test**: Place 4 random assets side by side — do they look like "a set"? No -> unify the style first, or change the approach.

**Step 3 - Narrative match**: Are you expressing "Breadth x Depth" (quantity x quality)? Or is it "process," "features," "story"? If not the former, don't force the fit.

If all three are yes, fork the v6 HTML directly, change the `SLIDE_FILES` array and timeline, and it's reusable. Retheme with `--bg / --accent / --ink` — the skin changes, the bones stay the same.

---

## Related References

- Full technical process: [references/animations.md](animations.md) · [references/animation-best-practices.md](animation-best-practices.md)
- Animation export pipeline: [references/video-export.md](video-export.md)
- Audio setup (BGM + SFX dual track): [references/audio-design-rules.md](audio-design-rules.md)
- Apple gallery-style lateral reference: [references/apple-gallery-showcase.md](apple-gallery-showcase.md)
- Source HTML (v6 + audio integration): `www.huasheng.ai/huashu-design-hero/index.html`
