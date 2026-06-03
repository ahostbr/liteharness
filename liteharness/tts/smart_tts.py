#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "requests",
#     "edge-tts",
# ]
# ///

"""
Smart TTS Hook for LiteHarness

Combines task summarization with TTS playback.

When smart summaries are enabled, generates a contextual AI announcement by calling
LiteSuite's local LLM API (http://127.0.0.1:7438/v1/llm/generate).

Audio is synthesized and played through LiteSuite's Voice API
(http://127.0.0.1:7438/v1/tts/speak) — the companion lip-syncs and emotion
detection fires automatically. Falls back to edge-tts direct if LiteSuite is not
running.

Configuration is read from kuroPlugin.tts in .claude/settings.json (project or global).

Usage (as a Claude Code hook command):
  python -m liteharness.tts.smart_tts "Work complete" --type stop
  python -m liteharness.tts.smart_tts "Task finished" --type subagent
  python -m liteharness.tts.smart_tts "Attention needed" --type notification
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# LiteSuite Voice API port (default 7438, overridable via env or --port)
_DEFAULT_PORT = int(os.environ.get("LITESUITE_VOICE_API_PORT", "7438"))


def _voice_url(path: str, port: int = _DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}{path}"


# ── Settings ─────────────────────────────────────────────────────────────────

def get_settings() -> dict:
    """Load kuroPlugin settings from .claude/settings.json (project or global fallback)."""
    # 0. CLAUDE_PROJECT_DIR is most reliable inside hooks
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        settings_path = Path(project_dir) / ".claude" / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, encoding="utf-8-sig") as f:
                    plugin = json.load(f).get("kuroPlugin", {})
                    if plugin:
                        return plugin
            except Exception as e:
                print(f"[SmartTTS] Settings read error ({settings_path}): {e}", file=sys.stderr)

    # 1. Walk up from cwd
    current = Path.cwd()
    while current != current.parent:
        settings_path = current / ".claude" / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, encoding="utf-8-sig") as f:
                    plugin = json.load(f).get("kuroPlugin", {})
                    if plugin:
                        return plugin
            except Exception as e:
                print(f"[SmartTTS] Settings read error ({settings_path}): {e}", file=sys.stderr)
        current = current.parent

    # 2. Global ~/.claude/settings.json
    global_path = Path.home() / ".claude" / "settings.json"
    if global_path.exists():
        try:
            with open(global_path, encoding="utf-8-sig") as f:
                return json.load(f).get("kuroPlugin", {})
        except Exception as e:
            print(f"[SmartTTS] Global settings read error: {e}", file=sys.stderr)

    return {}


def should_skip_global(source_arg: str = "") -> bool:
    """Prevent double-fire: if running from global hooks and a local project has TTS enabled."""
    source = source_arg or os.environ.get("LITESUITE_TTS_SOURCE", "")
    if source != "global":
        return False

    current = Path.cwd()
    while current != current.parent:
        settings_path = current / ".claude" / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path) as f:
                    hooks_config = json.load(f).get("hooks", {})
                    for event in ("Stop", "SubagentStop", "Notification"):
                        for group in hooks_config.get(event, []):
                            for hook in group.get("hooks", []):
                                cmd = hook.get("command", "")
                                if "smart_tts" in cmd:
                                    return True
            except Exception:
                pass
        current = current.parent
    return False


# ── Summary generation via LiteSuite LLM API ─────────────────────────────────

def _build_summary_prompt(
    task_description: str,
    summary_type: str,
    agent_name: str | None = None,
    user_name: str = "Ryan",
) -> str:
    """Build the TTS summary prompt."""
    if summary_type == "stop":
        context = "A coding session has ended."
    elif summary_type == "subagent":
        context = f"A subagent ({agent_name or 'builder'}) completed a task."
    else:
        context = "The user's attention is needed."

    return f"""Generate a brief TTS announcement summarizing COMPLETED work.

{task_description}
Context: {context}

Requirements:
- ALWAYS start with "{user_name}, " (name followed by comma)
- Under 20 words total
- Summarize the SPECIFIC outcome — mention key details from the result
- Use past tense — work is DONE
- Be specific, not generic
- Return ONLY the announcement text, no quotes

