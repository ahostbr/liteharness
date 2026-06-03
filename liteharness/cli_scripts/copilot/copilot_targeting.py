#!/usr/bin/env python3
"""Existing-pane discovery helpers for Copilot LiteHarness watcher scripts."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


SKILL_ROOT = Path.home() / ".copilot" / "skills" / "liteharness"
STATE_ROOT = SKILL_ROOT / "state"
TARGET_PATH = STATE_ROOT / "copilot_inbox_target.json"
PINNED_TARGET_PATH = STATE_ROOT / "copilot_inbox_target.pin.json"


def list_wt_windows() -> list[dict]:
    result = subprocess.run(
        [sys.executable, "-m", "liteharness.cli", "wt-list-panes", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "wt-list-panes failed")
    payload = json.loads(result.stdout)
    return payload if isinstance(payload, list) else []


def _iter_term_panes(windows: list[dict]) -> list[tuple[dict, dict]]:
    panes: list[tuple[dict, dict]] = []
    for window in windows:
        for pane in window.get("panes", []):
            class_name = (pane.get("class_name") or "").strip()
            if "TermControl" in class_name or pane.get("has_text_pattern"):
                panes.append((window, pane))
    return panes


def _candidate_score(window: dict, pane: dict, previous: dict | None) -> int:
    score = 0
    window_title = str(window.get("title", ""))
    pane_title = str(pane.get("title", ""))
    window_title_lower = window_title.lower()
    pane_title_lower = pane_title.lower()

    if pane.get("focused"):
        score += 200
    if "copilot" in window_title_lower:
        score += 80
    if "copilot" in pane_title_lower:
        score += 80

    if previous:
        if window_title and window_title == str(previous.get("window_title", "")):
            score += 140
        if pane_title and pane_title == str(previous.get("pane_title", "")):
            score += 140
        if int(window.get("handle", 0) or 0) == int(previous.get("window_handle", -1) or -1):
            score += 50
        if int(pane.get("id", -1) or -1) == int(previous.get("pane_id", -2) or -2):
            score += 25

    return score


def select_existing_target(
    agent_id: str,
    *,
    previous: dict | None = None,
    window_handle: int | None = None,
    pane_id: int | None = None,
) -> dict | None:
    windows = list_wt_windows()
    term_panes = _iter_term_panes(windows)
    if not term_panes:
        return None

    if window_handle is not None and pane_id is not None:
        for window, pane in term_panes:
            if int(window.get("handle", 0)) == int(window_handle) and int(pane.get("id", -1)) == int(pane_id):
                return {
                    "window_handle": int(window_handle),
                    "pane_id": int(pane_id),
                    "window_title": window.get("title", ""),
                    "pane_title": pane.get("title", ""),
                    "captured_at": time.time(),
                    "mode": "windows-terminal-headed",
                    "submit_key": "enter",
                    "agent_id": agent_id,
                }
        return None

    pinned = load_pinned_target()
    if pinned:
        pinned_handle = pinned.get("window_handle")
        pinned_pane_id = pinned.get("pane_id")
        if isinstance(pinned_handle, int) and isinstance(pinned_pane_id, int):
            for window, pane in term_panes:
                if int(window.get("handle", 0)) == pinned_handle and int(pane.get("id", -1)) == pinned_pane_id:
                    return {
                        "window_handle": pinned_handle,
                        "pane_id": pinned_pane_id,
                        "window_title": window.get("title", ""),
                        "pane_title": pane.get("title", ""),
                        "captured_at": time.time(),
                        "mode": "windows-terminal-headed",
                        "submit_key": "enter",
                        "agent_id": agent_id,
                    }

    best: tuple[int, dict, dict] | None = None
    for window, pane in term_panes:
        score = _candidate_score(window, pane, previous)
        candidate = (score, window, pane)
        if best is None or score > best[0]:
            best = candidate

    if best is None or best[0] <= 0:
        return None

    _, window, pane = best
    return {
        "window_handle": int(window["handle"]),
        "pane_id": int(pane["id"]),
        "window_title": window.get("title", ""),
        "pane_title": pane.get("title", ""),
        "captured_at": time.time(),
        "mode": "windows-terminal-headed",
        "submit_key": "enter",
        "agent_id": agent_id,
    }


def load_saved_target() -> dict | None:
    if not TARGET_PATH.exists():
        return None
    try:
        data = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_target(target: dict) -> dict:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    TARGET_PATH.write_text(json.dumps(target, indent=2), encoding="utf-8")
    return target


def load_pinned_target() -> dict | None:
    if not PINNED_TARGET_PATH.exists():
        return None
    try:
        data = json.loads(PINNED_TARGET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_pinned_target(target: dict) -> dict:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    PINNED_TARGET_PATH.write_text(json.dumps(target, indent=2), encoding="utf-8")
    return target
