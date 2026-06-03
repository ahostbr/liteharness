# Cinematic Patterns · Best Practices for Workflow Demo

> 5 key patterns for upgrading from "PowerPoint animation" to "launch-event cinematic."
> Distilled from two cinematic demos (Nuwa workflow + Darwin workflow) in the 2026-04 "Chat About Skills" deck — reproducible in practice.

---

## 0 · What Problem Does This Document Solve

When you need to create a "demo animation demonstrating a workflow" (typical scenarios: skill workflows, product onboarding, API call sequences, agent task execution), there are two common approaches:

| Paradigm                | What it looks like                                                                                        | Outcome                                                                                |
| ----------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| **PPT animation** (bad) | step 1 fade in → step 2 fade in → step 3 fade in, 4 boxes displayed simultaneously on screen              | Audience feels "this is just a PowerPoint with fade effects," no wow moment            |
| **Cinematic** (good)    | Scene-based, focuses on one thing at a time, transitions between scenes use dissolve / focus pull / morph | Audience feels "this is a product launch clip," they will want to screenshot and share |

The root difference is **not animation technology** — it is **narrative paradigm**. This document explains how to upgrade from the former to the latter.

---

## 1 · Five Core Patterns

### Pattern A · Dashboard + Cinematic Overlay Dual-Layer Structure

**Problem**: A pure cinematic defaults to a black screen + a single ▶ button — if the user flips to this page without clicking, they see nothing.

**Solution**:

```
DEFAULT state (always visible): Complete static workflow dashboard
  └── Audience immediately sees how this skill / workflow runs

POINT ▶ triggered (overlay comes up): 22-second cinematic
  └── Auto-fades back to DEFAULT after completion

```

**Implementation notes**:

- `.dash` is visible by default; `.cinema` defaults to `opacity: 0; pointer-events: none`
- `.play-cta` is a small gold button in the bottom-right corner (not a large central overlay)
- Click → `cinema.classList.add('show')` + `dash.classList.add('hide')`
- Run one `requestAnimationFrame` pass (not a loop); call `endCinematic()` to reverse state when done

**Anti-pattern**: Default = large ▶ overlay covering everything — the page is blank before clicking.

---

### Pattern B · Scene-based, NOT Step-based

**Problem**: Breaking an animation into "step 1 shows → step 2 shows → ..." is PowerPoint thinking.

**Solution**: Break into 5 scenes, each being an **independent shot** — full screen focuses on only one thing at a time:

| Scene Type         | Responsibility                                       | Duration |
| ------------------ | ---------------------------------------------------- | -------- |
| 1 · Invoke         | User input trigger (terminal typewriter)             | 3-4s     |
| 2 · Process        | Core workflow visualization (unique visual language) | 5-6s     |
| 3 · Result/Insight | Key extracted artifacts (visualized)                 | 4-5s     |
| 4 · Output         | Actual artifact display (file / diff / numbers)      | 3-4s     |
| 5 · Hero Reveal    | Closing hero moment (large text + value proposition) | 4-5s     |

**Total duration ≈ 22 seconds** — this is the golden length proven by testing:

- Under 18 seconds: the viewer has not settled in before it ends
- Over 25 seconds: loses patience
- 22 seconds is exactly enough to "hook → develop → close → leave an impression"

**Implementation notes**:

- `T = { DURATION: 22.0, s1_in: [0, 0.7], s2_in: [3.8, 4.6], ... }` — global timeline object
- Single `requestAnimationFrame(render)` loop handles opacity / transform calculations for all scenes
- Do not use setTimeout chains (prone to breaking, hard to debug)
- Easing must use `expoOut` / `easeOut` / cubic-bezier — **linear is forbidden**

---

### Pattern C · Each Demo's Visual Language Must Be Independent

**Problem**: After finishing the first cinematic, you get lazy on the second and reuse the same template (same orbit + pentagon + typewriter + big hero text), just swapping the copy.

**Consequence**: The audience notices the two skills "look identical," which communicates "these two skills are the same thing."

**Solution**: Each workflow has a different core metaphor — so the visual language must also differ.

**Comparison case**:

