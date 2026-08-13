---
name: ls-eva
description: Use when the user launches Claude from inside the LiteEVA panel, wants to create or work on a video project, or asks about agentic video editing. Subcommands — remotion, voiceover, music, ffmpeg. Triggers on 'LiteEVA', 'open EVA', 'launch EVA', 'video project', 'start a video', 'new video', 'EVA panel', 'video editor', 'create video', 'work on video', 'remotion', 'composition', 'render video', 'voiceover', 'TTS', 'generate narration', 'generate music', 'soundtrack', 'ACE-Step', 'ffmpeg', 'trim video', 'compress video', 'platform export'.
---

# LiteEVA — Agentic Video Editor

You are inside **LiteEVA**, LiteSuite's agentic video editor. Full video production toolkit — compose, narrate, score, and export without leaving Claude.

## Subcommands

| Command             | What it does                                                               |
| ------------------- | -------------------------------------------------------------------------- |
| `/ls-eva`           | Start or resume a video project — scaffold structure, open Remotion Studio |
| `/ls-eva remotion`  | Build, preview, or render Remotion compositions                            |
| `/ls-eva voiceover` | Generate voiceover audio (ElevenLabs or local TTS)                         |
| `/ls-eva music`     | Generate background music (ACE-Step, local)                                |
| `/ls-eva ffmpeg`    | Post-process: trim, compress, export to platform                           |

---

## Toolkit Location

The claude-code-video-toolkit is installed at a configurable path set during LiteEVA's setup wizard. Throughout this skill, this is referred to as `[toolkit-dir]`. Resolve it to the actual installed path shown in LiteEVA panel settings.

## Project Directory Convention

```
[toolkit-dir]/projects/[project-name]/
  compositions/        # Remotion scene .tsx files
  audio/               # Voiceover + music .mp3/.wav files
  output/              # Rendered video outputs
  frames/              # Extracted frames (for scroll animations)
```

## Port Map

| Service         | Port | Notes                                          |
| --------------- | ---- | ---------------------------------------------- |
| Remotion Studio | 7441 | Displayed in LiteEVA's BrowserShellPane        |
| LiteSuite TTS   | 5123 | Local voiceover fallback — no API key required |

## API Key Management

ElevenLabs API key is set in LiteEVA panel settings and stored securely via `electron.safeStorage`. Do not write API keys to `.env` files or hardcode them.

## Working Directory Context

The `TOOLKIT_CLAUDE_MD_TEMPLATE.md` in this skill's directory is the CLAUDE.md template that gets deployed to `[toolkit-dir]/CLAUDE.md` during setup.

---

## Protocol: Start a Video Project

1. **Clarify the video** — ask what they want to create if not stated
2. **Scaffold the project dir:**
   ```bash
   mkdir -p [toolkit-dir]/projects/[project-name]/{compositions,audio,output,frames}
   ```
3. **Start Remotion Studio:**
   ```bash
   cd [toolkit-dir] && npx remotion studio --port 7441
   ```
4. **Build compositions** (`/ls-eva remotion`)
5. **Generate voiceover per scene** (`/ls-eva voiceover`)
6. **Generate background music** (`/ls-eva music`)
7. **Render:** `npx remotion render [CompositionId] projects/[name]/output/final.mp4`
8. **Post-process + export to platform** (`/ls-eva ffmpeg`)

---

# Remotion — Video Composition

Remotion turns React components into frame-accurate video. Dev server runs on port **7441** and displays in the BrowserShellPane.

## Dev Server

```bash
cd [toolkit-dir]
npx remotion studio --port 7441
```

## Shared Component Library

Located at `[toolkit-dir]/src/components/`:

| Component            | Description                                                |
| -------------------- | ---------------------------------------------------------- |
| `AnimatedBackground` | Full-frame animated background (gradient, particle, wave)  |
| `SlideTransition`    | Slide in/out with configurable direction and easing        |
| `Label`              | Animated text label with fade, typewriter, or pop-in modes |
| `ProgressBar`        | Frame-synced progress indicator                            |
| `SceneTitle`         | Large title card with entrance animation                   |
| `LowerThird`         | Broadcast-style lower-third overlay                        |

```tsx
import { AnimatedBackground, SlideTransition, Label, ProgressBar } from "../components";
```

