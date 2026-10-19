"""Frozen roster for one save: every live seat from the registry, plus named offline seats.

Registry rows (`agents/`, `retired-agents/`) name the seats; the native transcript
(Claude) or the owned home (LiteTUI) supplies their launch contract. Nothing here
launches, wakes, writes or asserts liveness beyond the injected `live` probe. A seat
whose contract cannot be built stays in the denominator as a blocked row.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
from pathlib import Path
from typing import Callable

from . import config, roles
from .agent_store import AgentStore, StoreError, valid_id, valid_name
from .checkpoint_census import UNRECORDED_THINKING, Census, CensusRow, census
from .checkpoint_contract import (Blocker, ContractError, Execution, ExpectedSeat, Exclusion,
                                  OwnerProfile, Seat, Slot, wire)
from .checkpoint_store import checked_path, read_json

CLI_BY_REGISTRY = {'claude-code': 'claude', 'claude': 'claude', 'litetui': 'litetui',
                   'codex': 'codex', 'copilot': 'copilot'}
FIRST_RECOVERY_ACTION = 'Read your handoff, arm one inbox monitor, wait for your owner'
UNOBSERVED_LAYOUT = 'unobserved'


@dataclass(frozen=True)
class Candidate:
    """A seat named for the checkpoint: resolved contract, or the reason it is blocked."""
    expected: ExpectedSeat
    seat: Seat | None
    blocker: Blocker | None


@dataclass(frozen=True)
class Discovery:
    candidates: list[Candidate]
    skipped: list[tuple[str, str]]  # (registry stem, why): never a seat, never silently shrunk
    model_sources: dict[str, str] = field(default_factory=dict)  # agent id -> 'registry' | 'transcript'
    excluded: list[Exclusion] = field(default_factory=list)  # named seats left out, each with its reason


def claude_projects_root() -> Path:
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or (Path.home() / '.claude')) / 'projects'


def _live(row: dict) -> bool:
    from . import cli
    return cli._live_owner_pid(row) is not None


def _slot(row: dict, layout_revision: str) -> Slot | None:
    values = [row.get(key) for key in ('workspace_id', 'thread_id', 'pane_id', 'leaf_id')]
    if not all(isinstance(v, str) and v.strip() for v in values):
        return None
    return Slot(*values[:4], None, layout_revision)


def _parent(row: dict, agent_id: str) -> str | None:
    try:
        parent = valid_id(row.get('spawned_by'))
    except StoreError:
        return None
    return parent if parent != agent_id else None


def _revision(row: dict) -> str:
    # The runtime INSTANCE the receipts describe. Activity after the boundary is
    # judged against the observed boundary receipt, not against this token.
    return str(row.get('session_run_id') or row.get('registered_at') or row.get('started_at') or 'unrecorded')


def _owner_profile(role: str, name: str, directory: Path) -> OwnerProfile | None:
    return OwnerProfile(name, str(directory)) if role == 'owner' else None


def _claude_seat(row: dict, base: dict, selection: str, layout_revision: str, sources: dict) -> Seat:
    from .claude_identity import real_model, transcript_identity, transcript_model
    try:
        native_id, cwd, transcript = transcript_identity(row)
    except ValueError as exc:
        raise ContractError(f'Claude launch contract unresolved: {exc}') from exc
    model = real_model(row.get('model'))
    sources[base['agent_id']] = 'registry'
    if model is None:  # 11 of 25 live rows said 'unknown'; the transcript knows what actually ran
        model = transcript_model(transcript)
        sources[base['agent_id']] = 'transcript'
    if model is None:
        sources.pop(base['agent_id'], None)
        raise ContractError('Claude model is unknown in the registry and absent from the transcript')
    return Seat(
        conversation_id=native_id, cli='claude',
        execution=Execution('claude', model, row.get('thinking_level') or UNRECORDED_THINKING),
        data_root=str(claude_projects_root()), cwd=cwd,
        # A LOCATOR, not a memory store: Claude keeps no SOUL/DOMAIN/NOW folder, so an owner's
        # profile for a native seat is its session folder. Resume goes by transcript, not by this path.
        owner_profile=_owner_profile(base['role'], base['name'], transcript.parent),
        selection=selection, slot=_slot(row, layout_revision), **base)


def _litetui_seat(row: dict, base: dict, selection: str, layout_revision: str,
                  root: Path | None, index: dict) -> Seat:
    if root is None:
        raise ContractError('LiteTUI data root unknown; set LITETUI_DATA_ROOT')
    store = AgentStore(root)
    matches = [a for a in store.list_agents() if a.agent_id == base['agent_id']]
    if len(matches) != 1:
        raise ContractError('Owned home is absent or ambiguous')
    agent = matches[0]
    execution = agent.settings.get('execution') or {}
    if not all(isinstance(execution.get(k), str) and execution[k].strip()
               for k in ('backend', 'model', 'thinking_level')):
        raise ContractError('Owned execution is unchosen')
    named = [r for r in index.values() if isinstance(r, dict) and r.get('agent_id') == agent.agent_id
             and r.get('convo_id')]
    conversations = [d.name for d in store.list_conversations(agent)]
    if len(named) == 1:
        conversation = named[0]['convo_id']
    elif len(conversations) == 1:
        conversation = conversations[0]
    else:
        raise ContractError('Owned seat has no single selected conversation')
    cwd = row.get('cwd')
    if not isinstance(cwd, str) or not cwd.strip():
        raise ContractError('Owned seat has no recorded launch cwd')
    return Seat(
        conversation_id=conversation, cli='litetui',
        execution=Execution(execution['backend'], execution['model'], execution['thinking_level']),
        data_root=str(root), cwd=cwd, owner_profile=_owner_profile(base['role'], agent.name, agent.memory_root),
        selection=selection, slot=_slot(row, layout_revision), **base)


def _registry_rows(harness: Path, include_offline: list[str],
                   live: Callable[[dict], bool]) -> tuple[list[tuple[str, dict, str]], list]:
    """(agent_id, row, selection) for live current rows and named offline rows."""
    rows, skipped = [], []
    wanted = set(include_offline)
    for path in sorted((harness / 'agents').glob('*.json')):
        try:
            row = read_json(path)
            valid_id(path.stem)
        except (ContractError, StoreError):
            skipped.append((path.stem, 'not-a-seat-row'))
            continue
        if row.get('agent_id') != path.stem:
            skipped.append((path.stem, 'identity-mismatch'))
        elif path.stem in wanted:
            rows.append((path.stem, row, 'selected-current' if live(row) else 'explicit-offline'))
            wanted.discard(path.stem)
        elif live(row):
            rows.append((path.stem, row, 'selected-current'))
    for agent_id in sorted(wanted):  # retired history is selectable only by explicit id
        try:
            rows.append((agent_id, read_json(harness / 'retired-agents' / f'{valid_id(agent_id)}.json'),
                         'explicit-offline'))
        except (ContractError, StoreError):
            skipped.append((agent_id, 'offline-row-absent'))
    return rows, skipped


def discover_current(*, harness_root: Path | str, include_offline: list[str] = (),
                     exclude_ids: list[str] = (), coordinator_id: str | None = None,
                     litetui_root: Path | str | None = None,
                     layout_revision: str = UNOBSERVED_LAYOUT,
                     live: Callable[[dict], bool] | None = None) -> Discovery:
    """Name every live seat, plus `include_offline`, in a stable order.

    The caller (the coordinator) is a seat like any other here; the manifest's
    coordinator record, not this list, keeps restore from ever launching it.
    `exclude_ids` takes agent ids or seat names; with a known `coordinator_id`, a seat nobody
    spawned (started directly by the user) is also left out. Every seat left out is returned in
    `Discovery.excluded` with its reason, never dropped silently.
    """
    harness = checked_path(harness_root)
    rows, skipped = _registry_rows(harness, list(include_offline), live or _live)
    index_path = harness / 'names.json'
    try:
        index = read_json(index_path) if index_path.exists() else {}
    except ContractError:
        index = {}
    root = None
    if litetui_root is not None:
        root = Path(litetui_root)
    else:
        try:
            from . import owned_launch
            root = owned_launch.data_root()
        except Exception:  # RootUnknown / StoreError: LiteTUI rows block, Claude rows do not
            root = None
    requested = {str(v).casefold() for v in exclude_ids}  # an agent id or a seat name
    explicit = set(include_offline)
    # A caller that is not itself a live seat excludes nothing by default (save refuses such a caller anyway).
    coordinator_known = coordinator_id in {agent_id for agent_id, _, _ in rows}
    candidates = []
    sources: dict[str, str] = {}
    left_out: list[Exclusion] = []
    for agent_id, row, selection in rows:
        try:
            name = valid_name(row.get('name'))
        except StoreError:
            skipped.append((agent_id, 'unnamed-row'))
            continue
        # The coordinator is never excluded. A seat nobody spawned (the user started it themselves) is not ours
        # to hand off or compact unless it was named explicitly; that default needs a known coordinator.
        reason = None
        if agent_id != coordinator_id:
            if agent_id.casefold() in requested or name.casefold() in requested:
                reason = 'excluded-by-request'
            elif coordinator_known and agent_id not in explicit and _parent(row, agent_id) is None:
                reason = 'started-directly'
        if reason:
            if name.casefold() not in {e.name.casefold() for e in left_out}:  # the manifest wants unique names
                left_out.append(Exclusion(name, agent_id, reason))
            continue
        expected = ExpectedSeat(agent_id, name, selection)
        role = roles.row_role(row)
        base = dict(name=name, agent_id=agent_id, role=role, cognitive=row.get('cognitive') or 'unspecified',
                    parent_id=_parent(row, agent_id), disposition='held',
                    first_recovery_action=FIRST_RECOVERY_ACTION, source_revision=_revision(row))
        try:
            cli_name = CLI_BY_REGISTRY.get(str(row.get('cli')))
            if cli_name is None:
                raise ContractError('Unsupported seat CLI')
            if roles.normalize_role(role) is None:
                raise ContractError('Unknown seat role')
            if cli_name == 'claude':
                seat = _claude_seat(row, base, selection, layout_revision, sources)
            elif cli_name == 'litetui':
                seat = _litetui_seat(row, base, selection, layout_revision, root, index)
            else:
                raise ContractError('Seat CLI has no checkpoint adapter')
            wire(seat)
            candidates.append(Candidate(expected, seat, None))
        except (ContractError, StoreError, OSError) as exc:
            # Controlled text only; OS errors can carry private paths.
            detail = str(exc) if isinstance(exc, (ContractError, StoreError)) else 'Seat evidence unreadable'
            candidates.append(Candidate(expected, None, Blocker('seat-unresolved', agent_id,
                                                                f'{detail}; reconcile source evidence')))
    candidates.sort(key=lambda c: c.expected.name.casefold())
    return Discovery(candidates, skipped, sources, left_out)


def default_cwd_roots(seats: list[Seat]) -> list[Path]:
    """Drive anchors of the seats' own launch cwds, or `checkpoint.cwd_roots` when configured.

    The transcript, not this fence, is what ties a Claude seat to its cwd; set the
    config key to make the fence mean something.
    """
    configured = (config.load().get('checkpoint') or {}).get('cwd_roots')
    if isinstance(configured, list) and configured:
        return [Path(p) for p in configured]
    return sorted({Path(Path(s.cwd).anchor) for s in seats})


def roster_census(discovery: Discovery, *, harness_root: Path | str,
                  cwd_roots: list[Path | str] | None = None,
                  exclusions: list[Exclusion] = ()) -> Census:
    """Census every resolved candidate; unresolved ones keep their row and blocker.

    `exclusions` defaults to the seats discovery left out, so the manifest records who was skipped and why.
    """
    exclusions = list(exclusions) or list(discovery.excluded)
    seats = [c.seat for c in discovery.candidates if c.seat is not None]
    data_roots = sorted({Path(s.data_root) for s in seats})
    result = census(seats, harness_root=harness_root, authorized_data_roots=data_roots,
                    authorized_cwd_roots=cwd_roots if cwd_roots is not None else default_cwd_roots(seats),
                    exclusions=list(exclusions)) if seats else Census([], list(exclusions))
    by_id = {row.expected.agent_id: row for row in result.rows}
    rows = [by_id[c.expected.agent_id] if c.seat is not None else CensusRow(c.expected, None, [c.blocker])
            for c in discovery.candidates]
    return Census(rows, list(exclusions))
