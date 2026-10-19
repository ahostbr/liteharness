"""Shared plumbing for durable save/restore operations: Result building, journal appends, params.

The journal (CheckpointStore.append) is the only memory of what an operation attempted.
Everything here is a thin wrapper that keeps a Result's counts and roster honest.
"""
from __future__ import annotations

import json
from pathlib import Path

from .agent_store import valid_id
from .checkpoint_contract import (Blocker, ContractError, Counts, Receipt, Result, SCHEMA_VERSION,
                                  SeatResult, decode, wire)
from .checkpoint_store import (CheckpointStore, JournalEvent, Operation, _atomic_json, _mkdir,
                               checked_path, read_json)

READY = ('ready-idle', 'ready-held')
TERMINAL_OPERATION_STATES = ('complete', 'incomplete', 'rejected', 'canceled', 'unknown')


def seat_result(seat_id: str, state: str, *, evidence: list[Receipt] = (), errors: list[Blocker] = (),
                next_actions: list[str] | None = None) -> SeatResult:
    if next_actions is None:
        next_actions = {'failed': ['retry', 'inspect'], 'blocked': ['inspect'], 'unknown': ['reconcile', 'inspect'],
                        'preparing': ['wait'], 'starting': ['wait'], 'verifying': ['wait'],
                        'reattaching': ['wait']}.get(state, [])
    return SeatResult(seat_id, state, list(evidence), list(errors), next_actions)


def make_result(operation_id: str | None, checkpoint_id: str | None, state: str,
                seats: list[SeatResult], blockers: list[Blocker] = (),
                next_actions: list[str] | None = None) -> Result:
    counts = Counts(len(seats), sum(s.state in READY for s in seats),
                    sum(s.state == 'ready-held' for s in seats),
                    sum(s.state in ('failed', 'blocked') for s in seats),
                    sum(s.state == 'unknown' for s in seats))
    if next_actions is None:
        next_actions = ['status'] if state in ('accepted', 'running', 'canceling') else ['status', 'inspect']
        if state == 'incomplete' and any(s.state == 'failed' for s in seats):
            next_actions.append('retry')
        if any(s.state == 'unknown' for s in seats):
            next_actions.append('reconcile')
    result = Result(SCHEMA_VERSION, operation_id, checkpoint_id, state, counts, seats, list(blockers), next_actions)
    wire(result)
    return result


def rejected(reason: str, remedy: str, *, operation_id: str | None = None,
             checkpoint_id: str | None = None) -> Result:
    return make_result(operation_id, checkpoint_id, 'rejected', [], [Blocker(reason, None, remedy)],
                       ['status'])


class OperationLog:
    """Append-only journal writer for one operation; sequence comes from the store each time."""

    def __init__(self, store: CheckpointStore, operation_id: str):
        self.store, self.operation_id = store, operation_id

    def operation(self) -> Operation:
        operation = self.store.operation(self.operation_id)
        if operation is None:
            raise ContractError('Unknown operation')
        return operation

    def event(self, stage: str, seat_id: str | None = None, result: Result | None = None) -> Operation:
        operation = self.operation()
        sequence = len(operation.events)
        return self.store.append(self.operation_id, JournalEvent(sequence, stage, seat_id, result),
                                 expected_sequence=sequence)

    def last_result(self) -> Result | None:
        for event in reversed(self.operation().events):
            if event.result is not None:
                return event.result
        return None


# Operation parameters the closed Request contract cannot carry (coordinator, handoff path,
# offline ids). Written once, before the worker starts; keyed by operation id so the
# operations/ directory keeps containing only request journals.

def params_path(store: CheckpointStore, operation_id: str) -> Path:
    return checked_path(store.root / 'params' / f'{valid_id(operation_id)}.json', beneath=store.root)


def write_params(store: CheckpointStore, operation_id: str, params: dict) -> None:
    path = params_path(store, operation_id)
    if path.exists():
        return  # same request_id returns the same operation; its params are frozen
    _mkdir(path.parent)
    _atomic_json(path, params)


def read_params(store: CheckpointStore, operation_id: str) -> dict:
    path = params_path(store, operation_id)
    return read_json(path) if path.exists() else {}
