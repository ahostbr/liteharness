"""Opt-in mail clerk lifecycle and exact forwarding; never a second inbox reader."""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config, inbox
from .clerk_route import clerk_is_live, is_live_orchestrator, resolve_orchestrator

DEFAULT_MODELS = ["gpt-6-astra", "gpt-6.1-sol", "claude-sonnet-5-5", "claude-haiku-5-5"]
# Shipped empty on purpose: which senders are a human's own sources is a fact about one machine,
# so it lives in that machine's clerk.json ("direct_senders"), never in the package. `enable`
# keeps a list that is already there.
DIRECT_SENDERS: list[str] = []
ACTIONS = ("acked", "answered", "forwarded", "relayed", "digested")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _read_config() -> dict:
    path = config.get_root() / "clerk.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read clerk configuration: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("clerk.json must contain an object")
    return value


def _save(value: dict) -> None:
    config.atomic_write_json(config.get_root() / "clerk.json", value)


def _id(value: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("Expected a safe registered agent id")
    return value


def _remember(cfg: dict, orchestrator_id: str) -> None:
    """Record who the orchestrator is NOW. Advisory only: nothing routes on it (clerk_route decides live).
    Other readers of clerk.json get a current id, and the name outlives the row it was read from."""
    cfg["orchestrator_id"] = orchestrator_id
    name = _registrations().get(orchestrator_id, {}).get("name")
    if isinstance(name, str) and name.strip():
        cfg["orchestrator_name"] = name.splitlines()[0][:128]


def enable(orchestrator_id: str | None = None) -> None:
    """Switch routing on. `orchestrator_id` is optional: without it the live orchestrator is recorded if
    there is one. A stored id that has gone stale never refuses this and is simply replaced."""
    cfg = _read_config()
    target = _id(orchestrator_id) if orchestrator_id else resolve_orchestrator(cfg)
    cfg["enabled"] = True
    if target:
        _remember(cfg, target)
    cfg.setdefault("direct_senders", list(DIRECT_SENDERS))
    cfg.setdefault("allowed_models", list(DEFAULT_MODELS))
    _save(cfg)


def disable() -> None:
    cfg = _read_config()
    cfg["enabled"] = False
    _save(cfg)


def status() -> dict:
    cfg = _read_config()
    seat = cfg.get("clerk_id")
    return {"enabled": cfg.get("enabled") is True,
            "online": bool(seat and SAFE_ID.fullmatch(str(seat)) and clerk_is_live(seat)),
            "clerk_id": seat, "model": cfg.get("model"),
            # Read-only: who mail would be treated as "the orchestrator" right now, and the stored value.
            "orchestrator_id": resolve_orchestrator(cfg), "stored_orchestrator_id": cfg.get("orchestrator_id")}


def _registrations() -> dict[str, dict]:
    rows = {}
    for path in (config.get_root() / "agents").glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(row, dict):
                rows[path.stem] = row
        except (OSError, ValueError):
            continue
    return rows


def _name(agent_id: str) -> str:
    value = _registrations().get(agent_id, {}).get("name") or agent_id
    return str(value).splitlines()[0][:128]


def start(*, pane: str, spawn_cli: str = "litetui", model: str | None = None,
          backend: str | None = None, cwd: str | None = None) -> dict:
    from . import agent_names, cli

    if not pane or not pane.strip():
        raise ValueError("--pane must name an explicit target pane; no default placement")
    cfg = _read_config()
    if cfg.get("enabled") is not True:
        raise ValueError("Clerk is disabled; enable --for <orchestrator-id> first")
    orchestrator = resolve_orchestrator(cfg)
    if not orchestrator:
        raise ValueError("No live orchestrator found; start the orchestrator seat first. "
                         "The Clerk reports to whichever live agent holds the orchestrator role")
    if orchestrator != cfg.get("orchestrator_id"):
        _remember(cfg, orchestrator)
        _save(cfg)
    if spawn_cli not in ("litetui", "claude-code"):
        raise ValueError("Clerk supports --cli litetui or claude-code")
    model = model or ("gpt-6-astra" if spawn_cli == "litetui" else "claude-sonnet-5-5")
    allowed = cfg.get("allowed_models", [])
    if not isinstance(allowed, list) or not all(isinstance(m, str) for m in allowed):
        raise ValueError("allowed_models must be a list of model names")
    if model not in allowed:
        raise ValueError(f"Model {model!r} refused. Allowed models: {', '.join(allowed)}")
    if spawn_cli == "claude-code":
        if not model.startswith("claude-") or backend:
            raise ValueError("Claude Code requires a Claude model and no --backend")
    else:
        if model.startswith("claude-"):
            raise ValueError("Claude models require --cli claude-code")
        backend = backend or ("codex" if model in DEFAULT_MODELS[:2] else None)
        if backend not in ("codex", "lmstudio", "llamacpp", "ninfer"):
            raise ValueError("Specify a supported --backend for the allowed local model")
        if model in DEFAULT_MODELS[:2] and backend != "codex":
            raise ValueError("Hosted Astra/Sol models require --backend codex")
    before = _registrations()
    held = agent_names.resolve_name("Clerk")
    seat = cfg.get("clerk_id") or (held or {}).get("agent_id")
    if seat:
        _id(seat)
        if seat == orchestrator:
            raise ValueError("Clerk must have its own identity, never the orchestrator id")
        if clerk_is_live(seat):
            raise ValueError("Clerk is already online; use its normal inbox, not a second start")
    role = (Path(__file__).parent / "prompts/clerk.md").read_text(encoding="utf-8")
    role = role.replace("<orchestrator name>", _name(orchestrator)).replace("<orchestrator-id>", orchestrator)
    senders = cfg.get("direct_senders")
    senders = [s for s in senders if isinstance(s, str)] if isinstance(senders, list) else []
    role = role.replace("<direct senders>", ", ".join(f"`{s}`" for s in senders) or "none configured")
    role_file = config.get_root() / "clerk" / f"role-{orchestrator}.md"
    role_file.parent.mkdir(parents=True, exist_ok=True)
    role_file.write_text(role, encoding="utf-8")
    cli.cmd_spawn(
        spawn_cli="claude" if spawn_cli == "claude-code" else "litetui",
        # --thinking-level is a LiteTUI option: a fresh Claude spawn and a Claude resume both refuse it.
        model=model, backend=backend, thinking_level="high" if spawn_cli == "litetui" else None,
        cognitive="aurelius",
        cwd=str(Path(cwd or os.getcwd()).resolve()), name="Clerk", tier="worker",
        split_mode=True, split_pane=pane, spawned_by=orchestrator, prompt=role, resume_agent_id=seat,
        system_prompt_file=str(role_file) if spawn_cli == "litetui" else None,
    )
    # Existing spawn owns launch/wait/registration. Never publish an inferred UUID
    # merely because a launcher printed success, nor adopt an unrelated old row.
    candidates = [agent for agent, row in _registrations().items()
                  if (agent == seat if seat else agent not in before)
                  and agent != orchestrator and row.get("name") == "Clerk"
                  and row.get("spawned_by") == orchestrator and clerk_is_live(agent)]
    if len(candidates) != 1:
        raise ValueError("Spawn returned without one confirmed Clerk registration; inspect before retrying")
    current = _read_config()
    if current != cfg:
        raise ValueError("Clerk configuration changed during spawn; seat may exist, routing not updated")
    cfg.update(clerk_id=candidates[0], model=model, cli=spawn_cli, backend=backend)
    _save(cfg)
    return status()


def _owned_config() -> dict:
    cfg = _read_config()
    seat = _id(cfg.get("clerk_id"))
    if config.get_agent_id() != seat:
        raise ValueError("Forward/digest requires the Clerk's own identity")
    return cfg


def _target(cfg: dict, msg: dict) -> str | None:
    """The orchestrator a message is for: the one it was addressed to if that agent is still a live
    orchestrator, else the live orchestrator now. None when there is none: never the stored id."""
    addressed = msg.get("original_to")
    if addressed != cfg.get("clerk_id") and is_live_orchestrator(addressed, cfg):
        return addressed
    return resolve_orchestrator(cfg)


def _messages():
    # Read-only lookup by exact message id. Normal watchers retain claim ownership.
    for folder in (inbox.INBOX_NEW, inbox.INBOX_CUR, inbox.INBOX_DONE):
        for path in folder.glob("*.json"):
            try:
                msg = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(msg, dict):
                    yield msg
            except (OSError, ValueError):
                continue


def _original(msg_id: str, cfg: dict) -> dict:
    matches = [msg for msg in _messages() if msg.get("id") == msg_id]
    if len(matches) != 1:
        raise ValueError("Expected exactly one original maildir message with that id")
    msg = matches[0]
    if msg.get("to") != cfg["clerk_id"]:
        raise ValueError("Original message is not addressed to the Clerk")
    if not isinstance(msg.get("from"), str):
        raise ValueError("Original message has no sender identity")
    return msg


def message_body(msg: dict) -> str:
    if isinstance(msg.get("body"), str):
        return msg["body"]
    payload = msg.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return payload["text"]
    raise ValueError("Original message has no textual body; inspect its payload, do not paraphrase")


def _digest(orchestrator: str, msg: dict, action: str, summary: str, **extra) -> None:
    # One digest per orchestrator id the mail was for; scripts/clerk_audit.py reads all of them.
    path = config.get_root() / "clerk" / f"digest-{_id(orchestrator)}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "msg_id": msg["id"],
           "from": msg["from"], "from_name": _name(msg["from"]),
           "summary": summary[:140], "action": action, **extra}
    if action == "forwarded" and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            prior = json.loads(line)
            if (prior.get("msg_id") == msg["id"] and prior.get("action") == action
                    and prior.get("forwarded_id") == extra.get("forwarded_id")):
                return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def record_digest(msg_id: str, action: str, summary: str) -> None:
    if action not in ACTIONS or action == "forwarded":
        raise ValueError("Use clerk forward for forwarded receipts, not a claimed digest action")
    cfg = _owned_config()
    msg = _original(msg_id, cfg)
    # A routine receipt must not be lost while the orchestrator is offline: the stored id is good
    # enough to NAME the digest file, though never to address mail.
    orchestrator = _target(cfg, msg) or cfg.get("orchestrator_id")
    if not orchestrator:
        raise ValueError("No orchestrator is live or recorded; cannot name the digest")
    _digest(orchestrator, msg, action, summary)


