#!/usr/bin/env python3
"""Watch LiteHarness inbox for Copilot CLI and inject messages into a WT pane."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import liteharness.inbox as inbox
from liteharness import terminal_automation
from copilot_targeting import load_saved_target, save_target, select_existing_target


SKILL_ROOT = Path.home() / ".copilot" / "skills" / "liteharness"
STATE_ROOT = SKILL_ROOT / "state"
PID_PATH = STATE_ROOT / "copilot_inbox_watcher.pid"
HEARTBEAT_PATH = STATE_ROOT / "copilot_inbox_watcher.heartbeat.json"
LOG_PATH = STATE_ROOT / "copilot_inbox_watcher.log"
TARGET_PATH = STATE_ROOT / "copilot_inbox_target.json"
HARNESS_AGENTS = Path.home() / ".liteharness" / "agents"
LOCKS_DIR = STATE_ROOT / "message_locks"
POLL_INTERVAL_SEC = 5.0
LOCK_STALE_SEC = 300.0
LOG_MAX_BYTES = 1_048_576  # 1 MB before rotation
NO_TARGET_BACKOFF_BASE = 5.0
NO_TARGET_BACKOFF_MAX = 60.0


_no_target_state: dict[str, float | int] = {
    "next_log_at": 0.0,
    "delay": NO_TARGET_BACKOFF_BASE,
    "suppressed": 0,
}


def _rotate_log_if_needed() -> None:
    try:
        size = LOG_PATH.stat().st_size
    except OSError:
        return
    if size < LOG_MAX_BYTES:
        return
    backup = LOG_PATH.with_suffix(LOG_PATH.suffix + ".1")
    try:
        if backup.exists():
            backup.unlink()
        LOG_PATH.rename(backup)
    except OSError:
        try:
            LOG_PATH.write_text("", encoding="utf-8")
        except OSError:
            pass


def append_log(message: str) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    _rotate_log_if_needed()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _reset_no_target_backoff() -> None:
    suppressed = int(_no_target_state["suppressed"])
    if suppressed:
        try:
            append_log(
                f"headed target available; suppressed {suppressed} "
                f"prior 'no target' messages"
            )
        except Exception:
            pass
    _no_target_state["next_log_at"] = 0.0
    _no_target_state["delay"] = NO_TARGET_BACKOFF_BASE
    _no_target_state["suppressed"] = 0


def _log_no_target(message: str) -> None:
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


def write_pid_file() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": time.time()}
    PID_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def remove_pid_file() -> None:
    try:
        if PID_PATH.exists():
            data = json.loads(PID_PATH.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                PID_PATH.unlink()
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
    HEARTBEAT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_pid_file() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        data = json.loads(PID_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = (result.stdout or "").strip()
    if not output or output.startswith("INFO:"):
        return False
    return str(pid) in output


def another_watcher_is_active() -> bool:
    existing_pid = read_pid_file()
    return bool(existing_pid and existing_pid != os.getpid() and pid_is_alive(existing_pid))


def resolve_agent_id(explicit_agent_id: str | None) -> str | None:
    if explicit_agent_id:
        return explicit_agent_id

    try:
        data = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    agent_id = data.get("agent_id")
    if isinstance(agent_id, str) and agent_id:
        return agent_id

    env_agent_id = os.environ.get("LITEHARNESS_AGENT_ID")
    if env_agent_id:
        return env_agent_id

    copilot_agents: list[str] = []
    if HARNESS_AGENTS.exists():
        for path in HARNESS_AGENTS.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("cli") != "copilot-cli":
                continue
            candidate = payload.get("agent_id")
            if not isinstance(candidate, str) or not candidate:
                candidate = path.stem
            copilot_agents.append(candidate)
    if len(copilot_agents) == 1:
        return copilot_agents[0]
    return None


def registered_agent_ids() -> set[str]:
    ids: set[str] = set()
    if not HARNESS_AGENTS.exists():
        return ids
    for path in HARNESS_AGENTS.glob("*.json"):
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
    return bool(sender) and sender in registered_agent_ids()


def load_target() -> dict | None:
    data = load_saved_target()
    if not data:
        return None
    if data.get("mode") != "windows-terminal-headed":
        return None
    window_handle = data.get("window_handle")
    pane_id = data.get("pane_id")
    if isinstance(window_handle, int) and isinstance(pane_id, int):
        return data
    return None


def clear_target(reason: str) -> None:
    try:
        TARGET_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    append_log(f"cleared headed target: {reason}")


def refresh_target(agent_id: str, *, previous: dict | None) -> dict | None:
    try:
        target = select_existing_target(agent_id, previous=previous)
    except Exception as exc:
        append_log(f"target refresh failed: {exc!r}")
        return None
    if not target:
        return None
    return save_target(target)


def _is_target_message(message: dict, agent_id: str) -> bool:
    to = str(message.get("to", ""))
    if not to or message.get("from") == agent_id:
        return False
    return to == agent_id or to == "broadcast"


def _load_message(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_target_messages(agent_id: str) -> list[dict]:
    inbox.ensure_dirs()
    messages: list[dict] = []
    for directory in (inbox.INBOX_NEW,):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix != ".json":
                continue
            message = _load_message(path)
            if message is None or not _is_target_message(message, agent_id):
                continue
            message["_path"] = str(path)
            messages.append(message)
    return messages


def message_lock_path(message: dict) -> Path:
    filename = Path(str(message.get("_path", ""))).name
    if not filename:
        filename = str(message.get("id", "unknown"))
    return LOCKS_DIR / f"{filename}.lock"


def clear_stale_lock(lock_path: Path) -> bool:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}

    created_at = payload.get("created_at")
    pid = payload.get("pid")
    is_stale = False
    if isinstance(created_at, (int, float)) and (time.time() - float(created_at)) > LOCK_STALE_SEC:
        is_stale = True
    if isinstance(pid, int) and pid > 0 and not pid_is_alive(pid):
        is_stale = True
    if not payload:
        is_stale = True
    if not is_stale:
        return False
    try:
        lock_path.unlink()
        return True
    except OSError:
        return False


def acquire_message_lock(message: dict) -> Path | None:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = message_lock_path(message)
    payload = {
        "pid": os.getpid(),
        "created_at": time.time(),
        "message_id": message.get("id"),
    }

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if clear_stale_lock(lock_path):
                continue
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return lock_path


def release_message_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def complete_message(message: dict, *, result: str) -> bool:
    if inbox.complete(message, result=result):
        return True

    filename = Path(str(message.get("_path", ""))).name
    if not filename:
        return False

    src = inbox.INBOX_CUR / filename
    dst = inbox.INBOX_DONE / filename
    if dst.exists() and not src.exists():
        return True
    if not src.exists():
        return False

    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if payload:
        payload["result"] = result
        payload["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            src.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    try:
        os.replace(str(src), str(dst))
        return True
    except OSError:
        return False


def format_injected_prompt(message: dict, agent_id: str) -> str:
    sender = str(message.get("from", "unknown"))
    msg_type = str(message.get("type", "notification"))
    project = message.get("project")
    thread = message.get("thread_id")
    body = str(message.get("body", "")).strip()

    lines = [
        "[LiteHarness inbox message auto-injected by Copilot watcher]",
        f"From: {sender}",
        f"Type: {msg_type}",
    ]
    if project:
        lines.append(f"Project: {project}")
    if thread:
        lines.append(f"Thread: {thread}")
    lines.extend(
        [
            "Body:",
            body,
            "",
            "If a reply is needed, use LiteHarness inbox rather than plain terminal text.",
            f'Reply command: python -m liteharness.cli send {sender} "your reply" --from {agent_id}',
        ]
    )
    return "\n".join(lines)


def try_pty_injection(message: dict, agent_id: str) -> bool:
    """Format Copilot-style prompt, then delegate to terminal_automation.try_pty_inject.

    Copilot prompts include a reply command specific to the agent_id, so formatting
    happens in this watcher; the actual PTY write shell lives in terminal_automation.
    """
    prompt = format_injected_prompt(message, agent_id)
    return terminal_automation.try_pty_inject(
        agent_id,
        prompt,
        log_fn=append_log,
        message_id=str(message.get("id", "")),
        sender=str(message.get("from", "")),
    )


def inject_message_into_target(message: dict, target: dict, agent_id: str) -> bool:
    if os.environ.get("LITESUITE_BRIDGE_TOKEN"):
        append_log("skipping UIAutomation injection inside LiteSuite desktop environment")
        return False

    window_handle = int(target["window_handle"])
    pane_id = int(target["pane_id"])
    prompt = format_injected_prompt(message, agent_id)
    def run_send(current_target: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "liteharness.cli",
                "send-input",
                "--headed",
                f"{int(current_target['window_handle'])}:{int(current_target['pane_id'])}",
                prompt,
            ],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    result = run_send(target)
    if result.returncode == 0:
        append_log(
            f"injected message {message.get('id', '')} from {message.get('from', 'unknown')} "
            f"into {window_handle}:{pane_id} using submit_key=enter"
        )
        return True

    append_log(
        f"send-input failed for {window_handle}:{pane_id}: "
        f"rc={result.returncode} stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}"
    )
    refreshed = refresh_target(agent_id, previous=target)
    if refreshed:
        retry = run_send(refreshed)
        if retry.returncode == 0:
            append_log(
                f"retry injection succeeded for message {message.get('id', '')} into "
                f"{int(refreshed['window_handle'])}:{int(refreshed['pane_id'])}"
            )
            return True
        append_log(
            f"retry send-input failed for {int(refreshed['window_handle'])}:{int(refreshed['pane_id'])}: "
            f"rc={retry.returncode} stdout={retry.stdout.strip()!r} stderr={retry.stderr.strip()!r}"
        )
    clear_target(f"send-input failed for {window_handle}:{pane_id}")
    return False


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


def process_incoming_messages(agent_id: str) -> int:
    if os.environ.get("LITESUITE_BRIDGE_TOKEN"):
        return 0

    target = load_target()
    if not target:
        _log_no_target("no headed target captured; skipping injection scan")
        return 0
    _reset_no_target_backoff()

    delivered = 0
    for message in list_target_messages(agent_id):
        lock_path = acquire_message_lock(message)
        if lock_path is None:
            continue

        sender = str(message.get("from", ""))
        path = Path(message.get("_path", ""))
        try:
            if sender == agent_id:
                if path.parent == inbox.INBOX_NEW and inbox.claim(message):
                    message["_path"] = str(inbox.INBOX_CUR / path.name)
                    complete_message(message, result="ignored-self-sent-message")
                elif path.parent == inbox.INBOX_CUR and Path(message["_path"]).exists():
                    complete_message(message, result="ignored-self-sent-message")
                continue

            if not sender_is_whitelisted(sender):
                append_log(f"ignored non-whitelisted sender {sender!r}")
                if path.parent == inbox.INBOX_NEW and inbox.claim(message):
                    message["_path"] = str(inbox.INBOX_CUR / path.name)
                    complete_message(message, result="ignored-non-whitelisted-sender")
                elif path.parent == inbox.INBOX_CUR and Path(message["_path"]).exists():
                    complete_message(message, result="ignored-non-whitelisted-sender")
                continue

            if path.parent == inbox.INBOX_NEW:
                if not inbox.claim(message):
                    continue
                message["_path"] = str(inbox.INBOX_CUR / path.name)
            elif path.parent == inbox.INBOX_CUR:
                if not Path(message["_path"]).exists():
                    continue
            else:
                continue

            # Try PTY stdin first (no window focus needed), then UIAutomation.
            if try_pty_injection(message, agent_id):
                if complete_message(message, result="auto-injected-via-pty-stdin"):
                    delivered += 1
                else:
                    append_log(
                        f"complete failed for PTY-injected message {message.get('id', '')}; "
                        f"message may remain in cur/"
                    )
            elif inject_message_into_target(message, target, agent_id):
                if complete_message(message, result="auto-injected-into-copilot-terminal"):
                    delivered += 1
                else:
                    append_log(
                        f"complete failed for injected message {message.get('id', '')}; "
                        f"message may remain in cur/"
                    )
            else:
                if return_claimed_message_to_new(message):
                    append_log(f"delivery failed for message {message.get('id', '')}; returned to new/")
                else:
                    append_log(f"delivery failed for message {message.get('id', '')}; unable to return to new/")
        finally:
            release_message_lock(lock_path)
    return delivered


def run_once(agent_id: str, *, echo: bool) -> None:
    delivered = process_incoming_messages(agent_id)
    if echo:
        if delivered:
            print(f"Delivered {delivered} LiteHarness message(s) for {agent_id}.")
        else:
            print(f"No new LiteHarness messages for {agent_id}.")
    write_heartbeat(last_scan_at=time.time())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", help="Full LiteHarness agent ID to watch.")
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    parser.add_argument("--print", dest="echo", action="store_true", help="Print scan results.")
    args = parser.parse_args()

    agent_id = resolve_agent_id(args.agent_id)
    if not agent_id:
        append_log("watcher could not resolve a Copilot agent id")
        write_heartbeat(last_error="missing-agent-id")
        if args.echo:
            print("No Copilot LiteHarness agent ID found.")
        return 1

    if another_watcher_is_active():
        append_log("another watcher is already active; exiting duplicate instance")
        return 0

    write_pid_file()
    atexit.register(remove_pid_file)

    run_once(agent_id, echo=args.echo)
    if args.once:
        return 0

    while True:
        try:
            time.sleep(POLL_INTERVAL_SEC)
            run_once(agent_id, echo=False)
        except Exception as exc:
            append_log(f"watch loop failed: {exc!r}")
            write_heartbeat(last_error=repr(exc))


if __name__ == "__main__":
    raise SystemExit(main())
