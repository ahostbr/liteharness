"""Read-only, explicitly selected roster census. Never discovers a roster by PID.

Inputs are launch-context candidates, not authority. Owned homes supply execution;
CLI names supply original cwd/conversation. Registry is corroborating context, not
proof of liveness. Missing or corrupt evidence blocks rows without shrinking them.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .agent_store import AgentStore, StoreError
from .roles import normalize_role
from .checkpoint_contract import (Blocker, ContractError, Execution, ExpectedSeat,
                                  Exclusion, Seat, wire)
from .checkpoint_store import checked_path, read_json


@dataclass(frozen=True)
class CensusRow:
    expected: ExpectedSeat
    seat: Seat | None
    blockers: list[Blocker]


@dataclass(frozen=True)
class Census:
    rows: list[CensusRow]
    exclusions: list[Exclusion]

    @property
    def expected_count(self) -> int:
        return len(self.rows)

    @property
    def blockers(self) -> list[Blocker]:
        return [blocker for row in self.rows for blocker in row.blockers]


def _directory(value: str) -> Path:
    path = checked_path(value)
    if not path.is_dir():
        raise ContractError('Required directory is absent')
    return path


def _within(value: str, roots: list[Path]) -> Path:
    path = checked_path(value)
    if not any(path.is_relative_to(root) for root in roots):
        raise ContractError('Path is outside authorized roots')
    return path


def _registry(harness: Path, agent_id: str) -> dict | None:
    for folder in ('agents', 'retired-agents'):
        path = checked_path(harness / folder / f'{agent_id}.json')
        if path.exists():
            row = read_json(path)
            if row.get('agent_id') != agent_id:
                raise ContractError('Registry identity mismatch')
            return row
    return None


def _name_entry(index: dict, seat: Seat, *, required: bool) -> dict | None:
    matches = [(name, row) for name, row in index.items()
               if name.casefold() == seat.name.casefold()
               or (isinstance(row, dict) and row.get('agent_id') == seat.agent_id)]
    if not matches and not required:
        return None
    if len(matches) != 1:
        raise ContractError('Name index identity is absent or ambiguous')
    name, row = matches[0]
    if not isinstance(row, dict) or (name, row.get('agent_id'), row.get('convo_id')) != (
            seat.name, seat.agent_id, seat.conversation_id):
        raise ContractError('Stale name index conflicts with selected identity')
    duplicates = [r for r in index.values() if isinstance(r, dict)
                  and r.get('convo_id') == seat.conversation_id]
    if len(duplicates) != 1:
        raise ContractError('Duplicate indexed conversation')
    return row


def census(selected: list[Seat], *, harness_root: str | Path,
           authorized_data_roots: list[str | Path], authorized_cwd_roots: list[str | Path],
           exclusions: list[Exclusion]) -> Census:
    """Reconcile only explicit selections, including offline seats.

    The caller freezes selection under its chosen roster policy. A malformed stable
    identity/duplicate selection is a request error. Other evidence failures become
    blocked expected rows. There are no filesystem writes or liveness assertions.
    Native CLI transcript_path must be corroborated by a registry record and reside
    under that seat's explicitly authorized data root. No native path guesses.
    """
    expected = [ExpectedSeat(s.agent_id, s.name, s.selection) for s in selected]
    for row in expected:
        wire(row)
    for row in exclusions:
        wire(row)
    ids = [s.agent_id for s in expected]
    if len(set(ids)) != len(ids) or len({s.name.casefold() for s in expected}) != len(ids):
        raise ContractError('Duplicate selected identity/name')
    excluded = [s.agent_id for s in exclusions]
    if len(set(excluded)) != len(excluded) or set(ids).intersection(excluded):
        raise ContractError('Invalid excluded roster')

    # Global input failures still leave every selected row in the denominator.
    try:
        harness = _directory(str(harness_root))
        roots = [_directory(str(p)) for p in authorized_data_roots]
        cwd_roots = [_directory(str(p)) for p in authorized_cwd_roots]
        index_path = checked_path(harness / 'names.json')
        index = read_json(index_path) if index_path.exists() else {}
    except (ContractError, OSError):
        return Census([CensusRow(e, None, [Blocker('census-input', e.agent_id,
                       'Reconcile authorized roots and name index')]) for e in expected], exclusions)

    catalogs = {}
    catalog_errors = set()
    # Inspect authorized catalogs for ambiguity; reading dormant homes never
    # selects or wakes them. An unreadable catalog cannot prove unique ownership.
    for root in roots:
        try:
            catalogs[str(root)] = AgentStore(root).list_agents()
        except (StoreError, OSError):
            catalog_errors.add(str(root))

    rows = []
    for entry, candidate in zip(expected, selected):
        try:
            wire(candidate)  # fail closed for unknown CLI, absent context or traversal
            root = _directory(candidate.data_root)
            if root not in roots:
                raise ContractError('Data root not explicitly authorized')
            _directory(str(_within(candidate.cwd, cwd_roots)))
            index_entry = _name_entry(index, candidate, required=candidate.cli != 'litetui')
            registry = _registry(harness, candidate.agent_id)
            if registry:
                for key, value in (('name', candidate.name), ('cli', candidate.cli),
                                   ('spawned_by', candidate.parent_id)):
                    if key in registry and registry[key] != value:
                        raise ContractError('Registry conflicts with selected context')
                saved_role = registry.get('role') or registry.get('tier')
                if saved_role and normalize_role(saved_role) != normalize_role(candidate.role):
                    raise ContractError('Registry role conflicts with selected role; no downgrade')
            elif candidate.selection == 'selected-current' or candidate.cli != 'litetui':
                raise ContractError('Expected registry evidence is absent')

            seat = replace(candidate, role=normalize_role(candidate.role))
            if candidate.owner_profile:
                _directory(str(_within(candidate.owner_profile.memory_directory, [*roots, *cwd_roots])))
            if candidate.cli == 'litetui':
                if catalog_errors:
                    raise ContractError('Owned catalog is corrupt or inaccessible')
                matches = [a for agents in catalogs.values() for a in agents
                           if a.agent_id == candidate.agent_id or a.name.casefold() == candidate.name.casefold()]
                if len(matches) != 1:
                    raise ContractError('Owned identity is absent or ambiguous across roots')
                agent = matches[0]
                if (agent.name, agent.agent_id, agent.directory.parent.parent) != (
                        candidate.name, candidate.agent_id, root):
                    raise ContractError('Owned home disagrees with selected identity')
                store = AgentStore(root)
                conversation = store.locate_conversation(candidate.conversation_id)
                if conversation.parent != agent.conversations_root:
                    raise ContractError('Conversation belongs to another seat')
                # Also check selected other roots: copied conversations cannot be revived twice.
                for other_root, agents in catalogs.items():
                    if Path(other_root) != root:
                        for other in agents:
                            if any(p.name == candidate.conversation_id
                                   for p in AgentStore(other_root).list_conversations(other)):
                                raise ContractError('Duplicate conversation across roots')
                transcript = checked_path(conversation / 'convo.jsonl')
                settings = read_json(checked_path(conversation / 'settings.json'))
                if settings.get('seat_id') != candidate.agent_id:
                    raise ContractError('Conversation settings ownership mismatch')
                execution = agent.settings['execution']
                seat = replace(seat, execution=Execution(execution['backend'], execution['model'],
                                                            execution['thinking_level']))
                wire(seat)  # an unchosen home model is not a runnable execution contract
            else:
                if index_entry.get('cwd') != candidate.cwd:
                    raise ContractError('Native CLI requires its indexed original cwd')
                if index_entry.get('backend', '').split('/', 1)[0] != candidate.cli:
                    raise ContractError('Native CLI disagrees with name index')
                if (index_entry.get('model') != candidate.execution.model
                        or candidate.execution.backend != candidate.cli):
                    raise ContractError('Native execution conflicts with name index')
                transcript_value = registry.get('transcript_path')
                if not isinstance(transcript_value, str):
                    raise ContractError('Native transcript reference is absent')
                transcript = checked_path(transcript_value, beneath=root)
            if not transcript.is_file():
                raise ContractError('Selected transcript is absent')
            rows.append(CensusRow(entry, seat, []))
        except (ContractError, StoreError, OSError, TypeError, KeyError, AttributeError) as exc:
            # Contract/store errors contain controlled descriptions, not raw runtime
            # metadata. Never copy OS exceptions (which may contain private paths).
            detail = str(exc) if isinstance(exc, (ContractError, StoreError)) else 'Malformed runtime evidence'
            rows.append(CensusRow(entry, None, [Blocker('seat-evidence', entry.agent_id,
                        f'{detail}; reconcile source evidence')]))

    # Duplicate selection conversations block all involved rows, never pick a winner.
    conversations = [s.conversation_id for s in selected]
    return Census([CensusRow(row.expected, None, [*row.blockers,
                   Blocker('duplicate-conversation', row.expected.agent_id, 'Resolve duplicate conversation ownership')])
                   if conversations.count(candidate.conversation_id) > 1 else row
                   for row, candidate in zip(rows, selected)], exclusions)
