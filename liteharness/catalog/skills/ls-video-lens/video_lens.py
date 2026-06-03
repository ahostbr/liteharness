#!/usr/bin/env python3
"""
video-lens — YouTube Video Frame Indexer

Downloads a YouTube video, extracts frames at configurable intervals,
and uses a VLM (LM Studio or Claude API) to caption and index every frame.

Usage:
    python video_lens.py <youtube_url> [--interval 5] [--backend lmstudio|claude]
    python video_lens.py <youtube_url> --interval 1 --backend lmstudio --keep-frames
    python video_lens.py <youtube_url> --extract-only  # Just download + extract, no captioning
"""

import argparse
import subprocess
import json
import base64
import urllib.request
import tempfile
import os
import sys

# Fix Windows console encoding for Unicode output
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import re
import time
import shutil
from pathlib import Path
from datetime import timedelta

# ── Config ──────────────────────────────────────────────────────────────────

FFMPEG_PATH = os.environ.get(
    "FFMPEG_PATH",
    "ffmpeg",  # Override with FFMPEG_PATH env var if ffmpeg is not on PATH
)
LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234")

DEFAULT_PROMPT = (
    "You are a video frame analyst. Describe what you see in this video frame concisely. "
    "Focus on: people, actions, objects, text on screen, scene setting, and any notable visual elements. "
    "Be specific and factual. 2-4 sentences."
)


# ── Dependency Checks ──────────────────────────────────────────────────────

def check_dependencies():
    """Verify yt-dlp and ffmpeg are available."""
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: yt-dlp not found. Install: pip install yt-dlp")
        sys.exit(1)

    ffmpeg = _ffmpeg_cmd()
    try:
        subprocess.run([ffmpeg, "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print(f"ERROR: ffmpeg not found at '{ffmpeg}'")
        print("Set FFMPEG_PATH env var or install ffmpeg to PATH.")
        sys.exit(1)


def _ffmpeg_cmd() -> str:
    """Return the ffmpeg command — prefers PATH, falls back to FFMPEG_PATH."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    if Path(FFMPEG_PATH).is_file():
        return FFMPEG_PATH
    return "ffmpeg"


# ── Download ────────────────────────────────────────────────────────────────

def download_video(url: str, output_dir: Path) -> tuple[Path, dict]:
    """Download video with yt-dlp, return (video_path, metadata)."""
    # Get metadata
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-download", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to get video metadata: {result.stderr[:300]}")
        sys.exit(1)

    metadata = json.loads(result.stdout)
    title = metadata.get("title", "unknown")
    duration = metadata.get("duration", 0)

    print(f"  Title:    {title}")
    print(f"  Channel:  {metadata.get('channel', '?')}")
    print(f"  Duration: {timedelta(seconds=int(duration))}")

    # Download — prefer mp4, merge with our ffmpeg
    output_template = str(output_dir / "video.%(ext)s")
    dl_cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", output_template,
        "--no-playlist",
        "--ffmpeg-location", str(Path(_ffmpeg_cmd()).parent),
        url,
    ]

    print("  Downloading...")
    result = subprocess.run(dl_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: Download failed: {result.stderr[:500]}")
        sys.exit(1)

    video_files = sorted(output_dir.glob("video.*"))
    if not video_files:
        print("ERROR: No video file found after download")
        sys.exit(1)

    return video_files[0], metadata


# ── Frame Extraction ───────────────────────────────────────────────────────

def extract_frames(video_path: Path, output_dir: Path, interval: float) -> list[Path]:
    """Extract frames at given interval using ffmpeg. Returns sorted frame paths."""
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    fps = 1.0 / interval
    cmd = [
        _ffmpeg_cmd(),
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(frames_dir / "frame_%06d.jpg"),
        "-y",
    ]

    print(f"  Extracting frames (1 every {interval}s)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: Frame extraction failed: {result.stderr[:500]}")
        sys.exit(1)

    frames = sorted(frames_dir.glob("frame_*.jpg"))
    print(f"  Extracted {len(frames)} frames")
    return frames


# ── VLM Captioning — LM Studio ─────────────────────────────────────────────

def _find_loaded_vlm(api_base: str, preferred: str = "") -> str:
    """Auto-detect a loaded VLM model via LM Studio v0 API. Prefers specified model."""
    try:
        v0_url = f"{api_base}/api/v0/models"
        resp = json.loads(urllib.request.urlopen(v0_url, timeout=5).read())
        loaded_vlms = [
            m["id"] for m in resp.get("data", [])
            if m.get("state") == "loaded" and m.get("type") == "vlm"
        ]
        if not loaded_vlms:
            raise RuntimeError("No VLM loaded in LM Studio. Load a vision model first.")
        if preferred:
            for vid in loaded_vlms:
                if preferred.lower() in vid.lower() or vid.lower() in preferred.lower():
                    return vid
        return loaded_vlms[0]
    except RuntimeError:
        raise
    except Exception:
        pass
    raise RuntimeError("No VLM loaded in LM Studio. Load a vision model first.")


def caption_lmstudio(image_path: Path, system_prompt: str, api_url: str, model_name: str = "") -> str:
    """Caption a frame using LM Studio VLM."""
    model = _find_loaded_vlm(api_url, preferred=model_name)

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this video frame."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            },
        ],
        "max_tokens": 300,
        "temperature": 0.1,
    }).encode()

    req = urllib.request.Request(
        f"{api_url}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    text = resp["choices"][0]["message"]["content"]

    # Strip thinking tags
    if "<think>" in text:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return text.strip()


# ── VLM Captioning — Claude API ─────────────────────────────────────────────

def caption_claude(image_path: Path, system_prompt: str) -> str:
    """Caption a frame using Claude API (requires ANTHROPIC_API_KEY)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set. Export it or use --backend lmstudio.")

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 300,
        "system": system_prompt,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                },
                {"type": "text", "text": "Describe this video frame."},
            ],
        }],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return resp["content"][0]["text"].strip()


