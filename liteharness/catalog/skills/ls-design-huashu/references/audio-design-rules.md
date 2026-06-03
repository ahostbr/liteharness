# Audio Design Rules · huashu-design

> The audio application recipe for all animation demos. Used alongside `sfx-library.md` (asset inventory).
> Battle-tested: huashu-design hero v1-v9 iterations · Deep Gemini analysis of 3 official Anthropic videos · 8000+ A/B comparisons

---

## Core Principle · Dual-Track Audio (Iron Rule)

Animation audio **must be designed as two independent layers** — never just one:

| Layer                 | Role                             | Time Scale               | Relationship to Visual                  | Frequency Range        |
| --------------------- | -------------------------------- | ------------------------ | --------------------------------------- | ---------------------- |
| **SFX (beat layer)**  | Marks each visual beat           | Short, 0.2-2 seconds     | **Strong sync** (frame-level alignment) | **High freq 800Hz+**   |
| **BGM (ambient bed)** | Emotional foundation, soundscape | Continuous 20-60 seconds | Weak sync (segment-level)               | **Mid-low freq <4kHz** |

**An animation with only BGM is crippled** — the audience subconsciously senses "the visuals are moving but there's no audio response." This is the root cause of cheapness.

---

## Gold Standard · Golden Ratios

These values were derived from testing all 3 official Anthropic videos + our own v9 final — they are **hard engineering parameters** you can apply directly:

### Volume

- **BGM volume**: `0.40-0.50` (relative to full-scale 1.0)
- **SFX volume**: `1.00`
- **Loudness difference**: BGM peak is **-6 to -8 dB below SFX** (prominence comes from the loudness difference, not SFX absolute loudness)
- **amix parameter**: `normalize=0` (never use normalize=1 — it crushes dynamic range)

### Frequency Band Isolation (P1 Hard Optimization)

Anthropic's secret is not "loud SFX" — it's **frequency layering**:

```bash
[bgm_raw]lowpass=f=4000[bgm]      # BGM restricted to mid-low freq <4kHz
[sfx_raw]highpass=f=800[sfx]      # SFX pushed to mid-high freq 800Hz+
[bgm][sfx]amix=inputs=2:duration=first:normalize=0[a]
```

Why: The human ear is most sensitive to the 2-5kHz range (the "presence band"). If SFX all sit in this range and BGM covers the full spectrum, **SFX will be masked by BGM's high-frequency content**. Using highpass to push SFX up + lowpass to push BGM down gives each their own territory in the spectrum, and SFX clarity jumps up a notch.

### Fade

- BGM in: `afade=in:st=0:d=0.3` (0.3s, avoid hard cut)
- BGM out: `afade=out:st=N-1.5:d=1.5` (1.5s long tail, sense of closure)
- SFX has built-in envelopes — no additional fade needed

---

## SFX Cue Design Rules

### Density (how many SFX per 10 seconds)

Testing the 3 Anthropic videos reveals three density tiers:

| Video                | SFX per 10s | Product Personality              | Scenario                   |
| -------------------- | ----------- | -------------------------------- | -------------------------- |
| Artifacts (ref-1)    | **~9/10s**  | Feature-dense, information-heavy | Complex tool demo          |
| Code Desktop (ref-2) | **0**       | Pure atmosphere, meditative      | Developer tool focus state |
| Word (ref-3)         | **~4/10s**  | Balanced, office rhythm          | Productivity tool          |

**Heuristics**:

- Product personality: calm/focused → low SFX density (0-3/10s), BGM-dominant
- Product personality: lively/information-rich → high SFX density (6-9/10s), SFX drives the rhythm
- **Don't fill every visual beat** — silence is more sophisticated than density. **Cutting 30-50% of cues makes the remaining ones more dramatic**.

### Cue Selection Priority

Not every visual beat needs an SFX. Select by this priority:

**P0 Required** (omitting creates a jarring feeling):

- Typing (terminal/input)
- Click/select (moment of user decision)
- Focus switch (visual protagonist transfer)
- Logo reveal (brand closure)

**P1 Recommended**:

