#!/usr/bin/env python
"""Speak one message aloud. One-off, fire-and-forget, agent-facing.

This is the SPEAK-ONCE path and it is deliberately INDEPENDENT of TTS mode.
It never reads the mode flag and never writes it. An agent that wants to tell
Ryan one thing calls this; whether "speak every response" mode happens to be on
is a different question with a different owner (ttsmode.py).

    python speak.py "the build finished"
    echo "long text with 'quotes' and $vars" | python speak.py
    python speak.py --style alert "deploy failed on main"

Returns immediately by default: synthesis and playback happen in a detached
child so the caller's shell is never held and nothing is written to its console.
Pass --wait to block until the audio finishes.

Concurrent callers queue on a lock rather than talking over each other.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

VOICE_DEFAULT = "en-GB-SoniaNeural"

# Presets differ only in delivery, never in whether the message is spoken.
STYLES = {
    "info":   {"rate": "+0%"},
    "alert":  {"rate": "+8%"},
    "urgent": {"rate": "+18%"},
}

MAX_CHARS = 600          # speech beyond this is a document, not an alert
LOCK_TIMEOUT_S = 90.0    # give a queued speaker this long before giving up
LOCK_STALE_S = 180.0     # a lock older than this had its owner die

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "liteharness"
LOCK_PATH = STATE_DIR / "tts_speak.lock"


def _fail(msg: str, code: int = 1) -> "int":
    sys.stderr.write("speak: %s\n" % msg)
    return code


def _resolve_text(args) -> str:
    """Argv first, then stdin. Stdin is the quoting-proof path — prefer it for
    anything containing quotes, newlines, or shell metacharacters."""
    if args.text:
        text = " ".join(args.text)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        return ""
    return " ".join(text.split()).strip()


# --------------------------------------------------------------------------
# lock: queue concurrent speakers instead of letting them overlap
# --------------------------------------------------------------------------

def _acquire_lock(timeout: float = LOCK_TIMEOUT_S):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            return fd
        except FileExistsError:
            # Reclaim a lock whose owner died without releasing it.
            try:
                if time.time() - LOCK_PATH.stat().st_mtime > LOCK_STALE_S:
                    LOCK_PATH.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                return None
            time.sleep(0.25)


def _release_lock(fd) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    LOCK_PATH.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# synthesis + playback
# --------------------------------------------------------------------------

def _synthesize(text: str, out: Path, voice: str, rate: str) -> "str|None":
    """Returns None on success, else an error string."""
    try:
        import asyncio
        import edge_tts
    except ImportError:
        return "edge-tts is not installed (pip install edge-tts)"

    async def run():
        comm = edge_tts.Communicate(text, voice=voice, rate=rate)
        await comm.save(str(out))

    try:
        asyncio.run(run())
    except Exception as exc:                       # network, bad voice, etc.
        return "synthesis failed: %s: %s" % (type(exc).__name__, exc)

    if not out.exists() or out.stat().st_size == 0:
        return "synthesis produced no audio"
    return None


def _play(path: Path) -> "str|None":
    """ffplay first — it is present on this machine and, unlike playsound,
    does not wedge on Windows. Falls back to playsound, then to PowerShell's
    MediaPlayer so a missing ffmpeg is degraded rather than fatal."""
    import shutil

    ffplay = shutil.which("ffplay")
    if ffplay:
        rc = subprocess.call(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return None if rc == 0 else "ffplay exited %d" % rc

    try:
        from playsound import playsound
        playsound(str(path), block=True)
        return None
    except Exception:
        pass

    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps:
        script = (
            "Add-Type -AssemblyName PresentationCore;"
            "$p = New-Object System.Windows.Media.MediaPlayer;"
            "$p.Open([uri]'%s');"
            "Start-Sleep -Milliseconds 400;"
            "$p.Play();"
            "while ($p.NaturalDuration.HasTimeSpan -eq $false) { Start-Sleep -Milliseconds 100 };"
            "Start-Sleep -Seconds $p.NaturalDuration.TimeSpan.TotalSeconds;"
            "$p.Close()" % str(path).replace("\\", "/")
        )
        rc = subprocess.call([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None if rc == 0 else "powershell playback exited %d" % rc

    return "no audio player found (install ffmpeg, or pip install playsound)"


def _speak_now(text: str, voice: str, rate: str) -> int:
    lock = _acquire_lock()
    if lock is None:
        return _fail("another message is still speaking; gave up after %.0fs" % LOCK_TIMEOUT_S, 2)

    out = Path(tempfile.gettempdir()) / ("lh_tts_%s.mp3" % uuid.uuid4().hex[:12])
    try:
        err = _synthesize(text, out, voice, rate)
        if err:
            return _fail(err, 3)
        err = _play(out)
        if err:
            return _fail(err, 4)
        return 0
    finally:
        out.unlink(missing_ok=True)
        _release_lock(lock)


def _spawn_detached(text: str, voice: str, rate: str) -> int:
    """Hand the work to a detached child and return at once.

    The text goes through a FILE, never the command line: it is arbitrary prose
    and putting it in an argv string is how quoting bugs get in.
    """
    payload = Path(tempfile.gettempdir()) / ("lh_tts_%s.txt" % uuid.uuid4().hex[:12])
    payload.write_text(text, encoding="utf-8")

    cmd = [sys.executable, os.path.abspath(__file__),
           "--_worker", str(payload), "--voice", voice, "--rate", rate]

    kwargs = dict(stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL,
                  close_fds=True)
    if os.name == "nt":
        # Detached + no window: the child must not inherit or touch this console.
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        payload.unlink(missing_ok=True)
        return _fail("could not spawn speaker: %s" % exc, 5)
    return 0


def _check() -> int:
    import shutil
    ok = True
    try:
        import edge_tts  # noqa: F401
        print("edge-tts    OK")
    except ImportError:
        print("edge-tts    MISSING   pip install edge-tts")
        ok = False

    player = shutil.which("ffplay")
    if player:
        print("player      OK        ffplay")
    else:
        try:
            import playsound  # noqa: F401
            print("player      OK        playsound (ffplay preferred; install ffmpeg)")
        except ImportError:
            print("player      MISSING   install ffmpeg, or pip install playsound")
            ok = False

    print("state dir   %s" % STATE_DIR)
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(
        prog="speak.py",
        description="Speak one message aloud. Independent of TTS mode.")
    p.add_argument("text", nargs="*", help="message; omit to read stdin")
    p.add_argument("--style", choices=sorted(STYLES), default="info",
                   help="delivery preset (default: info)")
    p.add_argument("--voice", default=VOICE_DEFAULT)
    p.add_argument("--rate", default=None, help="override the style's rate, e.g. +10%%")
    p.add_argument("--wait", action="store_true",
                   help="block until the audio finishes (default: return immediately)")
    p.add_argument("--check", action="store_true", help="verify setup and exit")
    p.add_argument("--_worker", metavar="PAYLOAD", help=argparse.SUPPRESS)
    args = p.parse_args()

    if args.check:
        return _check()

    rate = args.rate or STYLES[args.style]["rate"]

    # Detached child: read the payload file, speak it, clean up.
    if args._worker:
        payload = Path(args._worker)
        try:
            text = payload.read_text(encoding="utf-8")
        except OSError as exc:
            return _fail("worker could not read payload: %s" % exc, 6)
        finally:
            payload.unlink(missing_ok=True)
        return _speak_now(text, args.voice, rate)

    text = _resolve_text(args)
    if not text:
        return _fail("no text given (pass it as an argument or on stdin)", 64)

    if len(text) > MAX_CHARS:
        sys.stderr.write("speak: text is %d chars; speaking the first %d. "
                         "TTS is for alerts, not documents.\n" % (len(text), MAX_CHARS))
        text = text[:MAX_CHARS].rsplit(" ", 1)[0]

    if args.wait:
        return _speak_now(text, args.voice, rate)
    return _spawn_detached(text, args.voice, rate)


if __name__ == "__main__":
    sys.exit(main())
