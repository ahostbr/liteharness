"""Resume a named Claude Code seat or a LiteTUI split from its saved identity."""

import json
import os
import re
import sys
import time
from pathlib import Path

from . import config


def _convo_root() -> Path:
    """Use LiteTUI's own data root; never guess another checkout's location."""
    configured = os.environ.get("LITETUI_DATA_ROOT", "").strip()
    if configured:
        remedy = "LITETUI_DATA_ROOT must be an absolute path or ~/..."
        try:
            root = Path(configured).expanduser()
        except RuntimeError as exc:
            raise ValueError(remedy) from exc
        if not root.is_absolute():
            raise ValueError(remedy)
        return root.resolve() / ".convos"
    try:
        from litetui.paths import data_root
    except ImportError as exc:
        raise ValueError("LiteTUI data root unknown: set LITETUI_DATA_ROOT") from exc
    return data_root() / ".convos"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _registry(agent_id: str) -> dict:
    # Retired identity is lookup-only, never restored into the active roster here.
    current = _read_json(config.get_root() / "agents" / f"{agent_id}.json")
    return current or _read_json(config.get_root() / "retired-agents" / f"{agent_id}.json")


_UUID_SHAPE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def resolve_target(value: str | None, convo_id: str | None) -> tuple[str | None, str | None, dict | None]:
    """`--resume` takes a NAME or an agent id (T0236-T2).

    Returns (agent_id, convo_id, index_entry). `index_entry` is None for a raw agent id;
    for a name it is the names.json row, whose convo_id is used instead of the `seat_id`
    scan (an explicit --convo still wins) and whose cwd becomes the default --cwd.
    A value that is a registry id is always an id; otherwise it is looked up as a name.
    """
    if not value:
        return value, convo_id, None
    if _registry(value).get("agent_id") == value or _UUID_SHAPE.match(value):
        return value, convo_id, None
    from . import agent_names
    entry = agent_names.resolve_name(value)
    if entry is None:
        raise ValueError(f"no agent named {value}; liteharness names --list")
    return entry["agent_id"], convo_id or entry["convo_id"], entry


def lookup(agent_id: str | None, convo_id: str | None) -> tuple[str, dict, dict]:
    """Registry is authoritative for name/hierarchy; convo settings for execution."""
    root = _convo_root()
    registry = _registry(agent_id) if agent_id else {}
    if agent_id and registry.get("agent_id") != agent_id:
        raise ValueError(f"no registry record for agent {agent_id}")
    if not convo_id:
        matches = [p.parent.name for p in root.glob("*/settings.json")
                   if _read_json(p).get("seat_id") == agent_id]
        if len(matches) != 1:
            raise ValueError(f"agent {agent_id} has {len(matches)} matching conversations; pass --convo")
        convo_id = matches[0]
    if not convo_id or Path(convo_id).name != convo_id or convo_id in (".", ".."):
        raise ValueError("invalid conversation id")
    settings = _read_json(root / convo_id / "settings.json")
    if not settings or not (root / convo_id / "convo.jsonl").is_file():
        raise ValueError(f"conversation {convo_id} not found under {root}")
    seat_id = settings.get("seat_id")
    if agent_id and seat_id != agent_id:
        raise ValueError(f"conversation {convo_id} belongs to {seat_id}, not {agent_id}")
    agent_id = agent_id or seat_id
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError(f"conversation {convo_id} has no seat id")
    if not registry:
        registry = _registry(agent_id)
    if registry.get("agent_id") != agent_id:
        raise ValueError(f"no registry record for agent {agent_id}")
    return convo_id, settings, registry


def _ancestor_pids(pid: int | str | None) -> list[int]:
    """Live process and at most six parents, stopping on process exit/reused pid."""
    try:
        import psutil

        process = psutil.Process(int(pid))
        chain = []
        for _ in range(7):  # child + six ancestors; do not walk to an unrelated root
            chain.append(process.pid)
            parent = process.parent()
            if parent is None or parent.create_time() > process.create_time():
                break
            process = parent
        return chain
    except ImportError:
        return []
    except (psutil.Error, ValueError, TypeError, OverflowError):
        return []