- Element enter/exit (modal / card)
- Completion/success feedback
- AI generation start/end
- Major transitions (scene changes)

**P2 Optional** (too many will create chaos):

- hover / focus-in
- Progress ticks
- Decorative ambient

### Timestamp Alignment Precision

- **Same-frame alignment** (0ms error): click/focus switch/logo landing
- **1-2 frames early** (-33ms): fast whoosh (gives audience psychological anticipation)
- **1-2 frames late** (+33ms): object landing/impact (matches real physics)

---

## BGM Selection Decision Tree

The huashu-design skill ships with 6 BGM tracks (`assets/bgm-*.mp3`):

```
What is the animation's personality?
├─ Product launch / tech demo → bgm-tech.mp3 (minimal synth + piano)
├─ Tutorial walkthrough / tool usage → bgm-tutorial.mp3 (warm, instructional)
├─ Educational / explaining concepts → bgm-educational.mp3 (curious, thoughtful)
├─ Marketing / brand promotion → bgm-ad.mp3 (upbeat, promotional)
└─ Need a variant of a similar style → bgm-*-alt.mp3 (alternate versions)
```

### When to Use No BGM (worth considering)

Reference Anthropic Code Desktop (ref-2): **0 SFX + pure Lo-fi BGM** can also be very refined.

**When to choose no BGM**:

- Animation duration <10s (BGM cannot establish itself)
- Product personality is "focus/meditative"
- Scene already has ambient sound / voiceover narration
- SFX density is very high (avoid auditory overload)

---

## Scene Recipes (Ready to Use)

### Recipe A · Product Launch Hero (same as huashu-design v9)

```
Duration: 25 seconds
BGM: bgm-tech.mp3 · 45% · band <4kHz
SFX density: ~6/10s

Cues:
  Terminal typing → type × 4 (0.6s interval)
  Enter           → enter
  Cards converge  → card × 4 (staggered 0.2s)
  Select          → click
  Ripple          → whoosh
  4 focus shifts  → focus × 4
  Logo            → thud (1.5s)

Volume: BGM 0.45 / SFX 1.0 · amix normalize=0
```

### Recipe B · Tool Feature Demo (reference: Anthropic Code Desktop)

```
Duration: 30-45 seconds
BGM: bgm-tutorial.mp3 · 50%
SFX density: 0-2/10s (very sparse)

Strategy: Let BGM + voiceover narration drive the piece; SFX only at **decisive moments** (file save / command execution complete)
```

### Recipe C · AI Generation Demo

```
Duration: 15-20 seconds
BGM: bgm-tech.mp3 or no BGM
SFX density: ~8/10s (high density)

Cues:
  User input           → type + enter
  AI starts processing → magic/ai-process (1.2s loop)
  Generation complete  → feedback/complete-done
  Result presentation  → magic/sparkle

Highlight: ai-process can loop 2-3 times throughout the generation process
```

### Recipe D · Pure Atmosphere Long Shot (reference: Artifacts)

```
Duration: 10-15 seconds
BGM: none
SFX: 3-5 carefully designed cues used solo

Strategy: Each SFX is the star — no "muddying together" problem from BGM.
Best for: Single product slow motion, close-up showcase
```

---

## ffmpeg Composition Templates

### Template 1 · Single SFX Overlaid onto Video

```bash
ffmpeg -y -i video.mp4 -itsoffset 2.5 -i sfx.mp3 \
  -filter_complex "[0:a][1:a]amix=inputs=2:normalize=0[a]" \
  -map 0:v -map "[a]" output.mp4
```

### Template 2 · Multiple SFX Timeline Composition (aligned to cue times)

```bash
ffmpeg -y \
  -i sfx-type.mp3 -i sfx-enter.mp3 -i sfx-click.mp3 -i sfx-thud.mp3 \
  -filter_complex "\
[0:a]adelay=1100|1100[a0];\
[1:a]adelay=3200|3200[a1];\
[2:a]adelay=7000|7000[a2];\
[3:a]adelay=21800|21800[a3];\
[a0][a1][a2][a3]amix=inputs=4:duration=longest:normalize=0[mixed]" \
  -map "[mixed]" -t 25 sfx-track.mp3
```