# ── Index Builder ──────────────────────────────────────────────────────────

def build_index(
    frames: list[Path],
    metadata: dict,
    url: str,
    interval: float,
    backend: str,
    system_prompt: str,
    api_url: str,
    model_name: str = "",
) -> dict:
    """Caption all frames and build the index dict."""
    title = metadata.get("title", "video")
    duration = metadata.get("duration", 0)

    index = {
        "video": {
            "title": title,
            "url": url,
            "duration_seconds": duration,
            "duration": str(timedelta(seconds=int(duration))),
            "channel": metadata.get("channel", ""),
            "upload_date": metadata.get("upload_date", ""),
        },
        "settings": {
            "interval_seconds": interval,
            "backend": backend,
            "total_frames": len(frames),
        },
        "frames": [],
    }

    print(f"\n  Captioning {len(frames)} frames via {backend}...")
    errors = 0

    for i, frame_path in enumerate(frames):
        ts_sec = i * interval
        ts_human = str(timedelta(seconds=int(ts_sec)))

        print(f"  [{i + 1}/{len(frames)}] {ts_human} ", end="", flush=True)

        start = time.perf_counter()
        caption = None
        for attempt in range(3):
            try:
                if backend == "lmstudio":
                    caption = caption_lmstudio(frame_path, system_prompt, api_url, model_name=model_name)
                else:
                    caption = caption_claude(frame_path, system_prompt)
                elapsed = (time.perf_counter() - start) * 1000
                print(f"({elapsed:.0f}ms)")
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.5)  # Brief pause before retry
                else:
                    caption = f"[ERROR] {str(e)[:200]}"
                    elapsed = 0
                    errors += 1
                    print(f"FAILED: {e}")

        index["frames"].append({
            "index": i,
            "timestamp_seconds": ts_sec,
            "timestamp": ts_human,
            "caption": caption,
            "frame_file": frame_path.name,
        })

    if errors:
        print(f"\n  WARNING: {errors}/{len(frames)} frames failed captioning")

    return index