Generate ONE specific announcement:"""


def generate_summary(
    task_description: str,
    summary_type: str,
    agent_name: str | None = None,
    user_name: str = "Ryan",
    port: int = _DEFAULT_PORT,
) -> str | None:
    """Generate AI summary via LiteSuite's /v1/llm/generate endpoint."""
    try:
        import requests

        prompt = _build_summary_prompt(task_description, summary_type, agent_name, user_name)
        response = requests.post(
            _voice_url("/v1/llm/generate", port),
            json={"prompt": prompt, "max_tokens": 100, "temperature": 0.7},
            timeout=15,
        )
        if response.status_code == 200:
            text = response.json().get("text", "").strip()
            if text:
                return text
        else:
            print(
                f"[SmartTTS] LLM generate error {response.status_code}: {response.text[:200]}",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"[SmartTTS] Summary generation failed: {e}", file=sys.stderr)
    return None


# ── TTS playback via LiteSuite Voice API ─────────────────────────────────────

def speak_via_litesuite(text: str, port: int = _DEFAULT_PORT) -> bool:
    """
    Send text to LiteSuite's /v1/tts/speak endpoint.

    This is fire-and-forget from the hook's perspective — LiteSuite handles
    synthesis, companion animation, and playback asynchronously.
    Returns True if the request was accepted (HTTP 200), False on error.
    """
    try:
        import requests

        response = requests.post(
            _voice_url("/v1/tts/speak", port),
            json={"text": text},
            timeout=5,
        )
        if response.status_code == 200:
            print(f"[SmartTTS] Sent to LiteSuite voice: {text[:80]}", file=sys.stderr)
            return True
        else:
            print(
                f"[SmartTTS] LiteSuite /v1/tts/speak error {response.status_code}",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"[SmartTTS] LiteSuite voice unavailable: {e}", file=sys.stderr)
    return False


def speak_via_edge_tts(text: str, voice: str = "en-GB-SoniaNeural") -> bool:
    """
    Fallback: generate + play audio directly via edge-tts CLI.
    Used when LiteSuite desktop is not running.
    """
    try:
        from .utils.tts_queue import acquire_tts_lock, release_tts_lock
        use_queue = True
    except ImportError:
        use_queue = False

    agent_id = f"smart_tts_{os.getpid()}"

    if use_queue:
        if not acquire_tts_lock(agent_id, timeout=25):
            print("[SmartTTS] Queue busy after 25s — skipping", file=sys.stderr)
            return False

    try:
        return _edge_tts_speak(text, voice)
    finally:
        if use_queue:
            release_tts_lock(agent_id)


def get_uv_path() -> str:
    """Locate the uv executable."""
    if os.environ.get("UV_PATH"):
        return os.environ["UV_PATH"]
    if sys.platform == "win32":
        return os.path.join(os.path.expanduser("~"), ".local", "bin", "uv.exe")
    return "uv"


def _edge_tts_speak(text: str, voice: str) -> bool:
    """Internal edge-tts synthesize + play."""
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
                print(f"[SmartTTS] edge-tts error: {result.stderr}", file=sys.stderr)
                return False

            # Play via PowerShell MediaPlayer
            ps_cmd = f"""
Add-Type -AssemblyName presentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open("{tmp_path}")
Start-Sleep -Milliseconds 800
$player.Play()
$duration = $player.NaturalDuration
if (-not $duration.HasTimeSpan) {{
    Start-Sleep -Milliseconds 500
    $duration = $player.NaturalDuration
}}
if ($duration.HasTimeSpan) {{
    $ms = $duration.TimeSpan.TotalMilliseconds + 500
    Start-Sleep -Milliseconds $ms
}} else {{
    $lastPos = -1
    $staleCount = 0
    $maxPolls = 120
    $polls = 0
    Start-Sleep -Milliseconds 500
    while ($staleCount -lt 3 -and $polls -lt $maxPolls) {{
        Start-Sleep -Milliseconds 500
        $curPos = $player.Position.TotalMilliseconds
        if ($curPos -le $lastPos) {{ $staleCount++ }} else {{ $staleCount = 0 }}
        $lastPos = $curPos
        $polls++
    }}
}}
$player.Close()
"""
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                timeout=60,
            )
            print(f"[SmartTTS] Announced (edge-tts): {text}", file=sys.stderr)
            return True
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(f"[SmartTTS] edge-tts fallback error: {e}", file=sys.stderr)
        return False


# ── Stdin context ─────────────────────────────────────────────────────────────

