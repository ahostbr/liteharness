---
name: ls-youtube
description: Use when the user wants anything pulled off a YouTube video — the transcript/subtitles/captions, the video file itself, or frames extracted from it with ffmpeg. Triggers on 'get transcript', 'grab transcript', 'youtube transcript', 'transcribe this video', 'get subtitles', 'download this youtube video', 'grab this video', 'save this video', 'extract frames', 'get frames from this video', 'screenshot every N seconds', or when a YouTube URL is shared with a request for its text, its video, or its stills.
allowed-tools: Bash(powershell.exe:*), Bash(yt-dlp:*), Bash(ffmpeg:*)
---

# YouTube Grabber — video, transcript, frames

Pull a YouTube video down, get its transcript, and cut it into frames — one script, one folder per video. Caption retrieval does **not** require LiteSuite running or installed: yt-dlp is tried first, then the standalone Python `youtube-transcript-api` package if the subtitle route fails or returns no usable file. **Audio transcription is opt-in and off by default:** only with `-Whisper`, and only after both caption routes fail, is original-language audio downloaded and transcribed using an **already-running** whisper.cpp server on `127.0.0.1:8080` (LiteSuite's Whisper). The script never starts a server or loads/downloads a model. If LiteSuite is installed, transcripts are also saved to LiteSuite's YouTube database so they appear in the YouTube panel.

**By default, you (the agent) summarize the transcript for the user.** The full transcript goes to a file; paste it only when the user asks for it.

## What lands where

| Artefact | Path |
| --- | --- |
| Video | `<MediaRoot>/<video_id>/video.mp4` |
| Frames | `<MediaRoot>/<video_id>/frames/%05d.jpg` |
| Transcript | `<MediaRoot>/<video_id>/transcript.md` (or `-OutputPath <file>` when given), plus stdout as markdown |

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

   # ONLY when the user asks for audio transcription: Whisper is opt-in, off by default
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTube.ps1" -Url "VIDEO_URL" -NoVideo -NoFrames -Whisper
   ```

3. **Summarize it (the default)**: The script prints a markdown transcript to stdout whose header names the **producing transcript route** (`captions`, `transcript-api` or `whisper-audio`), the video path, the frame count and the save status, and writes the same markdown to the transcript file. Read it and give the user **a summary in your own words**: title, channel and length; the main points; and any concrete numbers, settings, commands or names worth keeping. Then give the path to the full transcript file, and say where the video and frames landed if they were made. Say whether the text came from uploaded or automatic captions, since automatic ones mis-hear names and terms. Paste the full transcript only when the user asks for it ("full transcript", "verbatim", "the whole thing").

## Parameters

| Flag | Default | Meaning |
| --- | --- | --- |
| `-Url` | *(required)* | The YouTube URL. `watch?v=`, `youtu.be/` and `shorts/` are all understood |
| `-OutputPath` | `<MediaRoot>/<video_id>/transcript.md` | Write the markdown transcript to this FILE as well as stdout |
| `-MediaRoot` | `~/.litesuite/youtube` | Parent for the per-video folder holding `video.mp4` and `frames/` |
| `-Fps` | `1` | Frames per second of runtime. `1` gives one frame per second (846 for a 14:06 video) |
| `-Interval` | *(unset)* | Seconds between frames, e.g. `5`. Wins over `-Fps` when both are given |
| `-NoVideo` | off | Skip the download. Frames are still cut if a `video.mp4` is already on disk |
| `-NoFrames` | off | Skip frame extraction |
| `-Whisper` | **off** | Opt-in audio route: after every caption route fails, transcribe the audio with an already-running whisper.cpp on `127.0.0.1:8080`. Pass it only when the user asks |

## Error Handling

| Exit Code | Meaning | Action |
| --------- | ------- | ------ |
| 1 | yt-dlp failure (video unavailable, private, or metadata error) | The message carries yt-dlp's own ERROR line — read it and tell the user what it says |
| 2 | **No English subtitles** — `Get-Transcript.py` retrieved a caption catalog and confirmed no `en`, `en-US` or `en-GB` track | With `-Whisper` the main script still tries audio; helper exit 2 alone does not end the run |
| 3 | **Transcript routes FAILED** — no caption route yielded a usable transcript (and, with `-Whisper`, audio was unavailable or failed) | Do NOT infer "no subtitles" from refusal. Read the route diagnostics; a confirmed missing English track remains identified separately. Offer `-Whisper` to the user; never pass it on your own |

🔴 **2 and 3 are different answers.** A refused fetch or missing file is not proof that English
captions are absent. After yt-dlp exits non-zero, produces no file, or produces unusable JSON3,
the script tries `Get-Transcript.py` using `python` on PATH. The helper calls
`YouTubeTranscriptApi().list(video_id).find_transcript(["en", "en-US", "en-GB"]).fetch()` —
the same package route as Suite, retaining caption timings, preferring manual tracks and
accepting generated tracks. The caption helper does not translate or use ASR, cookies, proxies or the Suite bridge.
Only a successful catalog lookup with no requested track yields helper exit 2. Disabled-caption,
network, package and parse errors yield helper exit 3. Without `-Whisper` the run then exits 3
and says the audio route is off; with it, both proceed to the audio route.

🟡 **"Did not get any data blocks" means YouTube served the caption track EMPTY** (HTTP 200,
0 bytes), not that there are no captions. Seen on a video uploaded the same day: its automatic
English captions were listed but served empty on every yt-dlp client, while an older video's
automatic captions downloaded normally. The exit-3 message says so and, when the upload is at
most two days old, suggests retrying later. Tell the user that; don't call it "no captions".

The audio route (opt-in, `-Whisper`) probes `http://127.0.0.1:8080/` for whisper.cpp before downloading anything.
If unavailable or a different server answers, the run exits 3 and explains that it needs
LiteSuite's Whisper running. **Never start whisper-server, load a model or download one to
make this route work.** It selects audio-only formats by yt-dlp metadata, preferring the
`original` format note (not an auto-dub); after a direct/DASH failure such as HTTP 403, it
tries HLS (`m3u8`) audio. No format IDs are hard-coded. ffmpeg converts the audio to mono
16 kHz PCM WAV; curl sends it to `/inference` for English speech recognition. This is ASR,
not a verified caption or translation. Temporary audio is removed on success and failure.

- If `yt-dlp` is not installed, tell the user: `pip install yt-dlp`
- **Fallback dependency is optional for successful yt-dlp transcripts.** If absent from the selected `python` interpreter, the diagnostic names that interpreter and gives one command: `python -m pip install youtube-transcript-api`. Explain it; do not silently install or upgrade anything. An incompatible or broken installed package is an import failure, not "no captions".
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
- **Frames and the opt-in audio route require `ffmpeg`**: `winget install Gyan.FFmpeg`. Caption routes work without it. The audio route also requires `curl.exe` on PATH.
- Re-running is cheap: an existing `video.mp4` is not re-downloaded, and the DB inserts are idempotent on `video_id`.
- The video is fetched as `bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b` and merged to mp4 — a bare `b[ext=mp4]` silently hands back a lower resolution, because YouTube serves 1080p and above as video-only streams.
- Auto-generated subtitles (YouTube speech recognition) are included via `--write-auto-sub`; manual/uploaded subtitles are preferred via `--write-sub`.
- If you have a local archive database wired up, see the script's Step 5 comments for the expected JSON payload shape to plug in your own save endpoint.
- If LiteSuite is installed, transcripts are also inserted into LiteSuite's YouTube database (`%APPDATA%/litesuite/yt.db` — the same folder as `%APPDATA%/LiteSuite/yt.db` on Windows, table `yt_transcripts`) so they appear in the YouTube panel's Library tab.
- LiteSuite sync inserts only when that `video_id` isn't already saved (idempotent) — safe to re-run. The panel's full-text search index updates automatically via triggers.
