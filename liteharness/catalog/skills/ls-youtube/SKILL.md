---
name: ls-youtube
description: Use when the user wants anything pulled off a YouTube video — the transcript/subtitles/captions, the video file itself, or frames extracted from it with ffmpeg. Triggers on 'get transcript', 'grab transcript', 'youtube transcript', 'transcribe this video', 'get subtitles', 'download this youtube video', 'grab this video', 'save this video', 'extract frames', 'get frames from this video', 'screenshot every N seconds', or when a YouTube URL is shared with a request for its text, its video, or its stills.
allowed-tools: Bash(powershell.exe:*), Bash(yt-dlp:*), Bash(ffmpeg:*)
---

# YouTube Grabber — video, transcript, frames

Pull a YouTube video down, get its transcript, and cut it into frames — one script, one folder per video. If LiteSuite is installed, transcripts are also saved to LiteSuite's YouTube database so they appear in the YouTube panel.

## What lands where

| Artefact | Path |
| --- | --- |
| Video | `<MediaRoot>/<video_id>/video.mp4` |
| Frames | `<MediaRoot>/<video_id>/frames/%05d.jpg` |
| Transcript | stdout as markdown, plus `-OutputPath <file>` when given |

`MediaRoot` defaults to `~/.litesuite/youtube`. **`-OutputPath` is the transcript FILE**, not the media folder — it kept its original meaning so existing transcript-only callers are unaffected.

## Steps

1. **Parse the input**: Extract the YouTube URL from `$ARGUMENTS`. Note which artefacts the user asked for, and any output path.

2. **Run the script** — all three artefacts is the default:

   ```bash
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTube.ps1" -Url "VIDEO_URL"
   ```

   Only what was asked for:

   ```bash
   # transcript only — what this skill did before it grew the other two
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTube.ps1" -Url "VIDEO_URL" -NoVideo -NoFrames

   # video + transcript, no frames
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTube.ps1" -Url "VIDEO_URL" -NoFrames

   # one frame every 5 seconds instead of every second
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTube.ps1" -Url "VIDEO_URL" -Interval 5

   # somewhere other than ~/.litesuite/youtube, and the transcript to a named file
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTube.ps1" -Url "VIDEO_URL" -MediaRoot "D:/yt" -OutputPath "D:/yt/talk.md"
   ```

3. **Present the output**: The script prints a formatted markdown transcript to stdout whose header names the video path, the frame count and the save status. Display it, and tell the user where the video and frames landed.

## Parameters

| Flag | Default | Meaning |
| --- | --- | --- |
| `-Url` | *(required)* | The YouTube URL. `watch?v=`, `youtu.be/` and `shorts/` are all understood |
| `-OutputPath` | *(none)* | Write the markdown transcript to this FILE as well as stdout |
| `-MediaRoot` | `~/.litesuite/youtube` | Parent for the per-video folder holding `video.mp4` and `frames/` |
| `-Fps` | `1` | Frames per second of runtime. `1` gives one frame per second (846 for a 14:06 video) |
| `-Interval` | *(unset)* | Seconds between frames, e.g. `5`. Wins over `-Fps` when both are given |
| `-NoVideo` | off | Skip the download. Frames are still cut if a `video.mp4` is already on disk |
| `-NoFrames` | off | Skip frame extraction |

## Error Handling

| Exit Code | Meaning | Action |
| --------- | ------- | ------ |
| 1 | yt-dlp failure (video unavailable, private, or metadata error) | The message carries yt-dlp's own ERROR line — read it and tell the user what it says |
| 2 | **No English subtitles** — yt-dlp exited 0 and produced no captions. Definitive | Run `yt-dlp --list-subs --skip-download "VIDEO_URL"` and offer available languages |
| 3 | **Subtitle fetch FAILED** — yt-dlp exited non-zero (429, members-only, geo-block, network). Gated and retryable | Do NOT report this as "no subtitles". Read the ERROR line; wait and retry, or say what blocked it |

🔴 **2 and 3 are different answers and used to be the same one.** A refused fetch is retryable;
"this video has no English captions" is final. Reporting the first as the second sends the user
away from a video they could have had. yt-dlp's exit code is what separates them.

- If `yt-dlp` is not installed, tell the user: `pip install yt-dlp`
- **A failed video download or a missing ffmpeg does NOT fail the run.** The transcript is the older contract and most callers still want it, so both report their status in the markdown header and the script carries on. Read the header — `Video:` and `Frames:` say what actually happened.
- If `ffmpeg` is not on PATH the frames step names the install (`winget install Gyan.FFmpeg`) and skips. Nothing here vendors an ffmpeg.
- ⚠️ **`ffmpeg` exiting 0 is not proof it wrote anything.** The script counts the jpgs and says so explicitly when the count is zero, because a "done" over an empty folder is the failure that reads as success.
- The LiteSuite save is best-effort — failures are logged but never block transcript output.

## Notes

- **Requires `yt-dlp`**: install with `pip install yt-dlp` or `winget install yt-dlp.yt-dlp`.
- **A JS runtime is selected automatically** (`deno`, then `node`, then `bun`; override with
  `YT_DLP_JS_RUNTIME`). yt-dlp enables only `deno` by default and otherwise prints *"No supported
  JavaScript runtime could be found ... some formats may be missing"* — a WARNING, so the run
  continues and quietly returns a reduced format list. The script passes `--js-runtimes` on every
  yt-dlp call instead, and warns only when the box has none of the three. It changes no global
  yt-dlp configuration.
- **Failures name their cause.** yt-dlp's stderr is captured separately from its stdout and the
  decisive `ERROR:` line is put FIRST in the message, so a truncated diagnostic still says why.
- **Frames require `ffmpeg`**: `winget install Gyan.FFmpeg`. Everything else works without it.
- Re-running is cheap: an existing `video.mp4` is not re-downloaded, and the DB inserts are idempotent on `video_id`.
- The video is fetched as `bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b` and merged to mp4 — a bare `b[ext=mp4]` silently hands back a lower resolution, because YouTube serves 1080p and above as video-only streams.
- Auto-generated subtitles (YouTube speech recognition) are included via `--write-auto-sub`; manual/uploaded subtitles are preferred via `--write-sub`.
- If you have a local archive database wired up, see the script's Step 5 comments for the expected JSON payload shape to plug in your own save endpoint.
- If LiteSuite is installed, transcripts are also inserted into LiteSuite's YouTube database (`%APPDATA%/litesuite/yt.db` — the same folder as `%APPDATA%/LiteSuite/yt.db` on Windows, table `yt_transcripts`) so they appear in the YouTube panel's Library tab.
- LiteSuite sync inserts only when that `video_id` isn't already saved (idempotent) — safe to re-run. The panel's full-text search index updates automatically via triggers.