**Key parameters**:

- `adelay=N|N`: first value is left channel delay (ms), second is right channel — write both to ensure stereo alignment
- `normalize=0`: preserves dynamic range — critical!
- `-t 25`: truncate to specified duration

### Template 3 · Video + SFX track + BGM (with frequency band isolation)

```bash
ffmpeg -y -i video.mp4 -i sfx-track.mp3 -i bgm.mp3 \
  -filter_complex "\
[2:a]atrim=0:25,afade=in:st=0:d=0.3,afade=out:st=23.5:d=1.5,\
     lowpass=f=4000,volume=0.45[bgm];\
[1:a]highpass=f=800,volume=1.0[sfx];\
[bgm][sfx]amix=inputs=2:duration=first:normalize=0[a]" \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k final.mp4
```

---

## Failure Mode Quick Reference

| Symptom                               | Root Cause                                      | Fix                                                                                |
| ------------------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------- |
| SFX inaudible                         | BGM high frequencies masking it                 | Add `lowpass=f=4000` to BGM + `highpass=f=800` to SFX                              |
| Sound effects too harsh/loud          | SFX absolute volume too high                    | Lower SFX volume to 0.7, also lower BGM to 0.3, maintain the difference            |
| BGM and SFX rhythms clash             | Wrong BGM chosen (used music with strong beats) | Switch to ambient / minimal synth BGM                                              |
| BGM cuts abruptly when animation ends | No fade out applied                             | `afade=out:st=N-1.5:d=1.5`                                                         |
| SFX overlap into mud                  | Cues too dense + each SFX too long              | Keep SFX duration under 0.5s, cue intervals >= 0.2s                                |
| WeChat mp4 has no sound               | WeChat sometimes mutes autoplay                 | Don't worry about it — users who tap will hear sound; GIFs never have sound anyway |

---

## Visual-Audio Coordination (Advanced)

### SFX Timbre Must Match Visual Style

- Warm beige / paper texture visuals → SFX with **wooden/soft** timbre (Morse, paper snap, soft click)
- Cold dark tech visuals → SFX with **metallic/digital** timbre (beep, pulse, glitch)
- Hand-drawn / whimsical visuals → SFX with **cartoon/exaggerated** timbre (boing, pop, zap)

Our current `apple-gallery-showcase.md` warm beige palette → pair with `keyboard/type.mp3` (mechanical) + `container/card-snap.mp3` (soft) + `impact/logo-reveal-v2.mp3` (cinematic bass)

### SFX Can Guide Visual Rhythm

Advanced technique: **Design the SFX timeline first, then adjust the visual animation to align with SFX** (not the other way around).
Because every SFX cue is a "clock tick," visual animations adapting to SFX rhythm will be very stable — the reverse approach, where SFX chases the visuals, often produces ±1 frame misalignments that feel jarring.

---

## Quality Checklist (Pre-Release Self-Check)

- [ ] Loudness difference: SFX peak - BGM peak = -6 to -8 dB?
- [ ] Frequency bands: BGM lowpass 4kHz + SFX highpass 800Hz?
- [ ] amix normalize=0 (preserving dynamic range)?
- [ ] BGM fade-in 0.3s + fade-out 1.5s?
- [ ] Is SFX count appropriate (choose density per scene personality)?
- [ ] Does each SFX align with its visual beat within ±1 frame?
- [ ] Is the logo reveal SFX long enough (recommended 1.5s)?
- [ ] Listen once with BGM muted: do SFX alone have sufficient rhythmic feel?
- [ ] Listen once with SFX muted: does BGM alone have emotional arc?

Each layer should hold up on its own. If it only sounds good when both layers are combined, the design is not done right.

---

## References

- SFX asset inventory: `sfx-library.md`
- Visual style reference: `apple-gallery-showcase.md`
- Deep audio analysis of 3 Anthropic videos: see `AUDIO-BEST-PRACTICES.md` (local reference — not bundled)
- huashu-design v9 production case: see `hero-animation-v9-final.mp4` (local project file — not bundled)
