"""Codex Desktop automation via Win32 and clipboard paste.

Codex Desktop is an Electron app whose chat DOM is not exposed through
standard UIAutomation. This module treats it as a visible window: focus it,
click the prompt area, paste text through the clipboard, optionally submit,
then restore the user's cursor, Codex window placement, and original focus.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_PS_DIR = Path(__file__).parent / "ps_scripts"
POWERSHELL_PATH = r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"


def _run_ps_script(
    script_name: str,
    args: dict[str, Any] | None = None,
    timeout: int = 12,
) -> Any:
    """Run a bundled PowerShell helper and parse JSON output."""
    script_path = _PS_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"PS script not found: {script_path}")

    script_text = script_path.read_text(encoding="utf-8")
    encoded = base64.b64encode(script_text.encode("utf-16-le")).decode("ascii")

    env = {**os.environ}
    if args:
        env["LWTT_ARGS"] = json.dumps(args)

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        [
            POWERSHELL_PATH,
            "-NoProfile",
            "-Sta",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        creationflags=creationflags,
    )

    stdout = result.stdout.strip()
    if not stdout:
        if result.stderr.strip():
            raise RuntimeError(result.stderr.strip())
        return None
    return json.loads(stdout)


def list_codex_windows() -> list[dict]:
    """Return visible Codex Desktop windows with Win32 bounds."""
    data = _run_ps_script("codex_desktop.ps1", args={"action": "list"})
    if not data:
        return []
    windows = data.get("windows", [])
    if isinstance(windows, dict):
        windows = [windows]
    return windows


def send_to_codex_desktop(
    text: str,
    *,
    submit: bool = True,
    restore_mouse: bool = True,
    window_handle: int | None = None,
    click_x: int | None = None,
    click_y: int | None = None,
    dry_run: bool = False,
    probe_only: bool = False,
) -> dict:
    """Paste text into Codex Desktop and optionally submit it.

    The PowerShell helper snapshots the current cursor position, current
    foreground window, and Codex window placement before touching the UI. It
    restores those values in a finally block.
    """
    if not text and not dry_run and not probe_only:
        raise ValueError("text is required")

    return _run_ps_script(
        "codex_desktop.ps1",
        args={
            "action": "send",
            "text": text,
            "submit": submit,
            "restoreMouse": restore_mouse,
            "windowHandle": window_handle,
            "clickX": click_x,
            "clickY": click_y,
            "dryRun": dry_run,
            "probeOnly": probe_only,
        },
        timeout=20,
    )
