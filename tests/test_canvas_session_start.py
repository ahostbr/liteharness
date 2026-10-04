"""Pin the display routes and habit in actual SessionStart output, without a live app."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_session_start_teaches_the_canvas_display_routes_and_result_habit(tmp_path):
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
    assert "POST /canvas/present|center|background {kind, path|url}" in out.stdout
    assert "POST /canvas/clear" in out.stdout
    assert "GET /canvas/state" in out.stdout
    assert "canvas action=help has the rules" in out.stdout
    assert "a RESULT to look at     -> canvas action=present (image/video/model)" in out.stdout
    assert "glb -> center; image/video -> background" in out.stdout
