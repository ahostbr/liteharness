"""CLI third client: authenticated owner-local transport, existing GUI JSONL verbs."""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import uuid
from pathlib import Path

from . import config

MAX_FRAME = 1024 * 1024


def request(agent_id, command, *, timeout=30):
    from .cli import _live_owner_pid
    try:
        parsed_id = uuid.UUID(agent_id)
        if str(parsed_id) != agent_id.lower():
            raise ValueError("Agent ID must use the full hyphenated UUID form")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("--agent-id must be a full agent UUID") from exc
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("timeout must be between 0 and 300 seconds")
    path = config.get_root() / "agents" / f"{agent_id}.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(row, dict):
        raise ValueError("LiteTUI presence metadata must be an object")
    endpoint = row.get("litetui_rpc")
    if (row.get("agent_id") != agent_id or row.get("cli") != "litetui" or not _live_owner_pid(row)
            or not isinstance(endpoint, dict) or endpoint.get("agent_id") != agent_id
            or endpoint.get("pid") != row.get("session_pid") or endpoint.get("host") != "127.0.0.1"
            or type(endpoint.get("port")) is not int or not 1 <= endpoint["port"] <= 65535):
        raise ValueError("No live authenticated LiteTUI endpoint for this agent; run the updated LiteTUI build with a registered seat. No replacement was launched")
    token_file = endpoint.get("token_path")
    if not isinstance(token_file, str) or not isinstance(endpoint.get("nonce"), str) or not endpoint["nonce"]:
        raise ValueError("Invalid seat-local token metadata")
    token_path = Path(token_file)
    if not token_path.is_absolute() or token_path.is_symlink() or token_path.parent.name != agent_id or token_path.parent.parent.name != "rpc":
        raise ValueError("Invalid seat-local token path")
    if os.name != "nt" and token_path.stat().st_mode & 0o077:
        raise ValueError("Seat token file permissions are not private")
    secret = json.loads(token_path.read_text(encoding="utf-8"))
    if not isinstance(secret, dict):
        raise ValueError("Seat token metadata must be an object")
    if any(secret.get(key) != endpoint.get(key) for key in ("agent_id", "pid", "nonce")):
        raise ValueError("Stale or mismatched seat token identity")
    if not isinstance(secret.get("token"), str) or len(secret["token"]) != 64:
        raise ValueError("Invalid seat token")
    command = {**command, "id": uuid.uuid4().hex}
    raw = (json.dumps(command) + "\n").encode("utf-8")
    if len(raw) > MAX_FRAME:
        raise ValueError("Command exceeds local RPC frame limit")
    dispatched = False
    try:
        with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=timeout) as connection:
            connection.settimeout(timeout)
            with connection.makefile("rb") as stream:
                def receive():
                    frame = stream.readline(MAX_FRAME + 1)
                    if not frame:
                        raise EOFError("Local RPC peer closed")
                    if not frame.endswith(b"\n") or len(frame) > MAX_FRAME:
                        raise ValueError("Peer returned an oversized or incomplete JSONL frame; inspect state before retry")
                    value = json.loads(frame)
                    if not isinstance(value, dict):
                        raise ValueError("Peer returned a non-object frame")
                    return value
                connection.sendall((json.dumps({"agent_id": agent_id, "nonce": endpoint["nonce"],
                                               "token": secret["token"]}) + "\n").encode("utf-8"))
                auth = receive()
                if auth.get("type") != "authenticated" or auth.get("agent_id") != agent_id or auth.get("nonce") != endpoint["nonce"]:
                    raise ValueError("Local RPC authentication refused")
                dispatched = True
                connection.sendall(raw)
                response = receive()
                if response.get("type") != "response" or response.get("id") != command["id"]:
                    raise ValueError("Peer returned a mismatched response correlation id")
                if response.get("ok") is not True:
                    raise ValueError(str(response.get("error") or "LiteTUI operation refused"))
                return response.get("result")
    except (OSError, EOFError) as exc:
        if dispatched:
            raise ValueError("Connection failed after dispatch; outcome may be ambiguous. Inspect state; DO NOT automatically retry.") from exc
        raise ValueError("Local RPC endpoint unavailable; no replacement or authority takeover attempted") from exc


def parser():
    p = argparse.ArgumentParser(prog="liteharness litetui", description="Attach to an existing LiteTUI seat through its GUI JSONL control plane.")
    p.add_argument("--agent-id", help="full UUID of target seat (required except spawn)")
    p.add_argument("--timeout", type=float, default=30)
    verbs = p.add_subparsers(dest="verb", required=True)
    spawn = verbs.add_parser("spawn", help="supported PTY/split launch or explicit resume/reparent")
    modes = spawn.add_mutually_exclusive_group()
    modes.add_argument("--pty", action="store_true")
    modes.add_argument("--split", action="store_true")
    for flag in ("cwd", "backend", "model", "thinking-level", "name", "tier", "spawned-by", "resume", "convo", "pane", "direction", "cognitive", "prompt"):
        spawn.add_argument(f"--{flag}")
    spawn.add_argument("--kill-old", action="store_true")
    for name in ("status", "compact", "reconnect", "read"):
        verbs.add_parser(name)
    load = verbs.add_parser("load")
    load.add_argument("model")
    load.add_argument("--ctx", type=int)
    for name, field in (("unload", "model"), ("model", "model"), ("thinking", "level"), ("backend", "name"), ("send", "text")):
        verbs.add_parser(name).add_argument(field)
    verbs.add_parser("context").add_argument("context", type=int)
    verbs.add_parser("engine").add_argument("action", choices=("start", "stop", "status"))
    return p