## Custom Transitions

Available in `[toolkit-dir]/src/transitions/`:

| Transition     | Import                   | Description                   |
| -------------- | ------------------------ | ----------------------------- |
| `glitch`       | `glitchTransition`       | RGB channel split + scanlines |
| `rgbSplit`     | `rgbSplitTransition`     | Chromatic aberration sweep    |
| `zoomBlur`     | `zoomBlurTransition`     | Radial blur zoom push         |
| `lightLeak`    | `lightLeakTransition`    | Organic film light leak       |
| `clockWipe`    | `clockWipeTransition`    | Radial clockwise reveal       |
| `pixelate`     | `pixelateTransition`     | Pixelate out/in               |
| `checkerboard` | `checkerboardTransition` | Checkerboard reveal           |

```tsx
import { glitchTransition } from "../transitions/glitch";
const progress = interpolate(frame, [cutIn, cutIn + 15], [0, 1], { extrapolateRight: "clamp" });
return glitchTransition({ progress, children: <NextScene /> });
```

## Frame Animation Patterns

```tsx
import { useCurrentFrame, useVideoConfig, interpolate, spring, Sequence } from "remotion";

export const MyComposition: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();
  const opacity = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const scale = spring({ frame: frame - 10, fps, config: { damping: 12, stiffness: 180 } });
  return <div style={{ opacity, transform: `scale(${scale})` }}>Hello Remotion</div>;
};
```

### Sequence Layout

```tsx
<Sequence from={0} durationInFrames={90}><Intro /></Sequence>
<Sequence from={75} durationInFrames={120}><MainContent /></Sequence>
<Sequence from={180} durationInFrames={60}><Outro /></Sequence>
```

## Composition Definition

Register in `[toolkit-dir]/src/Root.tsx`:

```tsx
import { Composition } from "remotion";
import { MyScene } from "./compositions/MyScene";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="MyScene"
    component={MyScene}
    durationInFrames={300}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={{ title: "Demo", accent: "#6366f1" }}
  />
);
```

| Use Case         | FPS |
| ---------------- | --- |
| Default / export | 30  |
| Cinematic        | 24  |
| Smooth motion    | 60  |
| GIF export       | 15  |

## Rendering

```bash
cd [toolkit-dir]
npx remotion render MyScene out/my-scene.mp4
npx remotion render MyScene out/my-scene.mp4 --props '{"title":"Episode 1"}'
npx remotion still MyScene out/thumbnail.png --frame 60
```

## Audio Integration

```tsx
import { Audio, staticFile } from 'remotion';
<Audio src={staticFile('audio/voiceover-scene1.mp3')} startFrom={0} />
<Audio src={staticFile('audio/bg-music.mp3')} volume={0.3} />
```

Place mp3 files in `[toolkit-dir]/public/audio/` for `staticFile()` resolution.

---

# Voiceover — AI Voice Generation

Generates per-scene voiceover using ElevenLabs TTS, with local LiteSuite TTS fallback.

## Python Tool

```bash
cd [toolkit-dir]

.venv/Scripts/python tools/voiceover.py \
  --text "Welcome to our product demo." \
  --voice JBFqnCBsd6RMkjVDRZzb \
  --output projects/[project-name]/audio/scene1-vo.mp3

# With voice settings
.venv/Scripts/python tools/voiceover.py \
  --text "This is a dramatic line." \
  --voice pNInz6obpgDQGcFmaJgB \
  --stability 0.4 --similarity 0.85 --style 0.6 \
  --output projects/[project-name]/audio/scene2-vo.mp3
```

## Models

| Model ID                 | Use Case                                |
| ------------------------ | --------------------------------------- |
| `eleven_multilingual_v2` | Best quality — default for final output |
| `eleven_flash_v2_5`      | Fastest — good for drafts               |
| `eleven_turbo_v2_5`      | Balanced                                |

## Output Formats

| Format          | Description                  |
| --------------- | ---------------------------- |
| `mp3_44100_128` | Default, Remotion-compatible |
| `mp3_44100_192` | Higher quality               |
| `pcm_44100`     | Lossless                     |

## Voice Settings

