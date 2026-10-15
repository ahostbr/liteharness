"""Restart checkpoint v1: data contracts only, not launch or authorization policy.

Wire decoding is closed (unknown fields fail). Identity, versions and receipt
joins are validated before persistence. No arbitrary argv, environment or script
payload is accepted. See docs/checkpoint-contract.md for ownership boundaries.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from .agent_store import StoreError, valid_id, valid_name
from .roles import normalize_role

SCHEMA_VERSION = 1
CLI = Literal['claude', 'codex', 'copilot', 'litetui']
Action = Literal['save', 'restore', 'status', 'cancel', 'retry']
SeatState = Literal['preparing', 'saved', 'blocked', 'reattaching', 'starting',
                    'verifying', 'ready-idle', 'ready-held', 'failed', 'unknown']
OperationState = Literal['accepted', 'running', 'complete', 'incomplete',
                         'rejected', 'canceling', 'canceled', 'unknown']
ReceiptKind = Literal['handoff', 'compact', 'boundary', 'layout', 'manual-return',
                      'resource', 'readiness']

# Recommendations are labels, NOT implicit choices or executable policy.
RECOMMENDED_DECISIONS = {
    'roster': 'selected-current-plus-explicit-offline',
    'unsafe_stop': 'incomplete-with-blockers',
    'backup_scope': 'same-machine-continuity',
    'placement': 'matching-slot-else-offer-pane',
}


class ContractError(ValueError):
    """Malformed, unsupported or contradictory checkpoint data."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _text(value: str) -> None:
    _require(bool(value.strip()), 'Empty required text')


def _id(value: str) -> None:
    try:
        valid_id(value)
    except StoreError as exc:
        raise ContractError(str(exc)) from exc


def _path(value: str) -> None:
    path = Path(value)
    _require(path.is_absolute() and '..' not in path.parts,
             'Expected absolute path without traversal')


def _time(value: str) -> None:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ContractError('Invalid timestamp') from exc
    _require(stamp.tzinfo is not None, 'Timestamp requires timezone')


def decode(cls, value):
    """Strict JSON -> typed contract. No coercions, extra fields or migrations."""
    origin, args = get_origin(cls), get_args(cls)
    if origin is Literal:
        _require(any(type(value) is type(v) and value == v for v in args),
                 'Unsupported enumerated value')
        return value
    if origin in (Union, UnionType):
        for variant in args:
            try:
                return decode(variant, value)
            except ContractError:
                pass
        raise ContractError('Invalid optional/union value')
    if origin is list:
        _require(type(value) is list, 'Expected list')
        return [decode(args[0], v) for v in value]
    if origin is dict:
        _require(type(value) is dict, 'Expected object')
        return {decode(args[0], k): decode(args[1], v) for k, v in value.items()}
    if is_dataclass(cls):
        _require(type(value) is dict, 'Expected contract object')
        names = {f.name for f in fields(cls)}
        _require(set(value) == names, f'Fields do not match {cls.__name__}')
        hints = get_type_hints(cls)
        result = cls(**{k: decode(hints[k], v) for k, v in value.items()})
        if hasattr(result, 'validate'):
            result.validate()
        return result
    _require(type(value) is cls, 'Incorrect scalar type')
    return value


def wire(value) -> dict:
    """Validate even directly constructed dataclasses before crossing a boundary."""
    result = asdict(value)
    decode(type(value), result)
    return result


@dataclass(frozen=True)
class Decision:
    value: str
    basis: Literal['unconfirmed-recommendation', 'explicit-selection', 'human-confirmed']

    def validate(self):
        _text(self.value)


@dataclass(frozen=True)
class Decisions:
    roster: Decision
    unsafe_stop: Decision
    backup_scope: Decision
    placement: Decision


@dataclass(frozen=True)
class Execution:
    backend: str
    model: str
    thinking_level: str

    def validate(self):
        for value in (self.backend, self.model, self.thinking_level):
            _text(value)


@dataclass(frozen=True)
class Slot:
    workspace: str
    thread: str
    pane: str
    leaf: str
    tab: str | None
    layout_revision: str

    def validate(self):
        for value in (self.workspace, self.thread, self.pane, self.leaf, self.layout_revision):
            _text(value)


