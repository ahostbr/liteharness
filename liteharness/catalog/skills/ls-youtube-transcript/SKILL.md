---
name: ls-youtube-transcript
description: Use when the user wants a transcript, subtitles, or captions from a YouTube video. Triggers on 'get transcript', 'grab transcript', 'youtube transcript', 'transcribe this video', 'get subtitles', or when a YouTube URL is shared with a request for its text content.
allowed-tools: Bash(powershell.exe:*), Bash(yt-dlp:*)
---

# YouTube Transcript Exporter

Fetch, format, and archive YouTube transcripts using `yt-dlp`. If LiteSuite is installed, transcripts are automatically saved to LiteSuite's YouTube database so they appear in the YouTube panel.

## Steps

1. **Parse the input**: Extract the YouTube URL from `$ARGUMENTS`. Note any output path if specified.

2. **Run the script**:

   ```bash
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTubeTranscript.ps1" -Url "VIDEO_URL"
   ```

   If the user specified an output path, add `-OutputPath "PATH"`:

   ```bash
   powershell.exe -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/Get-YouTubeTranscript.ps1" -Url "VIDEO_URL" -OutputPath "PATH"
   ```

3. **Present the output**: The script prints a formatted markdown transcript to stdout. Display it to the user.

## Error Handling

| Exit Code | Meaning                                                        | Action                                                                                         |
| --------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 1         | yt-dlp failure (video unavailable, private, or metadata error) | Tell the user the video couldn't be accessed                                                   |
| 2         | No English subtitles found                                     | Run `yt-dlp --list-subs --skip-download "VIDEO_URL" 2>/dev/null` and offer available languages |

- If `yt-dlp` is not installed, tell the user: `pip install yt-dlp`
- The LiteSuite save is best-effort — failures are logged but never block transcript output.

## Notes

- **Requires `yt-dlp`**: install with `pip install yt-dlp` or `winget install yt-dlp.yt-dlp` if not already on your system.
- Auto-generated subtitles (YouTube speech recognition) are included via `--write-auto-sub`.
- Manual/uploaded subtitles are preferred when available via `--write-sub`.
- If you have a local archive database wired up, see the script's Step 5 comments for the expected JSON payload shape to plug in your own save endpoint.
- If LiteSuite is installed, transcripts are also inserted into LiteSuite's YouTube database (`%APPDATA%/../litesuite/yt.db`, table `yt_transcripts`) so they appear in the YouTube panel's Library tab.
- LiteSuite sync uses `INSERT OR IGNORE` — safe to re-run on the same video.