def forward(msg_id: str) -> str:
    from .mod_inbox import _locked

    cfg = _owned_config()
    # Reuse the existing nonblocking, crash-released OS lock. Contention refuses
    # visibly; it is never reported as a successful or duplicate forward.
    with _locked(config.get_root() / "clerk" / f"forward-{cfg['clerk_id']}.json"):
        return _forward(msg_id, cfg)


def _forward(msg_id: str, cfg: dict) -> str:
    msg = _original(msg_id, cfg)
    body = message_body(msg)
    orchestrator = _target(cfg, msg)
    if not orchestrator:
        # Sending to the stored id would be the silent loss this rule exists to end.
        raise ValueError("No live orchestrator to forward to; the original stays in the Clerk's "
                         "mailbox. Retry when an orchestrator is online")
    if orchestrator != cfg.get("orchestrator_id"):
        try:  # keep the advisory record current for other readers; never worth failing a forward
            current = _read_config()
            _remember(current, orchestrator)
            _save(current)
        except (OSError, ValueError):
            pass
    forwarded_body = (f"Clerk (not {_name(orchestrator)}):\n"
                      f"[FWD from {_name(msg['from'])} · msg {msg_id}]\n" + body)
    # Recover a sequential retry after send succeeded but digest append failed.
    existing = [m for m in _messages() if m.get("from") == cfg["clerk_id"]
                and m.get("to") == orchestrator and m.get("reply_to") == msg_id
                and m.get("forwarded_for") == msg["from"] and m.get("body") == forwarded_body]
    forwarded_id = existing[0]["id"] if existing else inbox.send(
        cfg["clerk_id"], orchestrator, forwarded_body,
        forwarded_for=msg["from"], reply_to=msg_id,
        thread_id=msg.get("thread_id"), priority=msg.get("priority", "normal"))
    try:
        _digest(orchestrator, msg, "forwarded", body, forwarded_id=forwarded_id)
    except OSError as exc:
        raise ValueError(f"Forward {forwarded_id} delivered, but digest failed: {exc}") from exc
    return forwarded_id


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="liteharness clerk", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    on = commands.add_parser("enable")
    on.add_argument("--for", dest="orchestrator", default=None,
                    help="Optional and advisory: the live orchestrator is found at routing time")
    commands.add_parser("disable")
    commands.add_parser("status")
    launch = commands.add_parser("start")
    launch.add_argument("--pane", required=True, help="Explicit target pane ID for split placement (no default)")
    launch.add_argument("--cli", choices=("litetui", "claude-code"), default="litetui")
    launch.add_argument("--model")
    launch.add_argument("--backend")
    launch.add_argument("--cwd")
    relay = commands.add_parser("forward")
    relay.add_argument("msg_id")
    digest = commands.add_parser("digest")
    digest.add_argument("msg_id")
    digest.add_argument("--action", choices=ACTIONS, required=True)
    digest.add_argument("--summary", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "enable":
            enable(args.orchestrator)
        elif args.command == "disable":
            disable()
        elif args.command == "start":
            start(pane=args.pane, spawn_cli=args.cli, model=args.model, backend=args.backend, cwd=args.cwd)
        elif args.command == "forward":
            print(f"Forwarded message {forward(args.msg_id)}")
            return
        elif args.command == "digest":
            record_digest(args.msg_id, args.action, args.summary)
            print("Digest appended")
            return
        print(json.dumps(status()))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Clerk refused: {exc}\n")