@dataclass(frozen=True)
class OwnerProfile:
    title: str
    memory_directory: str

    def validate(self):
        _text(self.title)
        _path(self.memory_directory)


@dataclass(frozen=True)
class Seat:
    name: str
    agent_id: str
    conversation_id: str
    cli: CLI
    execution: Execution
    data_root: str
    cwd: str
    role: str
    cognitive: str
    parent_id: str | None
    owner_profile: OwnerProfile | None
    selection: Literal['selected-current', 'explicit-offline']
    disposition: Literal['idle', 'waiting', 'held']
    first_recovery_action: str
    source_revision: str
    slot: Slot | None

    def validate(self):
        try:
            valid_name(self.name)
        except StoreError as exc:
            raise ContractError(str(exc)) from exc
        _id(self.agent_id)
        _id(self.conversation_id)
        if self.parent_id is not None:
            _id(self.parent_id)
            _require(self.parent_id != self.agent_id, 'Seat cannot parent itself')
        for value in (self.data_root, self.cwd):
            _path(value)
        for value in (self.role, self.cognitive, self.first_recovery_action, self.source_revision):
            _text(value)
        role = normalize_role(self.role)
        _require(role is not None, 'Unknown seat role')
        _require((role == 'owner') == (self.owner_profile is not None), 'Owner profile must be preserved')
        if self.cli == 'litetui' and self.owner_profile:
            _require(self.owner_profile.title == self.name, 'Owned owner title must match seat name')


@dataclass(frozen=True)
class Blocker:
    code: str
    seat_id: str | None
    remedy: str

    def validate(self):
        _text(self.code)
        _text(self.remedy)
        if self.seat_id is not None:
            _id(self.seat_id)


@dataclass(frozen=True)
class Exclusion:
    name: str
    agent_id: str
    reason: str

    def validate(self):
        _id(self.agent_id)
        _text(self.name)
        _text(self.reason)


@dataclass(frozen=True)
class Coordinator:
    agent_id: str
    mode: Literal['external-manual-return']
    read_first: str
    read_first_sha256: str

    def validate(self):
        _id(self.agent_id)
        _path(self.read_first)
        _digest(self.read_first_sha256)


def _digest(value: str) -> None:
    _require(len(value) == 64 and all(c in '0123456789abcdef' for c in value),
             'Expected lowercase SHA-256 digest')


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    checkpoint_id: str
    seat_id: str
    kind: ReceiptKind
    source_revision: str
    reference: str
    sha256: str
    durable: bool
    assurance: Literal['native', 'cooperative', 'unsupported']
    observed_at: str

    def validate(self):
        for value in (self.receipt_id, self.checkpoint_id, self.seat_id):
            _id(value)
        _text(self.source_revision)
        _path(self.reference)
        _digest(self.sha256)
        _time(self.observed_at)


@dataclass(frozen=True)
class Obligation:
    """Reference to owner-authoritative resources/jobs/schedules/flags, not replay data."""
    key: str
    owner_id: str
    kind: Literal['resource', 'job', 'schedule', 'flag', 'draft', 'approval', 'repository']
    reference: str
    revision: str
    state: Literal['recorded', 'reconciled', 'unknown', 'blocked']

    def validate(self):
        _text(self.key)
        _id(self.owner_id)
        _text(self.reference)
        _text(self.revision)


