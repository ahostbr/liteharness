#!/usr/bin/env python3
"""Watch LiteHarness inbox for this Codex agent and inject into standalone WT."""

from __future__ import annotations

import argparse
import atexit
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import liteharness.inbox as inbox
from liteharness import desktop_automation
from liteharness import terminal_automation


HARNESS_ROOT = Path.home() / ".liteharness"
AGENT_CONFIG_PATH = HARNESS_ROOT / "codex_agent.json"
SESSION_STATE_DIR = HARNESS_ROOT / "codex_sessions"
STATE_ROOT = Path.home() / ".codex" / "memories" / "liteharness"
LEGACY_PID_PATH = STATE_ROOT / "codex_inbox_watcher.pid"
LEGACY_HEARTBEAT_PATH = STATE_ROOT / "codex_inbox_watcher.heartbeat.json"
LEGACY_LOG_PATH = STATE_ROOT / "codex_inbox_watcher.log"
LEGACY_TARGET_PATH = STATE_ROOT / "codex_inbox_target.json"
POLL_INTERVAL_SEC = 5.0
APP_ID = "Codex"
POWERSHELL_EXE = "powershell.exe"
LOG_MAX_BYTES = 1_048_576  # 1 MB before rotation
LOG_ROTATE_KEEP = 1  # keep one .1 backup
NO_TARGET_BACKOFF_BASE = 5.0
NO_TARGET_BACKOFF_MAX = 60.0


_no_target_state: dict[str, float | int] = {
    "next_log_at": 0.0,
    "delay": NO_TARGET_BACKOFF_BASE,
    "suppressed": 0,
}


_ACTIVE_AGENT_ID: str | None = None


def agent_slug(agent_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in agent_id)


def state_path(agent_id: str, suffix: str) -> Path:
    return STATE_ROOT / f"codex_inbox_{agent_slug(agent_id)}_{suffix}"


def active_agent_id() -> str:
    return _ACTIVE_AGENT_ID or read_agent_id() or "unknown"


def pid_path(agent_id: str | None = None) -> Path:
    return state_path(agent_id or active_agent_id(), "watcher.pid")


def heartbeat_path(agent_id: str | None = None) -> Path:
    return state_path(agent_id or active_agent_id(), "watcher.heartbeat.json")


def log_path(agent_id: str | None = None) -> Path:
    return state_path(agent_id or active_agent_id(), "watcher.log")


def target_path(agent_id: str | None = None) -> Path:
    return state_path(agent_id or active_agent_id(), "target.json")


def _reset_no_target_backoff() -> None:
    if _no_target_state["suppressed"]:
        try:
            append_log(
                f"headed target available; suppressed {int(_no_target_state['suppressed'])} "
                f"prior 'no target' messages"
            )
        except Exception:
            pass
    _no_target_state["next_log_at"] = 0.0
    _no_target_state["delay"] = NO_TARGET_BACKOFF_BASE
    _no_target_state["suppressed"] = 0


def _log_no_target(message: str) -> None:
    """Log a 'no target' style message with exponential backoff.

    Resets to a 5s cadence after a successful headed target capture (caller
    should invoke `_reset_no_target_backoff()`); otherwise doubles up to 60s.
    """
    now = time.time()
    next_log_at = float(_no_target_state["next_log_at"])
    if now < next_log_at:
        _no_target_state["suppressed"] = int(_no_target_state["suppressed"]) + 1
        return
    suffix = ""
    suppressed = int(_no_target_state["suppressed"])
    if suppressed:
        suffix = f" (suppressed {suppressed} repeats)"
    append_log(message + suffix)
    delay = float(_no_target_state["delay"])
    _no_target_state["next_log_at"] = now + delay
    _no_target_state["delay"] = min(delay * 2.0, NO_TARGET_BACKOFF_MAX)
    _no_target_state["suppressed"] = 0


def _rotate_log_if_needed() -> None:
    path = log_path()
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < LOG_MAX_BYTES:
        return
    backup = path.with_suffix(path.suffix + ".1")
    try:
        if backup.exists():
            backup.unlink()
        path.rename(backup)
    except OSError:
        try:
            path.write_text("", encoding="utf-8")
        except OSError:
            pass


def read_agent_id() -> str | None:
    env_agent_id = os.environ.get("LITEHARNESS_AGENT_ID", "").strip()
    if env_agent_id:
        return env_agent_id

    session_agent_id = read_session_agent_id()
    if session_agent_id:
        return session_agent_id

    if AGENT_CONFIG_PATH.exists():
        try:
            data = json.loads(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        agent_id = data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id

    return None


def read_session_agent_id() -> str | None:
    for env_name in ("CODEX_THREAD_ID", "WT_SESSION"):
        session_key = os.environ.get(env_name, "").strip()
        if not session_key:
            continue
        safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session_key)
        session_path = SESSION_STATE_DIR / f"{safe_key}.json"
        if not session_path.exists():
            continue
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        agent_id = data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    return None


