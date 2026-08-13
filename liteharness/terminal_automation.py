"""
Windows Terminal UIAutomation via standalone PowerShell scripts.

Pure Python + PowerShell — no Electron dependency. Provides headed-mode
terminal control: enumerate panes, read buffer contents, send keystrokes,
and focus panes in Windows Terminal windows.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_PS_DIR = Path(__file__).parent / "ps_scripts"

POWERSHELL_PATH = r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"


def _run_ps_script(
    script_name: str,
    args: dict[str, Any] | None = None,
    timeout: int = 10,
) -> Any:
    """Run a .ps1 script via -EncodedCommand and return parsed JSON output."""
    script_path = _PS_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"PS script not found: {script_path}")

    script_text = script_path.read_text(encoding="utf-8")
    encoded = base64.b64encode(script_text.encode("utf-16-le")).decode("ascii")

    env = {**os.environ}
    if args:
        env["LWTT_ARGS"] = json.dumps(args)

    result = subprocess.run(
        [POWERSHELL_PATH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    stdout = result.stdout.strip()
    if not stdout:
        if result.stderr.strip():
            raise RuntimeError(result.stderr.strip())
        return None

    return json.loads(stdout)


def _as_list(value: Any) -> list:
    """Normalize Windows PowerShell 5.1's ConvertTo-Json single-element unwrapping.

    5.1 unwraps EVERY single-element array in the tree, not just the root: a one-pane
    window serializes `panes` as a bare dict (a single chain/shell likewise), so iterating
    yields key strings and consumers crash ('str' object has no attribute 'get'). Fleet
    terminals are single-pane, so this hits every window. Normalize at the choke point —
    @()-wrapping in the .ps1 cannot prevent nested unwrapping on 5.1.
    """
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _registry_agents() -> list[dict]:
    """Harness agent registrations: (name, session_pid, watcher_pid) where known."""
    agents = []
    registry = Path.home() / ".liteharness" / "agents"
    try:
        for record_path in registry.glob("*.json"):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = record.get("name")
            pids = {p for p in (record.get("session_pid"), record.get("watcher_pid")) if p}
            if name and pids:
                agents.append({"name": str(name), "pids": pids})
    except OSError:
        pass
    return agents


def _attribute_chains(windows: list[dict], chains: list[dict]) -> None:
    """Bind each pane chain to the window it belongs to — with evidence, never a guess.

    One WT process hosts many windows (default windowing behavior), so the process tree
    alone cannot say which window a shell chain belongs to. Tiers, strongest first:
      1. window-process: the WT pid owns exactly ONE window -> all its chains are its.
      2. registry: a harness agent's name appears as a whole token in exactly one window
         title AND that agent's session/watcher pid lives in exactly one chain -> bind.
         (Titles are DYNAMIC — claude retitles to the current task while busy — so this
         binds idle fleet windows and honestly skips busy ones.)
      3. elimination: exactly one window and one chain left unbound in a WT process.
    Unbound windows keep shells=[] with shell_attribution='none' — empty-but-honest beats
    the old behavior of stamping every window with the whole process pool.
    """
    by_wt_pid: dict[int, list[dict]] = {}
    for w in windows:
        by_wt_pid.setdefault(w.get("pid", 0), []).append(w)

    chain_bound: set[int] = set()  # indexes into chains

    def bind(window: dict, chain_idx: int, how: str) -> None:
        chain = chains[chain_idx]
        window["shells"] = _as_list(chain.get("shells"))
        window["shell_attribution"] = how
        chain_bound.add(chain_idx)

    for wt_pid, group in by_wt_pid.items():
        pid_chains = [i for i, c in enumerate(chains) if c.get("wt_pid") == wt_pid]

        # tier 1 — sole window of its process owns every chain
        if len(group) == 1:
            window = group[0]
            shells: list[dict] = []
            for i in pid_chains:
                shells.extend(_as_list(chains[i].get("shells")))
                chain_bound.add(i)
            window["shells"] = shells
            window["shell_attribution"] = "window-process" if shells else "none"
            continue

        # tier 2 — harness registry: name-in-title (window side) x pid-in-chain (chain side)
        unbound_windows = list(group)
        for agent in sorted(_registry_agents(), key=lambda a: -len(a["name"])):
            pattern = re.compile(rf"\b{re.escape(agent['name'])}\b", re.IGNORECASE)
            title_hits = [w for w in unbound_windows if pattern.search(w.get("title", "") or "")]
            chain_hits = [
                i for i in pid_chains
                if i not in chain_bound
                and agent["pids"] & {int(p) for p in _as_list(chains[i].get("member_pids"))}
            ]
            if len(title_hits) == 1 and len(chain_hits) == 1:
                bind(title_hits[0], chain_hits[0], "registry")
                unbound_windows.remove(title_hits[0])

        # tier 3 — profile-compatible elimination: a pane's TermControl name is the stable
        # PROFILE title ('Windows PowerShell', '...cmd.exe'), not the dynamic tab title, so
        # it can EXCLUDE windows whose profile cannot have spawned the chain's root shell.
        # Bind only when exactly one compatible unbound window remains for a chain.
        for chain_idx in [i for i in pid_chains if i not in chain_bound]:
            root_stem = str(chains[chain_idx].get("root_name", "")).lower().removesuffix(".exe")
            if not root_stem:
                continue
            compatible = [
                w for w in unbound_windows
                if any(root_stem in (p.get("title", "") or "").lower() for p in _as_list(w.get("panes")))
            ]
            if len(compatible) == 1:
                bind(compatible[0], chain_idx, "profile-elimination")
                unbound_windows.remove(compatible[0])

        # tier 4 — strict blind elimination
        remaining_chains = [i for i in pid_chains if i not in chain_bound]
        if len(unbound_windows) == 1 and len(remaining_chains) == 1:
            bind(unbound_windows[0], remaining_chains[0], "elimination")
            unbound_windows.clear()

        for window in unbound_windows:
            window["shells"] = []
            window["shell_attribution"] = "none"


def list_panes() -> list[dict]:
    """Enumerate all Windows Terminal windows and their panes.

    Returns a list of window dicts, each containing:
      - handle: int (native window handle)
      - title: str
      - pid: int
      - panes: list of pane dicts (id, title, focused, rect, ...)
      - shells: list of shell dicts (pid, name, cmdline, ...) — only the shells of THIS
        window's own pane chain (see _attribute_chains), never the whole process pool
      - shell_attribution: 'window-process' | 'registry' | 'elimination' | 'none'
    """
    data = _run_ps_script("list_panes.ps1", timeout=12)
    if not data:
        return []
    windows = _as_list(data.get("windows"))
    chains = _as_list(data.get("chains"))
    for w in windows:
        w["panes"] = _as_list(w.get("panes"))
    for c in chains:
        c["shells"] = _as_list(c.get("shells"))
        c["member_pids"] = _as_list(c.get("member_pids"))
    _attribute_chains(windows, chains)
    return windows


def read_buffer(window_handle: int, pane_id: int) -> str | None:
    """Read the text buffer of a specific pane.

    Returns the buffer text, or None if the pane was not found.
    """
    data = _run_ps_script(
        "read_buffer.ps1",
        args={"windowHandle": window_handle, "paneId": pane_id},
        timeout=12,
    )
    if not data:
        return None
    b64 = data.get("buffer_b64")
    if not b64:
        return None
    return base64.b64decode(b64).decode("utf-8")


def send_input(window_handle: int, pane_id: int, text: str, auto_enter: bool = True) -> bool:
    """Send keystrokes to a specific pane via SendKeys.

    Automatically appends {ENTER} unless the text already ends with it
    or auto_enter is False. This ensures commands are submitted.

    Uses System.Windows.Forms.SendKeys format:
      - Regular text is sent as-is
      - Special keys: {ENTER}, {TAB}, ^c (Ctrl+C), %x (Alt+X), +{F5} (Shift+F5)

    Returns True if the keys were sent successfully.
    """
    if text.strip().lower() in ("/exit", "/exit{enter}"):
        raise ValueError("/exit is blocked — use /clear to reset sessions")

    if auto_enter and not text.endswith("{ENTER}") and not text.endswith("\n"):
        text += "{ENTER}"

    result = _run_ps_script(
        "send_keys.ps1",
        args={
            "windowHandle": window_handle,
            "paneId": pane_id,
            "keys": text,
            "focusOnly": False,
        },
    )
    return bool(result)


def focus_pane(window_handle: int, pane_id: int) -> bool:
    """Focus a specific pane without sending any keys.

    Returns True if focus was set successfully.
    """
    result = _run_ps_script(
        "send_keys.ps1",
        args={
            "windowHandle": window_handle,
            "paneId": pane_id,
            "focusOnly": True,
        },
    )
    return bool(result)


def split_pane(
    window_handle: int,
    pane_id: int,
    direction: str = "vertical",
) -> bool:
    """Split a pane in the given direction ('vertical' or 'horizontal').

    Returns True if the split succeeded.
    """
    result = _run_ps_script(
        "split_pane.ps1",
        args={
            "windowHandle": window_handle,
            "paneId": pane_id,
            "direction": direction,
        },
    )
    return bool(result)


def command_palette(
    window_handle: int,
    pane_id: int,
    action_name: str,
    args: dict[str, Any] | None = None,
) -> bool:
    """Invoke a Windows Terminal command palette action by name.

    Returns True if the action was invoked successfully.
    """
    result = _run_ps_script(
        "command_palette.ps1",
        args={
            "windowHandle": window_handle,
            "paneId": pane_id,
            "actionName": action_name,
            "args": args,
        },
        timeout=12,
    )
    return bool(result)


def find_pane_by_title(title_substring: str) -> tuple[int, int] | None:
    """Find the first pane whose title contains the given substring.

    Returns (window_handle, pane_id) or None.
    """
    for window in list_panes():
        for pane in window.get("panes", []):
            if title_substring.lower() in (pane.get("title", "") or "").lower():
                return (window["handle"], pane["id"])
    return None


def find_pane_by_shell_name(shell_name: str) -> tuple[int, int] | None:
    """Find the first window/pane that has a shell process matching the name.

    Useful for finding e.g. 'claude.exe' or 'pwsh.exe' panes.
    Returns (window_handle, first_pane_id) or None.
    """
    for window in list_panes():
        for shell in window.get("shells", []):
            if shell_name.lower() in (shell.get("name", "") or "").lower():
                panes = window.get("panes", [])
                if panes:
                    return (window["handle"], panes[0]["id"])
    return None


def find_pane_by_buffer_markers(markers: list[str], sample_chars: int = 2_000_000) -> dict | None:
    """Find a Windows Terminal pane whose visible buffer contains one of the markers.

    This is safer than choosing the focused pane or the first pane in a Windows
    Terminal window. Multiple tabs/windows can share the same Windows Terminal
    process, so process ancestry only identifies the hosting window process, not
    the actual pane receiving input.
    """
    clean_markers = [marker for marker in markers if isinstance(marker, str) and marker.strip()]
    if not clean_markers:
        return None
    data = _run_ps_script(
        "find_pane_by_buffer_markers.ps1",
        args={"markers": clean_markers, "sampleChars": sample_chars},
        timeout=8,
    )
    if not data:
        return None
    match = data.get("match")
    return match if isinstance(match, dict) else None


def pty_send_input(agent_id: str, text: str) -> bool:
    """Write text to a PTY-managed agent's stdin via the daemon.

    Bypasses UIAutomation entirely — no window focus required. Writes
    directly to the ConPTY handle the daemon owns for this agent.

    Returns True if the daemon accepted the write, False if the agent
    is not a PTY session or the daemon is not running.
    """
    from .pty_daemon import send_command
    if not text.endswith("\r") and not text.endswith("\n"):
        text += "\r"
    result = send_command({"cmd": "send-input", "agent_id": agent_id, "text": text})
    return bool(result.get("ok"))


def try_pty_inject(
    agent_id: str,
    prompt: str,
    log_fn=None,
    message_id: str = "",
    sender: str = "",
) -> bool:
    """Attempt PTY stdin injection, logging success/failure via log_fn.

    Shared helper used by Codex and Copilot inbox watchers. Each watcher
    passes its own pre-formatted prompt (since formatting differs) and an
    optional log callback for watcher-specific log paths.
    """
    try:
        ok = pty_send_input(agent_id, prompt)
        if ok and log_fn:
            log_fn(
                f"PTY-injected message {message_id} from {sender or 'unknown'} "
                f"into agent {agent_id}"
            )
        return ok
    except Exception as exc:
        if log_fn:
            log_fn(f"PTY injection attempt failed: {exc!r}")
        return False
