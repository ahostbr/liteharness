"""Submit / status / cancel / retry for durable checkpoint operations.

Submit persists the operation (`CheckpointStore.submit`) BEFORE acknowledging, then starts a
detached worker that owns the work; the caller's process (an MCP tool call, killed at 120 s)
owns nothing. Status reads the journal; a dead worker with unfinished state reports `unknown`,
never `running`. One active mutating operation per store. Authorization is the CLI layer's job
(`checkpoint_cli`); nothing here trusts an actor id by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable
from uuid import uuid4

from .checkpoint_contract import (Blocker, ContractError, Manifest, Request, Result, SeatResult, wire)
from .checkpoint_ops import (OperationLog, make_result, read_params, rejected, seat_result, worker_path,
                             write_params, write_worker_record)
from .checkpoint_restore import RestoreParams, resolve_checkpoint
from .checkpoint_save import SaveParams, manifest_report
from .checkpoint_store import AdmissionRefused, CheckpointStore, Operation, _atomic_json, _mkdir, checked_path, read_json

ACTIVE = ('accepted', 'running', 'canceling')
STARTING_GRACE_SECONDS = 20.0
MUTATING = ('save', 'restore', 'retry')


def _start_detached(operation_id: str, harness_root: Path, log_path: Path) -> int:
    """Launch the worker so it outlives this process: no inherited console, output to a log.

    Absolute interpreter path, redirected stdio, own process group (launching-apps rule).
    """
    _mkdir(log_path.parent)
    argv = [sys.executable, '-m', 'liteharness.checkpoint_worker', '--operation-id', operation_id,
            '--harness-root', str(harness_root)]
    # The worker must import the very package that submitted it, not whichever checkout the
    # interpreter's site-packages happens to point at.
    package_parent = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(
        [package_parent, *filter(None, [os.environ.get('PYTHONPATH')])])}
    flags = {}
    if os.name == 'nt':
        flags['creationflags'] = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                                  | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        flags['start_new_session'] = True
    with log_path.open('ab') as log:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                                   cwd=str(harness_root), env=env, **flags)
    return process.pid


def _create_time(pid: int) -> float | None:
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:
        return None


@dataclass
class Service:
    store: CheckpointStore
    harness_root: Path
    launcher: Callable[[str, Path, Path], int] = _start_detached
    now: Callable[[], float] = time.time
    worker_alive_probe: Callable[[int, float], bool] | None = None

    # -- worker bookkeeping --------------------------------------------------------------------
    def _worker_path(self, operation_id: str) -> Path:
        return worker_path(self.store, operation_id)

    def _record_worker(self, operation_id: str, pid: int | None) -> None:
        write_worker_record(self.store, operation_id, pid, submitted=self.now() if pid is None else None)

    def worker_alive(self, operation_id: str) -> bool:
        path = self._worker_path(operation_id)
        if not path.exists():
            return False
        record = read_json(path)
        pid, created = record.get('pid'), record.get('created')
        if pid is None:  # recorded at submit, before the process exists: starting, within a grace
            return self.now() - float(record.get('submitted') or 0) < STARTING_GRACE_SECONDS
        if self.worker_alive_probe is not None:
            return self.worker_alive_probe(pid, created)
        actual = _create_time(pid) if isinstance(pid, int) else None
        if actual is None:
            return False
        return created is None or abs(actual - created) < 2.0  # a recycled pid has another birth time

    # -- reading -----------------------------------------------------------------------------------
    def _operations(self) -> list[Operation]:
        root = self.store.root / 'operations'
        found = []
        for path in sorted(root.glob('*.json')) if root.exists() else []:
            operation = self.store.operation_for_request(path.stem)
            if operation:
                found.append(operation)
        return found

    def status_of(self, operation: Operation) -> Result:
        result = next((e.result for e in reversed(operation.events) if e.result is not None), None)
        if result is None:
            state = 'accepted' if self.worker_alive(operation.operation_id) else 'unknown'
            return make_result(operation.operation_id, None, state, [], [])
        if result.state in ACTIVE and not self.worker_alive(operation.operation_id):
            # The worker died with work unfinished: nothing may claim it is still running.
            return make_result(operation.operation_id, result.checkpoint_id, 'unknown', list(result.seats),
                               [*result.blockers, Blocker('worker-dead', None, 'Reconcile, then retry the failed rows')])
        return result

    def _admit(self) -> str | None:
        busy = self.active_operation()
        return f'{busy.operation_id} is active' if busy is not None else None

    def active_operation(self) -> Operation | None:
        for operation in self._operations():
            if operation.request.action in MUTATING and self.status_of(operation).state in ACTIVE:
                return operation
        return None

    # -- actions -------------------------------------------------------------------------------------
    def submit(self, request: Request, *, actor_id: str, params: dict) -> Result:
        wire(request)
        try:
            existing = self.store.operation_for_request(request.request_id)
            if existing is not None:  # same id + same payload + same actor -> the same operation
                self.store.submit(request, actor_id=actor_id)
                return self.status_of(existing)
            operation = self.store.submit(request, actor_id=actor_id, admit=self._admit)
        except AdmissionRefused as exc:
            busy_id = str(exc).split()[0]
            return rejected('operation-active', f'Operation {busy_id} is still active; status it, cancel it, or wait',
                            operation_id=busy_id)
        except ContractError as exc:
            return rejected('request-conflict', str(exc))
        write_params(self.store, operation.operation_id, params)
        log_path = self.store.root / 'logs' / f'{operation.operation_id}.log'
        try:
            self._record_worker(operation.operation_id, None)  # "starting" is visible before the process exists
            pid = self.launcher(operation.operation_id, self.harness_root, log_path)
        except OSError:
            return make_result(operation.operation_id, None, 'unknown', [], [Blocker(
                'worker-not-started', None, 'The operation is saved but its worker did not start; submit again with a NEW request_id')])
        try:
            self._record_worker(operation.operation_id, pid)
        except OSError:
            pass  # the worker IS running; the starting record keeps it ACTIVE for its grace period
        return make_result(operation.operation_id, params.get('checkpoint_id') if request.action == 'restore' else None,
                           'accepted', self._roster_rows(params, request), [], ['status', 'cancel'])

    def _roster_rows(self, params: dict, request: Request) -> list[SeatResult]:
        if request.action != 'restore':
            return []
        manifest = self.store.read_manifest(request.checkpoint_id)
        return [seat_result(e.agent_id, 'preparing') for e in manifest.expected]

    def status(self, request: Request) -> Result:
        if request.request_id is not None:
            operation = self.store.operation_for_request(request.request_id)
        elif request.operation_id is not None:
            operation = self.store.operation(request.operation_id)
        elif request.checkpoint_id is not None:
            manifest = self.store.read_manifest(request.checkpoint_id)
            return manifest_report(manifest, None, manifest.state)
        else:
            operations = self._operations()
            operation = max(operations, key=lambda o: self._mtime(o), default=None)
        if operation is None:
            return rejected('unknown-operation', 'No such operation or request in this store')
        return self.status_of(operation)

    def _mtime(self, operation: Operation) -> float:
        return (self.store.root / 'operations' / f'{operation.request.request_id}.json').stat().st_mtime

    def cancel(self, request: Request) -> Result:
        operation = self.store.operation(request.operation_id)
        if operation is None:
            return rejected('unknown-operation', 'No such operation in this store')
        current = self.status_of(operation)
        if current.state not in ACTIVE:
            return current  # nothing to cancel; report what it is
        log = OperationLog(self.store, operation.operation_id)
        try:
            return self._request_cancel(operation, current, log)
        except ContractError as exc:
            return rejected('cancel-refused', f'{exc}; status the operation and cancel again')

    def _request_cancel(self, operation: Operation, current: Result, log: OperationLog) -> Result:
        if not any(e.stage == 'cancel-requested' for e in operation.events):
            # A restore that never journaled a result still has a frozen roster to report.
            seats = list(current.seats) or self._roster_rows({}, operation.request)
            result = make_result(operation.operation_id, current.checkpoint_id or operation.request.checkpoint_id,
                                 'canceling', seats, list(current.blockers))
            log.event('cancel-requested', None, result)
        return self.status_of(self.store.operation(operation.operation_id))

    def retry(self, request: Request, *, actor_id: str, reconciled: Callable[[str, str], bool]) -> Result:
        """Only reconciled `failed` rows of a finished restore, or any seat of an incomplete save."""
        target = self.store.operation(request.operation_id)
        if target is None:
            return rejected('unknown-operation', 'No such operation in this store')
        current = self.status_of(target)
        if current.state in ACTIVE:
            return rejected('operation-active', 'That operation is still active', operation_id=target.operation_id)
        by_id = {s.seat_id: s for s in current.seats}
        for seat_id in request.seat_ids:
            row = by_id.get(seat_id)
            if row is None:
                return rejected('unknown-seat', f'Seat {seat_id} is not in that operation')
            if target.request.action == 'restore' and row.state != 'failed':
                return rejected('not-retryable', f'Seat {seat_id} is {row.state}; only failed rows retry, '
                                'and unknown rows need reconciliation first')
            if target.request.action == 'restore' and not reconciled(seat_id, 'dead'):
                return rejected('not-reconciled', f'Seat {seat_id} may be alive; reconcile before retry')
        params = dict(read_params(self.store, target.operation_id))
        params.update({'retry_of': target.operation_id, 'kind': target.request.action, 'seat_ids': list(request.seat_ids)})
        return self.submit(request, actor_id=actor_id, params=params)
