"""Owned LiteTUI child launch requests; parent lookup is NOT a write capability.

The resolver supplies executable/role bootstrap. Folder execution and identity
are authoritative on resume; fresh launch requires explicit settled execution.
The child alone acquires/creates the AgentSession before registration or writes.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path

from .agent_store import AgentStore, StoreError, _unlinked, valid_id, valid_name


class RootUnknown(StoreError):
    """No installed canonical resolver or explicit root; not a corrupt-store fallback."""


def data_root(resolution: dict | None = None) -> Path:
    """Require an explicit authoritative root, never guess from cwd or names."""
    if resolution is None:
        resolution = {}
    if not isinstance(resolution, dict):
        raise StoreError('Launch resolution must be an object')
    supplied = resolution.get('liteTuiDataRoot')
    request = resolution.get('request') or {}
    if not isinstance(request, dict) or not isinstance(request.get('env', {}), dict):
        raise StoreError('Launch resolution has malformed request/environment')
    if supplied is None:
        supplied = (request.get('env') or {}).get('LITETUI_DATA_ROOT')
    if supplied is None:
        supplied = os.environ.get('LITETUI_DATA_ROOT')
    if supplied is None:
        try:
            from litetui.paths import data_root as canonical_root
        except ImportError as exc:
            raise RootUnknown('LiteTUI data root unknown; configure LITETUI_DATA_ROOT') from exc
        supplied = str(canonical_root())
    if not isinstance(supplied, str) or not supplied.strip():
        raise StoreError('Owned launch requires nonempty LiteTUI data root')
    root = Path(supplied)
    if not root.is_absolute() or '..' in root.parts:
        raise StoreError('Owned launch root must be absolute without traversal')
    root = _unlinked(root)
    if not root.is_dir():
        raise StoreError('Owned launch data root does not exist')
    env_root = (request.get('env') or {}).get('LITETUI_DATA_ROOT')
    if env_root is not None and env_root != str(root):
        raise StoreError('Resolver data root and child root disagree')
    return root


def request(resolution: dict, *, root: Path, name: str, agent_id: str,
            backend: str, model: str, thinking_level: str,
            fresh: bool = False, conversation_id: str | None = None) -> dict:
    """Bind exact argv/env to one home; no writable legacy fallback."""
    valid_name(name)
    valid_id(agent_id)
    if any(not isinstance(v, str) or not v.strip() for v in (backend, model, thinking_level)):
        raise StoreError('Owned launch requires explicit backend/model/thinking level')
    launch = deepcopy(resolution.get('request'))
    if (not isinstance(launch, dict) or not isinstance(launch.get('args'), list)
            or any(not isinstance(a, str) for a in launch['args'])
            or not isinstance(launch.get('env'), dict)):
        raise StoreError('Resolver returned malformed owned launch request')
    if data_root(resolution) != root:
        raise StoreError('Resolver root differs from selected agent root')
    if fresh:
        resolved_id = valid_id(resolution.get('agentId'))
        if resolved_id != agent_id or launch.get('harnessAgentId') != agent_id:
            raise StoreError('Fresh resolver identity disagrees with child launch')
        store = AgentStore(root)
        # Validate the whole catalog even if this exact folder is absent.
        if any(a.agent_id == agent_id or a.name.casefold() == name.casefold()
               for a in store.list_agents()) or store.agent_directory(name).exists():
            raise StoreError('Fresh agent home or identity already exists; resume it')
    args = launch['args']
    # Resolver may select execution flags, but never a different owned context.
    if any(a == flag or a.startswith(flag + '=') for a in args
           for flag in ('--agent', '--create-agent', '--agent-id', '--convo')):
        raise StoreError('Resolver already selected an agent or conversation')
    if (any(a == '--thinking-level' or a.startswith('--thinking-level=') for a in args)
            and any(a == '--reasoning-effort' or a.startswith('--reasoning-effort=') for a in args)):
        raise StoreError('Resolver returned mutually exclusive thinking flags')
    for flag, expected in (('--backend', backend), ('--model', model),
                           ('--thinking-level', thinking_level), ('--reasoning-effort', thinking_level)):
        positions = [i for i, arg in enumerate(args) if arg == flag or arg.startswith(flag + '=')]
        if len(positions) > 1:
            raise StoreError('Resolver returned duplicate execution flag')
        if positions:
            i = positions[0]
            actual = args[i].split('=', 1)[1] if '=' in args[i] else (args[i + 1] if i + 1 < len(args) else None)
            if actual != expected:
                raise StoreError('Resolver execution disagrees with selected authority')
        elif flag != '--reasoning-effort':
            if flag == '--thinking-level' and any(a == '--reasoning-effort' or a.startswith('--reasoning-effort=') for a in args):
                continue
            args.extend([flag, expected])
    if fresh:
        args.extend(['--create-agent', name, '--agent-id', agent_id])
    else:
        args.extend(['--agent', name])
        if conversation_id is not None:
            args.extend(['--convo', valid_id(conversation_id)])
        # A resumed transcript owns its system prompt, never a new role prompt.
        for flag in ('--system-prompt-file', '--system-prompt'):
            while flag in args:
                i = args.index(flag)
                if i + 1 >= len(args):
                    raise StoreError('Resolver returned bare system prompt flag')
                del args[i:i + 2]
    launch['env']['LITETUI_DATA_ROOT'] = str(root)
    launch['env']['LITEHARNESS_AGENT_ID'] = agent_id
    launch['env']['LITEHARNESS_NAME'] = name
    launch['harnessAgentId'] = agent_id
    return launch
