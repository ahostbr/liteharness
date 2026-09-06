"""End-to-end T418 proof: real subprocesses, real hook entry points, throwaway root.

Redirects HOME/USERPROFILE so `config.HARNESS_ROOT` (Path.home()/".liteharness")
lands in a temp tree — the live fleet's registry is never touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

STARTUP = "aaaaaaaa-1111-4111-8111-111111111111"
RESUMED = "bbbbbbbb-2222-4222-8222-222222222222"


def run(args, env, stdin_payload=None, timeout=8):
    return subprocess.run(
        [sys.executable, "-m", *args],
        input=json.dumps(stdin_payload) if stdin_payload is not None else "",
        env=env,
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def base_env(home: str, session_id: str, source: str | None):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "GEMINI", "LITECODE"))
    }
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["LITEHARNESS_CLI"] = "claude-code"
    env["LITEHARNESS_MODEL"] = "test-model"
    env["CLAUDE_CODE_SESSION_ID"] = session_id
    if source:
        env["LITEHARNESS_HOOK_SOURCE"] = source
    return env


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        home = str(Path(tmp) / "fakehome")
        Path(home).mkdir(parents=True, exist_ok=True)

        for ident, source in ((STARTUP, "startup"), (RESUMED, "resume")):
            payload = {
                "session_id": ident,
                "source": source,
                "hook_event_name": "SessionStart",
                "transcript_path": str(Path(home) / f"{ident}.jsonl"),
            }
            r = run(["liteharness.hooks", "register"], base_env(home, ident, source), payload)
            print(f"register({source}) rc={r.returncode}")

        agents = sorted(p.stem for p in (Path(home) / ".liteharness" / "agents").glob("*.json"))
        print("presence rows:", agents)

        # The watcher, holding the PRE-RESUME environment — the 2026-09-06 shape.
        env = base_env(home, STARTUP, None)
        try:
            r = run(["liteharness.hooks", "watch-auto"], env, timeout=8)
            out = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") + (exc.stderr or "")
        print("--- watch-auto output ---")
        print(out.strip() or "(no output)")
        print("--- verdict ---")
        armed_on_retired = f"Watching inbox for agent {STARTUP}" in out
        print("armed on the RETIRED startup id:", armed_on_retired)
        print("named both ids:", STARTUP in out and RESUMED in out)
        return 1 if armed_on_retired else 0


if __name__ == "__main__":
    raise SystemExit(main())
