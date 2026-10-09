"""Standalone caption fallback; stdout is diagnostics, output file is JSON3.

Uses the same youtube-transcript-api route as Suite, without importing Suite.
Exit 2 requires a successfully retrieved catalog with no requested English track.
All import, network, refusal, disabled-captions and invalid-data errors exit 3.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys


def main() -> int:
    if len(sys.argv) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]{11}", sys.argv[1]):
        print("youtube-transcript-api: expected video ID and output file")
        return 3

    video_id, output = sys.argv[1:]
    try:
        from youtube_transcript_api import NoTranscriptFound, YouTubeTranscriptApi
    except ModuleNotFoundError as exc:
        if exc.name == "youtube_transcript_api":
            print(
                f"youtube-transcript-api not installed for {sys.executable}.\n"
                "Install with: python -m pip install youtube-transcript-api"
            )
        else:
            print(f"youtube-transcript-api import FAILED: {type(exc).__name__}: {exc}")
        return 3
    except Exception as exc:
        print(f"youtube-transcript-api import FAILED: {type(exc).__name__}: {exc}")
        return 3

    try:
        # fetch() in the inspected package is list().find_transcript().fetch().
        # Split that shortcut only to scope NoTranscriptFound to catalog selection,
        # never to a network failure while fetching an existing track.
        catalog = YouTubeTranscriptApi().list(video_id)
        try:
            track = catalog.find_transcript(["en", "en-US", "en-GB"])
        except NoTranscriptFound:
            languages = ", ".join(item.language_code for item in catalog) or "none"
            print(
                "youtube-transcript-api: no English captions in retrieved catalog; "
                f"available languages: {languages}"
            )
            return 2

        events = []
        for entry in track.fetch():
            text = entry.text.strip()
            if not text:
                continue
            start, duration = float(entry.start), float(entry.duration)
            if not all(math.isfinite(value) and value >= 0 for value in (start, duration)):
                raise ValueError("invalid caption timing")
            events.append({
                "tStartMs": round(start * 1000),
                "dDurationMs": round(duration * 1000),
                "segs": [{"utf8": text}],
            })
        if not events:
            raise ValueError("English track returned no usable caption text")
        Path(output).write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"youtube-transcript-api fetch FAILED: {type(exc).__name__}: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
