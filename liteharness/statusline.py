"""
Claude Code status line for LiteHarness.

Reads the status JSON Claude Code writes to stdin and prints ONE line:

    O-5-1M  high  develop*  [ 43% 430K/1M ]  5h13% 7d41%
    model   effort branch    context bar      rate limits

Wire it up in settings.json:

    "statusLine": { "type": "command",
                    "command": "python -m liteharness.statusline",
                    "refreshInterval": 5 }

Invoked as a MODULE, never as a script path, for the same reason every other
liteharness hook is: a path embeds the plugin's version directory and its OS, and
both change underneath you. `python -m` is version-locked to whatever wheel is
actually installed.

DESIGN RULE, and it is the only one that really matters here: THIS MUST NEVER RAISE.
A status line runs on every refresh, so a traceback does not fail once — it papers
the user's terminal with stack traces forever, on a surface they cannot easily turn
off. Every field is therefore individually guarded and every one of them is optional:
no git, no rate limits, no effort, a truncated payload or outright malformed JSON all
degrade to a shorter line rather than to noise.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ESC = "\x1b"
RESET = f"{ESC}[0m"
BG_ORANGE = f"{ESC}[48;5;208m"
BG_GRAY = f"{ESC}[48;5;238m"
FG_BLACK = f"{ESC}[38;5;0m"
FG_LIGHT = f"{ESC}[38;5;250m"
FG_BRANCH = f"{ESC}[38;5;141m"
FG_GREEN = f"{ESC}[38;5;114m"
FG_AMBER = f"{ESC}[38;5;220m"
FG_RED = f"{ESC}[38;5;196m"
FG_DIM = f"{ESC}[38;5;245m"
FG_BLUE = f"{ESC}[38;2;96;165;250m"

# Colour is opt-out: some terminals and most CI logs render raw escapes as garbage.
NO_COLOR = bool(os.environ.get("NO_COLOR"))


def _c(code: str, text: str) -> str:
    return text if NO_COLOR else f"{code}{text}{RESET}"


def short_model(display_name: str) -> str:
    """'Claude Opus 5 (1M context)' -> 'O-5-1M'. Unrecognised names pass through."""
    name = re.sub(r"^Claude\s+", "", display_name or "").strip()
    name = re.sub(r"\s*\((\d+[KM])\s+context\)", r"-\1", name)
    for full, abbrev in (("Opus", "O-"), ("Sonnet", "S-"), ("Haiku", "H-"), ("Fable", "F-")):
        name = re.sub(rf"^{full}\s*", abbrev, name)
    return name or "Claude"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{round(n / 1000):,}K"
    return str(n)


def context_bar(ctx: dict) -> str:
    """
    Percentage-filled bar with the numbers written INSIDE it.

    The fill is background colour on spaces rather than block characters: a bar built
    from U+2588 renders as mojibake the moment it meets a console reading the pipe as
    a legacy codepage, and a status line is exactly where that is most visible.
    """
    if not isinstance(ctx, dict):
        return ""
    window = ctx.get("context_window_size") or 200_000
    try:
        window = int(window)
    except (TypeError, ValueError):
        window = 200_000

    used_pct = ctx.get("used_percentage")
    if used_pct is not None:
        try:
            used = round((float(used_pct) / 100.0) * window)
        except (TypeError, ValueError):
            used = 0
    else:
        try:
            used = int(ctx.get("total_input_tokens") or 0) + int(ctx.get("total_output_tokens") or 0)
        except (TypeError, ValueError):
            used = 0

    pct = round((used / window) * 100, 1) if window else 0.0
    text = f" {pct}% {_fmt_tokens(used)}/{_fmt_tokens(window)} "
    if NO_COLOR:
        return text.strip()

    width = len(text)
    filled = max(0, min(width, int((pct / 100.0) * width)))
    return (
        f"{BG_ORANGE}{FG_BLACK}{text[:filled]}{RESET}"
        f"{BG_GRAY}{FG_LIGHT}{text[filled:]}{RESET}"
    )


def git_branch(cwd: str | None) -> str:
    """Branch name, '*' if dirty. Silent when git is absent or this is not a repo."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=cwd or None,
        )
        if head.returncode != 0 or not head.stdout.strip():
            return ""
        branch = head.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=2, cwd=cwd or None,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            branch += "*"
        return _c(FG_BRANCH, branch)
    except Exception:
        # FileNotFoundError (no git), TimeoutExpired (a huge repo), anything else.
        return ""


def _pct_colour(pct: float) -> str:
    return FG_RED if pct >= 80 else FG_AMBER if pct >= 50 else FG_GREEN


def rate_limits(limits: dict) -> str:
    """5h / 7d usage. Plain percentages — no burn-rate projection."""
    if not isinstance(limits, dict):
        return ""
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        pct = window.get("used_percentage")
        if pct is None:
            continue
        try:
            pct = round(float(pct))
        except (TypeError, ValueError):
            continue
        parts.append(_c(_pct_colour(pct), f"{label}{pct}%"))
    return " ".join(parts)


def effort_badge(effort: dict) -> str:
    if not isinstance(effort, dict):
        return ""
    level = str(effort.get("level") or "").lower()
    return {
        "max": _c(FG_RED, "max"),
        "xhigh": _c(FG_AMBER, "xhigh"),
        "high": _c(FG_BLUE, "high"),
        "medium": _c(FG_DIM, "med"),
        "low": _c(FG_DIM, "low"),
    }.get(level, "")


def build_line(data: dict) -> str:
    segments = [
        short_model((data.get("model") or {}).get("display_name", "")),
        effort_badge(data.get("effort") or {}),
        git_branch((data.get("workspace") or {}).get("current_dir") or data.get("cwd")),
        context_bar(data.get("context_window") or {}),
        rate_limits(data.get("rate_limits") or {}),
    ]
    return "  ".join(s for s in segments if s)


def main() -> None:
    try:
        raw = sys.stdin.read()
    except Exception:
        print("")
        return
    if not raw or not raw.strip():
        print("")
        return
    try:
        data = json.loads(raw)
    except Exception:
        # Malformed payload is not worth a traceback on every refresh.
        print("")
        return
    try:
        print(build_line(data if isinstance(data, dict) else {}))
    except Exception:
        # Belt and braces. Whatever went wrong, the user gets a blank line, not a stack.
        print("")


if __name__ == "__main__":
    main()
