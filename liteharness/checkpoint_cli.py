"""`liteharness sessions checkpoint <save|restore|status|cancel|retry>`.

The CLI is the trust anchor: a mutating action needs an actor id that resolves to a live
registry ORCHESTRATOR (the `recovered_destination.issuer` checks: online, owner process alive,
not superseded). An id typed into a model-supplied argument, or a leaf id alone, grants nothing.
The MCP tool only forwards here. Dry-run and status are read-only and never launch.
`--json` prints exactly `wire(Result)`.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable
from uuid import uuid4

from . import config
from .checkpoint_contract import Blocker, ContractError, Request, Result, wire
from .checkpoint_ops import make_result, rejected, seat_result
from .checkpoint_restore import RestoreEnv, RestoreParams, resolve_checkpoint, run_restore
from .checkpoint_save import SaveParams
from .checkpoint_service import ACTIVE, Service
from .checkpoint_store import CheckpointStore

WAIT_POLL_SECONDS = 2.0
OK_STATES = ('accepted', 'complete', 'canceling', 'canceled')


class Unauthorized(Exception):
    pass


def authorize_orchestrator(actor_id: str | None, leaf_id: str | None = None, require_leaf: bool = False) -> str:
    """The actor must be a live registry ORCHESTRATOR; with a leaf id, the same row must own it."""
    from . import recovered_destination
    from .agent_store import valid_id
    try:
        actor = valid_id(actor_id)
        recovered_destination.issuer(actor)
        row = json.loads((config.get_root() / 'agents' / f'{actor}.json').read_text(encoding='utf-8'))
    except Exception as exc:  # unreadable, absent, stale, wrong role: all the same refusal
        raise Unauthorized('A live registry ORCHESTRATOR is required to change checkpoints') from exc
    if require_leaf and not leaf_id:
        raise Unauthorized('The tool path must present its leaf id; none was supplied')
    if leaf_id and row.get('leaf_id') != leaf_id:
        raise Unauthorized('The presented leaf does not belong to that orchestrator')
    return actor


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='liteharness sessions checkpoint',
                                description='Save and restore the whole fleet from one call.')
    p.add_argument('action', choices=['save', 'restore', 'status', 'cancel', 'retry'])
    p.add_argument('--checkpoint-id', help="a checkpoint id, or 'latest' (newest manifest in any state)")
    p.add_argument('--request-id', help='idempotency key; generated when omitted')
    p.add_argument('--operation-id')
    p.add_argument('--seat-id', action='append', default=[], help='retry: a seat to retry (repeatable)')
    p.add_argument('--dry-run', action='store_true', help='plan only: never launches, journals or closes anything')
    p.add_argument('--json', action='store_true', help='print exactly the typed Result')
    p.add_argument('--actor-id', help="the calling orchestrator's agent id (default: this session's)")
    p.add_argument('--leaf-id', help='the caller leaf; must belong to the actor row')
    p.add_argument('--require-leaf', action='store_true', help='refuse unless --leaf-id matches (tool path)')
    p.add_argument('--coordinator-handoff', help='save: absolute path of the coordinator (caller) handoff')
    p.add_argument('--include-offline', action='append', default=[], help='save: an offline seat id to include')
    p.add_argument('--keep-shells', action='store_true', help='restore: do not close bare shells afterwards')
    p.add_argument('--wait', type=float, default=0, help='poll up to N seconds for a terminal state')
    p.add_argument('--harness-root', help=argparse.SUPPRESS)
    return p


def _request(args, store: CheckpointStore) -> Request:
    if args.dry_run and args.action not in ('save', 'restore'):
        raise ContractError(f'--dry-run applies only to save and restore, not {args.action}')
    rid = args.request_id or str(uuid4())
    if args.action == 'save':
        return Request(1, 'save', rid, None, None, [], args.dry_run)
    if args.action == 'restore':
        return Request(1, 'restore', rid, resolve_checkpoint(store, args.checkpoint_id or 'latest'), None, [], args.dry_run)
    if args.action == 'status':
        cid = resolve_checkpoint(store, args.checkpoint_id) if args.checkpoint_id else None
        return Request(1, 'status', args.request_id, cid, args.operation_id, [], False)
    if args.action == 'cancel':
        return Request(1, 'cancel', rid, None, args.operation_id, [], False)
    return Request(1, 'retry', rid, None, args.operation_id, list(args.seat_id), False)


def _preview_save(root: Path, store: CheckpointStore, actor: str, args) -> Result:
    """Roster that WOULD be frozen; nothing is written, asked or compacted."""
    from .checkpoint_roster import discover_current, roster_census
    discovery = discover_current(harness_root=root, include_offline=args.include_offline)
    census = roster_census(discovery, harness_root=root)
    rows = [seat_result(r.expected.agent_id, 'blocked' if r.seat is None else 'preparing', errors=r.blockers)
            for r in census.rows]
    missing = [] if actor in {r.expected.agent_id for r in census.rows} else [
        Blocker('coordinator-missing', None, 'The caller is not a live seat in the registry')]
    return make_result(None, None, 'accepted' if not any(r.state == 'blocked' for r in rows) and not missing
                       else 'incomplete', rows, missing)


def _save_params(args, root: Path, actor: str) -> dict:
    handoff = Path(args.coordinator_handoff or '')
    if not handoff.is_absolute() or not handoff.is_file():
        raise ContractError('save needs --coordinator-handoff: the absolute path of your own handoff file')
    cfg = config.load().get('checkpoint') or {}
    return SaveParams(str(root), actor, str(handoff), str(uuid4()), include_offline=list(args.include_offline),
                      cwd_roots=cfg.get('cwd_roots'), litetui_root=cfg.get('litetui_root')).wire()


def resolve_actor(args) -> tuple[str, str | None, bool]:
    """(actor, leaf, require_leaf). Typed --actor-id/--leaf-id count only on the tool path.

    The bridge sets LITESUITE_LEAF_ID for the one-shot tool process. Without it this is a direct
    CLI call and the actor is the caller's own environment identity, whatever the flags say.
    """
    bridge_leaf = os.environ.get('LITESUITE_LEAF_ID', '').strip()
    if not bridge_leaf:
        return config.get_agent_id(), None, False
    if args.leaf_id and args.leaf_id != bridge_leaf:
        raise Unauthorized('The presented leaf is not the leaf this tool call arrived on')
    return args.actor_id or config.get_agent_id(), bridge_leaf, args.require_leaf


def run(args, *, authorize: Callable = authorize_orchestrator, service: Service | None = None,
        sleep: Callable[[float], None] = time.sleep) -> Result:
    root = Path(args.harness_root) if args.harness_root else config.get_root()
    store = CheckpointStore(root)
    service = service or Service(store, root)
    try:
        request = _request(args, store)
        wire(request)
    except ContractError as exc:
        return rejected('invalid-request', str(exc))
    if request.action == 'status':
        return _waited(service, service.status(request), args, sleep)
    try:
        actor, leaf, require_leaf = resolve_actor(args)
    except Unauthorized as exc:
        return rejected('unauthorized', str(exc))
    if request.dry_run:
        if request.action == 'save':
            return _preview_save(root, store, actor, args)
        return run_restore(store, None, RestoreParams(str(root), actor, request.checkpoint_id, keep_shells=args.keep_shells,
                                                      dry_run=True), RestoreEnv())
    try:
        actor = authorize(actor, leaf, require_leaf)
    except Unauthorized as exc:
        return rejected('unauthorized', str(exc))
    if request.action == 'save':
        try:
            params = _save_params(args, root, actor)
        except ContractError as exc:
            return rejected('invalid-request', str(exc))
        result = service.submit(request, actor_id=actor, params=params)
    elif request.action == 'restore':
        params = RestoreParams(str(root), actor, request.checkpoint_id, keep_shells=args.keep_shells).wire()
        result = service.submit(request, actor_id=actor, params=params)
    elif request.action == 'cancel':
        result = service.cancel(request)
    else:
        result = service.retry(request, actor_id=actor, reconciled=lambda seat, want: RestoreEnv().liveness(seat) == want)
    return _waited(service, result, args, sleep)


def _waited(service: Service, result: Result, args, sleep) -> Result:
    deadline = time.monotonic() + args.wait
    while args.wait and result.state in ACTIVE and result.operation_id and time.monotonic() < deadline:
        sleep(WAIT_POLL_SECONDS)
        result = service.status(Request(1, 'status', None, None, result.operation_id, [], False))
    return result


def not_ready_canceled(result: Result) -> bool:
    """A cancelled operation that left seats unfinished is NOT READY, whatever it was cancelled from."""
    return result.state == 'canceled' and result.counts.ready < result.counts.expected


def render(result: Result) -> str:
    c = result.counts
    lines = [f'{result.state.upper()}'
             + (f' operation {result.operation_id}' if result.operation_id else '')
             + (f' checkpoint {result.checkpoint_id}' if result.checkpoint_id else ''),
             f'  expected {c.expected}  ready {c.ready}  held {c.held}  failed/blocked {c.failed}  unknown {c.unknown}']
    for seat in result.seats:
        detail = '; '.join(f'{e.code}: {e.remedy}' for e in seat.errors)
        lines.append(f'  {seat.seat_id}  {seat.state}' + (f'  ({detail})' if detail else ''))
    lines += [f'  ! {b.code}: {b.remedy}' for b in result.blockers]
    if result.state in ('incomplete', 'rejected', 'unknown') or not_ready_canceled(result):
        lines.append('REFUSED' if result.state == 'rejected' else 'NOT READY')
    if result.allowed_next_actions:
        lines.append('  next: ' + ', '.join(result.allowed_next_actions))
    return '\n'.join(lines)


def main(argv: list[str] | None = None, **kwargs) -> int:
    args = build_parser().parse_args(argv)
    result = run(args, **kwargs)
    print(json.dumps(wire(result)) if args.json else render(result))
    return 0 if result.state in OK_STATES and not not_ready_canceled(result) else 2
