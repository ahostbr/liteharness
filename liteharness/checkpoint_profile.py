"""Read-only cognitive role+method observations; never prompt or launch authority.

Only the installed LiteTUI owner helpers interpret managed prompt metadata.
Missing/inactive scoped entries do NOT fall through to a guessed global profile.
"""
from __future__ import annotations

import hashlib
from importlib import import_module, metadata
import json
from pathlib import Path
from uuid import uuid4

from .agent_store import AgentStore, StoreError
from .checkpoint_contract import (ContractError, ProfileEvidence, ProfilePart, Receipt, Seat,
                                  cognitive_digest, decode, wire)
from .checkpoint_store import CheckpointStore, checked_path, _unique_object, _invalid_constant

NAMES = ('method.md', 'role.md')
NOTICE = 'cognitive role+method digest: profile changed since save'


def _unavailable(state: str, detail: str) -> dict[str, ProfilePart]:
    return {name: ProfilePart(state, None, detail) for name in NAMES}


def observe(seat: Seat, checkpoint_id: str, operation_id: str | None = None,
            saved_digest: str | None = None, saved_diagnostic: str | None = None) -> ProfileEvidence:
    """Exact by-ID home wins even if optional observation is unavailable/invalid."""
    home = source = version = None
    parts = _unavailable('unavailable', 'Managed cognitive observation was not available')
    try:
        store = AgentStore(seat.data_root)
        agent = store.find_agent(agent_id=seat.agent_id)
        home = str(agent.directory)
    except (StoreError, OSError) as exc:
        parts = _unavailable('unavailable', f'Owned home unavailable: {exc}')
    else:
        try:
            prompts = import_module('litetui.prompt_overrides')
            source = str(Path(prompts.__file__).absolute()) if prompts.__file__ else None
            try:
                version = metadata.version('litetui')
            except metadata.PackageNotFoundError:
                version = None  # source installs may have no distribution version
            required = ('scoped_file', 'read_text', 'metadata', 'metadata_path', 'stamp', 'sha')
            if source is None or not all(callable(getattr(prompts, name, None)) for name in required):
                raise ImportError('Installed LiteTUI managed prompt API unsupported')
        except (ImportError, AttributeError, TypeError, ValueError, OSError) as exc:
            parts = _unavailable('unavailable', f'Installed LiteTUI helper unavailable: {exc}')
        else:
            try:
                paths = {name: agent.directory / 'prompts' / name for name in NAMES}
                def signature():
                    return tuple((prompts.stamp(paths[name]),
                                  prompts.stamp(prompts.metadata_path(agent.directory, name))) for name in NAMES)
                before = signature()
                parts = {}
                for name in NAMES:
                    scoped = prompts.scoped_file(agent.directory, name)
                    row = prompts.metadata(agent.directory, name)
                    if scoped is None:
                        parts[name] = ProfilePart('inactive' if row is not None and not row['active'] else 'missing',
                                                  None, None)
                    else:
                        text = prompts.read_text(scoped)
                        parts[name] = ProfilePart('active', prompts.sha(text), None)
                    wire(parts[name])  # incompatible helper output is invalid observation, not launch failure
                # Repeat owner validation and compare its current body to the exact text hashed above.
                for name in NAMES:
                    scoped = prompts.scoped_file(agent.directory, name)
                    if parts[name].state == 'active' and (scoped is None
                            or prompts.sha(prompts.read_text(scoped)) != parts[name].digest):
                        raise ValueError('Managed cognitive content changed during observation')
                if signature() != before or store.find_agent(agent_id=seat.agent_id) != agent:
                    raise ValueError('Managed cognitive home or publication changed during observation')
            except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
                parts = _unavailable('invalid', f'Managed cognitive observation invalid: {exc}')
    current = cognitive_digest(parts)
    saved = current if operation_id is None else saved_digest
    changed = saved is not None and current is not None and saved != current
    result = ProfileEvidence(1, 'cognitive-role+method-v1', checkpoint_id, seat.agent_id, operation_id,
                             home, source, version, parts, saved, saved_diagnostic, current, NOTICE if changed else None)
    wire(result)
    return result


def retain(store: CheckpointStore, seat: Seat, observation: ProfileEvidence, at: str) -> Receipt:
    from .checkpoint_save import evidence
    kind = 'profile' if observation.operation_id is None else f'profile-{observation.operation_id}'
    path, digest = evidence(store, observation.checkpoint_id, seat.agent_id, kind, wire(observation))
    receipt = Receipt(str(uuid4()), observation.checkpoint_id, seat.agent_id, 'profile', seat.source_revision,
                      str(path), digest, True, 'native', at)
    wire(receipt)
    return receipt


def read(receipt: Receipt, checkpoint_id: str, seat_id: str, operation_id: str | None) -> ProfileEvidence:
    """Corrupt or cross-operation evidence is not a legacy checkpoint without a digest."""
    wire(receipt)
    if (receipt.kind != 'profile' or receipt.checkpoint_id != checkpoint_id or receipt.seat_id != seat_id
            or not receipt.durable):
        raise ContractError('Cognitive receipt identity or durability mismatch')
    try:
        raw = checked_path(receipt.reference).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt.sha256:
            raise ContractError('Cognitive receipt hash mismatch')
        value = decode(ProfileEvidence, json.loads(raw, object_pairs_hook=_unique_object,
                                                  parse_constant=_invalid_constant))
    except (OSError, ValueError) as exc:
        raise ContractError(f'Invalid cognitive receipt: {exc}') from exc
    if (value.checkpoint_id, value.seat_id, value.operation_id) != (checkpoint_id, seat_id, operation_id):
        raise ContractError('Cognitive evidence checkpoint/seat/operation mismatch')
    return value


def saved_digest(receipts: list[Receipt], seat: Seat, checkpoint_id: str) -> str | None:
    matches = [r for r in receipts if r.kind == 'profile' and r.seat_id == seat.agent_id]
    if not matches:
        return None  # legacy absence only; a present invalid receipt must fail visibly
    if len(matches) != 1 or matches[0].source_revision != seat.source_revision:
        raise ContractError('Ambiguous or stale cognitive receipt')
    return read(matches[0], checkpoint_id, seat.agent_id, None).current_digest
