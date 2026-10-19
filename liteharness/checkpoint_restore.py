"""One-call restore: a pure plan, then an idempotent driver over the saved roster.

Restore = `live seat(Seat)` folded over the roster, parents before children, owners before
crew, one launch at a time. The journal is the only memory of what was attempted: a launch
intent is appended BEFORE dispatch, so a crash between intent and result is `unknown` and is
never retried. A live seat is reattached, never relaunched; unknown liveness is `unknown`.
Never `--kill-old`, never a new name, never a takeover. Works from an incomplete checkpoint.
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

from . import config, roles
from .checkpoint_contract import Blocker, ContractError, Manifest, Receipt, Result, Seat, wire
from .checkpoint_ops import OperationLog, make_result, seat_result
from .checkpoint_save import evidence, sha256_file
from .checkpoint_store import CheckpointStore
from uuid import uuid4

SPAWN_TIMEOUT = 300.0
VERIFY_TIMEOUT = 30.0
MAX_LEAVES = 8  # fleet panes cap at 8 leaves
POINTER = ('Restored from checkpoint {checkpoint_id}. Resume as-is. Arm ONE inbox Monitor for your own id. '
           'Do not start new work, commit, push or rebuild. Wait for an inbox message from {coordinator}.')
HELD_POINTER = ' You were mid-work when saved: hold until your owner reconciles.'
PROMPT_REFUSAL = 'does not support explicit --prompt'  # T0589 slot recovery refuses before launching
WAKE_LINE = 'resumed: arm ONE inbox Monitor and read your inbox'
ROLE_RANK = {role: n for n, role in enumerate(roles.ROLES)}


@dataclass(frozen=True)
class SpawnOutcome:
    returncode: int | None  # None = timed out: the outcome is unknown, never retried
    tail: str = ''


@dataclass(frozen=True)
class PlanRow:
    seat_id: str
    name: str
    action: str  # launch | reattach | coordinator | blocked | unknown
    reason: str = ''


def _no_window() -> dict:
    """No console flash for a child of a worker that has none (Windows only)."""
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def _spawn(argv: list[str], env: dict, timeout: float) -> SpawnOutcome:
    try:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout,
                              cwd=str(config.get_root()), **_no_window())
    except subprocess.TimeoutExpired:
        return SpawnOutcome(None, 'timed out')
    return SpawnOutcome(done.returncode, ((done.stderr or '') + (done.stdout or ''))[-400:])


def _bridge(method: str, path: str, body: dict | None = None):
    from . import cli
    try:
        return cli._bridge_request(method, path, body)
    except Exception:
        return None


def _pty_list() -> list[dict] | None:
    result = _bridge('GET', '/pty/list')
    sessions = result.get('sessions') if isinstance(result, dict) else None
    return sessions if isinstance(sessions, list) and all(isinstance(s, dict) for s in sessions) else None


def _context() -> list[dict] | None:
    result = _bridge('GET', '/context')
    panes = result.get('activePanes') if isinstance(result, dict) else None
    return panes if isinstance(panes, list) and all(isinstance(p, dict) for p in panes) else None


def _leaves(pane_id: str) -> list[dict] | None:
    from urllib.parse import quote
    result = _bridge('GET', f'/canvas/leaves?paneId={quote(pane_id)}')
    leaves = result.get('leaves') if isinstance(result, dict) and result.get('ok') is True else None
    return leaves if isinstance(leaves, list) else None


def _close_leaf(leaf_id: str) -> bool:
    result = _bridge('POST', '/canvas/close-leaf', {'leafId': leaf_id})
    return isinstance(result, dict) and result.get('ok') is True


def _has_children(pid: int) -> bool | None:
    """True/False for a shell pid, None when it cannot be inspected (then: never touch it)."""
    try:
        import psutil
        return bool(psutil.Process(pid).children())
    except Exception:
        return None


SHELLS = frozenset({'powershell.exe', 'pwsh.exe', 'cmd.exe', 'bash.exe', 'sh.exe'})


def _process_name(pid: int) -> str | None:
    try:
        import psutil
        return psutil.Process(pid).name().lower()
    except Exception:
        return None


def default_liveness(agent_id: str) -> str:
    """'live' | 'dead' | 'unknown' from the CURRENT registry row and process creation identity.

    A PTY that claims the seat is never proof by itself: with no registry row it is `unknown`.
    Anything this cannot read, or a row without a usable pid and birth time, is `unknown`.
    """
    import psutil
    path = config.get_root() / 'agents' / f'{agent_id}.json'
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        row = None
    except (OSError, ValueError):
        return 'unknown'
    ptys = _pty_list()
    if ptys is None:
        return 'unknown'
    # An ordinary split-launched Claude PTY carries no harnessAgentId. A harnessAgentId that names this
    # seat means "something may still be starting for it" (unknown). The registered canvas session joins
    # a PTY to the seat but says nothing about its PROCESS: a crashed seat leaves a shell husk there.
    session = row.get('canvas_session_id') if isinstance(row, dict) else None
    named = any(isinstance(s, dict) and s.get('harnessAgentId') == agent_id for s in ptys)
    husk = next((s for s in ptys if isinstance(s, dict) and session and s.get('id') == session
                 and s.get('harnessAgentId') in (None, agent_id)), None)
    if not isinstance(row, dict) or row.get('exited_at'):
        return 'unknown' if named else 'dead'
    pid, born = row.get('session_pid'), row.get('session_process_started_at')
    try:
        pid, born = int(pid), float(born)
    except (TypeError, ValueError):
        return 'unknown'
    if pid <= 0 or born <= 0:
        return 'unknown'
    try:
        alive = psutil.Process(pid).create_time() * 1000
    except psutil.NoSuchProcess:
        # Gone. Unknown only if the PTY is named for this seat, or its shell still runs something.
        busy = bool(husk) and isinstance(husk.get('pid'), int) and _has_children(husk['pid']) is True
        return 'unknown' if named or busy else 'dead'
    except psutil.Error:
        return 'unknown'
    # A recycled pid has a different creation time: that is a dead seat, not a live one.
    return 'live' if abs(alive - born) < 2000 else 'dead'


@dataclass
class RestoreEnv:
    """The process boundary. Tests replace it; the plan and the driver are real."""
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], None] = time.sleep
    liveness: Callable[[str], str] = default_liveness
    spawn: Callable[[list[str], dict, float], SpawnOutcome] = _spawn
    registry: Callable[[str], dict | None] = lambda agent_id: _read_registry(agent_id)
    send_mail: Callable[[str, str, str], str] = lambda sender, to, body: _send_mail(sender, to, body)
    wake: Callable[[str, str], str] = lambda session_id, line: _wake(session_id, line)
    context: Callable[[], list[dict] | None] = _context
    pty_list: Callable[[], list[dict] | None] = _pty_list
    leaves: Callable[[str], list[dict] | None] = _leaves
    close_leaf: Callable[[str], bool] = _close_leaf
    has_children: Callable[[int], bool | None] = _has_children
    process_name: Callable[[int], str | None] = _process_name
    # Whether another operation's worker is still running, and how long since it last wrote its
    # journal. None = the real answer (the service's worker record, the journal file's mtime).
    operation_active: Callable | None = None
    journal_age: Callable | None = None
    spawn_timeout: float = SPAWN_TIMEOUT
    verify_timeout: float = VERIFY_TIMEOUT
    poll_interval: float = 1.0
    python: str = field(default_factory=lambda: sys.executable)


def _send_mail(sender: str, recipient: str, body: str) -> str:
    from . import inbox
    return inbox.send(sender, recipient, body, msg_type='TASK', priority='high')


def _wake(session_id: str, line: str) -> str:
    """Type ONE line into this exact session through the harness's own post-spawn mechanism.

    `_deliver_prompt` types the nudge once, confirms the turn by the seat's hook stamp, and
    retries with Enter only. There is no brief file for a resumed seat, so the path never exists.
    """
    from . import cli
    started = time.time()

    def write(text: str) -> None:
        reply = cli._bridge_request('POST', '/pty/write', {'session_id': session_id, 'data': text + '\r'})
        if not isinstance(reply, dict) or not reply.get('ok'):
            raise RuntimeError('pty/write refused')
    return cli._deliver_prompt(write, lambda: cli._turn_seen_since('canvas_session_id', session_id, started),
                               Path(os.devnull).parent / 'no-brief-for-a-resumed-seat', line, line, 0)


def _read_registry(agent_id: str) -> dict | None:
    try:
        row = json.loads((config.get_root() / 'agents' / f'{agent_id}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


@dataclass
class RestoreParams:
    harness_root: str
    coordinator_id: str  # the authorized actor: restore never launches or closes it
    checkpoint_id: str
    keep_shells: bool = False
    dry_run: bool = False
    seat_ids: list[str] | None = None  # retry: only these (reconciled failed) rows
    retry_of: str | None = None  # retry: the operation whose other rows are reported as they were

    def wire(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_wire(cls, value: dict) -> 'RestoreParams':
        return cls(**value)


# -- plan --------------------------------------------------------------------------------------

def plan(manifest: Manifest, liveness: dict[str, str], coordinator_id: str) -> list[PlanRow]:
    """Pure. Parent-first, owners before crew; a bad parent blocks only its own subtree."""
    seats = {s.agent_id: s for s in manifest.seats}
    expected = {e.agent_id: e for e in manifest.expected}
    saved_coordinator = manifest.coordinator.agent_id
    rows: dict[str, PlanRow] = {}
    for agent_id, e in expected.items():
        seat = seats.get(agent_id)
        if agent_id == saved_coordinator:
            rows[agent_id] = PlanRow(agent_id, e.name, 'coordinator')
        elif seat is None:
            rows[agent_id] = PlanRow(agent_id, e.name, 'blocked', 'unresolved-in-checkpoint')
        else:
            state = liveness.get(agent_id, 'unknown')
            rows[agent_id] = PlanRow(agent_id, e.name, {'live': 'reattach', 'dead': 'launch'}.get(state, 'unknown'),
                                     '' if state in ('live', 'dead') else 'liveness-unknown')

    def parent_of(agent_id: str) -> str | None:
        seat = seats.get(agent_id)
        parent = seat.parent_id if seat else None
        return None if parent in (None, saved_coordinator) else parent

    depth: dict[str, int] = {}
    cyclic: set[str] = set()

    def resolve(agent_id: str, path: tuple[str, ...]) -> int:
        if agent_id in depth:
            return depth[agent_id]
        if agent_id in path:
            cyclic.update(path[path.index(agent_id):])
            return 0
        parent = parent_of(agent_id)
        value = 0 if parent is None or parent not in expected else 1 + resolve(parent, (*path, agent_id))
        depth[agent_id] = value
        return value

    for agent_id in expected:
        resolve(agent_id, ())

    def verdict(agent_id: str, trail: tuple[str, ...] = ()) -> tuple[str, str] | None:
        row = rows[agent_id]
        if row.action == 'blocked':
            return 'blocked', row.reason
        if agent_id in cyclic:
            return 'blocked', 'parent-cycle'
        parent = parent_of(agent_id)
        if parent is None or row.action in ('coordinator', 'reattach'):
            return None
        if parent not in expected:
            return 'blocked', 'parent-missing'
        if parent in trail:
            return 'blocked', 'parent-cycle'
        above = verdict(parent, (*trail, agent_id))
        if above is not None:
            return 'blocked', 'parent-blocked'
        if rows[parent].action == 'unknown':
            return 'blocked', 'parent-unknown'
        return None

    for agent_id in list(rows):
        blocked = verdict(agent_id)
        if blocked and rows[agent_id].action in ('launch', 'unknown', 'blocked'):
            rows[agent_id] = PlanRow(agent_id, rows[agent_id].name, *blocked)

    def key(row: PlanRow):
        seat = seats.get(row.seat_id)
        return (depth.get(row.seat_id, 0), ROLE_RANK.get(roles.normalize_role(seat.role) if seat else '', 99),
                row.name.casefold())
    return sorted(rows.values(), key=key)


def resolve_checkpoint(store: CheckpointStore, checkpoint_id: str) -> str:
    """Explicit id, or `latest`: the newest manifest by created_at in ANY state."""
    if checkpoint_id != 'latest':
        return store.read_manifest(checkpoint_id).checkpoint_id
    newest = None
    for path in sorted(store.root.glob('*/manifest.json')) if store.root.exists() else []:
        try:
            manifest = store.read_manifest(path.parent.name)
        except ContractError:
            continue
        stamp = datetime.fromisoformat(manifest.created_at.replace('Z', '+00:00'))
        if newest is None or stamp > newest[0]:
            newest = (stamp, manifest.checkpoint_id)
    if newest is None:
        raise ContractError('No checkpoint exists to restore')
    return newest[1]


# -- placement ---------------------------------------------------------------------------------

def coordinator_panes(env: RestoreEnv, coordinator_id: str) -> set[str]:
    """Every pane id the coordinator occupies: restore never places a seat there."""
    row = env.registry(coordinator_id) or {}
    panes = env.context() or []
    found = {row['pane_id']} if isinstance(row.get('pane_id'), str) else set()
    session = row.get('canvas_session_id')
    for pane in panes:
        if session and any(session in leaf.get('sessionIds', []) for leaf in pane.get('leaves', [])
                           if isinstance(leaf, dict)):
            found.add(pane['id'])
    return found


def choose_pane(env: RestoreEnv, seat: Seat, excluded: set[str], restored_panes: list[str]) -> str | None:
    """Saved pane if it exists with room and is not excluded; else a pane already holding
    restored seats; else None, letting the placement hierarchy (and finally a new pane) decide."""
    panes = {p['id']: p for p in env.context() or [] if isinstance(p.get('id'), str)}

    def room(pane_id: str) -> bool:
        pane = panes.get(pane_id)
        count = pane.get('leafCount', 0) if pane else 0
        return pane is not None and pane_id not in excluded and isinstance(count, int) and 0 < count < MAX_LEAVES
    if seat.slot and room(seat.slot.pane):
        return seat.slot.pane
    return next((p for p in restored_panes if room(p)), None)


# -- driver --------------------------------------------------------------------------------------

class Restorer:
    def __init__(self, store: CheckpointStore, params: RestoreParams, env: RestoreEnv, operation_id: str):
        self.store, self.params, self.env, self.operation_id = store, params, env, operation_id
        self.log = OperationLog(store, operation_id) if operation_id else None  # None = dry-run: read-only
        self.manifest = store.read_manifest(params.checkpoint_id)
        self.states: dict[str, str] = {}
        self.errors: dict[str, list[Blocker]] = {}
        self.evidence: dict[str, list[Receipt]] = {}
        self.restored_panes: list[str] = []
        self.reattached: set[str] = set()

    def report(self, state: str, *, final: bool = False) -> Result:
        rows = [seat_result(e.agent_id, self.states.get(e.agent_id, 'preparing'),
                            evidence=self.evidence.get(e.agent_id, []), errors=self.errors.get(e.agent_id, []))
                for e in self.manifest.expected]
        if final and state == 'complete' and any(r.state not in ('ready-idle', 'ready-held') or r.errors for r in rows):
            state = 'incomplete'
        return make_result(self.operation_id, self.manifest.checkpoint_id, state, rows)

    def journal(self, stage: str, seat_id: str | None, result: Result | None) -> None:
        if self.log is not None:
            self.log.event(stage, seat_id, result)

    def journalled_states(self) -> dict[str, str]:
        """What this operation already attempted. An intent with no result is `unknown`."""
        seen: dict[str, str] = {}
        for event in (self.log.operation().events if self.log else []):
            if event.seat_id is None:
                continue
            if event.stage == 'launch-intent':
                seen[event.seat_id] = 'unknown'
            elif event.result is not None:
                match = next((s.state for s in event.result.seats if s.seat_id == event.seat_id), None)
                if match in ('ready-idle', 'ready-held', 'failed', 'unknown', 'blocked'):
                    seen[event.seat_id] = match
        return seen

    def adopt_prior_states(self, operation_id: str, retrying: set[str]) -> None:
        """Seats a retry does not touch keep the state, errors and evidence the retried operation left."""
        previous = self.store.operation(operation_id)
        last = next((e.result for e in reversed(previous.events) if e.result is not None), None) if previous else None
        for row in (last.seats if last else []):
            if row.seat_id not in retrying and row.seat_id in self.states:
                self.states[row.seat_id] = row.state
                self.errors[row.seat_id] = list(row.errors)
                self.evidence[row.seat_id] = list(row.evidence)

    def foreign_unknown(self) -> set[str]:
        """Seats another operation on this checkpoint started (intent) without ever reporting."""
        if self.log is None:
            return set()
        from .checkpoint_ops import read_params
        mine = self.operation_id
        unknown: set[str] = set()
        root = self.store.root / 'operations'
        for path in sorted(root.glob('*.json')) if root.exists() else []:
            other = self.store.operation_for_request(path.stem)
            if other is None or other.operation_id == mine:
                continue
            kind = read_params(self.store, other.operation_id).get('kind', other.request.action)
            checkpoint = other.request.checkpoint_id or read_params(self.store, other.operation_id).get('checkpoint_id')
            if kind != 'restore' or checkpoint != self.manifest.checkpoint_id:
                continue
            seen: dict[str, str] = {}
            for event in other.events:
                if event.seat_id is None:
                    continue
                if event.stage == 'launch-intent':
                    seen[event.seat_id] = 'unknown'
                elif event.result is not None:
                    seen[event.seat_id] = next((x.state for x in event.result.seats if x.seat_id == event.seat_id),
                                               seen.get(event.seat_id, ''))
            stuck = {seat for seat, state in seen.items() if state == 'unknown'}
            if stuck and self.expired(other):
                stuck = {seat for seat in stuck if self.env.liveness(seat) != 'dead'}
            unknown |= stuck
        return unknown

    def expired(self, other) -> bool:
        """An operation that is not active and has not written for longer than a launch can take.

        Its unknown seats are then judged by current identity alone: provably dead means the old
        attempt is over and the seat may be launched; anything else stays unknown. There is no
        action verb for this by design (the Action literal is card A's).
        """
        env = self.env
        if env.operation_active is not None:
            active = env.operation_active(other)
        else:
            from .checkpoint_service import ACTIVE, Service
            active = Service(self.store, self.store.root.parents[1]).status_of(other).state in ACTIVE
        if active:
            return False
        if env.journal_age is not None:
            age = env.journal_age(other)
        else:
            path = self.store.root / 'operations' / f'{other.request.request_id}.json'
            age = time.time() - path.stat().st_mtime
        return age > env.spawn_timeout + env.verify_timeout

    def cancelled(self) -> bool:
        return self.log is not None and any(e.stage == 'cancel-requested' for e in self.log.operation().events)

    def ready_state(self, seat: Seat) -> str:
        return 'ready-held' if seat.disposition == 'held' else 'ready-idle'

    def prompt_for(self, seat: Seat) -> str:
        text = POINTER.format(checkpoint_id=self.manifest.checkpoint_id, coordinator=self.params.coordinator_id)
        return text + (HELD_POINTER if seat.disposition == 'held' else '')

    def argv(self, seat: Seat, pane: str | None, *, prompt: bool) -> list[str]:
        out = [self.env.python, '-m', 'liteharness.cli', 'spawn', '--resume', seat.agent_id, '--split',
               '--spawned-by', self.params.coordinator_id]
        if pane:
            out += ['--pane', pane]
        if prompt:
            out += ['--prompt', self.prompt_for(seat)]
        return out

    def verified(self, seat: Seat, since: datetime | None = None) -> bool:
        """Registry row live by process identity and joined to a PTY that hosts this agent;
        with `since`, also a NEW registration (a launch), without it any current one (a reattach)."""
        row = self.env.registry(seat.agent_id)
        if not row or row.get('exited_at') or self.env.liveness(seat.agent_id) != 'live':
            return False
        if since is not None:
            try:
                if datetime.fromisoformat(str(row.get('registered_at'))) < since:
                    return False
            except ValueError:
                return False
        sessions = self.env.pty_list()
        session = row.get('canvas_session_id')
        return bool(session) and any(isinstance(s, dict) and s.get('id') == session
                                     and s.get('harnessAgentId') in (None, seat.agent_id) for s in sessions or [])

    def launch(self, seat: Seat, excluded: set[str]) -> str:
        env = self.env
        state = env.liveness(seat.agent_id)  # re-check: it may have come up since the plan
        if state == 'live':
            if not self.verified(seat):  # a bare "live" is not enough to call it reattached
                return 'unknown'
            self.reattached.add(seat.agent_id)
            return self.ready_state(seat)
        if state != 'dead':
            return 'unknown'
        pane = choose_pane(env, seat, excluded, self.restored_panes)
        started = env.now()
        self.journal('launch-intent', seat.agent_id, self.report('running'))
        child_env = {**os.environ, 'LITEHARNESS_AGENT_ID': self.params.coordinator_id,
                     'LITEHARNESS_EXCLUDE_PANES': ','.join(sorted(excluded))}
        outcome = env.spawn(self.argv(seat, pane, prompt=True), child_env, env.spawn_timeout)
        slot_path = outcome.returncode not in (None, 0) and PROMPT_REFUSAL in outcome.tail
        if slot_path:
            # A reserved slot takes no prompt and refuses BEFORE launching: safe to retry bare.
            outcome = env.spawn(self.argv(seat, pane, prompt=False), child_env, env.spawn_timeout)
        if outcome.returncode is None:
            return 'unknown'
        deadline = env.now().timestamp() + env.verify_timeout
        while not self.verified(seat, started):
            if env.now().timestamp() >= deadline:
                return 'failed' if outcome.returncode != 0 and env.liveness(seat.agent_id) == 'dead' else 'unknown'
            env.sleep(env.poll_interval)
        row = env.registry(seat.agent_id) or {}
        for pane_id in [p['id'] for p in env.context() or [] if any(
                row.get('canvas_session_id') in leaf.get('sessionIds', []) for leaf in p.get('leaves', [])
                if isinstance(leaf, dict))]:
            self.restored_panes.append(pane_id)
        if outcome.returncode != 0:
            self.errors.setdefault(seat.agent_id, []).append(Blocker(
                'spawn-exit-nonzero', seat.agent_id, 'Spawn exited non-zero but the seat registered live'))
        if slot_path:
            self.deliver_pointer(seat, row.get('canvas_session_id'))
        return self.ready_state(seat)

    def deliver_pointer(self, seat: Seat, session_id: str | None) -> None:
        """A slot-recovered seat took no --prompt: its pointer goes by inbox, then ONE fixed wake line.

        All content rides the inbox from the coordinator BEFORE the wake (a durable record); the wake
        is one fixed line typed once into the exact new session. Nothing else is ever typed, and never
        into any other session.
        """
        cid = self.manifest.checkpoint_id
        body = (self.prompt_for(seat)
                + f' When saved you were: {seat.disposition}. Next: {seat.first_recovery_action}.')
        failure = None
        try:
            mail_id = self.env.send_mail(self.params.coordinator_id, seat.agent_id, body)
            self.journal('pointer-mailed', seat.agent_id, None)
        except Exception as exc:
            mail_id, failure = None, f'inbox send failed ({type(exc).__name__}); no wake was typed'
        status = ''
        if failure is None and not session_id:
            failure = 'the new session id is unknown; no wake was typed'
        if failure is None:
            status = self.env.wake(session_id, WAKE_LINE)
            self.journal('pointer-wake', seat.agent_id, None)
            if 'turn confirmed' not in status:
                failure = 'the wake was typed but no turn was confirmed; message the seat'
        if failure:
            self.errors.setdefault(seat.agent_id, []).append(Blocker('pointer-not-delivered', seat.agent_id, failure))
            return
        path, digest = evidence(self.store, cid, seat.agent_id, f'pointer-{self.operation_id}',
                                {'pointer': 'delivered', 'mail_id': mail_id, 'session_id': session_id, 'wake': status})
        self.evidence.setdefault(seat.agent_id, []).append(Receipt(
            str(uuid4()), cid, seat.agent_id, 'readiness', seat.source_revision, str(path), digest, True,
            'cooperative', self.env.now().isoformat()))

    def run(self) -> Result:
        params, env = self.params, self.env
        prior = self.journalled_states()
        coordinator_ok = self.coordinator_evidence()
        liveness = {}
        for s in self.manifest.seats:
            if s.agent_id != self.manifest.coordinator.agent_id:
                state = env.liveness(s.agent_id)
                # `live` needs the registry row and a hosting PTY to agree, else it is not reattached.
                liveness[s.agent_id] = 'unknown' if state == 'live' and not self.verified(s) else state
        foreign = self.foreign_unknown()
        full = plan(self.manifest, liveness, params.coordinator_id)
        rows = [r for r in full if params.seat_ids is None or r.seat_id in params.seat_ids]
        seats = {s.agent_id: s for s in self.manifest.seats}
        excluded = coordinator_panes(env, params.coordinator_id)
        for row in full:
            self.states[row.seat_id] = prior.get(row.seat_id) or self.initial_state(row, seats.get(row.seat_id), coordinator_ok)
            if row.action == 'blocked':
                self.errors[row.seat_id] = [Blocker(row.reason, row.seat_id, 'Reconcile this seat, then restore it')]
        if params.retry_of and params.seat_ids is not None and self.log is not None:
            self.adopt_prior_states(params.retry_of, set(params.seat_ids))
        for seat_id in foreign:
            if self.states.get(seat_id) in ('preparing', 'starting'):
                self.states[seat_id] = 'unknown'
                self.errors.setdefault(seat_id, []).append(Blocker(
                    'prior-attempt-unknown', seat_id, 'Another operation started this seat and never reported; reconcile it'))
        if not coordinator_ok and not params.dry_run:
            result = self.report('incomplete')  # a caller who is not the coordinator launches nothing
            self.journal('refused', None, result)
            return result
        if params.dry_run:
            shells = [Blocker('would-close-shell', None, f'pane {pane} leaf {leaf}')
                      for pane, leaf, _, _ in ([] if params.keep_shells else self.shell_candidates(excluded))]
            planned = self.report('accepted' if not any(s == 'blocked' for s in self.states.values()) else 'incomplete')
            return make_result(None, planned.checkpoint_id, planned.state, planned.seats, shells)
        self.journal('plan', None, self.report('running'))
        for row in rows:
            seat = seats.get(row.seat_id)
            if row.action != 'launch' or seat is None or row.seat_id in prior or row.seat_id in foreign:
                if row.action == 'reattach' and seat is not None:
                    self.journal('reattached', row.seat_id, self.report('running'))
                continue
            if self.states.get(seat.parent_id or '', '') in ('failed', 'unknown', 'blocked'):
                self.states[row.seat_id] = 'blocked'
                self.errors[row.seat_id] = [Blocker('parent-failed', row.seat_id, 'Its parent did not come up')]
                self.journal('seat-result', row.seat_id, self.report('running'))
                continue
            if self.cancelled():
                break
            self.states[row.seat_id] = 'starting'
            outcome = self.launch(seat, excluded)
            self.states[row.seat_id] = outcome
            stage = 'reattached' if row.seat_id in self.reattached else 'seat-result'
            if outcome == 'failed':
                self.errors.setdefault(row.seat_id, []).append(
                    Blocker('launch-failed', row.seat_id, 'Spawn failed; reconcile, then retry this seat'))
            self.journal(stage, row.seat_id, self.report('running'))
        if not params.keep_shells and not self.cancelled():
            self.cleanup_shells(excluded)
        state = 'canceled' if self.cancelled() else 'complete'
        result = self.report(state, final=True)
        self.journal('final', None, result)
        return result

    def initial_state(self, row: PlanRow, seat: Seat | None, coordinator_ok: bool) -> str:
        if row.action == 'coordinator':
            return 'ready-idle' if coordinator_ok else 'blocked'
        if row.action == 'blocked':
            return 'blocked'
        if row.action == 'unknown':
            return 'unknown'
        if row.action == 'reattach':
            return self.ready_state(seat)
        return 'starting' if self.params.dry_run else 'preparing'

    def coordinator_evidence(self) -> bool:
        """The caller must be the saved coordinator, or the same NAMED seat under a new id."""
        saved = self.manifest.coordinator.agent_id
        actor, row = self.params.coordinator_id, self.env.registry(self.params.coordinator_id)
        name = next(e.name for e in self.manifest.expected if e.agent_id == saved)
        if actor != saved and (not row or str(row.get('name')) != name):
            self.errors[saved] = [Blocker('coordinator-mismatch', saved,
                                          'The caller is not the saved coordinator; no launch will happen')]
            return False
        if not self.params.dry_run:
            path, digest = evidence(self.store, self.manifest.checkpoint_id, saved, f'manual-return-{self.operation_id}',
                                    {'actor': actor, 'saved_coordinator': saved, 'name': name,
                                     'registered_at': (row or {}).get('registered_at')})
            seat = next((s for s in self.manifest.seats if s.agent_id == saved), None)
            if seat:
                self.evidence[saved] = [Receipt(str(uuid4()), self.manifest.checkpoint_id, saved, 'manual-return',
                                                seat.source_revision, str(path), digest, True, 'cooperative',
                                                self.env.now().isoformat())]
        return True

    # -- bare shells ---------------------------------------------------------------------------
    def shell_candidates(self, excluded: set[str]) -> list[tuple[str, str, str, str]]:
        """(pane, leaf, session, shell) of PTYs that look like what LiteSuite leaves after a restart.

        All of: the leaf is one a SAVED seat slot names (where LiteSuite put a placeholder back; a leaf
        no saved slot names is never closed), the PTY's root process is a known shell, it has NO child
        process, and it is not a seat. The PTY's cwd is deliberately not consulted: a creation cwd like
        a shared projects root would also match a terminal the user opened themselves. Anything unreadable or
        malformed is kept. At least one leaf per pane stays.
        """
        env = self.env
        sessions, panes = env.pty_list(), env.context()
        if sessions is None or panes is None:
            return []
        seat_sessions = {s.get('canvas_session_id') for e in self.manifest.expected if (s := env.registry(e.agent_id))}
        saved_leaves = {s.slot.leaf for s in self.manifest.seats if s.slot}
        by_id = {s.get('id'): s for s in sessions if isinstance(s, dict)}
        out = []
        for pane in panes:
            pane_id = pane.get('id') if isinstance(pane, dict) else None
            if not isinstance(pane_id, str) or pane_id in excluded:
                continue
            leaves = env.leaves(pane_id)
            if not isinstance(leaves, list):
                continue
            closable = []
            for leaf in leaves:
                if not isinstance(leaf, dict) or not isinstance(leaf.get('leafId'), str):
                    continue
                ids = leaf.get('sessionIds')
                if (not isinstance(ids, list) or len(ids) != 1 or ids[0] in seat_sessions
                        or leaf['leafId'] not in saved_leaves):
                    continue
                pty = by_id.get(ids[0])
                pid = pty.get('pid') if pty else None
                if pty is None or pty.get('harnessAgentId') or not isinstance(pid, int):
                    continue
                name = env.process_name(pid)
                if name not in SHELLS or env.has_children(pid) is not False:
                    continue  # a server, a busy shell, or one this cannot read
                closable.append((pane_id, leaf['leafId'], ids[0], name))
            out.extend(closable[:max(0, len(leaves) - 1)])  # keep at least one leaf per pane
        return out

    def cleanup_shells(self, excluded: set[str]) -> None:
        for pane, leaf, session, shell in self.shell_candidates(excluded):
            closed = self.env.close_leaf(leaf)
            self.journal(f"{'shell-closed' if closed else 'shell-close-failed'} pane={pane} leaf={leaf} "
                         f"session={session} shell={shell}", None, None)


def run_restore(store: CheckpointStore, operation_id: str | None, params: RestoreParams, env: RestoreEnv) -> Result:
    """operation_id None is a dry run: it lists the plan and the bare shells, journals nothing, launches nothing."""
    if params.dry_run != (operation_id is None):
        raise ContractError('A dry run has no operation; a real restore needs one')
    return Restorer(store, params, env, operation_id).run()
