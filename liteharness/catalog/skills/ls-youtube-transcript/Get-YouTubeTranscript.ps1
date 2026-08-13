# Get-YouTubeTranscript.ps1 — Full pipeline: metadata, subtitles, parse, DB save, output
param(
    [Parameter(Mandatory)]
    [string]$Url,
    [string]$OutputPath
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

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

try {
    # ── 2. Fetch metadata ─────────────────────────────────────────────────────

    $metaOutput = & yt-dlp --print title --print channel --print id --print uploader_url --skip-download $Url 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "yt-dlp failed to fetch metadata (exit code $LASTEXITCODE). Video may be unavailable or private."
        exit 1
    }

    $metaLines = $metaOutput -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
    if ($metaLines.Count -lt 4) {
        Write-Error "yt-dlp returned incomplete metadata ($($metaLines.Count) lines, expected 4)."
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

    # ── 3. Download subtitles ─────────────────────────────────────────────────

    & yt-dlp --write-auto-sub --write-sub --sub-lang "en,en-US,en-GB" --skip-download --sub-format json3 -o "$SubFileBase" $Url 2>$null

    # Glob for the subtitle file (yt-dlp appends .en.json3, .en-US.json3, etc.)
    $SubFile = Get-ChildItem "$SubFileBase*.json3" -ErrorAction SilentlyContinue | Select-Object -First 1

    if (-not $SubFile) {
        Write-Error "No subtitle file found. This video may not have English subtitles."
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
