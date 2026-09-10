"""End-to-end T563 proof: a watcher that armed BEFORE its registration still gets the mail.

Real subprocesses, real hook entry points, throwaway HOME — the live fleet's
registry is never touched. Sibling of scripts/t418_watch_auto_e2e.py, which
proves the arm-time guard; this proves the part that guard cannot reach.

🔴 THE ORDER IS THE WHOLE TEST, and it is the opposite of T418's. That script
registers both rows and THEN arms, so the guard has its answer waiting. The
failure Ryan hit does it the other way round:

    08:58:42  claude.exe 29436 starts
    08:58:46  watch-auto arms on the STARTUP uuid        <- 4 s in
    08:59:07  the seat's real presence registers         <- 21 s in, AFTER

At 08:58:46 nothing is wrong with the registry and nothing is wrong with the
guard: the successor simply does not exist yet. Every unit arm in
tests/test_watch_auto_resume_identity.py passed throughout, because they all
build the world first.

    A GUARD EVALUATED ONCE RACES ANY FACT THAT LANDS AFTER IT.

⚠️ `LITEHARNESS_SESSION_PID` IS SET, AND THAT IS NOT A CHEAT. `_resolve_session_pid`
honours it first (hooks.py:332) precisely so a non-Claude parent can name the
owning process; without it the ancestor walk looks for claude.exe and finds this
script instead. Pinning it makes the watcher and both registrations agree about
WHICH pid is being taken over — which is the real condition on Ryan's box, where
they agree because they share one claude.exe.

Usage:  python scripts/t563_watch_auto_reresolve_e2e.py [--gap-seconds 20] [--runs 2]
Exit 0 only if every run delivered.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

STARTUP = "cccccccc-3333-4333-8333-333333333333"
RESUMED = "dddddddd-4444-4444-8444-444444444444"
SENDER = "eeeeeeee-5555-4555-8555-555555555555"
BODY = "T563 e2e: this message is addressed to the id the sender can see"


def base_env(home: str, session_id: str, source: str | None, session_pid: int) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "GEMINI", "LITECODE"))
    }
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["LITEHARNESS_CLI"] = "claude-code"
    env["LITEHARNESS_MODEL"] = "test-model"
    env["LITEHARNESS_SESSION_PID"] = str(session_pid)
    env["CLAUDE_CODE_SESSION_ID"] = session_id
    if source:
        env["LITEHARNESS_HOOK_SOURCE"] = source
    return env


def register(home: str, ident: str, source: str, session_pid: int) -> int:
    payload = {
        "session_id": ident,
        "source": source,
        "hook_event_name": "SessionStart",
        "transcript_path": str(Path(home) / f"{ident}.jsonl"),
    }
    r = subprocess.run(
        [sys.executable, "-m", "liteharness.hooks", "register"],
        input=json.dumps(payload),
        env=base_env(home, ident, source, session_pid),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return r.returncode


def one_run(gap_seconds: float, run_no: int) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        home = str(Path(tmp) / "fakehome")
        Path(home).mkdir(parents=True, exist_ok=True)
        session_pid = os.getpid()

        register(home, STARTUP, "startup", session_pid)

        # The watcher, launched with the PRE-RESUME environment — it can only
        # ever name STARTUP, exactly like a watcher spawned from a session-start
        # shell snapshot.
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "liteharness.hooks", "watch-auto"],
            env=base_env(home, STARTUP, None, session_pid),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        lines: list[str] = []

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line.rstrip())

        threading.Thread(target=pump, daemon=True).start()

        try:
            armed_by = time.monotonic() + 10
            while time.monotonic() < armed_by:
                if any("Watching inbox for agent" in ln for ln in lines):
                    break
                time.sleep(0.2)
            armed_on_startup = any(STARTUP in ln for ln in lines)
            print(f"  run {run_no}: armed on STARTUP = {armed_on_startup}")
            if not armed_on_startup:
                print("  UNSOUND: the watcher did not arm on the startup id; nothing was tested")
                return False

            # ── the gap. On Ryan's box this was 21 seconds. ──────────────────
            time.sleep(gap_seconds)
            register(home, RESUMED, "resume", session_pid)
            print(f"  run {run_no}: takeover registered {gap_seconds:.0f}s after the arm")

            # The sender addresses the id the REGISTRY shows — what every other
            # seat does, and what silently went nowhere before this fix.
            subprocess.run(
                [sys.executable, "-m", "liteharness.cli", "send", RESUMED, BODY, "--from", SENDER,
                 "--force"],
                env=base_env(home, SENDER, None, session_pid),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if any(BODY in ln for ln in lines):
                    break
                time.sleep(0.3)

            delivered = any(BODY in ln for ln in lines)
            repointed = any("RE-POINTED" in ln for ln in lines)
            print(f"  run {run_no}: re-pointed = {repointed}, delivered = {delivered}")
            if not delivered:
                print("  --- watcher output ---")
                for ln in lines:
                    print("   ", ln)
            return delivered
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap-seconds", type=float, default=20.0)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    results = []
    for i in range(1, args.runs + 1):
        print(f"run {i} of {args.runs} (gap {args.gap_seconds:.0f}s)")
        results.append(one_run(args.gap_seconds, i))

    ok = all(results)
    print(f"\nT563 e2e: {sum(results)}/{len(results)} runs delivered — {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