| Dimension     | Nuwa (Distiller)                                             | Darwin (Skill Optimizer)                                   |
| ------------- | ------------------------------------------------------------ | ---------------------------------------------------------- |
| Core metaphor | Collect → Distill → Write                                    | Loop → Evaluate → Ratchet                                  |
| Visual motion | Floating / radiating / pentagon                              | Cycling / ascending / comparison                           |
| Scene 2       | 3D Orbit · 8 archive cards floating on a perspective ellipse | Spin Loop · token travels 5 laps around a 6-node ring      |
| Scene 3       | Pentagon · 5 tokens radiating from center                    | v1 vs v5 · side-by-side diff (red version vs gold version) |
| Scene 4       | SKILL.md typewriter                                          | Hill-Climb · full-screen curve drawing                     |
| Scene 5 hero  | "21 minutes" serif italic large text                         | Rotating gear + "KEPT +1.1" gold tag                       |

**Test standard**: Cover the copy and look only at the visuals — can you tell which demo this is? If not, you took a shortcut.

---

### Pattern D · Use AI-Generated Real Assets — Not Emoji or Hand-Drawn SVG

**Problem**: 3D orbit / gallery scenes need asset fragments floating around — emoji look cheap and have no brand identity; hand-drawn SVG book spines never look like real books.

**Solution**: Use `huashu-gpt-image` to generate a 4×2 grid image (8 thematically relevant objects · white background · 60px breathing space · unified style), then use `extract_grid.py --mode bbox` to cut it into 8 individual transparent PNGs.

**Prompt key points** (detailed prompt patterns in the `huashu-gpt-image` skill):

- IP anchoring ("1960s Caltech archive aesthetic" / "Hearthstone-style consistent treatment")
- White background (easy to cut; grey backgrounds are atmospheric but hard to make transparent)
- 4×2 not 5×5 (avoids last-row compression bug)
- Persona finishing ("You are a Wired magazine curator preparing an exhibition photo")

**Anti-pattern**: Using emoji as icons, using CSS silhouettes instead of product images.

---

### Pattern E · BGM + SFX Dual-Track System

**Problem**: Animation with no sound — the audience subconsciously thinks "this looks like a cheap demo."

**Solution**: BGM sustained layer + 11 SFX cues.

**Universal SFX cue recipe** (applicable to workflow demos):

| Timestamp      | SFX         | Trigger Scene                                             |
| -------------- | ----------- | --------------------------------------------------------- |
| 0.10s          | whoosh      | Terminal rises from below                                 |
| 3.0s           | enter       | Typewriter completes, press Enter                         |
| 4.0s           | slide-in    | Scene 2 elements enter                                    |
| 5-9s × 5 times | sparkle     | Key process nodes (each generation / token / data point)  |
| 14s            | click       | Switch to output scene                                    |
| 17.8s          | logo-reveal | Hero reveal moment                                        |
| typewriter     | type        | Trigger every 2 characters (do not push density too high) |

**Frequency band isolation**: BGM volume 0.32 (low-freq bed), SFX volume 0.55 (mid-high punch), sparkle 0.7 (needs to stand out), logo-reveal 0.85 (strongest hero moment).

**User control**:

- Must have ▶ launch overlay (browser autoplay restriction)
- Small mute button in top-right corner (user can toggle mute anytime)
- Do not make it "blasts audio the moment you scroll to this page"

---

## 2 · Static Dashboard Design Notes

The Dashboard is Layer 1 of the dual-layer structure — PMs who do not click ▶ should still understand this skill.

**Layout**: 3-column grid (or 1 large + 2 small), each panel answers one question:

| Panel Type                  | What question it answers                    | Example                                                               |
| --------------------------- | ------------------------------------------- | --------------------------------------------------------------------- |
| **Pipeline / Flow Diagram** | "What is this skill's workflow?"            | Nuwa 4-stage pipeline · Darwin autoresearch loop                      |
| **Snapshot / State**        | "What does real data from a run look like?" | Darwin 8-dimension rubric snapshot                                    |
| **Trajectory / Evolution**  | "How does it change across multiple runs?"  | Darwin 5-generation hill-climb curve                                  |
| **Examples / Gallery**      | "What has it produced so far?"              | Nuwa 21 personas gallery                                              |
| **Strip · Example I/O**     | "Input → Output"                            | Nuwa example strip: `› nuwa distill feynman → feynman.skill (21 min)` |

**Key constraints**:

- Information density must be sufficient (each panel must carry differentiated information)
- But do not pack in data slop (every number must mean something)
- Color palette consistent with cinematic (same color family, so switching between them is not jarring)

---

## 3 · Debugging and Dev Tools

Any long animation must ship with three dev tools — debugging without them will be an explosion.

### Tool 1 · `?seek=N` — Freeze to Second N

