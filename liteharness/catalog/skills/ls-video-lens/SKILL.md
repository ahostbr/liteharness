---
name: ls-video-lens
description: Use when the user wants to download a YouTube video and extract, caption, or index its frames visually. Triggers on 'index this video', 'caption video frames', 'extract frames', 'video-lens', 'analyze video visually', 'what happens in this video', 'frame by frame', 'video breakdown', 'screenshot every X seconds'.
---

# video-lens

Download a YouTube video, extract frames at configurable intervals, and caption/index every frame using a VLM (LM Studio or Claude API).

## Quick Reference

```bash
# Full pipeline — download, extract every 5s, caption via LM Studio
python ${CLAUDE_SKILL_DIR}/video_lens.py <YOUTUBE_URL>

# Every 1 second, keep frame images
python ${CLAUDE_SKILL_DIR}/video_lens.py <URL> --interval 1 --keep-frames

# Use Claude API instead of LM Studio
python ${CLAUDE_SKILL_DIR}/video_lens.py <URL> --backend claude

# Extract only (no captioning) — for Claude Code to read frames directly
python ${CLAUDE_SKILL_DIR}/video_lens.py <URL> --extract-only -w ./frames

# Custom prompt + output path
python ${CLAUDE_SKILL_DIR}/video_lens.py <URL> -i 3 -p "Focus on text visible on screen" -o output.json
```

## Arguments

| Flag             | Short | Default               | Description                   |
| ---------------- | ----- | --------------------- | ----------------------------- |
| `url`            | —     | required              | YouTube URL                   |
| `--interval`     | `-i`  | `5`                   | Seconds between frames        |
| `--backend`      | `-b`  | `lmstudio`            | `lmstudio` or `claude`        |
| `--output`       | `-o`  | auto                  | Output JSON path              |
| `--prompt`       | `-p`  | built-in              | Custom system prompt          |
| `--extract-only` | —     | false                 | Skip captioning, just extract |
| `--keep-frames`  | —     | false                 | Keep frame JPEGs after run    |
| `--workdir`      | `-w`  | temp                  | Working directory             |
| `--api-url`      | —     | `localhost:1234` | LM Studio endpoint            |

## Backends

### LM Studio (default)

Requires a VLM loaded in LM Studio (e.g., qwen-0.8b). Auto-detects the loaded model. Fast (~2-3s/frame on 0.8B), free, local.

### Claude API

Requires `ANTHROPIC_API_KEY` env var. Uses claude-sonnet-4-6. Higher quality captions but costs per frame.

### Claude Code (manual)

Use `--extract-only` to get frames, then read them directly:

```bash
python ${CLAUDE_SKILL_DIR}/video_lens.py <URL> --extract-only -w ./video_frames
```

Then use the Read tool on individual frame images from `./video_frames/frames/`.

## Output

Two files are generated (unless `--extract-only`):

**JSON** (`*_index.json`): Structured index with video metadata, settings, and per-frame entries (index, timestamp, caption, filename).

**Markdown** (`*_index.md`): Human-readable summary with timestamps and captions.

## Dependencies

- **yt-dlp**: `pip install yt-dlp`
- **ffmpeg**: Must be available on PATH, or set the `FFMPEG_PATH` env var to the full path of your ffmpeg executable. Download from https://ffmpeg.org/download.html.

## Interval Guide

| Interval | 10min video | 1hr video   | Best for                  |
| -------- | ----------- | ----------- | ------------------------- |
| `1s`     | 600 frames  | 3600 frames | Dense action, tutorials   |
| `5s`     | 120 frames  | 720 frames  | General content (default) |
| `10s`    | 60 frames   | 360 frames  | Talks, presentations      |
| `30s`    | 20 frames   | 120 frames  | Quick overview            |
| `60s`    | 10 frames   | 60 frames   | Long-form summary         |
