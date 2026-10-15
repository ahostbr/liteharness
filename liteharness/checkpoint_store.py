"""Atomic checkpoint/journal files; no worker, launcher, transport, or stale-lock reaper.

All writers serialize on an exclusive store lock. A crashed lock is a visible
reconciliation requirement, not permission to delete it. File fsync + replace
protect against interrupted writes; Windows power-loss durability is not claimed.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from .agent_store import StoreError, _unlinked, valid_id
from .checkpoint_contract import (ContractError, Manifest, Request, Result,
                                  decode, validate_result_roster, wire)


def checked_path(value: Path | str, *, beneath: Path | None = None) -> Path:
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ContractError('Expected absolute path without traversal')
    if beneath is not None and not path.is_relative_to(beneath):
        raise ContractError('Path is outside authorized root')
    try:
        return _unlinked(path)
    except StoreError as exc:
        raise ContractError(str(exc)) from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('Duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ContractError('Nonstandard JSON constant')


def read_json(path: Path) -> dict:
    checked_path(path)
    try:
        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (OSError, ValueError) as exc:
        raise ContractError('JSON evidence is absent or unreadable') from exc
    if type(value) is not dict:
        raise ContractError('Expected JSON object')
    return value


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _sync_directory(path: Path) -> None:
    # Python offers no portable directory FlushFileBuffers on Windows. Do not
    # imply that the rename alone establishes power-loss persistence there.
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _mkdir(path: Path) -> None:
    """Link newly created directories durably on hosts with directory fsync."""
    checked_path(path)
    if path.exists():
        if not path.is_dir():
            raise ContractError('Expected store directory')
        return
    _mkdir(path.parent)
    path.mkdir(exist_ok=True)
    _sync_directory(path.parent)


def validate_manifest_references(manifest: Manifest, authorized_roots: list[Path | str]) -> None:
    """Explicit host-supplied allowlist, never inferred from untrusted references."""
    wire(manifest)
    roots = [checked_path(root) for root in authorized_roots]
    if not roots or any(not root.is_dir() for root in roots):
        raise ContractError('Existing authorized reference roots required')
    references = [manifest.coordinator.read_first, *(r.reference for r in manifest.receipts),
                  *(s.owner_profile.memory_directory for s in manifest.seats if s.owner_profile)]
    for reference in references:
        path = checked_path(reference)
        if not any(path.is_relative_to(root) for root in roots):
            raise ContractError('Manifest reference is outside authorized roots')


def _atomic_json(path: Path, value: dict) -> None:
    checked_path(path)
    data = _bytes(value)
    fd, temp_name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        checked_path(path)  # serialized writers still recheck before replacement
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    stage: str
    seat_id: str | None
    result: Result | None

    def validate(self):
        if self.sequence < 0 or not self.stage.strip():
            raise ContractError('Invalid journal event')
        if self.seat_id is not None:
            valid_id(self.seat_id)


@dataclass(frozen=True)
class Operation:
    schema_version: int
    operation_id: str
    actor_id: str
    request: Request
    events: list[JournalEvent]

    def validate(self):
        if self.schema_version != 1:
            raise ContractError('Unsupported journal schema')
        valid_id(self.operation_id)
        if not self.actor_id.strip():
            raise ContractError('Validated audit actor required')
        if self.request.dry_run or self.request.action == 'status':
            raise ContractError('Read-only requests cannot create journal operations')
        if [e.sequence for e in self.events] != list(range(len(self.events))):
            raise ContractError('Noncontiguous operation journal')
        checkpoint_ids = set()
        for event in self.events:
            if event.result:
                if event.result.operation_id != self.operation_id:
                    raise ContractError('Result belongs to another operation')
                checkpoint = event.result.checkpoint_id
                if checkpoint is not None:
                    checkpoint_ids.add(checkpoint)
                if self.request.action == 'restore' and checkpoint != self.request.checkpoint_id:
                    raise ContractError('Result belongs to another requested checkpoint')
        if len(checkpoint_ids) > 1:
            raise ContractError('Operation cannot change checkpoint identity')


class CheckpointStore:
    """Caller supplies harness root; no config discovery, environment or side effects on init."""

    def __init__(self, harness_root: Path | str):
        self.root = checked_path(harness_root) / 'sessions' / 'checkpoints'
        checked_path(self.root)

    def _path(self, *parts: str) -> Path:
        return checked_path(self.root.joinpath(*parts), beneath=self.root)

    @contextmanager
    def _locked(self):
        checked_path(self.root)
        _mkdir(self.root)
        lock = self._path('.writer-lock')
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ContractError('Checkpoint store locked; reconcile owner before retry') from exc
        try:
            os.close(fd)
            yield
        finally:
            lock.unlink()

    def read_manifest(self, checkpoint_id: str) -> Manifest:
        valid_id(checkpoint_id)
        result = decode(Manifest, read_json(self._path(checkpoint_id, 'manifest.json')))
        if result.checkpoint_id != checkpoint_id:
            raise ContractError('Manifest directory identity mismatch')
        return result

    def latest_complete(self) -> Manifest | None:
        path = self._path('latest-complete.json')
        if not path.exists():
            return None
        pointer = read_json(path)
        if set(pointer) != {'checkpoint_id', 'sha256'}:
            raise ContractError('Invalid latest-complete pointer')
        manifest = self.read_manifest(pointer['checkpoint_id'])
        if manifest.state != 'complete' or hashlib.sha256(_bytes(wire(manifest))).hexdigest() != pointer['sha256']:
            raise ContractError('Latest-complete identity/content mismatch')
        return manifest

    def publish(self, manifest: Manifest, *, authorized_reference_roots: list[Path | str]) -> None:
        payload = wire(manifest)
        validate_manifest_references(manifest, authorized_reference_roots)
        with self._locked():
            latest = self.latest_complete()
            directory = self._path(manifest.checkpoint_id)
            _mkdir(directory)
            target = self._path(manifest.checkpoint_id, 'manifest.json')
            if target.exists():
                previous = self.read_manifest(manifest.checkpoint_id)
                if (previous.expected != manifest.expected or previous.exclusions != manifest.exclusions
                        or previous.coordinator != manifest.coordinator
                        or previous.created_at != manifest.created_at
                        or previous.host_id != manifest.host_id or previous.boot_id != manifest.boot_id):
                    raise ContractError('Checkpoint roster/origin is frozen; create a new checkpoint')
                current_seats = {s.agent_id: s for s in manifest.seats}
                stable = ('name', 'agent_id', 'conversation_id', 'cli', 'execution', 'data_root',
                          'cwd', 'role', 'cognitive', 'parent_id', 'owner_profile', 'selection')
                for old_seat in previous.seats:
                    new_seat = current_seats.get(old_seat.agent_id)
                    if new_seat is None or any(getattr(old_seat, key) != getattr(new_seat, key) for key in stable):
                        raise ContractError('Resolved seat identity is frozen; reconcile with a new checkpoint')
                if previous.state == 'complete' and wire(previous) != payload:
                    raise ContractError('Complete checkpoints are immutable')
            _atomic_json(target, payload)
            if manifest.state == 'complete':
                if latest and latest.checkpoint_id != manifest.checkpoint_id:
                    created = datetime.fromisoformat(manifest.created_at.replace('Z', '+00:00'))
                    latest_created = datetime.fromisoformat(latest.created_at.replace('Z', '+00:00'))
                    if created <= latest_created:
                        return  # retain by explicit ID, never regress/tie-reorder latest
                # If this second write fails, old latest remains valid. The new
                # complete manifest is an orphan discoverable by explicit ID.
                _atomic_json(self._path('latest-complete.json'), {
                    'checkpoint_id': manifest.checkpoint_id,
                    'sha256': hashlib.sha256(_bytes(payload)).hexdigest(),
                })

    def operation_for_request(self, request_id: str) -> Operation | None:
        valid_id(request_id)
        path = self._path('operations', f'{request_id}.json')
        if not path.exists():
            return None
        operation = decode(Operation, read_json(path))
        if operation.request.request_id != request_id:
            raise ContractError('Journal request identity mismatch')
        for event in operation.events:
            if event.result and event.result.checkpoint_id is not None and event.result.state != 'rejected':
                validate_result_roster(event.result, self.read_manifest(event.result.checkpoint_id))
        return operation

    def operation(self, operation_id: str) -> Operation | None:
        valid_id(operation_id)
        root = self._path('operations')
        if not root.exists():
            return None
        matches = []
        for path in sorted(root.glob('*.json')):
            candidate = self.operation_for_request(path.stem)
            if candidate and candidate.operation_id == operation_id:
                matches.append(candidate)
        if len(matches) > 1:
            raise ContractError('Duplicate operation identity')
        return matches[0] if matches else None

    def submit(self, request: Request, *, actor_id: str) -> Operation:
        """Persist acceptance before returning. actor_id must come from host authorization.

        A request ID is global to this store. Different payload OR actor conflicts;
        uncertain submitters look up the same ID, never mint a replacement ID.
        Cancel/retry are durable commands; their execution belongs to card C/D.
        """
        payload = wire(request)
        if request.dry_run or request.action == 'status':
            raise ContractError('Read-only requests cannot mutate the journal')
        with self._locked():
            existing = self.operation_for_request(request.request_id)
            if existing:
                if wire(existing.request) != payload or existing.actor_id != actor_id:
                    raise ContractError('request_id already bound to another request or actor')
                return existing
            operation = Operation(1, str(uuid4()), actor_id, request, [])
            record = wire(operation)
            _mkdir(self._path('operations'))
            _atomic_json(self._path('operations', f'{request.request_id}.json'), record)
            return operation

    def append(self, operation_id: str, event: JournalEvent, *, expected_sequence: int) -> Operation:
        """CAS append via whole-file atomic replace; a torn tail never appears committed.

        A launch-intent event may be stored here, but this module never dispatches
        it or infers completion from it. Caller owns state transitions/authorization.
        """
        wire(event)
        with self._locked():
            operation = self.operation(operation_id)
            if operation is None:
                raise ContractError('Unknown operation')
            if len(operation.events) != expected_sequence or event.sequence != expected_sequence:
                raise ContractError('Journal sequence changed; reconcile before append')
            if event.result:
                result = event.result
                if (operation.request.action == 'restore'
                        and result.checkpoint_id != operation.request.checkpoint_id):
                    raise ContractError('Result does not match requested checkpoint')
                if result.checkpoint_id is not None and result.state != 'rejected':
                    validate_result_roster(result, self.read_manifest(result.checkpoint_id))
            updated = Operation(1, operation.operation_id, operation.actor_id,
                                operation.request, [*operation.events, event])
            _atomic_json(self._path('operations', f'{operation.request.request_id}.json'), wire(updated))
            return updated