| Parameter      | Default | Range   | Notes                       |
| -------------- | ------- | ------- | --------------------------- |
| `--stability`  | 0.5     | 0.0-1.0 | Higher = more consistent    |
| `--similarity` | 0.75    | 0.0-1.0 | Higher = closer to original |
| `--style`      | 0.0     | 0.0-1.0 | Style exaggeration          |

## Popular Voices

| Name   | Voice ID               | Character                        |
| ------ | ---------------------- | -------------------------------- |
| George | `JBFqnCBsd6RMkjVDRZzb` | Deep, authoritative British male |
| Rachel | `21m00Tcm4TlvDq8ikWAM` | Warm, clear American female      |
| Adam   | `pNInz6obpgDQGcFmaJgB` | Calm, deep American male         |

## SSML Phoneme Control

```bash
.venv/Scripts/python tools/voiceover.py \
  --text '<speak>Launch <phoneme alphabet="ipa" ph="ˌdʒuːpɪtər">Jupiter</phoneme> in 3, 2, 1.</speak>' \
  --ssml --voice JBFqnCBsd6RMkjVDRZzb \
  --output projects/[project-name]/audio/scene1-vo.mp3
```

## Local TTS Fallback

> **Requires LiteSuite running** — the TTS service at port 5123 is provided by LiteSuite. If LiteSuite is not running, use ElevenLabs (API key required) or another TTS provider instead.

```bash
curl -s -X POST http://127.0.0.1:5123/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Welcome to the demo.", "voice": "default", "output": "projects/[project-name]/audio/scene1-vo.mp3"}'
```

## Per-Scene Workflow

1. Write scene scripts
2. Generate each voiceover with consistent voice
3. Copy mp3s to `[toolkit-dir]/public/audio/`
4. Wire into Remotion via `<Audio>`
5. Adjust timing in Remotion Studio

---

# Music — ACE-Step Local Generation

Generates background music using **ACE-Step 1.5** — runs entirely on local hardware, no API key required.

## Python Tool

```bash
cd [toolkit-dir]

.venv/Scripts/python tools/music_gen.py \
  --prompt "corporate ambient electronic, professional, subtle piano, clean, modern, no vocals" \
  --duration 30 \
  --output projects/[project-name]/audio/bg-music.mp3
```

## Task Types

| Task            | Flag           | Description                         |
| --------------- | -------------- | ----------------------------------- |
| Text to music   | (default)      | Generate from prompt                |
| Style transfer  | `--task cover` | Transfer style from reference       |
| Stem extraction | `--task stems` | Separate vocals, drums, bass, other |

## Prompt Structure

**Format:** `[Genre] [Emotion] [Instruments] [Timbre] [Era] [Production style] [Vocal style]`

```
"cinematic dramatic, intense, orchestral strings, powerful, epic, no vocals"
"upbeat pop, joyful, acoustic guitar + light percussion, warm, contemporary, lo-fi, no vocals"
```

## Scene Presets

| Preset         | Prompt                                                                                 |
| -------------- | -------------------------------------------------------------------------------------- |
| `corporate-bg` | `"corporate ambient electronic, professional, subtle piano, clean, modern, no vocals"` |
| `upbeat-tech`  | `"upbeat technology, energetic, synthesizer, bright, contemporary, instrumental"`      |
| `ambient`      | `"calm ambient, peaceful, pad synths, ethereal, modern, no vocals"`                    |
| `dramatic`     | `"cinematic dramatic, intense, orchestral, powerful, epic, no vocals"`                 |

```bash
.venv/Scripts/python tools/music_gen.py --preset corporate-bg --duration 30 --output projects/[project-name]/audio/bg.mp3
```

## Duration Guide

| Use Case                  | Duration |
| ------------------------- | -------- |
| Short background loop     | 15-30s   |
| Scene-length              | 30-60s   |
| Full intro/outro          | 60-120s  |
| Feature-length background | 120-300s |

## Volume Layering Convention

| Audio Layer      | Suggested Volume |
| ---------------- | ---------------- |
| Voiceover        | 1.0 (full)       |
| Background music | 0.2-0.35         |
| Sound effects    | 0.5-0.75         |

---

# FFmpeg — Post-Processing & Platform Export

FFmpeg handles all non-Remotion video work: conversion, trimming, platform exports, post-processing.

