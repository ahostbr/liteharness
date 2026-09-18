# Get-YouTube.ps1 — Full pipeline: metadata, VIDEO, subtitles, parse, FRAMES, DB save, output
#
# T894, RYAN 2026-09-18: "grab this video down the vdieo and transript . extract
# the frames with ffmpeg ... then edit the skill to include this workflow and
# rename it only ls-youtube drop the rest".
#
# The three artefacts land under one folder per video so they can be found
# together later: <MediaRoot>/<video_id>/{video.mp4, frames/%05d.jpg}. The
# transcript keeps its existing home (stdout, plus -OutputPath when given) —
# see the note on -OutputPath below.
param(
    [Parameter(Mandatory)]
    [string]$Url,

    # The MARKDOWN transcript file. Unchanged meaning from the transcript-only
    # skill this grew out of: callers and SKILL.md both already pass a FILE path
    # here, so repurposing it as a directory for the whole set would have broken
    # every existing invocation silently — the write would just land somewhere
    # else. Video and frames get their own root below.
    [string]$OutputPath,

    # Where <video_id>/ is created for the video and frames.
    [string]$MediaRoot = (Join-Path $HOME '.litesuite/youtube'),

    # Frames per second handed to ffmpeg's fps filter. 1 = one frame per second
    # of runtime (846 frames for a 14:06 video, measured on Ryan's URL).
    [double]$Fps = 1,

    # Seconds between frames, as an alternative spelling of -Fps for sparse
    # sampling: -Interval 5 is one frame every 5s. Wins over -Fps when both are
    # given, because it is the more specific request.
    [double]$Interval = 0,

    # The transcript-only path this skill used to be. Both still work alone.
    [switch]$NoVideo,
    [switch]$NoFrames
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ── 1a. JS runtime for yt-dlp (T894b) ─────────────────────────────────────────
# YouTube extraction needs a JS runtime. yt-dlp enables only `deno` by default and
# prints "No supported JavaScript runtime could be found ... has been deprecated,
# and some formats may be missing" when it is absent — which is a WARNING, so the
# run continues and silently returns a reduced format list. Observed on this box
# during the T894 acceptance run: the warning appeared on every call, and the
# video still downloaded, which is exactly why it is easy to leave in place.
#
#     A DEPRECATION WARNING THAT DEGRADES THE RESULT IS NOT COSMETIC. Nothing
#     downstream can tell "the best format" from "the best format deno could see".
#
# Overridable with YT_DLP_JS_RUNTIME (e.g. a project pin). Scoped to THIS skill's
# yt-dlp calls — it alters no global yt-dlp configuration.
$JsRuntime = $env:YT_DLP_JS_RUNTIME
if (-not $JsRuntime) {
    foreach ($cand in @('deno', 'node', 'bun')) {
        if (Get-Command $cand -ErrorAction SilentlyContinue) { $JsRuntime = $cand; break }
    }
}
if ($JsRuntime) {
    $JsRuntimeArgs = @('--js-runtimes', $JsRuntime)
    Write-Host "yt-dlp JS runtime: $JsRuntime" -ForegroundColor DarkGray
} else {
    $JsRuntimeArgs = @()
    Write-Warning "No JS runtime found (deno/node/bun). YouTube extraction may fail; install one (e.g. node) or set YT_DLP_JS_RUNTIME."
}

# ── 1. Setup ──────────────────────────────────────────────────────────────────

# Extract video ID from URL
if ($Url -match '[?&]v=([A-Za-z0-9_-]{11})') {
    $VideoId = $Matches[1]
} elseif ($Url -match 'youtu\.be/([A-Za-z0-9_-]{11})') {
    $VideoId = $Matches[1]
} elseif ($Url -match 'shorts/([A-Za-z0-9_-]{11})') {
    $VideoId = $Matches[1]
} else {
    Write-Error "Could not extract video ID from URL: $Url"
    exit 1
}

$TempDir = $env:TEMP
$SubFileBase = Join-Path $TempDir "yt-transcript-$VideoId"
$SavePayloadPath = Join-Path $TempDir "yt-save-$VideoId.json"

# ── Helper: bounded diagnostic that PRIORITISES the decisive ERROR (T894b) ────
# yt-dlp's stderr is mostly progress and warnings, and the one line that says WHY
# it failed is usually the LAST. Truncating from the front therefore throws away
# the only useful line and keeps the noise.
#
#     UNDER TRUNCATION, ORDER DECIDES WHAT SURVIVES. So the last ERROR leads,
#     remaining ERRORs follow, and warnings come last — the message degrades to
#     "the cause, cut short" instead of "progress bars, cut short".
function Get-DiagText([object[]]$lines, [int]$maxLen = 400) {
    if ($null -eq $lines) { return '' }
    $all = @($lines | ForEach-Object { "$_" } | Where-Object { $_ -and $_.Trim() })
    if (-not $all.Count) { return '' }
    $errors = @($all | Where-Object { $_ -match 'ERROR' })
    $rest   = @($all | Where-Object { $_ -notmatch 'ERROR' })
    $ordered = @()
    if ($errors.Count -eq 1) { $ordered += $errors[0] }
    elseif ($errors.Count -gt 1) { $ordered += @($errors[-1]) + @($errors[0..($errors.Count - 2)]) }
    $ordered += $rest
    $text = (($ordered -join ' | ') -replace '\s+', ' ').Trim()
    if ($text.Length -gt $maxLen) { $text = $text.Substring(0, $maxLen) + '...' }
    return $text
}

try {
    # ── 2. Fetch metadata ─────────────────────────────────────────────────────

    # T894b — stdout and stderr captured SEPARATELY. They used to be merged with
    # `2>$null`, which threw the diagnosis away: a members-only, private, 429 or
    # geo-blocked video fails HERE, and the message said only "may be unavailable
    # or private" while yt-dlp's own line naming the reason had been discarded.
    # Splitting them also keeps stderr from displacing the positional fields.
    $metaResult = & yt-dlp @JsRuntimeArgs --print title --print channel --print id --print uploader_url --skip-download $Url 2>&1
    $metaStdout = @($metaResult | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    $metaStderr = @($metaResult | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() })
    if ($LASTEXITCODE -ne 0) {
        Write-Error "yt-dlp failed to fetch metadata (exit code $LASTEXITCODE). $(Get-DiagText $metaStderr)"
        exit 1
    }

    $metaLines = @($metaStdout | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($metaLines.Count -lt 4) {
        Write-Error "yt-dlp returned incomplete metadata ($($metaLines.Count) lines, expected 4). $(Get-DiagText $metaStderr)"
        exit 1
    }

    $VideoTitle  = $metaLines[0]
    $ChannelName = $metaLines[1]
    $VideoId     = $metaLines[2]
    $ChannelUrl  = $metaLines[3]

    $ChannelHandle = ''
    if ($ChannelUrl -match '/@([^/\s]+)') {
        $ChannelHandle = "@$($Matches[1])"
    }

    # ── 2b. Download the video ────────────────────────────────────────────────
    # RYAN: "grab this video down the vdieo". Muxed mp4 preferred, falling back
    # to bestvideo+bestaudio and then to whatever exists, because a 1080p+ stream
    # on YouTube is video-only and the plain `b[ext=mp4]` would silently hand
    # back a lower-resolution copy instead. Measured on 1vw39QCcQjg: 1920x1080,
    # 160,942,386 bytes.

    $MediaDir  = Join-Path $MediaRoot $VideoId
    $VideoPath = Join-Path $MediaDir 'video.mp4'
    $FramesDir = Join-Path $MediaDir 'frames'
    $videoStatus = 'Skipped (-NoVideo)'
    $frameStatus = 'Skipped (-NoFrames)'

    if (-not $NoVideo) {
        if (-not (Test-Path $MediaDir)) { New-Item -ItemType Directory -Path $MediaDir -Force | Out-Null }
        if (Test-Path $VideoPath) {
            # Idempotent like the DB inserts below: re-running to refresh a
            # transcript must not re-pull 150 MB.
            $videoStatus = "Already downloaded: $VideoPath"
            Write-Host "Video: $videoStatus" -ForegroundColor Green
        } else {
            & yt-dlp @JsRuntimeArgs -f "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b" --merge-output-format mp4 -o (Join-Path $MediaDir 'video.%(ext)s') $Url
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VideoPath)) {
                # NOT fatal. The transcript is the older contract and still the
                # thing most callers want; a failed video download must not take
                # it down with it.
                $videoStatus = "Download failed (yt-dlp exit $LASTEXITCODE)"
                Write-Host "Video: $videoStatus" -ForegroundColor Yellow
            } else {
                $bytes = (Get-Item $VideoPath).Length
                $videoStatus = "$VideoPath ($([math]::Round($bytes / 1MB, 1)) MB)"
                Write-Host "Video: $videoStatus" -ForegroundColor Green
            }
        }
    }

    # ── 2c. Extract frames with ffmpeg ────────────────────────────────────────
    # RYAN: "extract the frames with ffmpeg". One jpg per sampled instant into
    # <video_id>/frames/%05d.jpg. -q:v 2 is ffmpeg's near-best JPEG quality; the
    # frames are for reading slides and UI out of a screencast, and the default
    # quantiser blurs small text.

    if (-not $NoFrames) {
        if ($NoVideo -and -not (Test-Path $VideoPath)) {
            # Say WHICH switch caused it. "0 frames" with no reason reads as a
            # broken ffmpeg rather than a choice the caller made.
            $frameStatus = 'Skipped (-NoVideo, and no video already on disk to read)'
            Write-Host "Frames: $frameStatus" -ForegroundColor Yellow
        } elseif (-not (Test-Path $VideoPath)) {
            $frameStatus = 'Skipped (no video file — the download above did not produce one)'
            Write-Host "Frames: $frameStatus" -ForegroundColor Yellow
        } elseif (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            # Named, not vendored. An ffmpeg this script downloaded would be a
            # second unmanaged copy of a tool most boxes already have.
            $frameStatus = 'Skipped (ffmpeg not on PATH — install it, e.g. winget install Gyan.FFmpeg)'
            Write-Host "Frames: $frameStatus" -ForegroundColor Yellow
        } else {
            if (-not (Test-Path $FramesDir)) { New-Item -ItemType Directory -Path $FramesDir -Force | Out-Null }
            # -Interval wins over -Fps: it is the more specific request, and
            # 1/Interval is the same filter expressed the other way round.
            $fpsExpr = if ($Interval -gt 0) { "1/$Interval" } else { "$Fps" }
            & ffmpeg -hide_banner -loglevel error -y -i $VideoPath -vf "fps=$fpsExpr" -q:v 2 (Join-Path $FramesDir '%05d.jpg')
            $ffmpegExit = $LASTEXITCODE
            $frameCount = @(Get-ChildItem $FramesDir -Filter '*.jpg' -ErrorAction SilentlyContinue).Count
            if ($ffmpegExit -ne 0) {
                $frameStatus = "ffmpeg exit $ffmpegExit ($frameCount frame(s) written before it stopped)"
                Write-Host "Frames: $frameStatus" -ForegroundColor Yellow
            } elseif ($frameCount -eq 0) {
                # A ZERO IS NOT A PASS. ffmpeg can exit 0 having written nothing
                # (an unreadable stream, a filter that matched no frames), and
                # "done" over an empty directory is the failure that looks like
                # success.
                $frameStatus = 'ffmpeg exited 0 but wrote NO frames — check the video stream'
                Write-Host "Frames: $frameStatus" -ForegroundColor Yellow
            } else {
                $frameStatus = "$frameCount frame(s) at fps=$fpsExpr -> $FramesDir"
                Write-Host "Frames: $frameStatus" -ForegroundColor Green
            }
        }
    }

    # ── 3. Download subtitles ─────────────────────────────────────────────────

    $subResult = & yt-dlp @JsRuntimeArgs --write-auto-sub --write-sub --sub-lang "en,en-US,en-GB" --skip-download --sub-format json3 -o "$SubFileBase" $Url 2>&1
    $subExit = $LASTEXITCODE
    $subStderr = @($subResult | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() })

    # Glob for the subtitle file (yt-dlp appends .en.json3, .en-US.json3, etc.)
    $SubFile = Get-ChildItem "$SubFileBase*.json3" -ErrorAction SilentlyContinue | Select-Object -First 1

    if (-not $SubFile) {
        # 🔴 T894b — A REFUSED FETCH IS NOT "NO SUBTITLES", and this branch used to
        # call every empty result the second thing. 429, members-only and network
        # failures are GATED AND RETRYABLE; "this video has no English captions" is
        # DEFINITIVE. Reporting the first as the second tells the user to stop
        # trying when they should wait, and the exit code carried the same lie.
        # yt-dlp's own exit code separates them: nonzero = it failed, 0 with no
        # file = it succeeded and there was nothing to get.
        if ($subExit -ne 0) {
            Write-Error "Subtitle fetch FAILED (yt-dlp exit $subExit) - a fetch failure, NOT no-subs. $(Get-DiagText $subStderr)"
            exit 3
        }
        Write-Error "No subtitle file found (yt-dlp exit 0) - no English subtitles. $(Get-DiagText $subStderr)"
        exit 2
    }

    # ── 4. Parse JSON3 ────────────────────────────────────────────────────────

    $json = Get-Content -Raw -Encoding UTF8 $SubFile.FullName | ConvertFrom-Json
    $events = @($json.events | Where-Object { $_.segs })

    $Lines = [System.Collections.Generic.List[string]]::new()
    $Segments = [System.Collections.Generic.List[object]]::new()
    $lastLine = ''

    foreach ($evt in $events) {
        $startMs = if ($null -ne $evt.tStartMs) { [long]$evt.tStartMs } else { 0 }
        $durationMs = if ($null -ne $evt.dDurationMs) { [double]$evt.dDurationMs } else { 0 }
        $startSec = [math]::Floor($startMs / 1000)
        $durationSec = [math]::Round($durationMs / 1000, 2)

        $h = [int][math]::Floor($startSec / 3600)
        $m = [int][math]::Floor(($startSec % 3600) / 60)
        $s = [int]($startSec % 60)

        if ($h -gt 0) {
            $ts = '{0:D2}:{1:D2}:{2:D2}' -f $h, $m, $s
        } else {
            $ts = '{0:D2}:{1:D2}' -f $m, $s
        }

        $text = ($evt.segs | ForEach-Object {
            if ($_.utf8) { $_.utf8 } else { '' }
        }) -join ''
        $text = ($text -replace "`n", ' ').Trim()

        if ($text -eq '' -or $text -eq $lastLine) { continue }
        $lastLine = $text

        $Lines.Add("**$ts** $text")
        $Segments.Add(@{
            text     = $text
            offset   = [int]$startSec
            duration = $durationSec
        })
    }

    # ── 5. (Optional) Save to a local archive — not configured by default ────────
    # To wire in your own archive backend, set the env var LITEYT_ARCHIVE_SCRIPT to
    # an absolute path of a Node.js/Python script that reads a JSON payload from
    # stdin with keys: videoId, youtubeUrl, videoTitle, channelName, channelHandle,
    # language, transcriptText, segments.  The script should write {"ok":true} or
    # {"ok":false,"error":"..."} to stdout.  If the env var is not set, this step
    # is silently skipped.

    $archiveStatus = 'Not configured'
    $archiveScript = $env:LITEYT_ARCHIVE_SCRIPT
    if ($archiveScript -and (Test-Path $archiveScript)) {
        try {
            $transcriptText = ($Segments | ForEach-Object { $_.text }) -join ' '
            $transcriptText = ($transcriptText -replace '\s+', ' ').Trim()

            $payload = @{
                videoId        = $VideoId
                youtubeUrl     = $Url
                videoTitle     = $VideoTitle
                channelName    = $ChannelName
                channelHandle  = $ChannelHandle
                language       = 'en'
                transcriptText = $transcriptText
                segments       = @($Segments)
            } | ConvertTo-Json -Depth 4 -Compress

            $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
            [System.IO.File]::WriteAllText($SavePayloadPath, $payload, $utf8NoBom)

            $ext = [System.IO.Path]::GetExtension($archiveScript).ToLower()
            if ($ext -eq '.cjs' -or $ext -eq '.js' -or $ext -eq '.mjs') {
                $saveResult = & cmd /c "node `"$archiveScript`" < `"$SavePayloadPath`"" 2>&1
            } else {
                $saveResult = & cmd /c "python `"$archiveScript`" < `"$SavePayloadPath`"" 2>&1
            }
            $parsed = $saveResult | ConvertFrom-Json -ErrorAction Stop

            if ($parsed.ok) {
                $archiveStatus = "Saved via LITEYT_ARCHIVE_SCRIPT"
                Write-Host "Archive: $archiveStatus" -ForegroundColor Green
            } else {
                $archiveStatus = "Save failed: $($parsed.error)"
                Write-Host "Archive: $archiveStatus" -ForegroundColor Yellow
            }
        } catch {
            $archiveStatus = "Save skipped: $($_.Exception.Message.Substring(0, [math]::Min($_.Exception.Message.Length, 100)))"
            Write-Host "Archive: $archiveStatus" -ForegroundColor Yellow
        }
    }

    # ── 5b. Save to LiteYT (best-effort, requires LiteSuite installed) ────────

    $liteytStatus = 'Not saved'
    try {
        # LiteYT v2.0 uses %APPDATA%/liteyt/, v1 used %APPDATA%/lite-yt-transcribe/
        $liteytDbPath = Join-Path (Join-Path $env:APPDATA 'liteyt') 'lite_yt_transcribe.sqlite'
        if (-not (Test-Path $liteytDbPath)) {
            $liteytDbPath = Join-Path (Join-Path $env:APPDATA 'lite-yt-transcribe') 'lite_yt_transcribe.sqlite'
        }

        if (Test-Path $liteytDbPath) {
            $transcriptText = ($Segments | ForEach-Object { $_.text }) -join ' '
            $transcriptText = ($transcriptText -replace '\s+', ' ').Trim()
            $segmentsJson = ($Segments | ConvertTo-Json -Depth 3 -Compress)

            # Write SQL to temp file (avoids quoting nightmares)
            $escapedTitle = $VideoTitle -replace "'", "''"
            $escapedText = $transcriptText -replace "'", "''"
            $escapedSegments = $segmentsJson -replace "'", "''"
            $escapedUrl = $Url -replace "'", "''"

            $sql = @"
INSERT OR IGNORE INTO transcripts (video_id, youtube_url, video_title, language, transcript_text, segments_json, created_at)
VALUES ('$VideoId', '$escapedUrl', '$escapedTitle', 'en', '$escapedText', '$escapedSegments', datetime('now'));
"@
            $sqlFile = Join-Path $TempDir "yt-liteyt-$VideoId.sql"
            $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
            [System.IO.File]::WriteAllText($sqlFile, $sql, $utf8NoBom)

            # Write a small Python helper script (avoids PowerShell argument quoting issues)
            $pyHelper = Join-Path $TempDir "yt-liteyt-$VideoId.py"
            $pyCode = @"
import sqlite3, sys
db_path = sys.argv[1]
sql_path = sys.argv[2]
conn = sqlite3.connect(db_path)
with open(sql_path, 'r', encoding='utf-8') as f:
    conn.executescript(f.read())
conn.close()
print('ok')
"@
            [System.IO.File]::WriteAllText($pyHelper, $pyCode, $utf8NoBom)

            $result = & python $pyHelper $liteytDbPath $sqlFile 2>&1
            if ($result -match 'ok') {
                $liteytStatus = "Saved to LiteYT"
                Write-Host "LiteYT: $liteytStatus" -ForegroundColor Green
            } else {
                $liteytStatus = "Save failed: $result"
                Write-Host "LiteYT: $liteytStatus" -ForegroundColor Yellow
            }

            Remove-Item $sqlFile -Force -ErrorAction SilentlyContinue
            Remove-Item $pyHelper -Force -ErrorAction SilentlyContinue
        } else {
            $liteytStatus = "Skipped (LiteYT DB not found)"
        }
    } catch {
        $liteytStatus = "Save skipped: $($_.Exception.Message.Substring(0, [math]::Min($_.Exception.Message.Length, 80)))"
        Write-Host "LiteYT: $liteytStatus" -ForegroundColor Yellow
    }

    # ── 5c. Save to LiteSuite YouTube panel (best-effort) ───────────────────
    # LiteSuite's YouTube panel reads %APPDATA%/litesuite/yt.db (== %APPDATA%/LiteSuite
    # on case-insensitive Windows), table yt_transcripts. Schema below mirrors
    # apps/desktop/src/litesuite/services/youtube/db.ts so a grabbed transcript shows
    # up in the panel's Library tab. FTS is kept in sync by the AFTER INSERT trigger.
    # Insert is idempotent on video_id (that column has no UNIQUE constraint).

    $litesuiteStatus = 'Not saved'
    try {
        $litesuiteDbDir  = Join-Path $env:APPDATA 'litesuite'
        $litesuiteDbPath = Join-Path $litesuiteDbDir 'yt.db'
        if (-not (Test-Path $litesuiteDbDir)) {
            New-Item -ItemType Directory -Path $litesuiteDbDir -Force | Out-Null
        }

        $transcriptText = ($Segments | ForEach-Object { $_.text }) -join ' '
        $transcriptText = ($transcriptText -replace '\s+', ' ').Trim()
        $segmentsJson = ($Segments | ConvertTo-Json -Depth 3 -Compress)

        $escapedTitle = $VideoTitle -replace "'", "''"
        $escapedText = $transcriptText -replace "'", "''"
        $escapedSegments = $segmentsJson -replace "'", "''"
        $escapedUrl = $Url -replace "'", "''"

        $sql = @"
CREATE TABLE IF NOT EXISTS yt_transcripts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id        TEXT NOT NULL,
  youtube_url     TEXT NOT NULL,
  video_title     TEXT,
  language        TEXT,
  transcript_text TEXT NOT NULL,
  segments_json   TEXT NOT NULL,
  summary_text    TEXT,
  batch_job_id    INTEGER,
  created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE VIRTUAL TABLE IF NOT EXISTS yt_transcripts_fts USING fts5(
  video_title, transcript_text, content=yt_transcripts, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS yt_transcripts_ai AFTER INSERT ON yt_transcripts BEGIN
  INSERT INTO yt_transcripts_fts(rowid, video_title, transcript_text) VALUES (new.id, new.video_title, new.transcript_text);
END;
CREATE TRIGGER IF NOT EXISTS yt_transcripts_ad AFTER DELETE ON yt_transcripts BEGIN
  INSERT INTO yt_transcripts_fts(yt_transcripts_fts, rowid, video_title, transcript_text) VALUES('delete', old.id, old.video_title, old.transcript_text);
END;
CREATE TRIGGER IF NOT EXISTS yt_transcripts_au AFTER UPDATE ON yt_transcripts BEGIN
  INSERT INTO yt_transcripts_fts(yt_transcripts_fts, rowid, video_title, transcript_text) VALUES('delete', old.id, old.video_title, old.transcript_text);
  INSERT INTO yt_transcripts_fts(rowid, video_title, transcript_text) VALUES (new.id, new.video_title, new.transcript_text);
END;
INSERT INTO yt_transcripts (video_id, youtube_url, video_title, language, transcript_text, segments_json, created_at)
SELECT '$VideoId', '$escapedUrl', '$escapedTitle', 'en', '$escapedText', '$escapedSegments', datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM yt_transcripts WHERE video_id = '$VideoId');
"@
        $lsSqlFile = Join-Path $TempDir "yt-litesuite-$VideoId.sql"
        $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($lsSqlFile, $sql, $utf8NoBom)

        $lsPyHelper = Join-Path $TempDir "yt-litesuite-$VideoId.py"
        $lsPyCode = @"
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
try:
    conn.execute('PRAGMA journal_mode=WAL')
except Exception:
    pass
with open(sys.argv[2], 'r', encoding='utf-8') as f:
    conn.executescript(f.read())
changed = conn.total_changes
conn.close()
print('inserted' if changed else 'exists')
"@
        [System.IO.File]::WriteAllText($lsPyHelper, $lsPyCode, $utf8NoBom)

        $lsResult = & python $lsPyHelper $litesuiteDbPath $lsSqlFile 2>&1
        if ($lsResult -match 'inserted') {
            $litesuiteStatus = "Saved to LiteSuite YouTube panel"
            Write-Host "LiteSuite: $litesuiteStatus" -ForegroundColor Green
        } elseif ($lsResult -match 'exists') {
            $litesuiteStatus = "Already in LiteSuite panel (skipped)"
            Write-Host "LiteSuite: $litesuiteStatus" -ForegroundColor Green
        } else {
            $litesuiteStatus = "Save failed: $lsResult"
            Write-Host "LiteSuite: $litesuiteStatus" -ForegroundColor Yellow
        }

        Remove-Item $lsSqlFile -Force -ErrorAction SilentlyContinue
        Remove-Item $lsPyHelper -Force -ErrorAction SilentlyContinue
    } catch {
        $litesuiteStatus = "Save skipped: $($_.Exception.Message.Substring(0, [math]::Min($_.Exception.Message.Length, 80)))"
        Write-Host "LiteSuite: $litesuiteStatus" -ForegroundColor Yellow
    }

    # ── 6. Build and output markdown ──────────────────────────────────────────

    $exportDate = Get-Date -Format 'yyyy-MM-dd HH:mm'
    $transcript = $Lines -join "`n`n"

    $Markdown = @"
# $ChannelName - $VideoTitle
**URL:** https://youtube.com/watch?v=$VideoId
**Channel:** $ChannelHandle
**Exported:** $exportDate
**Video:** $videoStatus
**Frames:** $frameStatus
**Archived:** $archiveStatus
**LiteYT:** $liteytStatus
**LiteSuite Panel:** $litesuiteStatus

---

$transcript
"@

    if ($OutputPath) {
        $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($OutputPath, $Markdown, $utf8NoBom)
        Write-Host "Written to: $OutputPath" -ForegroundColor Cyan
    }

    Write-Output $Markdown

} finally {
    # ── 7. Cleanup ────────────────────────────────────────────────────────────
    Remove-Item "$SubFileBase*.json3" -Force -ErrorAction SilentlyContinue
    Remove-Item $SavePayloadPath -Force -ErrorAction SilentlyContinue
}