```js
const seek = parseFloat(params.get("seek"));
if (!isNaN(seek)) {
  started = true;
  muted = true;
  frozenT = seek; // render() uses this t instead of elapsed
  cinema.classList.add("show");
  dash.classList.add("hide");
}

// inside render():
let t = frozenT !== null ? frozenT : elapsed % T.DURATION;
```

Usage: `http://.../slide.html?seek=12` — jump directly to the frame at second 12 without waiting for playback.

### Tool 2 · `?autoplay=1` — Skip ▶ Overlay

Convenient for Playwright automated screenshot testing, and for force-starting when embedded in an iframe.

### Tool 3 · Manual REPLAY Button

Small button in the top-right corner — users/debuggers can replay any number of times. CSS:

```css
.replay {
  position: absolute;
  top: 18px;
  right: 18px;
  background: rgba(212, 165, 116, 0.1);
  border: 1px solid rgba(212, 165, 116, 0.3);
  color: #d4a574;
  font-family: monospace;
  font-size: 10px;
  letter-spacing: 0.28em;
  text-transform: uppercase;
  padding: 6px 12px;
  border-radius: 1px;
  cursor: pointer;
  backdrop-filter: blur(6px);
  z-index: 6;
}
```

---

## 4 · iframe Embedding Pitfalls (if cinematic is embedded in a deck)

### Pitfall 1 · Parent window's click zones intercept iframe buttons

If the deck index.html has "left/right 22vw transparent click zones for page turning," they will **overlap the ▶ play button inside the iframe** — user clicks the button but it registers as "next page."

**Fix**: Add `top: 12vh; bottom: 25vh` to click zones, leaving the top and bottom 25% non-intercepting, so both the central ▶ and the bottom-right ▶ inside the iframe are clickable.

### Pitfall 2 · iframe steals focus, keyboard events are lost

After the user clicks inside the iframe, focus is inside it — the parent window's ←/→ keyboard events stop being received.

**Fix**:

```js
iframe.addEventListener('load', () => {
  // Inject keyboard forwarder
  const doc = iframe.contentDocument;
  doc.addEventListener('keydown', (e) => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: e.key, ... }));
  });
  // After click, pull focus back to parent window
  doc.addEventListener('click', () => setTimeout(() => window.focus(), 0));
});
```

### Pitfall 3 · file:// vs https:// behavior differences

A cinematic that works locally under file:// may break after deployment because:

- Under file://, the iframe contentDocument is same-origin
- Under https:// it is also same-origin (if same host), but audio autoplay restrictions are stricter

**Fix**:

- Before deploying, test with a local `python3 -m http.server` once
- BGM must only call `bgm.play()` after the user clicks ▶ — never play on page-load

---

## 5 · Anti-Pattern Quick Reference

| Anti-pattern                                             | Correct pattern                                                               |
| -------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Default = black screen ▶ overlay                         | Default = static dashboard, ▶ is supplementary                                |
| 4 steps displayed side-by-side simultaneously, fading in | 5 scenes with full-screen transitions, each focusing on one thing             |
| Reuse template with swapped copy for different demos     | Each demo has independent visual language (distinguishable with copy covered) |
| emoji / hand-drawn SVG as assets                         | gpt-image-2 large image + extract_grid cutout                                 |
| No BGM, no SFX                                           | BGM + 11 SFX cues, dual-track                                                 |
| setTimeout chains for scheduling                         | requestAnimationFrame + global timeline T object                              |
| Linear animation                                         | Expo / cubic-bezier easing                                                    |
| No dev tools                                             | `?seek=N` + `?autoplay=1` + REPLAY button                                     |
| Buttons inside iframe swallowed by parent click zones    | Give click zones top/bottom margins to make room for buttons                  |

---

## 6 · Time Budget

Following this pattern set, a complete cinematic demo (including dashboard):

| Task                                              | Time                                              |
| ------------------------------------------------- | ------------------------------------------------- |
| Design 5-scene narrative + visual language        | 30 minutes (be careful — determines independence) |
| Dashboard static layout + content                 | 1 hour                                            |
| Cinematic 5 scenes implementation                 | 1.5 hours                                         |
| Audio cue timing + replay button                  | 30 minutes                                        |
| Playwright screenshot validation at 5 key moments | 15 minutes                                        |
| **Total per demo**                                | **3-4 hours**                                     |

The second demo reuses the framework but **visual language must be independent** — approximately 2-3 hours.
