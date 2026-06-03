#!/usr/bin/env python3
"""
AI image generation via Google Gemini API.

Setup:
    pip install google-genai Pillow python-dotenv
    # Set env var: GOOGLE_AI_API_KEY=your_key_here
    # Get your key at: https://aistudio.google.com/apikey

Commands:
    generate (default):
        gen_image.py out.png "A 3D cube on black background"
        gen_image.py out.png "cube" --aspect 16:9 --size 4K --model pro
        gen_image.py out.png "cube" "sphere" "pyramid"          # batch
        gen_image.py out.png "same style, rocket" --ref style.png
        gen_image.py out.png "gear icon" --style styles/blue_glass_3d.md

    edit:
        gen_image.py edit out.png "Change bg to blue" -i input.png
        gen_image.py edit out.png "Merge these styles" -i a.png -i b.png -i c.png
        gen_image.py edit out.png "Apply this style" -i photo.png --ref style.png

    describe:
        gen_image.py describe image.png
        gen_image.py describe a.png b.png --prompt "Compare these two images"

Models: flash (default, fast), pro (best quality, 4K), legacy (fallback)
Aspect ratios: 1:1, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9
Resolutions: 1K (default), 2K, 4K (pro model only)
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

# ─── Models ──────────────────────────────────────────────────────────────────

MODELS = {
    "flash": "gemini-2.5-flash-image",         # default, fast + affordable
    "pro": "gemini-3-pro-image-preview",       # best quality, up to 4K
    "legacy": "gemini-2.0-flash-exp",          # Fallback
}
DEFAULT_MODEL = "flash"


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


# ─── Response extraction ─────────────────────────────────────────────────────

def _extract_image(response) -> tuple[Optional[bytes], str]:
    """Extract image bytes and any text from a Gemini response."""
    text_parts = []
    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data, ""
            elif hasattr(part, "text") and part.text:
                text_parts.append(part.text)
    return None, "\n".join(text_parts)


# ─── Style templates ─────────────────────────────────────────────────────────

def load_style_template(style_path: Path) -> str:
    """Load a prompt template from a markdown style file. Expects {subject} placeholder."""
    if not style_path.exists():
        raise FileNotFoundError(f"Style file not found: {style_path}")
    content = style_path.read_text()
    pattern = r'(?:##?\s*(?:Prompt\s*)?Template)[^\n]*\n+(?:.*?\n)*?```[^\n]*\n(.*?)```'
    match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
    if match:
        template = match.group(1).strip()
        return re.sub(
            r'\[YOUR SUBJECT[^\]]*\]|\[SUBJECT\]|\{subject\}',
            '{subject}', template, flags=re.IGNORECASE,
        )
    raise ValueError(f"No prompt template found in {style_path}.")


def apply_style_template(template: str, subject: str) -> str:
    if '{subject}' in template:
        return template.format(subject=subject)
    return f"{subject}. {template}"


# ─── Generate ────────────────────────────────────────────────────────────────

def generate_image(
    prompt: str,
    output_path: Path,
    reference_images: Optional[list[Path]] = None,
    aspect_ratio: str = "16:9",
    image_size: str = "1K",
    model: str = DEFAULT_MODEL,
) -> None:
    """Generate a new image. Streams when no reference images provided."""
    client = _get_client()
    model_id = _resolve_model(model)

    if reference_images:
        # With references: non-streaming multimodal
        contents: list = [prompt]
        for ref_path in reference_images[:14]:
            if ref_path.exists():
                contents.append(Image.open(ref_path))
            else:
                print(f"Warning: Reference image not found: {ref_path}")

        config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])
        # Pro model supports imageConfig with references too
        if model_id == MODELS["pro"]:
            config = types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=types.ImageConfig(image_size=image_size),
            )

        response = client.models.generate_content(
            model=model_id, contents=contents, config=config,
        )
        image_data, text = _extract_image(response)
        if image_data:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_data)
            print(f"Image saved to: {output_path} ({image_size})")
        elif text:
            print(text)
    else:
        # No references: streaming with full imageConfig
        contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
        ]
        image_config_kwargs = {"aspect_ratio": aspect_ratio, "image_size": image_size}
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=types.ImageConfig(**image_config_kwargs),
        )

        for chunk in client.models.generate_content_stream(
            model=model_id, contents=contents, config=config,
        ):
            if chunk.candidates is None or chunk.candidates[0].content is None or chunk.candidates[0].content.parts is None:
                continue
            part = chunk.candidates[0].content.parts[0]
            if part.inline_data and part.inline_data.data:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(part.inline_data.data)
                print(f"Image saved to: {output_path} ({image_size})")
                return
            elif hasattr(part, "text") and part.text:
                print(part.text)


# ─── Edit ─────────────────────────────────────────────────────────────────────

def edit_image(
    input_paths: list[Path],
    prompt: str,
    output_path: Path,
    reference_images: Optional[list[Path]] = None,
    image_size: str = "1K",
    model: str = DEFAULT_MODEL,
) -> None:
    """Edit one or more images. Supports style transfer, multi-image mixing (up to 14)."""
    client = _get_client()
    model_id = _resolve_model(model)

    # Images first, then prompt (matches MCP server pattern)
    contents: list = []
    for ip in input_paths[:14]:
        if ip.exists():
            contents.append(Image.open(ip))
        else:
            print(f"Warning: Input image not found: {ip}")

    # Add reference images for style guidance
    if reference_images:
        remaining = 14 - len(contents)
        for ref_path in reference_images[:remaining]:
            if ref_path.exists():
                contents.append(Image.open(ref_path))
            else:
                print(f"Warning: Reference image not found: {ref_path}")

    contents.append(prompt)

    config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])
    if model_id == MODELS["pro"]:
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=types.ImageConfig(image_size=image_size),
        )

    response = client.models.generate_content(
        model=model_id, contents=contents, config=config,
    )
    image_data, text = _extract_image(response)
    if image_data:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_data)
        print(f"Edited image saved to: {output_path} ({image_size})")
    elif text:
        print(text)
    else:
        print("Error: No image returned from edit", file=sys.stderr)


# ─── Describe ─────────────────────────────────────────────────────────────────

def describe_image(
    image_paths: list[Path],
    prompt: str = "Describe this image in detail.",
    model: str = DEFAULT_MODEL,
) -> None:
    """Analyze and describe one or more images. Text-only output."""
    client = _get_client()
    model_id = _resolve_model(model)

    contents: list = []
    for ip in image_paths:
        if ip.exists():
            contents.append(Image.open(ip))
        else:
            print(f"Warning: Image not found: {ip}", file=sys.stderr)

    if not contents:
        print("Error: No valid images provided", file=sys.stderr)
        sys.exit(1)

    contents.append(prompt)

    response = client.models.generate_content(
        model=model_id, contents=contents,
        config=types.GenerateContentConfig(response_modalities=["TEXT"]),
    )
    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text:
                print(part.text)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI image generation via Google Gemini API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # ── generate (default when no subcommand) ──
    gen = sub.add_parser("generate", aliases=["gen"], help="Generate image from prompt")
    gen.add_argument("output", help="Output image path (base path if batch)")
    gen.add_argument("prompts", nargs="+", help="One or more prompts/subjects")
    gen.add_argument("--ref", "-r", action="append", dest="references", help="Reference image (repeatable, up to 14)")
    gen.add_argument("--style", "-s", help="Path to style template .md file")
    gen.add_argument("--aspect", "-a", default="16:9", choices=["1:1", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"])
    gen.add_argument("--size", default="1K", choices=["1K", "2K", "4K"], help="Resolution (pro model only, default: 1K)")
    gen.add_argument("--model", "-m", default=DEFAULT_MODEL, help="Model: pro (default), flash, legacy")

    # ── edit ──
    ed = sub.add_parser("edit", help="Edit/mix images with a prompt")
    ed.add_argument("output", help="Output image path")
    ed.add_argument("prompt", help="Edit instructions")
    ed.add_argument("-i", "--input", action="append", dest="inputs", required=True, help="Input image (repeatable, up to 14)")
    ed.add_argument("--ref", "-r", action="append", dest="references", help="Reference image for style guidance")
    ed.add_argument("--size", default="1K", choices=["1K", "2K", "4K"])
    ed.add_argument("--model", "-m", default=DEFAULT_MODEL)

    # ── describe ──
    desc = sub.add_parser("describe", aliases=["desc"], help="Analyze/describe images")
    desc.add_argument("images", nargs="+", help="Image paths to analyze")
    desc.add_argument("--prompt", "-p", default="Describe this image in detail.", help="Analysis prompt")
    desc.add_argument("--model", "-m", default=DEFAULT_MODEL)

    # Default to generate if no subcommand (backward compat)
    # Check if first non-flag arg is a known subcommand
    known_commands = {"generate", "gen", "edit", "describe", "desc"}
    first_positional = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if first_positional and first_positional not in known_commands:
        # Prepend "generate" so old-style invocations still work
        sys.argv.insert(1, "generate")

    args = parser.parse_args()
    _load_env()

    if args.command in ("generate", "gen"):
        prompts = args.prompts
        if args.style:
            template = load_style_template(Path(args.style))
            print(f"Loaded style: {args.style}")
            prompts = [apply_style_template(template, p) for p in prompts]

        ref_images = [Path(r) for r in args.references] if args.references else None

        if len(prompts) == 1:
            generate_image(prompts[0], Path(args.output), reference_images=ref_images,
                           aspect_ratio=args.aspect, image_size=args.size, model=args.model)
        else:
            out = Path(args.output)
            stem, suffix, parent = out.stem, out.suffix, out.parent
            for i, prompt in enumerate(prompts, 1):
                numbered = parent / f"{stem}_{i}{suffix}"
                print(f"\nGenerating {i}/{len(prompts)}...")
                generate_image(prompt, numbered, reference_images=ref_images,
                               aspect_ratio=args.aspect, image_size=args.size, model=args.model)

    elif args.command == "edit":
        input_paths = [Path(p) for p in args.inputs]
        ref_images = [Path(r) for r in args.references] if args.references else None
        edit_image(input_paths, args.prompt, Path(args.output),
                   reference_images=ref_images, image_size=args.size, model=args.model)

    elif args.command in ("describe", "desc"):
        describe_image([Path(p) for p in args.images], prompt=args.prompt, model=args.model)


if __name__ == "__main__":
    main()
