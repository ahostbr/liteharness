"""FirstRun's desktop-only human-origin entry into the LiteHarness lifecycle owner.

No `liteharness spawn --human` flag or MCP/HTTP tool exists. This is an IPC
adapter invoked by the desktop process. It is not hostile-code isolation: an
agent with local Python execution can import it, and renderer automation can
invoke the same IPC as a human. The trust boundary is the existing FirstRun UI.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config, fleet_policy, naming
from .cli import _bridge_request, _rename_canvas_seat

INIT_SKILL = "/liteharness:ls-init-liteharness"


def _presence(agent_id: str) -> dict:
    try:
        return json.loads((config.get_root() / "agents" / f"{agent_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _visible_view(session_id: str) -> dict | None:
    """Only the exact PTY in a real canvas leaf is a visible seat."""
    result = _bridge_request("GET", "/context")
    if not isinstance(result, dict) or (result.get("ok") is False) or "activePanes" not in result:
        return None
    # Hidden panes are retained layout, not a seat visible to the user.
    for pane in result.get("activePanes", []):
        for leaf in pane.get("leaves", []):
            if session_id in leaf.get("sessionIds", []):
                return {"pane_id": pane["id"], "leaf_id": leaf["leafId"]}
    return None


def _cleanup(session_id: str, reason: str) -> dict:
    try:
        result = _bridge_request("DELETE", f"/pty/{session_id}")
    except Exception as exc:
        return {"ok": False, "error": f"{reason}; FATAL CLEANUP FAILED for {session_id}: {exc}; check GET /pty/orphans"}
    if isinstance(result, dict) and result.get("success") is True and result.get("ok") is not False and not result.get("error"):
        return {"ok": False, "error": f"{reason}; kill requested for {session_id}; process reap pending"}
    return {"ok": False, "error": f"{reason}; FATAL CLEANUP FAILED for {session_id}: {result}; check GET /pty/orphans"}


def bootstrap(name: str, cwd: str, *, wait: float = 90, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Create one governed, visible Claude orchestrator in an existing project.

    The desktop passes only a name and project root, not a client-declared
    parent, backend, model, or launch command. The policy and bridge resolver
    remain the same owners used by fleet spawn. Unknown creation state is never
    guessed or cleaned up by an invented id.
    """
    if (not isinstance(name, str) or not re.fullmatch(r"[\w -]{2,32}", name, re.UNICODE)
            or not re.search(r"\w", name, re.UNICODE) or not isinstance(cwd, str)):
        return {"ok": False, "error": "FirstRun requires a safe name and project directory; nothing spawned"}
    target = Path(cwd).expanduser()
    if not target.is_absolute() or not target.is_dir():
        return {"ok": False, "error": "Project cwd must be an absolute existing directory; nothing spawned"}
    target = target.resolve()
    name = name.strip()
    if naming.is_name_taken(name):
        return {"ok": False, "error": f"Name {name!r} is already held; nothing spawned"}
    # Claude CLI with NO --model keeps its own default. The same floor used by
    # `spawn` handles a missing/malformed policy; reported governed models are
    # checked after registration, even when the request itself was ungoverned.
    refusal = fleet_policy.gate(None, None, None)
    if refusal:
        return {"ok": False, "error": refusal}
    try:
        resolved = _bridge_request("POST", "/harness/spawn/resolve", {
            "cli": "claude", "name": name, "tier": "orchestrator",
            "cwd": str(target), "prompt": f"{INIT_SKILL} {name}",
        })
    except Exception as exc:
        return {"ok": False, "error": f"Spawn resolution failed; nothing spawned: {exc}"}
    if not isinstance(resolved, dict) or not resolved.get("ok") or not isinstance(resolved.get("request"), dict):
        error = resolved.get("error", "unknown") if isinstance(resolved, dict) else repr(resolved)
        return {"ok": False, "error": f"Spawn resolution refused; nothing spawned: {error}"}
    launch = resolved["request"]
    agent_id = resolved.get("agentId")
    if not isinstance(agent_id, str) or not agent_id:
        return {"ok": False, "error": "Spawn resolution returned no agent ID; nothing spawned"}
    if not isinstance(launch.get("shell"), str) or not isinstance(launch.get("args"), list):
        return {"ok": False, "error": "Spawn resolution returned no native argv; nothing spawned"}
    if f"{INIT_SKILL} {name}" not in launch["args"] or "--model" in launch["args"]:
        return {"ok": False, "error": "Spawn resolution changed the FirstRun prompt/model; nothing spawned"}
    if launch.get("cwd") != str(target):
        return {"ok": False, "error": "Spawn resolution changed project cwd; nothing spawned"}
    env = dict(launch.get("env", {}))
    # The owner-minted ID, not an ambient parent ID. The child's hook uses
    # this identity; an empty spawned_by overrides inherited agent ancestry.
    env.update({"LITEHARNESS_AGENT_ID": agent_id, "LITEHARNESS_REQUESTED_NAME": name,
                "LITEHARNESS_TIER": "orchestrator", "LITEHARNESS_SPAWN_MODE": "canvas",
                "LITEHARNESS_SPAWNED_BY": ""})
    try:
        created = _bridge_request("POST", "/pty/create", {
            "shell": launch["shell"], "args": launch.get("args", []),
            "env": env, "cwd": str(target), "harnessAgentId": agent_id,
        })
    except Exception as exc:
        return {"ok": False, "error": f"Launch may have created a session: {exc}; check GET /pty/orphans"}
    if not isinstance(created, dict) or created.get("error") or created.get("ok") is False:
        return {"ok": False, "error": f"Launch may have created a session: {created}; check GET /pty/orphans"}
    session_id = created.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {"ok": False, "error": "Launch returned no session ID; check GET /pty/orphans"}
    deadline = clock() + wait
    row, view = {}, None
    while clock() < deadline:
        try:
            row = _presence(agent_id)
            view = _visible_view(session_id)
        except Exception as exc:
            return _cleanup(session_id, f"Visible seat verification failed: {exc}")
        registered_cwd = row.get("cwd")
        # Compare resolved absolute paths: Windows spellings/case may differ while
        # pointing at the same directory; a missing/relative value proves nothing.
        cwd_matches = (isinstance(registered_cwd, str) and bool(registered_cwd)
                       and Path(registered_cwd).is_absolute()
                       and Path(registered_cwd).resolve() == target)
        if row.get("name") and (row["name"] != name or row.get("tier") != "orchestrator"
                                or row.get("spawned_by") or not cwd_matches):
            return _cleanup(session_id, f"Accepted identity mismatch: requested {name!r}, registered {row['name']!r}")
        if view and row.get("name") == name:
            break
        sleep(1)
    if not view or row.get("name") != name:
        return _cleanup(session_id, "No verified orchestrator registration and visible canvas leaf")
    try:
        why = fleet_policy.verify_seat(agent_id, config.get_root(), wait=0, backend="claude", allow_silent=True)
    except Exception as exc:
        why = f"Fleet seat verification failed: {exc}"
    if why:
        return _cleanup(session_id, why)
    accepted = row["name"]
    # The hook cannot know its session before create returns. Complete the
    # existing presence linkage so later kill/title routing uses the real PTY.
    row.update({"canvas_session_id": session_id, "pane_id": view["pane_id"],
                "leaf_id": view["leaf_id"], "spawn_mode": "canvas"})
    try:
        config.atomic_write_json(config.get_root() / "agents" / f"{agent_id}.json", row)
    except OSError as exc:
        return _cleanup(session_id, f"Could not link visible session to registered seat: {exc}")
    try:
        rename_error = _rename_canvas_seat(session_id, accepted)
    except Exception as exc:
        rename_error = str(exc)
    if rename_error:
        return {"ok": False, "error": f"Agent registered but canvas title was not updated: {rename_error}; session {session_id} retained for repair",
                "agent_id": agent_id, "session_id": session_id}
    return {"ok": True, "agent_id": agent_id, "session_id": session_id,
            "pane_id": view["pane_id"], "name": accepted, "cwd": str(target)}