def save_outputs(index: dict, output_path: str):
    """Write JSON index and markdown summary."""
    # JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(f"\n  JSON index: {output_path}")

    # Markdown
    md_path = str(Path(output_path).with_suffix(".md"))
    vid = index["video"]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {vid['title']}\n\n")
        f.write(f"**URL:** {vid['url']}  \n")
        f.write(f"**Channel:** {vid['channel']}  \n")
        f.write(f"**Duration:** {vid['duration']}  \n")
        f.write(f"**Frame interval:** {index['settings']['interval_seconds']}s  \n")
        f.write(f"**Frames indexed:** {index['settings']['total_frames']}  \n\n")
        f.write("---\n\n")
        for frame in index["frames"]:
            f.write(f"## [{frame['timestamp']}] Frame {frame['index']}\n\n")
            f.write(f"{frame['caption']}\n\n")
    print(f"  Markdown:   {md_path}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download a YouTube video, extract frames, and caption them with a VLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python video_lens.py https://youtu.be/abc123 --interval 5
  python video_lens.py https://youtu.be/abc123 -i 1 -b claude --keep-frames
  python video_lens.py https://youtu.be/abc123 --extract-only -w ./my_frames
        """,
    )

    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "--interval", "-i", type=float, default=5.0,
        help="Seconds between frame captures (default: 5)",
    )
    parser.add_argument(
        "--backend", "-b", choices=["lmstudio", "claude"], default="lmstudio",
        help="VLM backend for captioning (default: lmstudio)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output JSON path (default: <video_title>_index.json)",
    )
    parser.add_argument(
        "--prompt", "-p", default=None,
        help="Custom system prompt for captioning",
    )
    parser.add_argument(
        "--extract-only", action="store_true",
        help="Download + extract frames only, skip captioning (for manual review)",
    )
    parser.add_argument(
        "--keep-frames", action="store_true",
        help="Keep extracted frame images after indexing",
    )
    parser.add_argument(
        "--workdir", "-w", default=None,
        help="Working directory for downloads/frames (default: temp dir)",
    )
    parser.add_argument(
        "--api-url", default=LMSTUDIO_URL,
        help=f"LM Studio API URL (default: {LMSTUDIO_URL})",
    )
    parser.add_argument(
        "--model", "-m", default="",
        help="LM Studio model name to use (e.g., 'devstral', 'glm-ocr'). Substring match. Default: auto-detect.",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("  video-lens — YouTube Frame Indexer")
    print("=" * 60)

    check_dependencies()

    system_prompt = args.prompt or DEFAULT_PROMPT

    # Working directory
    if args.workdir:
        work_dir = Path(args.workdir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="video_lens_"))
        cleanup = not args.keep_frames and not args.extract_only

    print(f"\n  Work dir: {work_dir}")

    try:
        # 1. Download
        print("\n[1/3] Downloading video...")
        video_path, metadata = download_video(args.url, work_dir)

        # 2. Extract frames
        print("\n[2/3] Extracting frames...")
        frames = extract_frames(video_path, work_dir, args.interval)

        if not frames:
            print("No frames extracted!")
            sys.exit(1)

        # 3. Caption (unless extract-only)
        if args.extract_only:
            print(f"\n[3/3] Extract-only mode — {len(frames)} frames in: {work_dir / 'frames'}")
            # Write a minimal index without captions
            title = metadata.get("title", "video")
            safe_title = re.sub(r'[^\w\s\-]', '', title)[:60].strip().replace(' ', '_')
            output_path = args.output or f"{safe_title}_frames.json"
            index = {
                "video": {
                    "title": title,
                    "url": args.url,
                    "duration_seconds": metadata.get("duration", 0),
                    "duration": str(timedelta(seconds=int(metadata.get("duration", 0)))),
                    "channel": metadata.get("channel", ""),
                },
                "settings": {
                    "interval_seconds": args.interval,
                    "total_frames": len(frames),
                    "frames_dir": str(work_dir / "frames"),
                },
                "frames": [
                    {
                        "index": i,
                        "timestamp_seconds": i * args.interval,
                        "timestamp": str(timedelta(seconds=int(i * args.interval))),
                        "frame_path": str(frame_path),
                    }
                    for i, frame_path in enumerate(frames)
                ],
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(index, f, indent=2, ensure_ascii=False)
            print(f"  Frame manifest: {output_path}")
        else:
            print("\n[3/3] Captioning frames...")
            index = build_index(
                frames, metadata, args.url, args.interval,
                args.backend, system_prompt, args.api_url,
                model_name=args.model,
            )

            title = metadata.get("title", "video")
            safe_title = re.sub(r'[^\w\s\-]', '', title)[:60].strip().replace(' ', '_')
            output_path = args.output or f"{safe_title}_index.json"

            save_outputs(index, output_path)

        # Copy frames out if keeping
        if args.keep_frames and cleanup:
            frames_out = Path.cwd() / "frames"
            frames_out.mkdir(exist_ok=True)
            for f in frames:
                shutil.copy2(f, frames_out / f.name)
            print(f"\n  Frames copied to: {frames_out}")

        print("\nDone!")

    finally:
        if cleanup and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