def _parent_has_endpoint(caller):
    path = config.get_root() / "agents" / f"{caller}.json"
    if not path.exists():
        return False
    row = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(row, dict) or row.get("agent_id") != caller:
        raise ValueError("Parent identity metadata is malformed")
    if "litetui_rpc" in row and not isinstance(row["litetui_rpc"], dict):
        raise ValueError("Parent advertised endpoint is malformed; refusing offline fallback")
    return "litetui_rpc" in row


def _offline_capabilities(backend, model, timeout):
    # The capability catalogue remains owned by LiteTUI, not this package.
    try:
        from litetui.model_capabilities import offline_capabilities
    except ImportError:
        import shutil
        import subprocess
        executable = shutil.which("litetui")
        if not executable:
            raise ValueError("Updated LiteTUI capability CLI unavailable; install/update LiteTUI through its supported workflow")
        result = subprocess.run([executable, "--capabilities", "--backend", backend, "--model", model],
                                capture_output=True, text=True, timeout=timeout,
                                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        if result.returncode:
            raise ValueError("LiteTUI read-only capability CLI refused; update LiteTUI or inspect its catalogue")
        return json.loads(result.stdout)
    return offline_capabilities(backend, model)


def validate_spawn(caller, backend, model, level, *, timeout=30):
    if level in (None, "default"):
        return
    if not caller or not backend or not model:
        raise ValueError("Explicit thinking requires parent identity, backend and model for capability validation")
    if _parent_has_endpoint(caller):
        # A connected refusal or stale advertised endpoint is final: no fallback
        # may overturn measured negative/unknown capability or identity failure.
        capability = request(caller, {"type": "gui.models.capabilities", "backend": backend, "model": model}, timeout=timeout)
    else:
        capability = _offline_capabilities(backend, model, timeout)
    if level not in capability.get("thinking", {}).get("levels", []):
        raise ValueError(f"SPAWN REFUSED: {backend}/{model} does not support thinking level {level!r}; nothing launched or prompted")


def _spawn(args):
    from . import cli
    caller = args.spawned_by or os.environ.get("LITEHARNESS_AGENT_ID")
    if args.resume or args.convo:
        # Name/native-backend resolution, live-seat refusal and original cwd
        # belong to the existing resume owner. Do not pre-resolve as a raw ID.
        if not args.pty:
            args.split = True
    else:
        if not args.model or not args.backend or not args.thinking_level:
            raise ValueError("spawn requires backend, model and thinking-level")
        if not caller:
            raise ValueError("spawn needs --spawned-by or LITEHARNESS_AGENT_ID")
        validate_spawn(caller, args.backend, args.model, args.thinking_level,
                       timeout=args.timeout)
    cli.cmd_spawn(spawn_cli="litetui", model=args.model, backend=args.backend,
                  thinking_level=args.thinking_level, cwd=args.cwd, prompt=args.prompt,
                  name=args.name, tier=args.tier, spawned_by=caller,
                  pty_mode=args.pty, split_mode=args.split, split_pane=args.pane,
                  split_direction=args.direction, cognitive=args.cognitive,
                  resume_agent_id=args.resume, resume_convo_id=args.convo, kill_old=args.kill_old)


def main(argv):
    p = parser()
    args = p.parse_args(argv)
    if args.verb == "spawn":
        _spawn(args)
        return
    if not args.agent_id:
        p.error("--agent-id is required for attachment; no new seat is implicitly launched")
    commands = {
        "status": {"type": "gui.state"},
        "compact": {"type": "gui.conversations.compact"},
        "reconnect": {"type": "gui.models.reconnect"},
        "read": {"type": "gui.conversations.read"},
    }
    if args.verb == "load":
        command = {"type": "gui.models.load", "slug": args.model}
        if args.ctx is not None:
            command["ctx"] = args.ctx
    elif args.verb == "unload":
        command = {"type": "gui.models.unload", "slug": args.model}
    elif args.verb == "model":
        command = {"type": "gui.models.select", "slug": args.model}
    elif args.verb == "thinking":
        command = {"type": "gui.thinking.set", "level": args.level}
    elif args.verb == "context":
        command = {"type": "gui.context.set", "context": args.context}
    elif args.verb == "backend":
        command = {"type": "gui.backend.set", "name": args.name}
    elif args.verb == "engine":
        command = {"type": f"gui.engine.{args.action}"}
    elif args.verb == "send":
        command = {"type": "gui.prompt.submit", "message": args.text}
    else:
        command = commands[args.verb]
    if command.get("ctx", 1) < 1 or command.get("context", 1) < 1:
        p.error("context/ctx must be a positive integer")
    print(json.dumps(request(args.agent_id, command, timeout=args.timeout), default=str))
