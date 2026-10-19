"""One-call restore: real plan, journal and driver; only the process boundary is faked."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from liteharness.checkpoint_contract import (Blocker, Coordinator, Decision, Decisions, Execution, ExpectedSeat,
                                             Manifest, OwnerProfile, RECOMMENDED_DECISIONS, Request, Seat, Slot)
from liteharness.checkpoint_ops import OperationLog
from liteharness.checkpoint_restore import (PlanRow, RestoreEnv, RestoreParams, SpawnOutcome, plan,
                                            resolve_checkpoint, run_restore)
from liteharness.checkpoint_store import CheckpointStore
from checkpoint_fixtures import put, uid

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
COORD = uid(1)
CID = uid(900)


class Crash(BaseException):
    """A process death between two journal writes."""


def seat(n, *, role='builder', parent=COORD, disposition='idle', pane=None, root=None):
    slot = Slot('ws', 'th', pane, f'leaf-{n}', None, 'rev-1') if pane else None
    profile = OwnerProfile(f'Seat-{n}', str(root)) if role == 'owner' else None
    return Seat(f'Seat-{n}', uid(n), uid(n + 100), 'claude', Execution('claude', 'claude-x', 'unrecorded'),
                str(root), str(root), role, 'tesla', parent, profile, 'selected-current', disposition,
                'Read your handoff', 'rev-1', slot)


def manifest(root, seats, *, state='incomplete', unresolved=(), created=T0, checkpoint_id=CID):
    decisions = Decisions(**{k: Decision(v, 'unconfirmed-recommendation') for k, v in RECOMMENDED_DECISIONS.items()})
    read_first = root / 'read-first.md'
    read_first.write_text('SAVE', encoding='utf-8')
    coordinator = seat(1, role='orchestrator', parent=None, root=root)
    everyone = [coordinator, *seats]
    expected = [ExpectedSeat(s.agent_id, s.name, s.selection) for s in everyone]
    expected += [ExpectedSeat(uid(n), f'Seat-{n}', 'selected-current') for n in unresolved]
    return Manifest(1, checkpoint_id, created.isoformat(), 'host', 'boot', {'liteharness': 'test'}, decisions,
                    Coordinator(COORD, 'external-manual-return', str(read_first), 'a' * 64), expected, everyone,
                    [], [], [], [Blocker('seat-unresolved', uid(n), 'reconcile') for n in unresolved], state)


class Clock:
    def __init__(self):
        self.t = T0 + timedelta(hours=1)

    def now(self):
        self.t += timedelta(seconds=1)
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


class Box:
    """Registry, PTYs, panes and a `spawn` that really changes them."""

    def __init__(self, tmp):
        self.tmp, self.clock = tmp, Clock()
        self.state, self.registry, self.script = {}, {}, {}
        self.ptys, self.panes, self.calls, self.closed = [], [], [], []
        self.leaves_by_pane, self.children = {}, {}
        self.registry[COORD] = {'agent_id': COORD, 'name': 'Seat-1', 'registered_at': T0.isoformat(),
                                'canvas_session_id': 'pty-coord', 'pane_id': 'pane-coord'}
        self.panes.append({'id': 'pane-coord', 'leafCount': 2, 'leaves': [{'id': 'lc', 'sessionIds': ['pty-coord']}]})

    def env(self, **kw):
        return RestoreEnv(now=self.clock.now, sleep=self.clock.sleep, liveness=lambda a: self.state.get(a, 'dead'),
                          spawn=self.spawn, registry=lambda a: self.registry.get(a), context=lambda: self.panes,
                          pty_list=lambda: self.ptys, leaves=lambda p: self.leaves_by_pane.get(p),
                          close_leaf=lambda leaf: self.closed.append(leaf) or True,
                          has_children=lambda pid: self.children.get(pid), python='C:/py/python.exe',
                          verify_timeout=5, poll_interval=1, **kw)

    def spawn(self, argv, env, timeout):
        agent_id = argv[argv.index('--resume') + 1]
        self.calls.append((argv, env))
        action = self.script.get(agent_id, 'ok')
        if isinstance(action, list):
            action = action.pop(0)
        if action == 'crash':
            raise Crash()
        if action == 'timeout':
            return SpawnOutcome(None)
        if action == 'fail':
            return SpawnOutcome(1, 'boom')
        if action == 'refuse-prompt':
            self.script[agent_id] = 'ok'
            return SpawnOutcome(1, 'Reserved-slot recovery does not support explicit --prompt; nothing launched')
        if action == 'ok':
            session = f'pty-{agent_id[-3:]}'
            self.registry[agent_id] = {'agent_id': agent_id, 'registered_at': self.clock.now().isoformat(),
                                       'canvas_session_id': session}
            self.state[agent_id] = 'live'
            self.ptys.append({'id': session, 'pid': 100, 'harnessAgentId': agent_id})
            self.panes.append({'id': f'pane-{agent_id[-3:]}', 'leafCount': 1,
                               'leaves': [{'id': 'x', 'sessionIds': [session]}]})
        return SpawnOutcome(0)


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def start(tmp_path, m, op=700, **params):
    store = CheckpointStore(tmp_path)
    store.publish(m, authorized_reference_roots=[tmp_path])
    dry = params.get('dry_run', False)
    p = RestoreParams(str(tmp_path), COORD, m.checkpoint_id, **params)
    if dry:
        return store, p, None
    operation = store.submit(Request(1, 'restore', uid(op), m.checkpoint_id, None, [], False), actor_id=COORD)
    return store, p, operation.operation_id


def launched(box):
    return [a[a.index('--resume') + 1] for a, _ in box.calls]


# -- plan ---------------------------------------------------------------------------------------

def test_plan_orders_owners_before_crew_and_parents_before_children(tmp_path):
    owner = seat(2, role='owner', root=tmp_path)
    crew = seat(3, role='builder', parent=owner.agent_id, root=tmp_path)
    scout = seat(4, role='scout', parent=COORD, root=tmp_path)
    grand = seat(5, role='builder', parent=crew.agent_id, root=tmp_path)
    rows = plan(manifest(tmp_path, [grand, scout, crew, owner]), {s: 'dead' for s in map(uid, range(2, 6))}, COORD)
    order = [r.seat_id for r in rows if r.action == 'launch']
    assert order == [owner.agent_id, scout.agent_id, crew.agent_id, grand.agent_id]
    assert [r.action for r in rows if r.seat_id == COORD] == ['coordinator']


def test_plan_missing_parent_and_cycle_block_only_their_subtrees(tmp_path):
    orphan = seat(2, parent=uid(77), root=tmp_path)
    under_orphan = seat(3, parent=orphan.agent_id, root=tmp_path)
    a, b = seat(4, parent=uid(5), root=tmp_path), seat(5, parent=uid(4), root=tmp_path)
    fine = seat(6, root=tmp_path)
    rows = {r.seat_id: r for r in plan(manifest(tmp_path, [orphan, under_orphan, a, b, fine]),
                                        {uid(n): 'dead' for n in range(2, 7)}, COORD)}
    assert (rows[uid(2)].action, rows[uid(2)].reason) == ('blocked', 'parent-missing')
    assert (rows[uid(3)].action, rows[uid(3)].reason) == ('blocked', 'parent-blocked')
    assert rows[uid(4)].reason == rows[uid(5)].reason == 'parent-cycle'
    assert rows[uid(6)].action == 'launch'


def test_plan_live_is_reattach_unknown_is_unknown_unresolved_is_blocked(tmp_path):
    s2, s3, s4 = (seat(n, root=tmp_path) for n in (2, 3, 4))
    rows = {r.seat_id: r.action for r in plan(manifest(tmp_path, [s2, s3, s4], unresolved=[9]),
            {uid(2): 'live', uid(3): 'unknown', uid(4): 'dead'}, COORD)}
    assert rows == {COORD: 'coordinator', uid(2): 'reattach', uid(3): 'unknown', uid(4): 'launch', uid(9): 'blocked'}


# -- driver -------------------------------------------------------------------------------------

def test_restore_launches_in_order_one_at_a_time_and_reports_ready(tmp_path, box):
    owner = seat(2, role='owner', root=tmp_path)
    crew = seat(3, parent=owner.agent_id, root=tmp_path, disposition='held')
    store, params, op = start(tmp_path, manifest(tmp_path, [crew, owner]))
    result = run_restore(store, op, params, box.env())
    assert launched(box) == [owner.agent_id, crew.agent_id]
    states = {s.seat_id: s.state for s in result.seats}
    assert states == {COORD: 'ready-idle', owner.agent_id: 'ready-idle', crew.agent_id: 'ready-held'}
    assert result.state == 'complete' and result.counts.held == 1
    argv, env = box.calls[1]
    assert argv[:6] == ['C:/py/python.exe', '-m', 'liteharness.cli', 'spawn', '--resume', crew.agent_id]
    assert '--split' in argv and argv[argv.index('--spawned-by') + 1] == COORD
    assert not {'--kill-old', '--name', '--takeover'} & set(argv)
    prompt = argv[argv.index('--prompt') + 1]
    assert prompt.startswith(f'Restored from checkpoint {CID}.') and 'Arm ONE inbox Monitor' in prompt
    assert prompt.endswith('hold until your owner reconciles.')
    idle_prompt = box.calls[0][0][box.calls[0][0].index('--prompt') + 1]
    assert 'mid-work' not in idle_prompt


def test_live_seat_is_reattached_never_relaunched_and_unknown_is_never_launched(tmp_path, box):
    live, murky, dead = (seat(n, root=tmp_path) for n in (2, 3, 4))
    box.state = {live.agent_id: 'live', murky.agent_id: 'unknown'}
    store, params, op = start(tmp_path, manifest(tmp_path, [live, murky, dead]))
    result = run_restore(store, op, params, box.env())
    assert launched(box) == [dead.agent_id]
    states = {s.seat_id: s.state for s in result.seats}
    assert states[live.agent_id] == 'ready-idle' and states[murky.agent_id] == 'unknown'
    assert result.state == 'incomplete' and 'reconcile' in result.allowed_next_actions


def test_launch_intent_is_journalled_before_dispatch_and_a_crash_leaves_unknown(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.script[s2.agent_id] = 'crash'
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    with pytest.raises(Crash):
        run_restore(store, op, params, box.env())
    stages = [(e.stage, e.seat_id) for e in OperationLog(store, op).operation().events]
    assert ('launch-intent', s2.agent_id) in stages and not any(st == 'seat-result' for st, _ in stages)
    box.calls.clear()
    again = run_restore(store, op, params, box.env())  # a restarted worker
    assert box.calls == [], 'an intent with no result is unknown, never retried'
    assert {s.seat_id: s.state for s in again.seats}[s2.agent_id] == 'unknown'


def test_timeout_is_unknown_and_never_auto_retried(tmp_path, box):
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    box.script[s2.agent_id] = 'timeout'
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, s3]))
    result = run_restore(store, op, params, box.env())
    assert launched(box) == [s2.agent_id, s3.agent_id]
    states = {s.seat_id: s.state for s in result.seats}
    assert states[s2.agent_id] == 'unknown' and states[s3.agent_id] == 'ready-idle'
    assert 'retry' not in next(s for s in result.seats if s.seat_id == s2.agent_id).allowed_next_actions


def test_failed_parent_blocks_its_children_but_siblings_survive(tmp_path, box):
    parent = seat(2, role='owner', root=tmp_path)
    child = seat(3, parent=parent.agent_id, root=tmp_path)
    other = seat(4, root=tmp_path)
    box.script[parent.agent_id] = 'fail'
    store, params, op = start(tmp_path, manifest(tmp_path, [parent, child, other]))
    result = run_restore(store, op, params, box.env())
    states = {s.seat_id: s.state for s in result.seats}
    assert states[parent.agent_id] == 'failed' and states[child.agent_id] == 'blocked'
    assert states[other.agent_id] == 'ready-idle'
    assert launched(box) == [parent.agent_id, other.agent_id]
    assert 'retry' in next(s for s in result.seats if s.seat_id == parent.agent_id).allowed_next_actions


def test_spawn_returning_zero_without_a_registration_is_unknown_not_ready(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.script[s2.agent_id] = 'zero-no-register'
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    assert {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'unknown'


def test_restores_from_an_incomplete_checkpoint_and_lists_blocked_rows(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2], unresolved=[9], state='incomplete'))
    result = run_restore(store, op, params, box.env())
    states = {s.seat_id: s.state for s in result.seats}
    assert states[s2.agent_id] == 'ready-idle' and states[uid(9)] == 'blocked'
    assert result.state == 'incomplete'


def test_prompt_refusal_from_slot_recovery_is_retried_bare_with_a_blocker(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.script[s2.agent_id] = ['refuse-prompt']
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    assert len(box.calls) == 2 and '--prompt' in box.calls[0][0] and '--prompt' not in box.calls[1][0]
    row = next(s for s in result.seats if s.seat_id == s2.agent_id)
    assert row.state == 'ready-idle' and [e.code for e in row.errors] == ['pointer-not-delivered']
    assert result.state == 'incomplete', 'up but not briefed is not complete'


def test_coordinator_is_never_launched_and_a_foreign_caller_launches_nothing(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    stranger = uid(55)
    box.registry[stranger] = {'agent_id': stranger, 'name': 'Someone-Else', 'registered_at': T0.isoformat()}
    result = run_restore(store, op, RestoreParams(str(tmp_path), stranger, CID), box.env())
    assert {s.seat_id: s.state for s in result.seats}[COORD] == 'blocked'
    assert COORD not in launched(box)


def test_same_named_coordinator_under_a_new_id_is_accepted_and_is_the_spawner(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    store, _, op = start(tmp_path, manifest(tmp_path, [s2]))
    new = uid(56)
    box.registry[new] = {'agent_id': new, 'name': 'Seat-1', 'registered_at': T0.isoformat(), 'canvas_session_id': 'pty-new'}
    result = run_restore(store, op, RestoreParams(str(tmp_path), new, CID), box.env())
    assert result.state == 'complete'
    assert box.calls[0][0][box.calls[0][0].index('--spawned-by') + 1] == new
    assert COORD not in launched(box)


def test_cancel_stops_scheduling_new_launches(tmp_path, box):
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, s3]))
    OperationLog(store, op).event('cancel-requested', None, None)
    result = run_restore(store, op, params, box.env())
    assert box.calls == [] and result.state == 'canceled'


def test_retry_only_launches_the_selected_seats(tmp_path, box):
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, s3]), seat_ids=[s3.agent_id])
    run_restore(store, op, params, box.env())
    assert launched(box) == [s3.agent_id]


# -- placement ----------------------------------------------------------------------------------

def test_saved_pane_with_room_is_used_and_the_coordinator_pane_is_always_excluded(tmp_path, box):
    s2 = seat(2, root=tmp_path, pane='pane-fleet')
    box.panes.append({'id': 'pane-fleet', 'leafCount': 3, 'leaves': []})
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    argv, env = box.calls[0]
    assert argv[argv.index('--pane') + 1] == 'pane-fleet'
    assert env['LITEHARNESS_EXCLUDE_PANES'] == 'pane-coord'
    assert env['LITEHARNESS_AGENT_ID'] == COORD


@pytest.mark.parametrize('saved', ['pane-coord', 'pane-full', 'pane-gone'])
def test_saved_pane_that_is_the_coordinators_full_or_missing_is_not_passed(tmp_path, box, saved):
    s2 = seat(2, root=tmp_path, pane=saved)
    box.panes.append({'id': 'pane-full', 'leafCount': 8, 'leaves': []})
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    argv, env = box.calls[0]
    assert '--pane' not in argv and 'pane-coord' in env['LITEHARNESS_EXCLUDE_PANES'].split(',')


def test_later_seats_prefer_a_pane_that_already_holds_restored_seats(tmp_path, box):
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, s3]))
    run_restore(store, op, params, box.env())
    second = box.calls[1][0]
    assert second[second.index('--pane') + 1] == f'pane-{s2.agent_id[-3:]}'


# -- dry run, latest, shells ------------------------------------------------------------------------

def test_dry_run_launches_nothing_journals_nothing_and_lists_shells(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.panes.append({'id': 'pane-a', 'leafCount': 2, 'leaves': []})
    box.leaves_by_pane['pane-a'] = [{'leafId': 'l1', 'sessionIds': ['sh1']}, {'leafId': 'l2', 'sessionIds': ['sh2']}]
    box.ptys += [{'id': 'sh1', 'pid': 11}, {'id': 'sh2', 'pid': 12}]
    box.children = {11: False, 12: False}
    store, params, _ = start(tmp_path, manifest(tmp_path, [s2]), dry_run=True)
    result = run_restore(store, None, params, box.env())
    assert box.calls == [] and box.closed == [] and result.operation_id is None
    assert [b.code for b in result.blockers] == ['would-close-shell']
    assert {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'starting'
    assert not (store.root / 'operations').exists()


def test_bare_shell_cleanup_closes_only_childless_shells_and_keeps_one_leaf_per_pane(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.panes += [{'id': 'pane-a', 'leafCount': 3, 'leaves': []}, {'id': 'pane-b', 'leafCount': 1, 'leaves': []}]
    box.leaves_by_pane['pane-a'] = [{'leafId': 'l1', 'sessionIds': ['sh1']}, {'leafId': 'l2', 'sessionIds': ['sh2']},
                                    {'leafId': 'l3', 'sessionIds': ['sh3']}]
    box.leaves_by_pane['pane-b'] = [{'leafId': 'l4', 'sessionIds': ['sh4']}]
    box.panes.append({'id': 'pane-c', 'leafCount': 2, 'leaves': []})
    box.leaves_by_pane['pane-c'] = [{'leafId': 'l7', 'sessionIds': ['sh6']}, {'leafId': 'l8', 'sessionIds': ['sh7']}]
    box.leaves_by_pane['pane-coord'] = [{'leafId': 'l5', 'sessionIds': ['sh5']}, {'leafId': 'l6', 'sessionIds': ['pty-coord']}]
    box.ptys += [{'id': f'sh{n}', 'pid': 10 + n} for n in range(1, 8)]
    box.children = {11: False, 12: True, 13: False, 14: False, 15: False, 16: False, 17: False}
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    # pane-a: sh2 has a child so it stays; pane-b keeps its only leaf; pane-c closes one of two
    # childless shells; the coordinator's pane is untouched.
    assert box.closed == ['l1', 'l3', 'l7']
    stages = [e.stage for e in OperationLog(store, op).operation().events]
    assert stages.count('shell-closed') == 3


def test_keep_shells_and_unreadable_children_close_nothing(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.panes.append({'id': 'pane-a', 'leafCount': 2, 'leaves': []})
    box.leaves_by_pane['pane-a'] = [{'leafId': 'l1', 'sessionIds': ['sh1']}, {'leafId': 'l2', 'sessionIds': ['sh2']}]
    box.ptys += [{'id': 'sh1', 'pid': 11}, {'id': 'sh2', 'pid': 12}]
    box.children = {11: None, 12: None}
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    assert box.closed == []
    box.children = {11: False, 12: False}
    store, params, op = start(tmp_path, manifest(tmp_path, [s2], checkpoint_id=uid(901)), op=701, keep_shells=True)
    run_restore(store, op, params, box.env())
    assert box.closed == []


def test_latest_is_the_newest_manifest_in_any_state(tmp_path):
    store = CheckpointStore(tmp_path)
    old = manifest(tmp_path, [seat(2, root=tmp_path)], state='incomplete', created=T0)
    new = manifest(tmp_path, [seat(2, root=tmp_path)], state='incomplete', created=T0 + timedelta(hours=1),
                   checkpoint_id=uid(901))
    for m in (old, new):
        store.publish(m, authorized_reference_roots=[tmp_path])
    assert resolve_checkpoint(store, 'latest') == uid(901)
    assert resolve_checkpoint(store, CID) == CID
    with pytest.raises(Exception):
        resolve_checkpoint(CheckpointStore(tmp_path / 'empty'), 'latest')


def test_dry_run_and_operation_id_must_agree(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    with pytest.raises(Exception, match='dry run'):
        run_restore(store, op, replace(params, dry_run=True), box.env())
