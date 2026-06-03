# claude-code-video-toolkit (LiteEVA Edition)

This is the working-directory context for the claude-code-video-toolkit when operated via LiteEVA.

---

## What This Toolkit Does

Full agentic video production pipeline:

- **Remotion** — React-based frame-accurate video composition
- **FFmpeg** — Video processing, trimming, platform exports
- **ElevenLabs** — AI voiceover generation (TTS)
- **ACE-Step 1.5** — Local AI music generation

---

## Directory Layout

```
claude-code-video-toolkit/       ← this dir ([toolkit-dir])
  src/
    Root.tsx                     # Remotion composition registry
    compositions/                # Per-scene React compositions
    components/                  # Shared UI components (AnimatedBackground, Label, etc.)
    transitions/                 # Custom transitions (glitch, rgbSplit, zoomBlur, etc.)
  public/
    audio/                       # Static audio assets served to Remotion
  tools/
    voiceover.py                 # ElevenLabs TTS tool
    music_gen.py                 # ACE-Step music generation tool
  projects/
    [project-name]/
      compositions/              # Project-specific scene files
      audio/                     # Generated voiceover + music mp3s
      output/                    # Rendered video exports
      frames/                    # Extracted frames (scroll animations)
  .venv/                         # Python virtual environment (Windows)
    Scripts/
      python.exe                 # Always use this — not system Python
  out/                           # Remotion render output (single-file renders)
```

---

## Port Map

| Service                | Port                          |
| ---------------------- | ----------------------------- |
| Remotion Studio        | **7441** (LiteEVA — NOT 3000) |
| LiteSuite TTS fallback | 5123                          |

---

## Tools Reference

### Remotion Studio

```bash
# Start dev server (always use port 7441 inside LiteEVA)
npx remotion studio --port 7441

# Render a composition
npx remotion render [CompositionId] out/output.mp4
npx remotion render [CompositionId] projects/[name]/output/final.mp4
```

The studio UI is displayed in LiteEVA's BrowserShellPane at `http://localhost:7441`.

### FFmpeg

Use ffmpeg from your system PATH, or the bundled binary if LiteSuite is installed at `[litesuite-dir]`:

```bash
# Bundled (requires LiteSuite installed — replace [litesuite-dir] with actual path):
[litesuite-dir]/tools/ffmpeg/ffmpeg.exe

# System ffmpeg (install: https://ffmpeg.org/download.html or `winget install ffmpeg`):
ffmpeg
```

Use forward slashes in all FFmpeg commands (Windows-compatible). See the FFmpeg section in `/ls-eva ffmpeg` for full command reference.

### Voiceover Tool

```bash
.venv/Scripts/python tools/voiceover.py \
  --text "Your script here." \
  --voice JBFqnCBsd6RMkjVDRZzb \
  --output projects/[project-name]/audio/scene1-vo.mp3
```

- API key is managed by LiteEVA panel settings via `electron.safeStorage` — do not use `.env` files
- Local TTS fallback: `http://localhost:5123/tts` (no key required)

### Music Generation Tool

```bash
.venv/Scripts/python tools/music_gen.py \
  --prompt "corporate ambient electronic, professional, subtle piano, no vocals" \
  --duration 30 \
  --output projects/[project-name]/audio/bg-music.mp3
```

- Runs entirely locally via ACE-Step 1.5 — no API key or internet required
- Duration range: 15–300 seconds

---

## Skills Loaded in LiteEVA

When Claude is launched from the LiteEVA panel, the following skills are available:

| Command             | Purpose                                                   |
| ------------------- | --------------------------------------------------------- |
| `/ls-eva`           | Orchestrator — project scaffolding, workflow coordination |
| `/ls-eva remotion`  | Remotion composition, transitions, rendering              |
| `/ls-eva ffmpeg`    | Video processing, platform exports, post-production       |
| `/ls-eva voiceover` | ElevenLabs TTS voiceover generation                       |
| `/ls-eva music`     | ACE-Step local music generation                           |

---

## API Keys

**Do not create or modify `.env` files.** All API keys are managed by the LiteEVA panel:

- **ElevenLabs** — Set in LiteEVA settings → stored via `electron.safeStorage` → injected at tool invocation
- **ACE-Step music** — No key needed (local model)
- **FFmpeg** — No key needed (bundled binary)

---

## Output Convention

All project outputs go under `projects/[project-name]/`. Never write renders or audio to the repo root.

```
projects/my-product-demo/
  audio/scene1-vo.mp3       ← voiceover
  audio/bg-music.mp3        ← music
  output/final.mp4          ← Remotion render
  output/youtube.mp4        ← FFmpeg platform export
  output/tiktok.mp4
```

---

## Python Environment

Always invoke Python tools via the venv:

```bash
# Correct
.venv/Scripts/python tools/voiceover.py ...
.venv/Scripts/python tools/music_gen.py ...

# Wrong — do not use system python
python tools/voiceover.py ...
```

The `.venv/` contains all ACE-Step, ElevenLabs SDK, and other dependencies isolated from the system.