def _old_pty(registry: dict, sessions: list) -> dict | None:
    sessions = [s for s in sessions if isinstance(s, dict)]
    sid = registry.get("canvas_session_id")
    by_id = [s for s in sessions if sid and s.get("id") == sid]
    if len(by_id) == 1:
        return by_id[0]  # authoritative pane link beats incidental pid matches
    if len(by_id) > 1:
        return None
    for pid in _ancestor_pids(registry.get("session_pid")):
        matches = [s for s in sessions if str(s.get("pid")) == str(pid)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


def _spawn_claude_resume(entry: dict, *, convo_id: str | None, cwd: str | None,
                         pane: str | None, direction: str | None, name: str | None,
                         tier: str | None, model: str | None, backend: str | None,
                         thinking_level: str | None, spawned_by: str | None,
                         prompt: str | None) -> None:
    """Claude resolves sessions by project cwd; never substitute the caller's cwd."""
    from . import cli
    registry = _registry(entry["agent_id"])
    if cli._live_owner_pid(registry):
        raise ValueError(f"live: message {entry['name']} by inbox instead")
    if registry.get("session_pid") and not registry.get("registered_at"):
        from .hooks import _pid_alive
        if _pid_alive(registry["session_pid"]):
            raise ValueError("old seat pid is alive but its registration time is unknown")
    original_cwd = entry.get("cwd")
    if not isinstance(original_cwd, str) or not original_cwd.strip():
        raise ValueError(f"Claude seat {entry['name']} has no original cwd in the name index")
    working_dir = Path(original_cwd).expanduser()
    if not working_dir.is_absolute() or not working_dir.is_dir():
        raise ValueError(f"Claude seat original cwd must be an absolute existing directory: {original_cwd}")
    if cwd and Path(cwd).expanduser().resolve() != working_dir.resolve():
        raise ValueError(f"Claude resume requires its original cwd: {original_cwd}")
    session_id = entry.get("convo_id")
    # This value is typed into the Claude shell command by the existing launcher.
    if not isinstance(session_id, str) or not _UUID_SHAPE.fullmatch(session_id):
        raise ValueError(f"Claude seat {entry['name']} has no valid conversation id in the name index")
    if convo_id and convo_id != session_id:
        raise ValueError(f"Claude resume requires its indexed conversation id: {session_id}")
    if backend or thinking_level:
        raise ValueError("--backend and --thinking-level overrides apply only to LiteTUI resume")
    if name and name != entry["name"]:
        raise ValueError(f"Claude resume keeps its indexed name: {entry['name']}")
    # Delegate to the existing Claude launcher (trust, placement, env and registration).
    # Clearing the native resume fields avoids recursing back into this dispatcher.
    cli.cmd_spawn(spawn_cli="claude", split_mode=True, split_pane=pane,
                  split_direction=direction, cwd=str(working_dir), name=entry["name"],
                  model=model or entry.get("model"), tier=tier or registry.get("tier"),
                  spawned_by=spawned_by if spawned_by is not None else registry.get("spawned_by"),
                  prompt=prompt, additional_args=f"--resume {session_id}")


def spawn_resume(*, agent_id: str | None, convo_id: str | None, pane: str | None,
                 direction: str | None, cwd: str | None, name: str | None,
                 tier: str | None, model: str | None, backend: str | None,
                 thinking_level: str | None, spawned_by: str | None,
                 kill_old: bool, prompt: str | None) -> None:
    from . import owned_launch, owned_resume
    from .agent_store import AgentStore, StoreError
    try:
        root = owned_launch.data_root()
    except owned_launch.RootUnknown:
        root = None  # ONLY missing canonical resolver; corrupt/invalid roots still fail closed
    if root is not None:
        store = AgentStore(root)
        agents = store.list_agents()  # strict even if selected folder is inactive/corrupt
        selected = [a for a in agents if a.agent_id == agent_id or a.name.casefold() == (agent_id or '').casefold()]
        membership = any(d.name == convo_id for a in agents for d in store.list_conversations(a)) if convo_id else False
        if (selected or membership or (agent_id and not _UUID_SHAPE.fullmatch(agent_id)
                                       and store.agent_directory(agent_id).exists())):
            owned_resume.spawn(value=agent_id, conversation_id=convo_id, pane=pane,
                               direction=direction, cwd=cwd, name=name, tier=tier,
                               model=model, backend=backend, thinking_level=thinking_level,
                               spawned_by=spawned_by, kill_old=kill_old, prompt=prompt)
            return
    from . import agent_names, cli
    if kill_old and not agent_id:
        raise ValueError("--kill-old requires --resume <agent-id>")
    agent_id, convo_id, entry = resolve_target(agent_id, convo_id)
    if entry is not None and kill_old:
        # Rule 25: a named seat is never closed to be resumed. Nothing below runs.
        raise ValueError(f"a named resume never closes a seat: message {entry['name']} by inbox, "
                         "or resume it once its process is gone")
    if entry is not None and str(entry.get("backend") or "").split("/", 1)[0].lower() == "claude":
        _spawn_claude_resume(entry, convo_id=convo_id, cwd=cwd, pane=pane,
                             direction=direction, name=name, tier=tier, model=model,
                             backend=backend, thinking_level=thinking_level,
                             spawned_by=spawned_by, prompt=prompt)
        return
    # Existing Claude route above is unchanged. LiteTUI archives never become
    # writable resume targets: require the authoritative owned folder instead.
    owned_resume.spawn(value=agent_id, conversation_id=convo_id, pane=pane,
                       direction=direction, cwd=cwd, name=name, tier=tier,
                       model=model, backend=backend, thinking_level=thinking_level,
                       spawned_by=spawned_by, kill_old=kill_old, prompt=prompt)
