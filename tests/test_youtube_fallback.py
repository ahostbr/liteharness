"""Offline tests at the real PowerShell/Python script boundaries. No live services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "liteharness/catalog/skills/ls-youtube"
SHELLS = [path for name in ("powershell", "pwsh") if (path := shutil.which(name))]

# This is only the external caption package boundary; both shipped scripts run.
PACKAGE = '''
import os
from types import SimpleNamespace
class NoTranscriptFound(Exception): pass
class TranscriptsDisabled(Exception): pass
class Track:
    language_code = "de"
    def fetch(self):
        mode = os.environ["CAPTION_MODE"]
        if mode == "fetch_error": raise RuntimeError("HTTP 429 alternate route refused")
        if mode == "fetch_no_transcript": raise NoTranscriptFound("fetch error, not catalog absence")
        if mode == "empty": return []
        if mode == "bad_timing": return [SimpleNamespace(text="bad", start=float("nan"), duration=1)]
        return [SimpleNamespace(text="Hello café\\nworld's $value; & literal", start=65.9, duration=2.25),
                SimpleNamespace(text="Hello café\\nworld's $value; & literal", start=67, duration=1),
                SimpleNamespace(text="Later", start=3661, duration=1)]
class Catalog:
    def __iter__(self): return iter([Track()])
    def find_transcript(self, languages):
        assert languages == ["en", "en-US", "en-GB"]
        if os.environ["CAPTION_MODE"] == "no_english": raise NoTranscriptFound("no English")
        return Track()
class YouTubeTranscriptApi:
    def list(self, video_id):
        assert video_id == "fg3LR1UHI1c"
        with open(os.environ["CALL_LOG"], "a") as f: f.write("api-list\\n")
        if os.environ["CAPTION_MODE"] == "list_error": raise RuntimeError("network timeout")
        if os.environ["CAPTION_MODE"] == "disabled": raise TranscriptsDisabled("disabled, not proven absent")
        return Catalog()
'''


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(params=SHELLS or [None], ids=lambda p: Path(p).stem if p else "no-powershell")
def shell(request):
    if request.param is None:
        pytest.skip("PowerShell is required for script boundary tests")
    return request.param


@pytest.fixture
def run_skill(tmp_path, shell):
    # Deliberately exercise spaces, apostrophes and shell metacharacters in paths.
    sandbox = tmp_path / "space's & literal"
    sandbox.mkdir()
    package = sandbox / "youtube_transcript_api"
    package.mkdir()
    (package / "__init__.py").write_text(PACKAGE, encoding="utf-8")
    output = sandbox / "transcript's & result.md"
    log = sandbox / "calls.txt"
    wrapper = sandbox / "run.ps1"
    wrapper.write_text('''
function yt-dlp {
    Add-Content -LiteralPath $env:CALL_LOG -Value ('yt-dlp ' + ($args -join ' '))
    if ($args -contains '--print') {
        if ($env:META_FAIL -eq '1') {
            Write-Error 'ERROR: metadata unavailable'; $global:LASTEXITCODE = 1; return
        }
        'Title'; 'Channel'; 'fg3LR1UHI1c'; 'https://youtube.com/@channel'
        $global:LASTEXITCODE = 0; return
    }
    if ($args -contains '--write-auto-sub') {
        $base = $args[([array]::IndexOf($args, '-o') + 1)]
        if ($env:SUB_MODE -in @('ok', 'partial', 'invalid', 'empty')) {
            $json = if ($env:SUB_MODE -eq 'invalid') { 'invalid json' }
                    elseif ($env:SUB_MODE -eq 'empty') { '{"events":[]}' }
                    else { '{"events":[{"tStartMs":65000,"dDurationMs":2250,"segs":[{"utf8":"Primary caption"}]}]}' }
            [IO.File]::WriteAllText("$base.en.json3", $json)
        }
        if ($env:SUB_MODE -in @('ok', 'nofile', 'invalid', 'empty')) {
            $global:LASTEXITCODE = 0; return
        }
        Write-Error 'ERROR: Unable to download video subtitles: HTTP Error 429: Too Many Requests'
        $global:LASTEXITCODE = 1; return
    }
    # Media download remains best-effort.
    Write-Error 'ERROR: video download refused'; $global:LASTEXITCODE = 1
}
function ffmpeg {
    Add-Content -LiteralPath $env:CALL_LOG -Value 'ffmpeg'
    $target = $args[-1] -replace '%05d', '00001'
    [IO.File]::WriteAllText($target, 'mock frame')
    $global:LASTEXITCODE = 0
}
function python {
    Add-Content -LiteralPath $env:CALL_LOG -Value ('python ' + ($args -join ' '))
    & ''' + ps_quote(sys.executable) + ''' @args
    $global:LASTEXITCODE = $LASTEXITCODE
}
& ''' + ps_quote(SKILL / "Get-YouTube.ps1") + ''' @args
exit $LASTEXITCODE
''', encoding="utf-8-sig")

    def run(sub="fail", api="ok", media=False, frames=False, metadata_fail=False):
        output.unlink(missing_ok=True)
        log.unlink(missing_ok=True)
        env = os.environ.copy()
        env.update(TEMP=str(sandbox), TMP=str(sandbox), APPDATA=str(sandbox / "appdata"),
                   PYTHONPATH=str(sandbox), PYTHONIOENCODING="utf-8", CAPTION_MODE=api,
                   CALL_LOG=str(log), SUB_MODE=sub, META_FAIL="1" if metadata_fail else "0",
                   LITEYT_ARCHIVE_SCRIPT="", YT_DLP_JS_RUNTIME="node")
        if api == "missing":
            (package / "__init__.py").write_text(
                "raise ModuleNotFoundError(\"No module named 'youtube_transcript_api'\", name='youtube_transcript_api')",
                encoding="utf-8")
        args = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
                "-Url", "https://youtu.be/fg3LR1UHI1c", "-OutputPath", str(output),
                "-MediaRoot", str(sandbox / "media")]
        if not media:
            args.append("-NoVideo")
        if not frames:
            args.append("-NoFrames")
        result = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=30)
        text = result.stdout + result.stderr
        markdown = output.read_text(encoding="utf-8") if output.exists() else ""
        calls = log.read_text(encoding="utf-8-sig") if log.exists() else ""
        return result.returncode, text, markdown, calls, sandbox
    return run


def test_primary_success_never_needs_optional_package(run_skill):
    code, text, md, calls, folder = run_skill(sub="ok", api="missing")
    assert code == 0, text
    assert "**Transcript route:** yt-dlp (JSON3 subtitles)" in md
    assert "**01:05** Primary caption" in md
    assert "Get-Transcript.py" not in calls
    assert not list(folder.glob("yt-transcript-*.json3"))


@pytest.mark.parametrize("sub", ["fail", "partial", "nofile", "invalid", "empty"])
def test_fallback_uses_shared_renderer_and_standalone_archive(run_skill, sub):
    code, text, md, calls, folder = run_skill(sub=sub)
    assert code == 0, text
    assert any(cause in text for cause in ("HTTP Error 429", "no subtitle file produced", "Subtitle data FAILED"))
    assert "**Transcript route:** youtube-transcript-api (standalone Python captions)" in md
    assert "**01:05** Hello café world's $value; & literal" in md
    assert "**01:01:01** Later" in md
    assert md.count("Hello café") == 1
    assert "Primary caption" not in md
    assert calls.count("api-list") == 1
    assert not list(folder.glob("yt-transcript-*.json3"))
    # Existing best-effort DB sync needs no running Suite; isolate it to test APPDATA.
    import sqlite3
    with sqlite3.connect(folder / "appdata/litesuite/yt.db") as conn:
        row = conn.execute("SELECT segments_json FROM yt_transcripts").fetchone()
    segments = json.loads(row[0])
    assert segments[0]["offset"] == 65
    assert segments[0]["duration"] == 2.25


@pytest.mark.parametrize("sub", ["fail", "nofile"])
def test_only_catalog_confirmed_no_english_is_exit2(run_skill, sub):
    code, text, md, calls, folder = run_skill(sub=sub, api="no_english")
    assert code == 2, text
    assert "catalog confirmed" in text
    assert "available languages: de" in text
    assert md == ""
    assert calls.count("api-list") == 1


@pytest.mark.parametrize("api,diagnostic", [
    ("list_error", "network timeout"), ("fetch_error", "alternate route refused"),
    ("fetch_no_transcript", "fetch error, not catalog absence"),
    ("disabled", "TranscriptsDisabled"), ("empty", "no usable caption text"),
    ("bad_timing", "invalid caption timing"), ("missing", "not installed for"),
])
def test_all_routes_failed_are_exit3_with_diagnostics(run_skill, api, diagnostic):
    code, text, md, calls, folder = run_skill(api=api)
    text = ' '.join(text.split())  # Windows PowerShell wraps Write-Error to console width.
    assert code == 3, text
    assert "FAILED on all routes" in text
    assert "HTTP Error 429" in text
    assert diagnostic in text
    assert "catalog confirmed" not in text
    assert md == ""
    if api == "missing":
        assert text.count("python -m pip install youtube-transcript-api") == 1
    assert not list(folder.glob("yt-transcript-*.json3"))


def test_missing_file_does_not_turn_network_failure_into_no_english(run_skill):
    code, text, md, calls, folder = run_skill(sub="nofile", api="list_error")
    assert code == 3, text
    assert "no subtitle file produced" in text
    assert "network timeout" in text


@pytest.mark.parametrize("sub", ["invalid", "empty"])
def test_unusable_primary_data_is_failure_not_absence(run_skill, sub):
    code, text, md, calls, folder = run_skill(sub=sub, api="list_error")
    assert code == 3, text
    assert "Subtitle data FAILED" in text
    assert "network timeout" in text
    assert md == ""


def test_metadata_failure_preserves_exit1_and_does_not_fetch(run_skill):
    code, text, md, calls, folder = run_skill(metadata_fail=True)
    assert code == 1, text
    assert "metadata unavailable" in text
    assert "--write-auto-sub" not in calls
    assert "Get-Transcript.py" not in calls


def test_video_failure_remains_best_effort_and_no_frames(run_skill):
    code, text, md, calls, folder = run_skill(media=True)
    assert code == 0, text
    assert "**Video:** Download failed" in md
    assert "**Frames:** Skipped (-NoFrames)" in md
    assert "ffmpeg\n" not in calls


def test_no_video_can_still_extract_existing_video_frames(run_skill):
    # Obtain the sandbox without network then seed an existing media file.
    code, text, md, calls, folder = run_skill(sub="ok")
    media = folder / "media/fg3LR1UHI1c"
    media.mkdir(parents=True)
    (media / "video.mp4").write_bytes(b"mock video")
    code, text, md, calls, folder = run_skill(sub="ok", frames=True)
    assert code == 0, text
    assert "**Video:** Skipped (-NoVideo)" in md
    assert "**Frames:** 1 frame(s)" in md
    assert "ffmpeg" in calls