def read_stdin_context() -> dict | None:
    """Read hook context JSON from stdin (Claude Code passes this)."""
    try:
        data = sys.stdin.read()
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


def get_task_and_result_from_transcript(
    transcript_path: str, retries: int = 3, delay: float = 0.5
) -> tuple[str | None, str | None]:
    """Extract first user message (task) and last assistant message (result) from JSONL."""
    task = None
    result = None

    for attempt in range(retries):
        try:
            if not os.path.exists(transcript_path):
                if attempt < retries - 1:
                    time.sleep(delay)
                    continue
                return None, None

            with open(transcript_path, encoding="utf-8") as f:
                lines = f.readlines()

            last_assistant_text = None
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line.strip())
                    entry_type = entry.get("type")
                    if entry_type == "user" and task is None:
                        msg = entry.get("message", {})
                        content = msg.get("content") if isinstance(msg, dict) else None
                        if content and isinstance(content, str):
                            task = content[:200]
                    elif entry_type == "assistant":
                        msg = entry.get("message", {})
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    last_assistant_text = text
                except json.JSONDecodeError:
                    continue

            if last_assistant_text:
                result = last_assistant_text[:500]

            return task, result

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                print(
                    f"[SmartTTS] Transcript read error after {retries} attempts: {e}",
                    file=sys.stderr,
                )

    return None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="LiteHarness Smart TTS hook")
    parser.add_argument("fallback", nargs="?", default="Task complete", help="Fallback message")
    parser.add_argument(
        "--type", "-t",
        choices=["stop", "subagent", "notification"],
        default="subagent",
        help="Announcement type",
    )
    parser.add_argument("--task", default=None, help="Task description override")
    parser.add_argument("--agent", "-a", default=None, help="Agent name")
    parser.add_argument("--voice", "-v", default=None, help="TTS voice override (edge-tts fallback)")
    parser.add_argument(
        "--source", default="",
        help="Hook source (global/project) for double-fire prevention",
    )
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT,
        help="LiteSuite Voice API port (default: 7438)",
    )
    args = parser.parse_args()

    # Guard: skip double-fire when global hooks and local hooks both active
    if should_skip_global(args.source):
        sys.exit(0)

    # Load plugin settings
    settings = get_settings()
    tts_config = settings.get("tts", {})
    smart_summaries = tts_config.get("smartSummaries", False)
    user_name = tts_config.get("userName", "Ryan")
    voice = tts_config.get("voice") or args.voice or "en-GB-SoniaNeural"

    # Read stdin hook context
    task = args.task
    result = None
    stdin_context = read_stdin_context()

    if stdin_context:
        last_msg = stdin_context.get("last_assistant_message")
        if last_msg and isinstance(last_msg, str) and len(last_msg.strip()) > 5:
            result = last_msg.strip()[:500]

        if not result:
            transcript_path = stdin_context.get("agent_transcript_path")
            if transcript_path:
                task, result = get_task_and_result_from_transcript(transcript_path)

    # Reject shell-unexpanded variables
    if not task or task.startswith("$") or task.strip() == "" or task == "null":
        task = None

    # Try AI summary if enabled and we have content
    message = None
    if smart_summaries and (result or task):
        task_for_ai = (
            f"Task: {task or 'Background task'}\n\nResult: {result}" if result else task
        )
        message = generate_summary(
            task_description=task_for_ai,
            summary_type=args.type,
            agent_name=args.agent,
            user_name=user_name,
            port=args.port,
        )

    # Enforce userName prefix on AI-generated messages
    if message and user_name and user_name != "Your Name":
        if not message.startswith(user_name):
            stripped = message
            if stripped.lower().startswith("ryan,"):
                stripped = stripped[5:].lstrip()
            elif stripped.lower().startswith("ryan "):
                stripped = stripped[4:].lstrip()
            message = f"{user_name}, {stripped}"

    # Fall back to provided fallback message
    if not message:
        fallback = args.fallback
        if fallback and not fallback.startswith(user_name):
            message = (
                f"{user_name}{fallback}"
                if fallback.startswith(",")
                else f"{user_name}, {fallback}"
            )
        else:
            message = fallback

    # Route audio: LiteSuite Voice API first, edge-tts direct as fallback
    if not speak_via_litesuite(message, port=args.port):
        print("[SmartTTS] Falling back to edge-tts direct", file=sys.stderr)
        speak_via_edge_tts(message, voice=voice)


if __name__ == "__main__":
    main()