def write_pid_file() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": time.time()}
    pid_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def remove_pid_file() -> None:
    try:
        path = pid_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                path.unlink()
    except (OSError, json.JSONDecodeError):
        pass


def write_heartbeat(*, last_scan_at: float | None = None, last_error: str | None = None) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "updated_at": time.time(),
        "last_scan_at": last_scan_at,
        "last_error": last_error,
    }
    heartbeat_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_log(message: str) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    _rotate_log_if_needed()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_path().open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def registered_agent_ids() -> set[str]:
    agents_dir = HARNESS_ROOT / "agents"
    ids: set[str] = set()
    if not agents_dir.exists():
        return ids
    for path in agents_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        agent_id = data.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            ids.add(agent_id)
        elif path.stem:
            ids.add(path.stem)
    return ids


def sender_is_whitelisted(sender: str) -> bool:
    if not sender:
        return False
    return sender in registered_agent_ids()


def load_target() -> dict | None:
    path = target_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = data.get("mode")
    if mode == "codex-desktop":
        return {
            "mode": mode,
            "window_handle": data.get("window_handle"),
        }
    if mode != "windows-terminal-headed":
        return None
    window_handle = data.get("window_handle")
    pane_id = data.get("pane_id")
    if isinstance(window_handle, int) and isinstance(pane_id, int):
        return {
            "mode": mode,
            "window_handle": window_handle,
            "pane_id": pane_id,
        }
    return None


def clear_target(reason: str) -> None:
    try:
        target_path().unlink(missing_ok=True)
    except OSError:
        pass
    append_log(f"cleared headed target: {reason}")


