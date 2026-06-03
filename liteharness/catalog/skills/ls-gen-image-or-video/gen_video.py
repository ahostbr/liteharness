#!/usr/bin/env python3
"""
AI video generation via Google Veo (Gemini API).

Setup:
    pip install google-genai python-dotenv
    # Set env var: GOOGLE_AI_API_KEY=your_key_here

Commands:
    generate (default):
        gen_video.py out.mp4 "A cinematic aerial shot of a coastal city"
        gen_video.py out.mp4 "Rotating 3D globe" --aspect 16:9 --duration 8 --res 1080p
        gen_video.py out.mp4 "Exploding view of a house" --model fast

    extend:
        gen_video.py extend out.mp4 --input clip.mp4 "Continue the camera pan"

    image-to-video:
        gen_video.py i2v out.mp4 --image hero.png "Animate this scene with parallax"
        gen_video.py i2v out.mp4 --image start.png --last-frame end.png "Interpolate"

Models: standard (Veo 3.1, default), fast (Veo 3.1 fast), veo2 (Veo 2)
Aspect ratios: 16:9 (default), 9:16
Resolutions: 720p (default), 1080p, 4k (duration must be 8 for 1080p/4k)
Durations: 4, 6, 8 seconds (default: 8)
"""

import argparse
import base64
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# ─── Models ──────────────────────────────────────────────────────────────────

MODELS = {
    "standard": "veo-3.1-generate-preview",
    "fast": "veo-3.1-fast-generate-preview",
    "veo2": "veo-2.0-generate-001",
}
DEFAULT_MODEL = "fast"  # good balance of speed/cost/quality

# ─── Pricing reference (per second of video) ────────────────────────────────
# standard 720p/1080p: $0.40/sec | 4k: $0.60/sec
# fast     720p/1080p: $0.15/sec | 4k: $0.35/sec
# veo2     720p:       $0.35/sec

# ─── Env ─────────────────────────────────────────────────────────────────────

def _load_env():
    """Load API key from env or known .env locations."""
    load_dotenv()
    for env_candidate in [
        Path(__file__).parent / ".env",
    ]:
        if env_candidate.exists():
            load_dotenv(env_candidate)
            break
    if not os.environ.get("GOOGLE_AI_API_KEY"):
        print("Error: GOOGLE_AI_API_KEY not set", file=sys.stderr)
        print("Set env var or create .env with: GOOGLE_AI_API_KEY=your_key_here")
        print("Get key: https://aistudio.google.com/apikey")
        sys.exit(1)


def _get_client() -> genai.Client:
    return genai.Client(api_key=os.environ.get("GOOGLE_AI_API_KEY"))


def _resolve_model(name: str) -> str:
    return MODELS.get(name, name)  # accept alias or raw model ID


def _estimate_cost(duration: int, model: str, resolution: str) -> str:
    """Estimate cost for a generation."""
    rates = {
        "standard": {"720p": 0.40, "1080p": 0.40, "4k": 0.60},
        "fast": {"720p": 0.15, "1080p": 0.15, "4k": 0.35},
        "veo2": {"720p": 0.35, "1080p": 0.35, "4k": 0.35},
    }
    rate = rates.get(model, rates["fast"]).get(resolution, 0.15)
    return f"~${rate * duration:.2f}"


def _load_media_bytes(path: Path, mime_type: str) -> dict:
    """Load a file as base64 inline data for the API."""
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"inlineData": {"mimeType": mime_type, "data": data}}


# ─── Generate ────────────────────────────────────────────────────────────────

def generate_video(
    prompt: str,
    output_path: Path,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration: int = 8,
    model: str = DEFAULT_MODEL,
    person_generation: str = "allow_all",
    num_videos: int = 1,
) -> None:
    """Generate a video from a text prompt."""
    client = _get_client()
    model_id = _resolve_model(model)

    cost = _estimate_cost(duration, model, resolution)
    print(f"Generating {duration}s video @ {resolution} with {model_id}")
    print(f"Estimated cost: {cost}")
    print(f"Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")

    operation = client.models.generate_videos(
        model=model_id,
        prompt=prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            duration_seconds=duration,
            person_generation=person_generation,
            number_of_videos=num_videos,
        ),
    )

    # Poll until done
    print("Waiting for generation", end="", flush=True)
    poll_count = 0
    while not operation.done:
        time.sleep(5)
        operation = client.operations.get(operation)
        poll_count += 1
        if poll_count % 2 == 0:
            print(".", end="", flush=True)

    print()  # newline after dots

    if not operation.result or not operation.result.generated_videos:
        print("Error: No video returned. May have been blocked by safety filters.", file=sys.stderr)
        sys.exit(1)

    # Download and save each video
    output_path.parent.mkdir(parents=True, exist_ok=True)
    videos = operation.result.generated_videos

    for i, video in enumerate(videos):
        if num_videos > 1:
            stem, suffix = output_path.stem, output_path.suffix
            save_path = output_path.parent / f"{stem}_{i + 1}{suffix}"
        else:
            save_path = output_path

        client.files.download(file=video.video)
        video.video.save(str(save_path))
        print(f"Video saved to: {save_path}")


# ─── Image to Video ─────────────────────────────────────────────────────────

