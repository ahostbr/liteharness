"""
Claude Code status line for LiteHarness.

Reads the status JSON Claude Code writes to stdin and prints ONE line:

    43% 1M SwiftRelay 1f09ba64 O-5-1M high BUILDER develop* 0.8x 5h13% 7d41% 90h2m 0.7%/h
    bar     name       id       model  effort role  branch   rate limits

The three mode orders below are the three orders of a PowerShell status line script. The
mode comes from LITEHARNESS_STATUSLINE_MODE (a user setting), else
~/.claude/statusline-mode, else "full". The agent id comes from the payload's session_id,
else LITEHARNESS_AGENT_ID (`liteharness spawn` blanks that variable, so an inherited id
never outranks the session's own); the role from LITEHARNESS_ROLE:

    full     bar name id model effort role branch rates
    compact  name id bar effort branch rates
    minimal  bar effort branch rates

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
import time
from pathlib import Path

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
FG_FAINT = f"{ESC}[38;5;240m"
FG_NAME = f"{ESC}[38;5;219m"
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


def agent_badge(session_id: str | None) -> str:
    """'1f09ba64-...' -> 'SwiftRelay 1f09ba64'. Silent when there is no session id.

    WHY THIS EXISTS. Every other segment describes the MODEL or the REPO; none of them
    said WHO. On a machine running several agents at once - which is the entire point of
    the harness - four identical status lines is not a status line, it is wallpaper. The
    PowerShell status line script this layout follows had carried the name and id for
    months, so the packaged version silently lacked them, because the two are never on
    screen at the same time.

    `session_id` is already in the payload Claude Code writes to stdin. It was read by
    nothing here, so the id was present and discarded on every refresh.

    NEVER RAISES, and that is not decoration: get_name() touches the filesystem
    (~/.liteharness/names/<uuid>, plus the config root), so it can fail for reasons that
    have nothing to do with this line - a missing home directory, a permissions change,
    a half-written override. This runs every few seconds on a surface the user cannot
    easily turn off, so a traceback here does not fail once, it papers the terminal
    forever. Degrade to the bare id, then to silence.
    """
    # The id's source order: the payload's session_id, then LITEHARNESS_AGENT_ID only when
    # the payload carries none. `liteharness spawn` blanks that variable because an
    # inherited agent id belongs to somebody else, so it must never outrank the session's
    # own id. The NAME always comes from session_id.
    has_sid = isinstance(session_id, str) and bool(session_id)
    env_id = os.environ.get("LITEHARNESS_AGENT_ID", "").strip()
    if has_sid:
        short = session_id[:8]
    elif env_id:
        short = env_id.split("_")[-1][:8]
    else:
        return ""
    if not has_sid:
        return short
    try:
        from liteharness.naming import get_name

        name = (get_name(session_id) or "").strip()
    except Exception:
        # A name is a nicety; the id is the part that actually disambiguates.
        name = ""
    if name:
        # Name before id: who (name, id) reads before what (model).
        return f"{_c(FG_NAME, name)} {short}"
    return short


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{round(n / 1000):,}K"
    return str(n)


def context_bar(ctx: dict, model: str = "") -> str:
    """
    Percentage-filled bar with the numbers written INSIDE it.

    The fill is background colour on spaces rather than block characters: a bar built
    from U+2588 renders as mojibake the moment it meets a console reading the pipe as
    a legacy codepage, and a status line is exactly where that is most visible.
    """
    if not isinstance(ctx, dict):
        return ""
    # Trust the payload's window; without one, 4.5/4.6 models ran 1M (as the PowerShell script does).
    fallback = 1_000_000 if re.search(r"4\.[56]", model) else 200_000
    window = ctx.get("context_window_size") or fallback
    try:
        window = int(window)
    except (TypeError, ValueError):
        window = fallback

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
    text = f" {pct:g}% {_fmt_tokens(window)} "
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
            ["git", "--no-optional-locks", "status", "--porcelain"],
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


def rate_limits(limits: dict, now: float | None = None) -> str:
    """[ratio] 5hN% 7dN% [XhYm] [N%/h] - the rate block of the PowerShell status line script.

    ratio = 7d used% / elapsed% of the 168h window: 1.0x is exactly sustainable, above
    runs dry before reset. N%/h = remaining 7d % / hours to reset = the safe spend rate.
    """
    if not isinstance(limits, dict):
        return ""
    now = time.time() if now is None else now
    parts, ratio, burn = [], "", ""
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
        text = f"{label}{pct}%"
        try:
            secs = max(0, int(window["resets_at"]) - int(now)) if key == "seven_day" else None
        except (KeyError, TypeError, ValueError):
            secs = None
        if secs is not None:
            # As in the PowerShell script: the countdown sits inside the 7d span, uncoloured.
            text += f" {'' if NO_COLOR else RESET}{secs // 3600}h{(secs % 3600) // 60}m"
            elapsed = 168.0 - secs / 3600.0
            if elapsed > 0.5 and pct > 0:
                r = round(pct / (elapsed / 168.0 * 100), 2)
                ratio = _c(FG_RED if r >= 1.5 else FG_AMBER if r >= 1.0 else FG_GREEN, f"{r:g}x")
            hours = round(secs / 3600, 1)
            if hours > 0:
                b = round((100 - pct) / hours, 1)
                burn = _c(FG_GREEN if b >= 1.0 else FG_AMBER if b >= 0.5 else FG_RED, f"{b:g}%/h")
        parts.append(_c(_pct_colour(pct), text))
    if not parts:
        return ""
    return " ".join(p for p in (ratio, *parts, burn) if p)


def _shimmer(text: str, stops: list[tuple[int, int, int]], phase: float) -> str:
    """Cyclic truecolor gradient whose phase rides wall-clock time, so each refresh
    renders it rotated - a shimmer on the existing refresh cadence, no timer."""
    if NO_COLOR:
        return text
    out, k, n = "", len(stops), len(text)
    for i, ch in enumerate(text):
        seg = ((i / n + phase) % 1.0) * k
        idx = int(seg) % k
        a, b, f = stops[idx], stops[(idx + 1) % k], seg - int(seg)
        r, g, bl = (int(a[j] + (b[j] - a[j]) * f) for j in range(3))
        out += f"{ESC}[1;38;2;{r};{g};{bl}m{ch}"
    return out + RESET


_MAX_STOPS = [(38, 222, 200), (120, 130, 235), (200, 96, 240)]  # teal->blue->magenta
_ULTRA_STOPS = [(210, 180, 255), (124, 58, 237)]  # bright<->deep purple


def effort_badge(effort: dict, session_id: str | None = None) -> str:
    if not isinstance(effort, dict):
        return ""
    level = str(effort.get("level") or "").lower()
    phase = (int(time.time() * 1000) % 4000) / 4000.0
    if level == "max":
        return _shimmer("max", _MAX_STOPS, phase)
    if level == "xhigh":
        # Ultracode reports "xhigh". The agent drops ~/.claude/ultracode/<session_id>
        # while it is on; CLAUDE_CODE_WORKFLOWS is the explicit always-on path.
        ultra = bool(os.environ.get("CLAUDE_CODE_WORKFLOWS"))
        if not ultra and session_id:
            try:
                ultra = (Path.home() / ".claude" / "ultracode" / session_id).exists()
            except Exception:
                ultra = False
        return _shimmer("ultra" if ultra else "xhigh", _ULTRA_STOPS, phase)
    return {
        "high": _c(FG_BLUE, "high"),
        "medium": _c(FG_DIM, "med"),
        "low": _c(FG_FAINT, "low"),
    }.get(level, "")


def display_mode() -> str:
    mode = os.environ.get("LITEHARNESS_STATUSLINE_MODE", "")
    if not mode:
        try:
            mode = (Path.home() / ".claude" / "statusline-mode").read_text(encoding="utf-8")
        except Exception:
            mode = ""
    return mode.strip().lower() or "full"


def build_line(data: dict, mode: str | None = None) -> str:
    model = short_model((data.get("model") or {}).get("display_name", ""))
    bar = context_bar(data.get("context_window") or {}, model)
    badge = agent_badge(data.get("session_id"))
    effort = effort_badge(data.get("effort") or {}, data.get("session_id"))
    role = os.environ.get("LITEHARNESS_ROLE", "").upper()
    branch = git_branch((data.get("workspace") or {}).get("current_dir") or data.get("cwd"))
    rates = rate_limits(data.get("rate_limits") or {})
    mode = mode or display_mode()
    if mode == "minimal":
        segments = [bar, effort, branch, rates]
    elif mode == "compact":
        segments = [badge, bar, effort, branch, rates]
    else:
        # The bar leads: it is the one field a reader acts on, so nothing variable-length
        # may push it off the end of a narrow pane. Then WHO (name, id) before WHAT (model).
        segments = [bar, badge, model, effort, role, branch, rates]
    return " ".join(s for s in segments if s)


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
