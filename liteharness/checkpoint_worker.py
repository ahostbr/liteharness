"""Detached worker: `python -m liteharness.checkpoint_worker --operation-id X`.

Owns one journaled operation from `accepted` to a terminal state. It survives the process
that submitted it. Nothing here decides policy: it loads the operation and its params and
hands them to the save saver or the restore driver. A crash leaves the journal unfinished,
which status reports as `unknown`; the worker never relaunches work it cannot prove it did not do.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .checkpoint_contract import Blocker, ContractError
from .checkpoint_ops import OperationLog, make_result, read_params, seat_result, write_worker_record
from .checkpoint_restore import RestoreEnv, RestoreParams, run_restore
from .checkpoint_save import SaveEnv, SaveParams, run_save
from .checkpoint_store import CheckpointStore

IN_FLIGHT = ('preparing', 'starting', 'verifying', 'reattaching')


def register_self(store: CheckpointStore, operation_id: str) -> None:
    """First thing the worker does: record its own pid, so status never depends on the submitter's write."""
    write_worker_record(store, operation_id, os.getpid())


def run(operation_id: str, harness_root: Path, *, save_env: SaveEnv | None = None,
        restore_env: RestoreEnv | None = None):
    store = CheckpointStore(harness_root)
    operation = store.operation(operation_id)
    if operation is None:
        raise ContractError('Unknown operation')
    register_self(store, operation_id)
    params = read_params(store, operation_id)
    action = operation.request.action
    kind = params.get('kind', action)  # a retry runs the kind of the operation it retries
    try:
        if kind == 'save':
            only = params.get('seat_ids') if action == 'retry' else None
            return run_save(store, operation_id, SaveParams.from_wire(
                {k: v for k, v in params.items() if k in SaveParams.__dataclass_fields__}),
                save_env or SaveEnv.from_config(), only=only)
        if kind == 'restore':
            wired = {k: v for k, v in params.items() if k in RestoreParams.__dataclass_fields__}
            return run_restore(store, operation_id, RestoreParams.from_wire(wired), restore_env or RestoreEnv())
        raise ContractError('Operation kind has no worker')
    except Exception as exc:  # leave a terminal, honest record; never a bare crash
        log = OperationLog(store, operation_id)
        blocker = Blocker('worker-error', None, f'{type(exc).__name__}; see the operation log, then retry')
        last = log.last_result()
        if last is not None:
            # Seats caught mid-flight are unknown, not "starting": the worker no longer knows.
            seats = [seat_result(s.seat_id, 'unknown' if s.state in IN_FLIGHT else s.state, evidence=s.evidence,
                                 errors=s.errors) for s in last.seats]
            result = make_result(operation_id, last.checkpoint_id, 'incomplete', seats, [*last.blockers, blocker])
        else:
            result = make_result(operation_id, operation.request.checkpoint_id, 'rejected', [], [blocker])
        log.event('worker-error', None, result)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='liteharness.checkpoint_worker')
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--harness-root', required=True)
    args = parser.parse_args(argv)
    run(args.operation_id, Path(args.harness_root))
    return 0


if __name__ == '__main__':
    sys.exit(main())