@dataclass(frozen=True)
class ExpectedSeat:
    """Frozen denominator, including rows whose launch contract cannot be resolved."""
    agent_id: str
    name: str
    selection: Literal['selected-current', 'explicit-offline']

    def validate(self):
        _id(self.agent_id)
        try:
            valid_name(self.name)
        except StoreError as exc:
            raise ContractError(str(exc)) from exc


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    checkpoint_id: str
    created_at: str
    host_id: str
    boot_id: str
    runtime_versions: dict[str, str]
    decisions: Decisions
    coordinator: Coordinator
    expected: list[ExpectedSeat]
    seats: list[Seat]
    exclusions: list[Exclusion]
    receipts: list[Receipt]
    obligations: list[Obligation]
    blockers: list[Blocker]
    state: Literal['complete', 'incomplete']

    def validate(self):
        _require(self.schema_version == SCHEMA_VERSION, 'Unsupported checkpoint schema')
        _id(self.checkpoint_id)
        _time(self.created_at)
        _text(self.host_id)
        _text(self.boot_id)
        _require(bool(self.runtime_versions), 'Runtime versions required')
        for key, value in self.runtime_versions.items():
            _text(key)
            _text(value)
        ids = [s.agent_id for s in self.expected]
        _require(bool(ids) and len(set(ids)) == len(ids), 'Expected seats must be nonempty and unique')
        _require(len({s.name.casefold() for s in self.expected}) == len(ids), 'Duplicate seat name')
        # A conversation identity cannot be restored twice, including across roots.
        _require(len({s.conversation_id for s in self.seats}) == len(self.seats), 'Duplicate conversation')
        _require(self.coordinator.agent_id in ids, 'Coordinator must remain in expected roster')
        excluded = [e.agent_id for e in self.exclusions]
        _require(len(set(excluded)) == len(excluded) and not set(ids).intersection(excluded),
                 'Excluded and expected identities must be disjoint')
        _require(all(b.seat_id is None or b.seat_id in ids for b in self.blockers), 'Unknown blocker seat')
        _require(all(o.owner_id in ids for o in self.obligations), 'Unknown obligation owner')
        _require(len({o.key for o in self.obligations}) == len(self.obligations), 'Duplicate obligation')
        _require(len({r.receipt_id for r in self.receipts}) == len(self.receipts), 'Duplicate receipt')
        _require(len({(r.seat_id, r.kind) for r in self.receipts}) == len(self.receipts),
                 'Ambiguous receipt kind for seat')
        seats = {s.agent_id: s for s in self.seats}
        _require(len(seats) == len(self.seats) and set(seats) <= set(ids), 'Invalid resolved roster')
        for expected in self.expected:
            seat = seats.get(expected.agent_id)
            if seat is None:
                _require(any(b.seat_id == expected.agent_id for b in self.blockers), 'Unresolved seat needs blocker')
            else:
                _require((seat.name, seat.selection) == (expected.name, expected.selection), 'Roster identity mismatch')
        for receipt in self.receipts:
            _require(receipt.checkpoint_id == self.checkpoint_id and receipt.seat_id in seats,
                     'Receipt identity does not match checkpoint')
            seat = seats[receipt.seat_id]
            revision = (seat.slot.layout_revision if receipt.kind == 'layout' and seat.slot
                        else seat.source_revision)
            _require(receipt.source_revision == revision, 'Stale receipt revision')
        if self.state == 'complete':
            _require(not self.blockers, 'Blocked checkpoint cannot be complete')
            _require(all(o.state not in ('unknown', 'blocked') for o in self.obligations),
                     'Unresolved obligation cannot be complete')
            for seat in self.seats:
                required = {'handoff', 'compact', 'boundary'}
                if seat.slot:
                    required.add('layout')
                ready = {r.kind for r in self.receipts if r.seat_id == seat.agent_id
                         and r.durable and r.assurance != 'unsupported'}
                _require(required <= ready, 'Missing durable checkpoint receipts')
                observed = {r.kind: datetime.fromisoformat(r.observed_at.replace('Z', '+00:00'))
                            for r in self.receipts if r.seat_id == seat.agent_id}
                _require(observed['handoff'] <= observed['compact'] <= observed['boundary'],
                         'Compaction must follow handoff and precede final boundary')


