---
name: ls-video-download
description: Download YouTube videos via yt-dlp and sync them into LiteSuite's YouTube ecosystem. Extracts metadata, saves to LiteSuite's data directory, registers in LiteSuite's YouTube database. Supports transcript grabbing, frame extraction, and audio-only mode. Triggers on '/video-download', 'download this video', 'download video', 'save this video', 'grab this video', 'download from youtube', 'yt-dlp download'.
allowed-tools: Bash(yt-dlp:*), Bash(python:*), Bash(ffmpeg:*), Bash(sqlite3:*), Bash(powershell.exe:*)
---

# /video-download - YouTube Video Downloader with LiteSuite Sync

Download YouTube videos via yt-dlp and register them in LiteSuite's YouTube database so they appear in the YouTube panel.

## Syntax

```
/video-download <URL> [flags]
```

## Flags

| Flag              | Description                                                  |
| ----------------- | ------------------------------------------------------------ |
| `--transcript`    | Also grab the transcript (invokes /youtube-transcript skill) |
| `--frames N`      | Extract frames every N seconds (uses ffmpeg)                 |
| `--audio-only`    | Download audio only as .m4a (for podcasts)                   |
| `--output <path>` | Custom output directory (default: LiteSuite's videos dir)    |

## Implementation

When the user invokes `/video-download`, follow these steps:

### Step 1: Parse Input

Extract the YouTube URL and any flags from the user's input.

### Step 2: Determine Output Directory

Default video storage: `%APPDATA%/../litesuite/videos/`

If `--output` is specified, use that directory instead.

Create the output directory if it doesn't exist:

```bash
mkdir -p "$OUTPUT_DIR"
```

### Step 3: Extract Metadata

```bash
yt-dlp --print "%(id)s|%(title)s|%(channel)s|%(uploader_url)s|%(duration)s|%(upload_date)s|%(description)s|%(view_count)s|%(thumbnail)s" --skip-download "URL"
```

Parse the pipe-delimited output into variables: video_id, title, channel, channel_url, duration, upload_date, description, view_count, thumbnail_url.

### Step 4: Download Video

**Standard (best quality mp4):**

```bash
yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]" -o "$OUTPUT_DIR/%(id)s.%(ext)s" "URL"
```

**Audio-only (if `--audio-only`):**

```bash
yt-dlp -f "bestaudio[ext=m4a]/bestaudio" -o "$OUTPUT_DIR/%(id)s.%(ext)s" "URL"
```

Report download progress to the user as it runs.

### Step 5: Save Metadata JSON

Write a metadata sidecar file alongside the video:

```bash
# Create $OUTPUT_DIR/<video_id>.meta.json with:
{
  "video_id": "...",
  "title": "...",
  "channel": "...",
  "channel_url": "...",
  "duration_seconds": N,
  "upload_date": "YYYYMMDD",
  "description": "...",
  "view_count": N,
  "thumbnail_url": "...",
  "downloaded_at": "ISO8601",
  "file_path": "...",
  "audio_only": false
}
```

### Step 6: Register in LiteSuite YouTube Database

Insert the video metadata into LiteSuite's YouTube database so it shows up in the YouTube panel:

```bash
# LiteSuite YT DB location
LITESUITE_YT_DB="$APPDATA/../litesuite/yt.db"
```

If the DB exists, insert into the `yt_transcripts` table:

```sql
INSERT OR IGNORE INTO transcripts (video_id, youtube_url, video_title, language, transcript_text, segments_json, created_at)
VALUES ('<video_id>', '<url>', '<title>', 'en', '[Video downloaded - no transcript]', '[]', datetime('now'));
```

Use sqlite3 CLI or Python sqlite3 module:

```bash
sqlite3 "$LITESUITE_YT_DB" "INSERT OR IGNORE INTO yt_transcripts ..."
```

Or via Python fallback:

```bash
python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(sys.argv[2]); c.commit(); c.close()" "$LITESUITE_YT_DB" "$SQL"
```

### Step 7: Optional — Transcript (if `--transcript`)

Invoke the `/youtube-transcript` skill on the same URL. This will:

- Fetch the transcript via yt-dlp
- Save to a local transcript store (if configured)
- Save to LiteSuite (updating the transcript entry we just created)

Use the Skill tool:

```
Skill("youtube-transcript", args: "URL")
```

### Step 8: Optional — Frame Extraction (if `--frames N`)

Extract frames every N seconds using ffmpeg:

```bash
VIDEO_FILE="$OUTPUT_DIR/<video_id>.mp4"
FRAMES_DIR="$OUTPUT_DIR/<video_id>_frames"
mkdir -p "$FRAMES_DIR"
ffmpeg -i "$VIDEO_FILE" -vf "fps=1/$N" "$FRAMES_DIR/frame_%04d.png" -y
```

Report how many frames were extracted.

### Step 9: Report Results

Show the user a summary:

```
## Download Complete

**Video:** <title>
**Channel:** <channel>
**Duration:** <duration>
**File:** <file_path> (<file_size>)
**LiteSuite:** Registered (will appear in YouTube panel)
**Transcript:** <status if --transcript>
**Frames:** <count if --frames>
```

## Examples

### Basic download

```
/video-download https://youtu.be/RG38jA-DFeM
```

### Download with transcript

```
/video-download https://youtu.be/RG38jA-DFeM --transcript
```

### Download with transcript and frame extraction

```
/video-download https://youtu.be/RG38jA-DFeM --transcript --frames 5
```

### Audio-only (podcast)

```
/video-download https://youtu.be/RG38jA-DFeM --audio-only
```

### Custom output

```
/video-download https://youtu.be/RG38jA-DFeM --output C:/Videos/research
```

## Notes

- **Requirements:** `yt-dlp` and `ffmpeg` must be installed and on PATH. Install with: `pip install yt-dlp` and download ffmpeg from https://ffmpeg.org/download.html (add to PATH).
- LiteSuite YT DB location: `%APPDATA%/../litesuite/yt.db`
- LiteSuite videos dir: `%APPDATA%/../litesuite/videos/`
- If LiteSuite YT DB doesn't exist, the video is still downloaded — just not registered.
- The `--transcript` flag chains into the existing `/youtube-transcript` skill which handles saving the transcript alongside the downloaded video.
- The `--frames` flag requires ffmpeg. Frame extraction creates `<video_id>_frames/` alongside the video file.
- Different from `/video-lens` which only extracts and captions frames — this skill downloads the actual video file and syncs into LiteSuite's YouTube ecosystem.
