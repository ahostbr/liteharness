"""
Edge TTS backend for LiteHarness.

Uses Microsoft Edge's free TTS service via edge-tts package.
High quality neural voices, no API key required.

This backend is called by smart_tts.py. It can also be used standalone:
  python -m liteharness.tts.utils.edge_tts "Your text here"
  python -m liteharness.tts.utils.edge_tts "Your text here" --voice en-US-JennyNeural
"""

import argparse
import os
import subprocess
import sys
import tempfile


def get_uv_path() -> str:
    """Get UV executable path — checks env var first, then well-known location."""
    if os.environ.get("UV_PATH"):
        return os.environ["UV_PATH"]
    if sys.platform == "win32":
        return os.path.join(os.path.expanduser("~"), ".local", "bin", "uv.exe")
    return "uv"


def speak(text: str, voice: str = "en-GB-SoniaNeural") -> bool:
    """Generate and play TTS audio using Edge TTS CLI."""
    try:
        from .tts_queue import acquire_tts_lock, release_tts_lock
        use_queue = True
    except ImportError:
        use_queue = False

    agent_id = f"edge_tts_{os.getpid()}"

    if use_queue:
        if not acquire_tts_lock(agent_id, timeout=5):
            print("[EdgeTTS] Queue busy — playing anyway", file=sys.stderr)
            use_queue = False

    try:
        return _speak_internal(text, voice)
    finally:
        if use_queue:
            release_tts_lock(agent_id)


def _speak_internal(text: str, voice: str = "en-GB-SoniaNeural") -> bool:
    """Internal TTS implementation."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            uv_path = get_uv_path()

            result = subprocess.run(
                [
                    uv_path, "run", "--with", "edge-tts", "edge-tts",
                    "--voice", voice,
                    "--text", text,
                    "--write-media", tmp_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                print(f"[EdgeTTS] Generation error: {result.stderr}", file=sys.stderr)
                return False

            # Play using PowerShell MediaPlayer (Windows)
            ps_cmd = f"""
Add-Type -AssemblyName presentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open("{tmp_path}")
Start-Sleep -Milliseconds 300
$player.Play()
$duration = $player.NaturalDuration
if ($duration.HasTimeSpan) {{
    $ms = $duration.TimeSpan.TotalMilliseconds + 500
    Start-Sleep -Milliseconds $ms
}} else {{
    Start-Sleep -Seconds 5
}}
$player.Close()
"""
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                timeout=30,
            )
            return True
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(f"[EdgeTTS] Error: {e}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Edge TTS backend")
    parser.add_argument("text", nargs="?", default="Task complete!", help="Text to speak")
    parser.add_argument("--voice", "-v", default="en-GB-SoniaNeural", help="Voice name")
    args = parser.parse_args()
    speak(args.text, args.voice)


if __name__ == "__main__":
    main()
