"""Pin the display routes and habit in actual SessionStart output, without a live app."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _session_start_stdout(tmp_path) -> str:
    """The SessionStart text of a seat inside LiteSuite, without a live app."""
    prompts = tmp_path / "prompts"
    (prompts / "preambles").mkdir(parents=True)
    for name in ("bootstrap-harness.md", "canvas-display.md", "preambles/worker-preamble.md"):
        (prompts / name).write_text(f"# {name}\n", encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("LITEHARNESS_", "LITESUITE_", "CODEX_", "CLAUDE_"))
    }
    env.update({
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "PYTHONPATH": str(ROOT),
        "PYTHONIOENCODING": "utf-8",
        "LITESUITE_BRIDGE_TOKEN": "test-only-no-server",
        "LITEHARNESS_PROMPTS_DIR": str(tmp_path / "prompts"),
    })
    script = """
from unittest.mock import patch
from liteharness import hooks
with patch.object(hooks, '_adopt_pid_owner', return_value='canvas-session-start-test'), \\
     patch.object(hooks, '_bridge_listening', return_value=False), \\
     patch.object(hooks.config, 'get_model', return_value='test-model'), \\
     patch.object(hooks.config, 'get_cli', return_value='claude-code'):
    hooks.register_presence()
"""
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def test_session_start_teaches_the_canvas_display_routes_and_result_habit(tmp_path):
    stdout = _session_start_stdout(tmp_path)
    assert "POST /canvas/present|center|background {kind, path|url}" in stdout
    assert "POST /canvas/clear" in stdout
    assert "GET /canvas/state" in stdout
    assert "canvas action=help has the rules" in stdout
    assert "a RESULT to look at     -> canvas action=present (image/video/model)" in stdout
    assert "glb -> center; image/video -> background" in stdout


def test_session_start_teaches_browser_tabs_by_default(tmp_path):
    """T0234 (the user, 2026-09-30): agents use the tab system of one browser pane, not a new pane per page."""
    stdout = _session_start_stdout(tmp_path)
    assert "opens a TAB in your one agent browser pane" in stdout
    assert "newPane=true ONLY when a separate pane is" in stdout
    assert "each page is a TAB of" in stdout and "never a new pane per page" in stdout