@dataclass(frozen=True)
class Request:
    schema_version: int
    action: Action
    request_id: str | None
    checkpoint_id: str | None
    operation_id: str | None
    seat_ids: list[str]
    dry_run: bool

    def validate(self):
        _require(self.schema_version == SCHEMA_VERSION, 'Unsupported request schema')
        for value in (self.request_id, self.checkpoint_id, self.operation_id):
            if value is not None:
                _id(value)
        for value in self.seat_ids:
            _id(value)
        _require(len(set(self.seat_ids)) == len(self.seat_ids), 'Duplicate retry seat')
        _require(not self.dry_run or self.action in ('save', 'restore'), 'Unsupported dry-run action')
        _require(self.action == 'status' or self.request_id is not None, 'Mutation requires request_id')
        if self.action == 'restore':
            _require(self.checkpoint_id is not None, 'Restore requires explicit checkpoint_id')
        if self.action in ('cancel', 'retry'):
            _require(self.operation_id is not None, 'Action requires operation_id')
        if self.action != 'retry':
            _require(not self.seat_ids, 'seat_ids only applies to retry')
        if self.action == 'retry':
            _require(bool(self.seat_ids), 'Retry requires explicit seats')
        if self.action in ('save', 'restore'):
            _require(self.operation_id is None, 'New operation cannot select operation_id')
        if self.action in ('save', 'cancel', 'retry'):
            _require(self.checkpoint_id is None, 'Unexpected checkpoint_id')
        if self.action == 'status':
            _require(self.request_id is None or (self.operation_id is None and self.checkpoint_id is None),
                     'Status request lookup cannot mix selectors')
            _require(not (self.operation_id and self.checkpoint_id), 'Status requires at most one selector')


@dataclass(frozen=True)
class SeatResult:
    seat_id: str
    state: SeatState
    evidence: list[Receipt]
    errors: list[Blocker]
    allowed_next_actions: list[Literal['wait', 'retry', 'reconcile', 'inspect']]

    def validate(self):
        _id(self.seat_id)
        _require(all(r.seat_id == self.seat_id for r in self.evidence), 'Evidence seat mismatch')
        _require(all(e.seat_id == self.seat_id for e in self.errors), 'Error seat mismatch')
        if self.state == 'unknown':
            _require('retry' not in self.allowed_next_actions, 'Unknown outcome requires reconciliation')


@dataclass(frozen=True)
class Counts:
    expected: int
    ready: int
    held: int
    failed: int
    unknown: int


@dataclass(frozen=True)
class Result:
    schema_version: int
    operation_id: str | None
    checkpoint_id: str | None
    state: OperationState
    counts: Counts
    seats: list[SeatResult]
    blockers: list[Blocker]
    allowed_next_actions: list[Literal['status', 'cancel', 'retry', 'reconcile', 'inspect']]

    def validate(self):
        _require(self.schema_version == SCHEMA_VERSION, 'Unsupported result schema')
        for value in (self.operation_id, self.checkpoint_id):
            if value is not None:
                _id(value)
        _require(len({s.seat_id for s in self.seats}) == len(self.seats), 'Duplicate result seat')
        counts = Counts(len(self.seats), sum(s.state in ('ready-idle', 'ready-held') for s in self.seats),
                        sum(s.state == 'ready-held' for s in self.seats),
                        sum(s.state in ('failed', 'blocked') for s in self.seats),
                        sum(s.state == 'unknown' for s in self.seats))
        _require(self.counts == counts, 'Result counts disagree with expected rows')
        if self.state == 'complete':
            _require(not self.blockers and all(s.state in ('saved', 'ready-idle', 'ready-held')
                                              and not s.errors for s in self.seats),
                     'Incomplete rows cannot produce complete result')


def validate_runtime_versions(manifest: Manifest, supported: dict[str, set[str]]) -> None:
    """Explicit compatibility table, never an implicit migration/version downgrade."""
    wire(manifest)
    for runtime, version in manifest.runtime_versions.items():
        _require(version in supported.get(runtime, set()), 'Unsupported runtime version')


def legacy_snapshot_summary(value: dict) -> dict:
    """Read-only classification; legacy terminal v2 can NEVER become v1 complete."""
    _require(type(value) is dict and type(value.get('schema_version')) is int
             and value['schema_version'] == 2 and type(value.get('sessions')) is list,
             'Unsupported legacy snapshot')
    return {'source_schema': 2, 'state': 'legacy-partial', 'session_count': len(value['sessions']),
            'blocker': 'Fresh roster and checkpoint receipts required'}
