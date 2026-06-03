"""
pyttsx3 TTS backend for LiteHarness.

Offline fallback TTS — no network, no API key, no dependencies beyond pyttsx3.
Useful when LiteSuite voice API and edge-tts are both unavailable.

Usage:
  python -m liteharness.tts.utils.pyttsx3_tts "Your text here"
"""

import sys


def speak(text: str) -> bool:
    """Speak text using pyttsx3 (offline, synchronous)."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 180)
        engine.setProperty("volume", 0.8)
        engine.say(text)
        engine.runAndWait()
        return True
    except ImportError:
        print("[pyttsx3] Package not installed. Run: uv pip install pyttsx3", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[pyttsx3] Error: {e}", file=sys.stderr)
        return False


def main() -> None:
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Task complete!"
    speak(text)


if __name__ == "__main__":
    main()