## Binary

Use ffmpeg from your system PATH or from the bundled binary if LiteSuite is installed:

```bash
# If LiteSuite is installed, use the bundled binary (requires LiteSuite running):
FFMPEG="${LITESUITE_DIR:-<your-litesuite-dir>}/tools/ffmpeg/ffmpeg.exe"

# Otherwise, use system ffmpeg (install: https://ffmpeg.org/download.html or `winget install ffmpeg`):
FFMPEG="ffmpeg"
```

## Common Operations

### GIF to MP4

```bash
$FFMPEG -i input.gif -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -movflags faststart -pix_fmt yuv420p -vcodec libx264 -crf 23 output.mp4
```

### Resize

```bash
$FFMPEG -i input.mp4 -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" output_1080p.mp4
```

### Compress

```bash
$FFMPEG -i input.mp4 -vcodec libx264 -crf 24 -preset slow -movflags faststart output.mp4
```

### Trim / Cut

```bash
$FFMPEG -ss 00:00:10 -to 00:01:20 -i input.mp4 -c copy trimmed.mp4
```

### Speed Adjustment

```bash
$FFMPEG -i input.mp4 -filter_complex "[0:v]setpts=PTS/2.0[v];[0:a]atempo=2.0[a]" -map "[v]" -map "[a]" output_2x.mp4
```

### Extract Audio

```bash
$FFMPEG -i input.mp4 -vn -acodec libmp3lame -q:a 2 audio.mp3
```

### Concatenation

```bash
cat > concat_list.txt << 'EOF'
file 'clip1.mp4'
file 'clip2.mp4'
EOF
$FFMPEG -f concat -safe 0 -i concat_list.txt -c copy joined.mp4
```

### Fade In / Fade Out

```bash
$FFMPEG -i input.mp4 -vf "fade=t=in:st=0:d=1,fade=t=out:st=9:d=1" -af "afade=t=in:st=0:d=1,afade=t=out:st=9:d=1" output_fades.mp4
```

### Silence Removal

```bash
$FFMPEG -i input.mp4 -af "silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-50dB" output_no_silence.mp4
```

### Captions (SRT)

```bash
$FFMPEG -i input.mp4 -vf "subtitles=captions.srt:force_style='FontSize=24,PrimaryColour=&Hffffff'" output_captions.mp4
```

### Color Grading

```bash
$FFMPEG -i input.mp4 -vf "eq=brightness=0.05:contrast=1.1:saturation=1.2" output_graded.mp4
```

## Platform Presets

### YouTube (1080p60)

```bash
$FFMPEG -i input.mp4 -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" -vcodec libx264 -crf 18 -preset slow -r 60 -acodec aac -b:a 192k -movflags faststart output_youtube.mp4
```

### TikTok (9:16 vertical)

```bash
$FFMPEG -i input.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2" -vcodec libx264 -crf 23 -preset slow -acodec aac -b:a 128k -movflags faststart output_tiktok.mp4
```

### Instagram (Square 1:1)

```bash
$FFMPEG -i input.mp4 -vf "crop=min(iw\,ih):min(iw\,ih),scale=1080:1080" -vcodec libx264 -crf 23 -preset slow -acodec aac -b:a 128k -movflags faststart output_instagram.mp4
```

### Twitter/X (1080p, max 2:20)

```bash
$FFMPEG -i input.mp4 -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" -vcodec libx264 -crf 23 -preset slow -acodec aac -b:a 128k -movflags faststart -t 00:02:20 output_twitter.mp4
```

### LinkedIn (720p+)

```bash
$FFMPEG -i input.mp4 -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2" -vcodec libx264 -crf 23 -preset slow -acodec aac -b:a 128k -movflags faststart output_linkedin.mp4
```

### WebM (VP9)

```bash
$FFMPEG -i input.mp4 -vcodec libvpx-vp9 -crf 33 -b:v 0 -acodec libopus -b:a 128k output.webm
```

## Frame Extraction (for scroll animations)

```bash
mkdir -p [toolkit-dir]/projects/[project-name]/frames
$FFMPEG -i rendered.mp4 -vf "fps=30" -q:v 2 [toolkit-dir]/projects/[project-name]/frames/frame_%04d.jpg
```