def powershell_encode(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def show_windows_toast(title: str, body: str) -> None:
    title_b64 = base64.b64encode(xml_escape(title).encode("utf-8")).decode("ascii")
    body_b64 = base64.b64encode(xml_escape(body).encode("utf-8")).decode("ascii")
    script = f"""
$encoding = [System.Text.Encoding]::UTF8
$titleText = $encoding.GetString([System.Convert]::FromBase64String("{title_b64}"))
$bodyText = $encoding.GetString([System.Convert]::FromBase64String("{body_b64}"))
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$doc = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $doc.GetElementsByTagName("text")
$textNodes.Item(0).AppendChild($doc.CreateTextNode($titleText)) | Out-Null
$textNodes.Item(1).AppendChild($doc.CreateTextNode($bodyText)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($toast)
"""
    subprocess.run(
        [POWERSHELL_EXE, "-NoProfile", "-NoLogo", "-EncodedCommand", powershell_encode(script)],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def message_preview(message: dict) -> str:
    sender = str(message.get("from", "unknown"))
    msg_type = str(message.get("type", "notification"))
    body = " ".join(str(message.get("body", "")).split())
    if len(body) > 120:
        body = f"{body[:117]}..."
    return f"{sender} ({msg_type}): {body}" if body else f"{sender} ({msg_type})"


def fetch_targeted_messages(agent_id: str) -> list[dict]:
    try:
        return inbox.poll(agent_id)
    except Exception as exc:
        append_log(f"inbox poll failed: {exc!r}")
        return []


def format_injected_prompt(message: dict) -> str:
    sender = str(message.get("from", "unknown"))
    msg_type = str(message.get("type", "notification"))
    project = message.get("project")
    thread = message.get("thread_id")
    body = str(message.get("body", "")).strip()
    lines = [
        "[LiteHarness inbox message auto-injected from standalone Windows Terminal watcher]",
        f"From: {sender}",
        f"Type: {msg_type}",
    ]
    if project:
        lines.append(f"Project: {project}")
    if thread:
        lines.append(f"Thread: {thread}")
    lines.extend([
        "Body:",
        body,
        "",
        "If a reply is needed, send it through LiteHarness inbox.",
    ])
    return "\n".join(lines)


def inject_message_into_target(message: dict, target: dict) -> bool:
    if os.environ.get("LITESUITE_BRIDGE_TOKEN"):
        append_log("skipping UIAutomation injection inside LiteSuite desktop environment")
        return False

    prompt = format_injected_prompt(message)
    mode = target.get("mode")

    if mode == "codex-desktop":
        try:
            result = desktop_automation.send_to_codex_desktop(
                prompt,
                submit=True,
                restore_mouse=True,
                window_handle=target.get("window_handle"),
            )
        except Exception as exc:
            append_log(f"Codex Desktop injection failed: {exc!r}")
            return False
        if result and result.get("ok"):
            append_log(
                f"injected message {message.get('id', '')} from {message.get('from', 'unknown')} "
                f"into Codex Desktop window {result.get('window', {}).get('handle')}"
            )
            return True
        append_log(f"Codex Desktop injection returned false: {(result or {}).get('error', 'unknown')}")
        return False

    window_handle = target.get("window_handle")
    pane_id = target.get("pane_id")
    if not isinstance(window_handle, int) or not isinstance(pane_id, int):
        append_log(f"invalid headed target: {target!r}")
        clear_target("invalid headed target")
        return False

    try:
        ok = terminal_automation.send_input(window_handle, pane_id, prompt, auto_enter=True)
    except Exception as exc:
        append_log(f"send_input failed for {window_handle}:{pane_id}: {exc!r}")
        clear_target(f"send_input exception for {window_handle}:{pane_id}")
        return False
    if ok:
        append_log(
            f"injected message {message.get('id', '')} from {message.get('from', 'unknown')} "
            f"into {window_handle}:{pane_id}"
        )
    else:
        append_log(f"send_input returned false for {window_handle}:{pane_id}")
        clear_target(f"send_input returned false for {window_handle}:{pane_id}")
    return ok


def return_claimed_message_to_new(message: dict) -> bool:
    src = inbox.INBOX_CUR / Path(message.get("_path", "")).name
    if not src.exists():
        return False
    dst = inbox.INBOX_NEW / src.name
    try:
        os.replace(str(src), str(dst))
    except OSError:
        return False
    return True


def _try_pty_for_message(message: dict, agent_id: str) -> bool:
    """Format the Codex-style prompt, then delegate to terminal_automation.try_pty_inject."""
    prompt = format_injected_prompt(message)
    return terminal_automation.try_pty_inject(
        agent_id,
        prompt,
        log_fn=append_log,
        message_id=str(message.get("id", "")),
        sender=str(message.get("from", "")),
    )


def process_incoming_messages(agent_id: str) -> int:
    if os.environ.get("LITESUITE_BRIDGE_TOKEN"):
        return 0

    delivered = 0
    for message in fetch_targeted_messages(agent_id):
        sender = str(message.get("from", ""))
        if sender == agent_id:
            continue
        if not sender_is_whitelisted(sender):
            append_log(f"ignored non-whitelisted sender {sender!r}")
            continue

        # Short-circuit: skip claim when neither PTY daemon nor headed target
        # is reachable. Prevents claim/unclaim thrash on every poll cycle.
        has_target = load_target() is not None
        has_pty_daemon = False
        try:
            from liteharness.pty_daemon import is_daemon_running
            has_pty_daemon = is_daemon_running()
        except Exception:
            has_pty_daemon = False
        if not has_target and not has_pty_daemon:
            _log_no_target("no PTY daemon and no headed target; skipping claim")
            continue
        _reset_no_target_backoff()

        if not inbox.claim(message):
            continue
        claimed = dict(message)

        # Try PTY stdin first (no window focus needed), then UIAutomation.
        if _try_pty_for_message(claimed, agent_id):
            inbox.complete(claimed, result="auto-injected-via-pty-stdin")
            delivered += 1
        else:
            target = load_target()
            if not target:
                _log_no_target("no headed target and PTY injection failed; returning message to new")
                return_claimed_message_to_new(claimed)
                continue
            if inject_message_into_target(claimed, target):
                result = "auto-injected-into-codex-desktop" if target.get("mode") == "codex-desktop" else "auto-injected-into-standalone-terminal"
                inbox.complete(claimed, result=result)
                delivered += 1
            else:
                if return_claimed_message_to_new(claimed):
                    append_log(f"delivery failed for message {claimed.get('id', '')}; returned to new")
                else:
                    append_log(f"delivery failed for message {claimed.get('id', '')}; unable to return to new")
    return delivered


def alert_for_messages(messages: list[dict], *, echo: bool) -> None:
    if not messages:
        return

    if len(messages) == 1:
        body = message_preview(messages[0])
    else:
        body = f"{len(messages)} new LiteHarness messages. Latest: {message_preview(messages[0])}"

    if echo:
        print(body)
    show_windows_toast("LiteHarness Inbox", body)


def run_once(*, seen_ids: set[str], echo: bool) -> set[str]:
    agent_id = read_agent_id()
    if not agent_id:
        if echo:
            print("No Codex LiteHarness agent ID found.")
        write_heartbeat(last_error="missing-agent-id")
        return seen_ids

    delivered = process_incoming_messages(agent_id)
    if delivered:
        append_log(f"delivered {delivered} message(s) for {agent_id}")
        if echo:
            print(f"Delivered {delivered} LiteHarness message(s) for {agent_id}.")
    elif echo:
        print(f"No new LiteHarness messages for {agent_id}.")
    write_heartbeat(last_scan_at=time.time())
    return seen_ids


def main() -> int:
    global _ACTIVE_AGENT_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    parser.add_argument("--print", dest="echo", action="store_true", help="Print scan results to stdout.")
    args = parser.parse_args()
    _ACTIVE_AGENT_ID = read_agent_id()

    write_pid_file()
    atexit.register(remove_pid_file)

    seen_ids: set[str] = set()
    seen_ids = run_once(seen_ids=seen_ids, echo=args.echo)
    if args.once:
        return 0

    while True:
        try:
            time.sleep(POLL_INTERVAL_SEC)
            seen_ids = run_once(seen_ids=seen_ids, echo=False)
        except Exception as exc:
            append_log(f"watch loop failed: {exc!r}")
            write_heartbeat(last_error=repr(exc))


if __name__ == "__main__":
    raise SystemExit(main())