def image_to_video(
    prompt: str,
    output_path: Path,
    image_path: Path,
    last_frame_path: Path | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration: int = 8,
    model: str = DEFAULT_MODEL,
) -> None:
    """Generate video from a starting image (and optional ending frame)."""
    client = _get_client()
    model_id = _resolve_model(model)

    if not image_path.exists():
        print(f"Error: Image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    cost = _estimate_cost(duration, model, resolution)
    print(f"Image-to-video: {image_path.name} -> {duration}s @ {resolution}")
    if last_frame_path:
        print(f"Last frame: {last_frame_path.name}")
    print(f"Estimated cost: {cost}")

    # Build Image with raw bytes + mime type
    import mimetypes
    mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    image = types.Image(imageBytes=image_path.read_bytes(), mimeType=mime)

    # For i2v, person_generation must be allow_adult
    config = types.GenerateVideosConfig(
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration_seconds=duration,
        person_generation="allow_adult",
        number_of_videos=1,
    )

    kwargs = {
        "model": model_id,
        "prompt": prompt,
        "image": image,
        "config": config,
    }

    if last_frame_path and last_frame_path.exists():
        last_mime = mimetypes.guess_type(str(last_frame_path))[0] or "image/jpeg"
        last_image = types.Image(imageBytes=last_frame_path.read_bytes(), mimeType=last_mime)
        kwargs["last_frame"] = last_image

    operation = client.models.generate_videos(**kwargs)

    print("Waiting for generation", end="", flush=True)
    while not operation.done:
        time.sleep(5)
        operation = client.operations.get(operation)
        print(".", end="", flush=True)
    print()

    if not operation.result or not operation.result.generated_videos:
        print("Error: No video returned.", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    video = operation.result.generated_videos[0]
    client.files.download(file=video.video)
    video.video.save(str(output_path))
    print(f"Video saved to: {output_path}")


# ─── Extend ──────────────────────────────────────────────────────────────────

def extend_video(
    prompt: str,
    output_path: Path,
    input_video: Path,
    model: str = DEFAULT_MODEL,
) -> None:
    """Extend an existing video by ~7 seconds. Limited to 720p."""
    client = _get_client()
    model_id = _resolve_model(model)

    if not input_video.exists():
        print(f"Error: Video not found: {input_video}", file=sys.stderr)
        sys.exit(1)

    print(f"Extending {input_video.name} by ~7s (720p only)")
    cost = _estimate_cost(7, model, "720p")
    print(f"Estimated cost: {cost}")

    # Upload video via Files API
    print("Uploading video...")
    uploaded_video = client.files.upload(file=str(input_video))

    operation = client.models.generate_videos(
        model=model_id,
        prompt=prompt,
        video=uploaded_video,
        config=types.GenerateVideosConfig(
            resolution="720p",
            person_generation="allow_adult",
            number_of_videos=1,
        ),
    )

    print("Waiting for extension", end="", flush=True)
    while not operation.done:
        time.sleep(5)
        operation = client.operations.get(operation)
        print(".", end="", flush=True)
    print()

    if not operation.result or not operation.result.generated_videos:
        print("Error: No video returned.", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    video = operation.result.generated_videos[0]
    client.files.download(file=video.video)
    video.video.save(str(output_path))
    print(f"Extended video saved to: {output_path}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI video generation via Google Veo (Gemini API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # ── generate (default) ──
    gen = sub.add_parser("generate", aliases=["gen"], help="Generate video from text prompt")
    gen.add_argument("output", help="Output video path (.mp4)")
    gen.add_argument("prompt", help="Video description")
    gen.add_argument("--aspect", "-a", default="16:9", choices=["16:9", "9:16"])
    gen.add_argument("--res", "-r", default="720p", choices=["720p", "1080p", "4k"])
    gen.add_argument("--duration", "-d", type=int, default=8, choices=[4, 6, 8])
    gen.add_argument("--model", "-m", default=DEFAULT_MODEL, help="standard, fast (default), veo2")
    gen.add_argument("--count", "-n", type=int, default=1, help="Number of videos (1-4)")

    # ── image-to-video ──
    i2v = sub.add_parser("i2v", help="Generate video from image")
    i2v.add_argument("output", help="Output video path (.mp4)")
    i2v.add_argument("prompt", help="Animation description")
    i2v.add_argument("--image", "-i", required=True, help="Starting image")
    i2v.add_argument("--last-frame", "-l", help="Optional ending frame for interpolation")
    i2v.add_argument("--aspect", "-a", default="16:9", choices=["16:9", "9:16"])
    i2v.add_argument("--res", "-r", default="720p", choices=["720p", "1080p", "4k"])
    i2v.add_argument("--duration", "-d", type=int, default=8, choices=[4, 6, 8])
    i2v.add_argument("--model", "-m", default=DEFAULT_MODEL)

    # ── extend ──
    ext = sub.add_parser("extend", help="Extend existing video by ~7 seconds")
    ext.add_argument("output", help="Output video path (.mp4)")
    ext.add_argument("prompt", help="Continuation description")
    ext.add_argument("--input", "-i", required=True, help="Source video to extend")
    ext.add_argument("--model", "-m", default=DEFAULT_MODEL)

    # Default to generate if no subcommand
    known_commands = {"generate", "gen", "i2v", "extend"}
    first_positional = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if first_positional and first_positional not in known_commands:
        sys.argv.insert(1, "generate")

    args = parser.parse_args()
    _load_env()

    if args.command in ("generate", "gen"):
        generate_video(
            args.prompt, Path(args.output),
            aspect_ratio=args.aspect, resolution=args.res,
            duration=args.duration, model=args.model,
            num_videos=min(args.count, 4),
        )
    elif args.command == "i2v":
        image_to_video(
            args.prompt, Path(args.output),
            image_path=Path(args.image),
            last_frame_path=Path(args.last_frame) if args.last_frame else None,
            aspect_ratio=args.aspect, resolution=args.res,
            duration=args.duration, model=args.model,
        )
    elif args.command == "extend":
        extend_video(
            args.prompt, Path(args.output),
            input_video=Path(args.input), model=args.model,
        )


if __name__ == "__main__":
    main()
