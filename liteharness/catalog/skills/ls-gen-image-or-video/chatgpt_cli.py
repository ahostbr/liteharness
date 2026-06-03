#!/usr/bin/env python3
"""ChatGPT CLI — Control ChatGPT Desktop via Chrome DevTools Protocol.

Launches ChatGPT Desktop with --remote-debugging-port, then exposes
image generation, chat, and navigation as simple CLI commands.

Usage:
    # Launch ChatGPT Desktop with CDP
    cli-anything-chatgpt launch

    # Generate an image (one command!)
    cli-anything-chatgpt image "A dark cockpit UI for an AI agent swarm wizard"

    # Generate and save to specific path
    cli-anything-chatgpt image "epic sunset" --output ~/Pictures/sunset.png

    # Send any prompt
    cli-anything-chatgpt chat "Explain quantum computing in 3 sentences"

    # Read the latest response
    cli-anything-chatgpt read

    # Navigate to new chat
    cli-anything-chatgpt new

    # Check status
    cli-anything-chatgpt status

    # Interactive REPL
    cli-anything-chatgpt
"""

import sys
import os
import json
import time
import click
import requests
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from cli_anything.chatgpt.core import cdp
except ImportError:
    import chatgpt_cdp as cdp

_json_output = False


def output(data, message: str = ""):
    if _json_output:
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        if message:
            click.echo(message)
        if isinstance(data, dict):
            for k, v in data.items():
                click.echo(f"  {k}: {v}")
        elif isinstance(data, list):
            for item in data:
                click.echo(f"  - {item}")
        else:
            click.echo(str(data))


@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON (for agents)")
@click.pass_context
def cli(ctx, use_json):
    """ChatGPT CLI — Control ChatGPT Desktop via CDP."""
    global _json_output
    _json_output = use_json
    if ctx.invoked_subcommand is None:
        _repl()


@cli.command()
def launch():
    """Launch ChatGPT Desktop with CDP enabled."""
    result = cdp.launch()
    if result["status"] == "already_running":
        output(result, "ChatGPT Desktop already running with CDP.")
    elif result["status"] == "launched":
        output(result, f"ChatGPT Desktop launched. CDP on port {cdp.CDP_PORT}.")
    else:
        output(result, "Failed to launch ChatGPT Desktop.")


@cli.command()
def status():
    """Check if ChatGPT Desktop is running with CDP."""
    running = cdp.is_running()
    if running:
        try:
            version = requests.get(f"{cdp.CDP_URL}/json/version", timeout=2).json()
            page_id = cdp.get_page_id()
            output({
                "running": True,
                "browser": version.get("Browser", "unknown"),
                "electron": version.get("User-Agent", "").split("Electron/")[1].split(" ")[0] if "Electron/" in version.get("User-Agent", "") else "unknown",
                "page_id": page_id,
                "port": cdp.CDP_PORT
            }, "ChatGPT Desktop is online.")
        except Exception as e:
            output({"running": True, "error": str(e)}, "CDP responding but error reading details.")
    else:
        output({"running": False}, "ChatGPT Desktop is not running with CDP. Run: cli-anything-chatgpt launch")


@cli.command()
@click.argument("prompt")
@click.option("--output", "-o", "output_path", default=None, help="Save image to this path")
@click.option("--timeout", "-t", default=120, help="Max seconds to wait for image (default: 120)")
@click.option("--size", "-s", default="1024x1024", help="Image size hint in prompt")
def image(prompt, output_path, timeout, size):
    """Generate an image via ChatGPT's image model.

    Example:
        cli-anything-chatgpt image "A dark UI mockup for an AI agent panel"
    """
    if not cdp.is_running():
        click.echo("Launching ChatGPT Desktop...")
        cdp.launch()

    # Enhance prompt with image generation instruction
    full_prompt = f"Generate an image: {prompt}"

    click.echo(f"Sending prompt to ChatGPT...")
    result = cdp.type_and_submit(full_prompt)

    if "ERROR" in result:
        output({"error": result}, f"Failed: {result}")
        return

    click.echo(f"Waiting for image generation (up to {timeout}s)...")
    image_url = cdp.wait_for_image(timeout=timeout)

    if image_url:
        data = {"status": "generated", "url": image_url}

        if output_path:
            try:
                import urllib.request
                save_path = Path(output_path).expanduser()
                save_path.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(image_url, str(save_path))
                data["saved_to"] = str(save_path)
                click.echo(f"Image saved to: {save_path}")
            except Exception as e:
                data["save_error"] = str(e)
                click.echo(f"Image generated but save failed: {e}")
                click.echo(f"URL: {image_url}")
        else:
            click.echo(f"Image generated!")
            click.echo(f"URL: {image_url}")

        output(data)
    else:
        response = cdp.read_response()
        output({"status": "no_image", "response_preview": response[:500]},
               "No image detected. ChatGPT may have responded with text instead.")


@cli.command()
@click.argument("prompt")
def chat(prompt):
    """Send a text prompt to ChatGPT.

    Example:
        cli-anything-chatgpt chat "Explain quantum computing in 3 sentences"
    """
    if not cdp.is_running():
        click.echo("Launching ChatGPT Desktop...")
        cdp.launch()

    result = cdp.type_and_submit(prompt)
    if "ERROR" in result:
        output({"error": result})
        return

    click.echo("Prompt sent. Waiting for response...")
    time.sleep(8)

    response = cdp.read_response()
    output({"response": response}, "")


@cli.command()
def read():
    """Read the latest ChatGPT response."""
    if not cdp.is_running():
        output({"error": "ChatGPT not running"}, "Not running. Use: cli-anything-chatgpt launch")
        return
    response = cdp.read_response()
    output({"response": response}, "")


@cli.command()
def new():
    """Start a new chat."""
    if not cdp.is_running():
        output({"error": "ChatGPT not running"})
        return
    result = cdp.navigate_new_chat()
    output({"result": result}, result)


@cli.command()
def page():
    """Dump current page text (for debugging)."""
    if not cdp.is_running():
        output({"error": "ChatGPT not running"})
        return
    text = cdp.get_page_text(5000)
    output({"text": text}, text)


def _repl():
    """Interactive REPL mode."""
    click.echo("ChatGPT CLI — Interactive Mode")
    click.echo("Commands: launch, status, image <prompt>, chat <prompt>, read, new, page, quit")
    click.echo("")

    if not cdp.is_running():
        click.echo("ChatGPT not running. Type 'launch' to start.")

    while True:
        try:
            line = input("chatgpt> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nBye!")
            break

        if not line:
            continue
        if line in ("quit", "exit", "q"):
            break

        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "launch":
            result = cdp.launch()
            click.echo(f"  {result['status']}")
        elif cmd == "status":
            click.echo(f"  Running: {cdp.is_running()}")
        elif cmd == "image":
            if not arg:
                click.echo("  Usage: image <prompt>")
                continue
            cdp.type_and_submit(f"Generate an image: {arg}")
            click.echo("  Prompt sent. Waiting...")
            url = cdp.wait_for_image(timeout=90)
            if url:
                click.echo(f"  Image: {url}")
            else:
                click.echo("  No image found. Use 'read' to see response.")
        elif cmd == "chat":
            if not arg:
                click.echo("  Usage: chat <prompt>")
                continue
            cdp.type_and_submit(arg)
            click.echo("  Sent. Waiting...")
            time.sleep(8)
            click.echo(cdp.read_response())
        elif cmd == "read":
            click.echo(cdp.read_response())
        elif cmd == "new":
            click.echo(cdp.navigate_new_chat())
        elif cmd == "page":
            click.echo(cdp.get_page_text(3000))
        else:
            click.echo(f"  Unknown command: {cmd}")


def main():
    cli()


if __name__ == "__main__":
    main()
