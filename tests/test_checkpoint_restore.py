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


def seat(n, *, role='builder', parent=COORD, disposition='idle', pane=None, root=None, leaf=None, model='claude-x'):
    slot = Slot('ws', 'th', pane or 'pane-saved', leaf or f'leaf-{n}', None, 'rev-1') if (pane or leaf) else None
    profile = OwnerProfile(f'Seat-{n}', str(root)) if role == 'owner' else None
    return Seat(f'Seat-{n}', uid(n), uid(n + 100), 'claude', Execution('claude', model, 'unrecorded'),
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
        self.proc_names = {}
        self.hidden = []
        self.events, self.wake_status, self.mail_error = [], 'nudge; turn confirmed on attempt 1 (hook stamp)', None
        self.registry[COORD] = {'agent_id': COORD, 'name': 'Seat-1', 'registered_at': T0.isoformat(),
                                'canvas_session_id': 'pty-coord', 'pane_id': 'pane-coord'}
        self.panes.append({'id': 'pane-coord', 'leafCount': 2, 'leaves': [{'id': 'lc', 'sessionIds': ['pty-coord']}]})

    def env(self, **kw):
        base = dict(now=self.clock.now, sleep=self.clock.sleep, liveness=lambda a: self.state.get(a, 'dead'),
                    spawn=self.spawn, registry=lambda a: self.registry.get(a), context=lambda: self.panes,
                    pty_list=lambda: self.ptys, leaves=lambda p: self.leaves_by_pane.get(p),
                    close_leaf=lambda leaf: self.closed.append(leaf) or True,
                    has_children=lambda pid: self.children.get(pid), python='C:/py/python.exe',
                    send_mail=self.mail, wake=self.wake, model_known=lambda a: True, default_model=lambda: None, hidden_panes=lambda: self.hidden,
                    process_name=lambda pid: self.proc_names.get(pid, 'powershell.exe'),
                    verify_timeout=5, poll_interval=1)
        return RestoreEnv(**{**base, **kw})

    def make_live(self, agent_id):
        session = f'pty-{agent_id[-3:]}'
        self.registry[agent_id] = {'agent_id': agent_id, 'registered_at': T0.isoformat(), 'canvas_session_id': session}
        self.state[agent_id] = 'live'
        # An ordinary split-launched Claude seat's PTY carries NO harnessAgentId (measured 2026-10-19).
        self.ptys.append({'id': session, 'pid': 100})

    def mail(self, sender, recipient, body):
        if self.mail_error:
            raise self.mail_error
        self.events.append(('mail', sender, recipient, body))
        return 'mail-1'

    def wake(self, session_id, line):
        self.events.append(('wake', session_id, line))
        return self.wake_status

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
            self.ptys.append({'id': session, 'pid': 100})
            self.panes.append({'id': f'pane-{agent_id[-3:]}', 'leafCount': 1,
                               'leaves': [{'id': 'x', 'sessionIds': [session]}]})
        return SpawnOutcome(0)


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def holders(box, tmp_path, leaves):
    """Live seats whose SAVED slot names these leaves: where LiteSuite would put placeholders back."""
    out = []
    for i, leaf in enumerate(leaves):
        held = seat(40 + i, root=tmp_path, leaf=leaf)
        box.make_live(held.agent_id)
        out.append(held)
    return out


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
    box.make_live(live.agent_id)
    box.state[murky.agent_id] = 'unknown'
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


def slot_seat(box, tmp_path, n=2, disposition='idle'):
    s2 = seat(n, root=tmp_path, disposition=disposition)
    box.script[s2.agent_id] = ['refuse-prompt']
    return s2


def test_slot_recovered_seat_gets_its_pointer_by_inbox_first_then_one_fixed_wake(tmp_path, box):
    s2 = slot_seat(box, tmp_path, disposition='held')
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    assert len(box.calls) == 2 and '--prompt' in box.calls[0][0] and '--prompt' not in box.calls[1][0]
    session = box.registry[s2.agent_id]['canvas_session_id']
    assert [e[0] for e in box.events] == ['mail', 'wake'], 'content by inbox BEFORE the wake'
    kind, sender, recipient, body = box.events[0]
    assert (sender, recipient) == (COORD, s2.agent_id)
    assert body.startswith(f'Restored from checkpoint {CID}.') and 'hold until your owner reconciles' in body
    assert 'Next: Read your handoff' in body
    assert box.events[1] == ('wake', session, 'resumed: arm ONE inbox Monitor and read your inbox')
    row = next(s for s in result.seats if s.seat_id == s2.agent_id)
    assert row.state == 'ready-held' and not row.errors
    assert [r.kind for r in row.evidence] == ['readiness'] and result.state == 'complete'
    stages = [e.stage for e in OperationLog(store, op).operation().events]
    assert stages.index('pointer-mailed') < stages.index('pointer-wake')


def test_seats_launched_with_a_prompt_are_never_mailed_or_woken(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    assert box.events == []


def test_wake_that_confirms_no_turn_is_a_pointer_not_delivered_blocker(tmp_path, box):
    s2 = slot_seat(box, tmp_path)
    box.wake_status = 'UNVERIFIED: nudge typed once, Enter pressed twice more, and the hooks never stamped a turn'
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    row = next(s for s in result.seats if s.seat_id == s2.agent_id)
    assert row.state == 'ready-idle' and [e.code for e in row.errors] == ['pointer-not-delivered']
    assert [e[0] for e in box.events] == ['mail', 'wake'] and result.state == 'incomplete'


def test_failed_inbox_send_means_no_wake_is_typed(tmp_path, box):
    s2 = slot_seat(box, tmp_path)
    box.mail_error = OSError('disk')
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    row = next(s for s in result.seats if s.seat_id == s2.agent_id)
    assert [e.code for e in row.errors] == ['pointer-not-delivered'] and 'no wake was typed' in row.errors[0].remedy
    assert box.events == []


def test_seat_that_came_up_between_plan_and_launch_is_reattached_not_relaunched(tmp_path, box):
    s2 = seat(2, root=tmp_path, disposition='held')
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    box.make_live(s2.agent_id)  # registry row + hosting PTY exist; only the plan-time probe said dead
    answers = iter(['dead', 'live', 'live', 'live'])  # dead at the plan, live at the re-check
    env = box.env()
    env.liveness = lambda agent_id: next(answers) if agent_id == s2.agent_id else 'dead'
    result = run_restore(store, op, params, env)
    assert box.calls == []
    assert {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'ready-held'
    assert 'reattached' in [e.stage for e in OperationLog(store, op).operation().events]


def test_default_wake_types_the_fixed_line_once_into_the_exact_session_only(monkeypatch):
    from liteharness import cli
    from liteharness.checkpoint_restore import WAKE_LINE, _wake
    writes = []
    monkeypatch.setattr(cli, '_bridge_request', lambda method, path, body=None, **k: writes.append((method, path, body)) or {'ok': True})
    monkeypatch.setattr(cli, '_turn_seen_since', lambda key, value, t0: key == 'canvas_session_id' and value == 'pty-9')
    status = _wake('pty-9', WAKE_LINE)
    assert 'turn confirmed on attempt 1' in status
    assert writes == [('POST', '/pty/write', {'session_id': 'pty-9', 'data': WAKE_LINE + chr(13)})]
    writes.clear()
    monkeypatch.setattr(cli, '_turn_seen_since', lambda *a: False)
    monkeypatch.setattr(cli.time, 'sleep', lambda s: None)
    ticks = iter(range(10_000))
    monkeypatch.setattr(cli.time, 'time', lambda: next(ticks))
    status = _wake('pty-9', WAKE_LINE)
    assert status.startswith('UNVERIFIED')
    assert {w[2]['session_id'] for w in writes} == {'pty-9'}
    assert [w[2]['data'] for w in writes] == [WAKE_LINE + chr(13), chr(13), chr(13)], 'retries press Enter only'


def test_coordinator_is_never_launched_and_a_foreign_caller_launches_nothing(tmp_path, box):
    s2 = seat(2, root=tmp_path, parent=None)  # a root seat: only the gate, not a blocked parent, can stop it
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    stranger = uid(55)
    box.registry[stranger] = {'agent_id': stranger, 'name': 'Someone-Else', 'registered_at': T0.isoformat()}
    result = run_restore(store, op, RestoreParams(str(tmp_path), stranger, CID), box.env())
    assert {s.seat_id: s.state for s in result.seats}[COORD] == 'blocked'
    assert launched(box) == [], 'a caller who is not the coordinator launches NOTHING'
    assert result.state == 'incomplete'
    assert any(e.code == 'coordinator-mismatch' for r in result.seats for e in r.errors)


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
    box.ptys += [{'id': 'sh1', 'pid': 11, 'cwd': str(tmp_path)}, {'id': 'sh2', 'pid': 12, 'cwd': str(tmp_path)}]
    box.children = {11: False, 12: False}
    store, params, _ = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2'])]), dry_run=True)
    result = run_restore(store, None, params, box.env())
    assert box.calls == [] and box.closed == [] and result.operation_id is None
    assert [b.code for b in result.blockers] == ['would-close-shell', 'shell-summary']
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
    box.ptys += [{'id': f'sh{n}', 'pid': 10 + n, 'cwd': str(tmp_path)} for n in range(1, 8)]
    box.children = {11: False, 12: True, 13: False, 14: False, 15: False, 16: False, 17: False}
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2', 'l3', 'l4', 'l5', 'l7', 'l8'])]))
    run_restore(store, op, params, box.env())
    # pane-a: sh2 has a child so it stays; pane-b keeps its only leaf; pane-c closes one of two
    # childless shells; the coordinator's pane is untouched.
    assert box.closed == ['l1', 'l3', 'l7']
    stages = [e.stage for e in OperationLog(store, op).operation().events]
    assert len([st for st in stages if st.startswith('shell-closed ')]) == 3


def test_keep_shells_and_unreadable_children_close_nothing(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.panes.append({'id': 'pane-a', 'leafCount': 2, 'leaves': []})
    box.leaves_by_pane['pane-a'] = [{'leafId': 'l1', 'sessionIds': ['sh1']}, {'leafId': 'l2', 'sessionIds': ['sh2']}]
    box.ptys += [{'id': 'sh1', 'pid': 11, 'cwd': str(tmp_path)}, {'id': 'sh2', 'pid': 12, 'cwd': str(tmp_path)}]
    box.children = {11: None, 12: None}
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2'])]))
    run_restore(store, op, params, box.env())
    assert box.closed == []
    box.children = {11: False, 12: False}
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2'])], checkpoint_id=uid(901)), op=701, keep_shells=True)
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


# -- critic round: H2, M2, M3 -----------------------------------------------------------------------------

def test_live_by_process_alone_without_a_registry_or_pty_join_is_not_reattached(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.state[s2.agent_id] = 'live'  # the probe says live, but nothing else corroborates it
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    assert box.calls == [] and {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'unknown'


def test_a_new_restore_of_the_same_checkpoint_does_not_launch_a_seat_another_operation_left_unknown(tmp_path, box):
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    m = manifest(tmp_path, [s2, s3])
    store, params, op = start(tmp_path, m)
    box.script[s2.agent_id] = 'crash'
    with pytest.raises(Crash):
        run_restore(store, op, params, box.env())
    box.calls.clear()
    operation = store.submit(Request(1, 'restore', uid(701), CID, None, [], False), actor_id=COORD)
    again = run_restore(store, operation.operation_id, params, box.env())
    states = {s.seat_id: s.state for s in again.seats}
    assert launched(box) == [s3.agent_id], 'the intent-without-result seat is never launched by a second operation'
    assert states[s2.agent_id] == 'unknown' and states[s3.agent_id] == 'ready-idle'
    assert any(e.code == 'prior-attempt-unknown' for r in again.seats for e in r.errors)


def default_world(tmp_path, monkeypatch, row, ptys):
    import os
    from liteharness import config
    from liteharness import checkpoint_restore as mod
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(mod, '_pty_list', lambda: ptys)
    if row is not None:
        (tmp_path / 'agents').mkdir(exist_ok=True)
        (tmp_path / 'agents' / f'{uid(2)}.json').write_text(json.dumps(row))
    return mod.default_liveness(uid(2))


def mine():
    import os
    import psutil
    return os.getpid(), psutil.Process(os.getpid()).create_time() * 1000


@pytest.mark.parametrize('case,expected', [
    ('process-matches', 'live'),
    ('recycled-pid', 'dead'),
    ('no-pid', 'unknown'),
    ('zero-pid', 'unknown'),
    ('no-birth', 'unknown'),
    ('exited', 'dead'),
    ('gone-and-unhosted', 'dead'),
    ('gone-but-hosted', 'unknown'),
    ('no-row-hosted', 'unknown'),
    ('no-row-unhosted', 'dead'),
    ('no-row-pty-unreadable', 'unknown'),
])
def test_default_liveness_branches(tmp_path, monkeypatch, case, expected):
    pid, born = mine()
    hosted = [{'id': 'p', 'harnessAgentId': uid(2)}]
    row = {'agent_id': uid(2), 'session_pid': pid, 'session_process_started_at': born}
    ptys = []
    if case == 'recycled-pid':
        row['session_process_started_at'] = born - 60_000
    elif case == 'no-pid':
        row.pop('session_pid')
    elif case == 'zero-pid':
        row['session_pid'] = 0
    elif case == 'no-birth':
        row.pop('session_process_started_at')
    elif case == 'exited':
        row['exited_at'] = '2026-10-19T00:00:00+00:00'
    elif case.startswith('gone'):
        row['session_pid'] = 4_000_000
        ptys = hosted if case == 'gone-but-hosted' else []
    elif case.startswith('no-row'):
        row, ptys = None, (hosted if case == 'no-row-hosted' else None if case == 'no-row-pty-unreadable' else [])
    assert default_world(tmp_path, monkeypatch, row, ptys) == expected


# -- critic round: H3 bare-shell cleanup -------------------------------------------------------------------

def shell_world(box, tmp_path, ptys, leaves):
    box.panes.append({'id': 'pane-a', 'leafCount': len(leaves), 'leaves': []})
    box.leaves_by_pane['pane-a'] = leaves
    box.ptys += ptys
    box.children = {p['pid']: False for p in ptys if 'pid' in p}


def two_leaves(a, b):
    return [{'leafId': 'l1', 'sessionIds': [a]}, {'leafId': 'l2', 'sessionIds': [b]}]


def test_a_childless_process_that_is_not_a_shell_is_never_closed(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    shell_world(box, tmp_path, [{'id': 'sh1', 'pid': 11, 'cwd': str(tmp_path)}, {'id': 'sh2', 'pid': 12, 'cwd': str(tmp_path)}],
                two_leaves('sh1', 'sh2'))
    box.proc_names = {11: 'python.exe', 12: 'powershell.exe'}  # sh1 is a server that happens to have no children
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2'])]))
    run_restore(store, op, params, box.env())
    assert box.closed == ['l2'], 'only the shell is closable; the leaf of the python process stays'


def test_a_childless_shell_at_a_leaf_no_saved_slot_names_is_never_closed(tmp_path, box):
    """Even with a cwd equal to a saved seat's cwd (a user's own terminal can sit in a shared root)."""
    s2 = seat(2, root=tmp_path)
    shell_world(box, tmp_path, [{'id': 'sh1', 'pid': 11, 'cwd': str(tmp_path)}, {'id': 'sh2', 'pid': 12, 'cwd': str(tmp_path)}],
                two_leaves('sh1', 'sh2'))
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['somewhere-else'])]))
    run_restore(store, op, params, box.env())
    assert box.closed == []


def test_only_leaves_a_saved_slot_names_are_closable_and_the_pty_cwd_is_irrelevant(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    shell_world(box, tmp_path, [{'id': 'sh1', 'pid': 11}, {'id': 'sh2', 'pid': 12, 'cwd': 'D:/elsewhere'}],
                two_leaves('sh1', 'sh2'))
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2'])]))
    run_restore(store, op, params, box.env())
    assert box.closed == ['l1'], 'both name saved slots; one leaf per pane stays; no cwd is consulted'


def test_malformed_rows_are_skipped_never_raised(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.panes.append({'leafCount': 2})  # a pane with no id
    box.panes.append({'id': 'pane-b', 'leafCount': 3, 'leaves': []})
    box.leaves_by_pane['pane-b'] = [{'sessionIds': ['sh1']}, {'leafId': 'l2', 'sessionIds': 'sh2'}, 'garbage',
                                    {'leafId': 'l4', 'sessionIds': ['sh4']}]
    box.ptys += [{'id': 'sh1', 'pid': 11, 'cwd': str(tmp_path)}, 'junk', {'id': 'sh4', 'pid': 'x', 'cwd': str(tmp_path)}]
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l2', 'l4'])]))
    result = run_restore(store, op, params, box.env())
    assert box.closed == [] and result.state == 'complete'


def test_the_journal_names_what_was_closed(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    shell_world(box, tmp_path, [{'id': 'sh1', 'pid': 11, 'cwd': str(tmp_path)}, {'id': 'sh2', 'pid': 12, 'cwd': str(tmp_path)}],
                two_leaves('sh1', 'sh2'))
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2'])]))
    run_restore(store, op, params, box.env())
    closed = [e.stage for e in OperationLog(store, op).operation().events if e.stage.startswith('shell-closed')]
    assert closed == ['shell-closed pane=pane-a leaf=l1 session=sh1 shell=powershell.exe']


def test_retry_result_shows_prior_states_for_seats_it_does_not_retry(tmp_path, box):
    from liteharness.checkpoint_ops import make_result, seat_result
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    m = manifest(tmp_path, [s2, s3])
    store, params, first = start(tmp_path, m)
    rows = [seat_result(COORD, 'ready-idle'), seat_result(s2.agent_id, 'failed'), seat_result(s3.agent_id, 'ready-held')]
    OperationLog(store, first).event('final', None, make_result(first, CID, 'incomplete', rows))
    retry_op = store.submit(Request(1, 'retry', uid(702), None, first, [s2.agent_id], False), actor_id=COORD)
    retry = RestoreParams(str(tmp_path), COORD, CID, seat_ids=[s2.agent_id], retry_of=first)
    result = run_restore(store, retry_op.operation_id, retry, box.env())
    states = {s.seat_id: s.state for s in result.seats}
    assert launched(box) == [s2.agent_id]
    assert states[s2.agent_id] == 'ready-idle' and states[s3.agent_id] == 'ready-held'


# -- critic round 2: N1/N6 identity join without harnessAgentId --------------------------------------------

def test_an_ordinary_live_seat_without_harness_agent_id_is_reattached_not_unknown(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.make_live(s2.agent_id)
    assert all('harnessAgentId' not in p for p in box.ptys), 'faithful fake: ordinary PTYs carry no harnessAgentId'
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    assert box.calls == [] and {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'ready-idle'


def test_children_of_a_live_ordinary_parent_are_not_blocked_by_an_unverifiable_parent(tmp_path, box):
    parent, child = seat(2, role='owner', root=tmp_path), seat(3, parent=uid(2), root=tmp_path)
    box.make_live(parent.agent_id)
    store, params, op = start(tmp_path, manifest(tmp_path, [parent, child]))
    result = run_restore(store, op, params, box.env())
    assert launched(box) == [child.agent_id]
    assert {s.seat_id: s.state for s in result.seats}[child.agent_id] == 'ready-idle'


def test_a_pty_that_names_a_different_agent_does_not_verify_the_seat(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.make_live(s2.agent_id)
    box.ptys[-1]['harnessAgentId'] = uid(99)  # present, so it must equal the seat id
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    assert box.calls == [] and {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'unknown'


def test_a_pty_that_is_not_the_registered_session_does_not_verify_the_seat(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    box.make_live(s2.agent_id)
    box.ptys[-1]['id'] = 'some-other-pty'
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env())
    assert {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'unknown'


def test_a_seat_that_came_up_at_the_recheck_must_verify_or_it_is_unknown(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    answers = iter(['dead', 'live', 'live', 'live'])  # dead at the plan, "live" at the re-check, but no registry row
    env = box.env()
    env.liveness = lambda agent_id: next(answers) if agent_id == s2.agent_id else 'dead'
    result = run_restore(store, op, params, env)
    assert box.calls == [] and {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'unknown'


def test_a_crashed_seats_shell_husk_joined_only_by_session_id_reads_dead_not_unknown(tmp_path, monkeypatch):
    from liteharness import checkpoint_restore as mod
    pid, born = mine()
    row = {'agent_id': uid(2), 'session_pid': 4_000_000, 'session_process_started_at': born, 'canvas_session_id': 'pty-7'}
    husk = [{'id': 'pty-7', 'pid': 55}]
    monkeypatch.setattr(mod, '_has_children', lambda pid: False)
    assert default_world(tmp_path, monkeypatch, row, husk) == 'dead', 'a session-id join says nothing about the process'
    monkeypatch.setattr(mod, '_has_children', lambda pid: True)
    assert default_world(tmp_path, monkeypatch, row, husk) == 'unknown', 'the PTY still runs something'
    monkeypatch.setattr(mod, '_has_children', lambda pid: None)
    assert default_world(tmp_path, monkeypatch, row, husk) == 'unknown', 'an unreadable shell may host a live seat'
    assert default_world(tmp_path, monkeypatch, row, [{'id': 'pty-7'}]) == 'unknown', 'a husk with no pid cannot be inspected'
    assert default_world(tmp_path, monkeypatch, row, [{'id': 'pty-7', 'pid': 55, 'harnessAgentId': uid(2)}]) == 'unknown'
    assert default_world(tmp_path, monkeypatch, row, [{'id': 'pty-8'}]) == 'dead'


def test_a_husk_seat_is_launchable_end_to_end_and_retry_accepts_it_as_reconciled(tmp_path, box, monkeypatch):
    """The plan, the N3 expiry and the retry gate all read the same real liveness."""
    from liteharness import checkpoint_restore as mod
    s2 = seat(2, root=tmp_path)
    pid, born = mine()
    row = {'agent_id': s2.agent_id, 'session_pid': 4_000_000, 'session_process_started_at': born, 'canvas_session_id': 'pty-7'}
    (tmp_path / 'agents').mkdir(exist_ok=True)
    (tmp_path / 'agents' / f'{s2.agent_id}.json').write_text(json.dumps(row))
    from liteharness import config
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(mod, '_pty_list', lambda: [{'id': 'pty-7', 'pid': 55}])
    monkeypatch.setattr(mod, '_has_children', lambda pid: False)
    assert mod.default_liveness(s2.agent_id) == 'dead'  # what retry's reconciled() asks
    box.registry.pop(s2.agent_id, None)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, params, box.env(liveness=lambda a: mod.default_liveness(a) if not box.calls else box.state.get(a, 'dead')))
    assert launched(box) == [s2.agent_id] and {s.seat_id: s.state for s in result.seats}[s2.agent_id] == 'ready-idle'



# -- critic round 2: N3 an unknown from another operation is not forever ------------------------------------

def left_unknown_by_a_dead_operation(tmp_path, box):
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, s3]))
    box.script[s2.agent_id] = 'crash'
    with pytest.raises(Crash):
        run_restore(store, op, params, box.env())
    box.calls.clear()
    box.script[s2.agent_id] = 'ok'
    return store, params, s2, s3


def second_restore(store, params, box, **kw):
    operation = store.submit(Request(1, 'restore', uid(701), CID, None, [], False), actor_id=COORD)
    return run_restore(store, operation.operation_id, params, box.env(**kw))


@pytest.mark.parametrize('age,active,liveness,relaunched', [
    (10_000, False, 'dead', True),     # old, nobody writing it, identity says dead: the unknown expires
    (10, False, 'dead', False),        # young: the launch may still be in flight
    (10_000, True, 'dead', False),     # its operation is still ACTIVE
    (10_000, False, 'unknown', False), # liveness cannot say dead
])
def test_an_old_launch_intent_without_result_expires_only_when_it_is_provably_dead(tmp_path, box, age, active, liveness, relaunched):
    store, params, s2, s3 = left_unknown_by_a_dead_operation(tmp_path, box)
    env_kw = dict(journal_age=lambda op: age, operation_active=lambda op: active)
    if liveness != 'dead':
        box.state[s2.agent_id] = liveness
    second_restore(store, params, box, **env_kw)
    assert (s2.agent_id in launched(box)) is relaunched


# -- critic round 3: R2 a silent no-op must be visible --------------------------------------------------------

def summary_stage(store, op):
    return [e.stage for e in OperationLog(store, op).operation().events if e.stage.startswith('shell-summary')]


def test_final_report_journals_one_shell_summary_line_with_counts(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    shell_world(box, tmp_path, [{'id': 'sh1', 'pid': 11}, {'id': 'sh2', 'pid': 12}], two_leaves('sh1', 'sh2'))
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, *holders(box, tmp_path, ['l1', 'l2'])]))
    run_restore(store, op, params, box.env())
    assert summary_stage(store, op) == ['shell-summary found=2 matched=2 closed=1']


def test_a_silent_noop_is_visible_when_no_bare_shell_matches_a_saved_leaf(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    shell_world(box, tmp_path, [{'id': 'sh1', 'pid': 11}, {'id': 'sh2', 'pid': 12}], two_leaves('sh1', 'sh2'))
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    assert box.closed == [] and summary_stage(store, op) == ['shell-summary found=2 matched=0 closed=0']


def test_summary_is_journaled_even_when_there_are_no_shells_and_skipped_with_keep_shells(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    assert summary_stage(store, op) == ['shell-summary found=0 matched=0 closed=0']
    store2, params2, op2 = start(tmp_path, manifest(tmp_path, [s2], checkpoint_id=uid(902)), op=703, keep_shells=True)
    run_restore(store2, op2, params2, box.env())
    assert summary_stage(store2, op2) == []


def test_dry_run_carries_the_same_summary_line_as_a_note(tmp_path, box):
    s2 = seat(2, root=tmp_path)
    shell_world(box, tmp_path, [{'id': 'sh1', 'pid': 11}, {'id': 'sh2', 'pid': 12}], two_leaves('sh1', 'sh2'))
    store, params, _ = start(tmp_path, manifest(tmp_path, [s2]), dry_run=True)
    result = run_restore(store, None, params, box.env())
    notes = [b for b in result.blockers if b.code == 'shell-summary']
    assert len(notes) == 1 and notes[0].remedy == '2 bare shells found, 0 matched a saved leaf, 0 would close'


# -- critic approval follow-ups: S1 fail-open, S2 exited row --------------------------------------------------

def test_has_children_distinguishes_a_missing_pid_from_an_unreadable_one(monkeypatch):
    import psutil
    from liteharness import checkpoint_restore as mod

    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, 'Process', gone)
    assert mod._has_children(55) is False
    monkeypatch.setattr(psutil, 'Process', denied)
    assert mod._has_children(55) is None


def test_a_husk_whose_shell_pid_no_longer_exists_still_reads_dead(tmp_path, monkeypatch):
    from liteharness import checkpoint_restore as mod
    pid, born = mine()
    row = {'agent_id': uid(2), 'session_pid': 4_000_000, 'session_process_started_at': born, 'canvas_session_id': 'pty-7'}
    monkeypatch.undo()
    assert default_world(tmp_path, monkeypatch, row, [{'id': 'pty-7', 'pid': 4_000_001}]) == 'dead'


@pytest.mark.parametrize('children,expected', [(False, 'dead'), (True, 'unknown'), (None, 'unknown')])
def test_an_exited_row_whose_shell_now_runs_something_is_not_dead(tmp_path, monkeypatch, children, expected):
    from liteharness import checkpoint_restore as mod
    pid, born = mine()
    row = {'agent_id': uid(2), 'session_pid': 4_000_000, 'session_process_started_at': born,
           'canvas_session_id': 'pty-7', 'exited_at': '2026-10-19T00:00:00+00:00'}
    monkeypatch.setattr(mod, '_has_children', lambda pid: children)
    assert default_world(tmp_path, monkeypatch, row, [{'id': 'pty-7', 'pid': 55}]) == expected


# -- round 5: F2 restore never passes an unknown model ----------------------------------------------------------

def test_restore_never_passes_model_and_notes_a_seat_whose_model_is_unconfirmed(tmp_path, box):
    s2, s3 = seat(2, root=tmp_path), seat(3, root=tmp_path)
    store, params, op = start(tmp_path, manifest(tmp_path, [s2, s3]))
    result = run_restore(store, op, params, box.env(model_known=lambda agent_id: agent_id != s2.agent_id,
                                                    default_model=lambda: 'claude-x[1m]'))
    assert all('--model' not in argv and 'unknown' not in ' '.join(argv) for argv, _ in box.calls)
    notes = [e.stage for e in OperationLog(store, op).operation().events if e.stage.startswith('model-unconfirmed')]
    assert notes == [f'model-unconfirmed seat={s2.agent_id}: resumed without --model']
    assert result.state == 'complete', 'a non-blocking note never changes the outcome'


# -- round 5: F5 a saved pane id may be an alias of an active pane ------------------------------------------------

def test_a_saved_canvas_pane_id_resolves_to_its_active_zen_mirror(tmp_path, box):
    s2 = seat(2, root=tmp_path, pane='canvas-pane-3')
    box.panes.append({'id': 'zen-term-3', 'mirrorId': 'canvas-pane-3', 'leafCount': 2, 'leaves': []})
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    argv = box.calls[0][0]
    assert argv[argv.index('--pane') + 1] == 'zen-term-3', 'the ACTIVE id is what the placement receives'


def test_a_saved_zen_pane_id_resolves_when_the_context_lists_the_canvas_form(tmp_path, box):
    s2 = seat(2, root=tmp_path, pane='zen-term-4')
    box.panes.append({'id': 'canvas-pane-4', 'syncId': 'sync-canvas-pane-4', 'mirrorId': 'zen-term-4', 'leafCount': 1, 'leaves': []})
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    argv = box.calls[0][0]
    assert argv[argv.index('--pane') + 1] == 'canvas-pane-4'


def test_a_hidden_pane_only_helps_resolve_it_never_receives_the_seat(tmp_path, box):
    s2 = seat(2, root=tmp_path, pane='canvas-pane-5')
    box.panes.append({'id': 'zen-term-5', 'syncId': 's5', 'leafCount': 1, 'leaves': []})
    box.hidden = [{'id': 'canvas-pane-5', 'syncId': 's5', 'leafCount': 1, 'leaves': []}, {'id': 'lonely', 'leafCount': 1}]
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    argv = box.calls[0][0]
    assert argv[argv.index('--pane') + 1] == 'zen-term-5'
    s3 = seat(3, root=tmp_path, pane='lonely')
    store, params, op = start(tmp_path, manifest(tmp_path, [s3], checkpoint_id=uid(903)), op=703)
    box.calls.clear()
    run_restore(store, op, params, box.env())
    assert '--pane' not in box.calls[0][0], 'a hidden pane with no active mirror is not a destination'


def test_the_coordinators_pane_is_excluded_under_any_of_its_aliases(tmp_path, box):
    s2 = seat(2, root=tmp_path, pane='canvas-pane-9')
    box.panes[:] = [{'id': 'zen-term-9', 'mirrorId': 'canvas-pane-9', 'leafCount': 2,
                     'leaves': [{'id': 'lc', 'sessionIds': ['pty-coord']}]}]
    box.registry[COORD]['pane_id'] = 'canvas-pane-9'  # presence says the canvas form, /context the zen one
    store, params, op = start(tmp_path, manifest(tmp_path, [s2]))
    run_restore(store, op, params, box.env())
    assert '--pane' not in box.calls[0][0], 'the saved pane IS the coordinator pane under another name'


# -- round 6: M-A an unknown-registry seat is never silently moved to another model ---------------------------------

OPUS, SONNET = 'claude-opus-5-5', 'claude-sonnet-5-5'


def unknown_model_world(box, tmp_path, observed, default, **params):
    s2 = seat(2, root=tmp_path, model=observed)
    child = seat(3, root=tmp_path, parent=s2.agent_id)
    store, p, op = start(tmp_path, manifest(tmp_path, [s2, child]), **params)
    env = box.env(model_known=lambda a: a not in (s2.agent_id,), default_model=lambda: default)
    return s2, child, store, p, op, env


def test_opus_transcript_and_sonnet_default_is_blocked_and_nothing_is_launched(tmp_path, box):
    s2, child, store, p, op, env = unknown_model_world(box, tmp_path, OPUS, SONNET)
    result = run_restore(store, op, p, env)
    assert box.calls == []
    row = next(r for r in result.seats if r.seat_id == s2.agent_id)
    assert row.state == 'blocked' and [e.code for e in row.errors] == ['model-mismatch']
    assert OPUS in row.errors[0].remedy and SONNET in row.errors[0].remedy and '--allow-default-model' in row.errors[0].remedy
    kid = next(r for r in result.seats if r.seat_id == child.agent_id)
    assert kid.state == 'blocked', 'its subtree waits'
    assert 'parent blocked: model-mismatch' in kid.errors[0].remedy and 'did not come up' not in kid.errors[0].remedy
    assert result.state == 'incomplete'


def test_a_matching_base_model_launches_and_a_context_suffix_does_not_count(tmp_path, box):
    s2, child, store, p, op, env = unknown_model_world(box, tmp_path, SONNET, SONNET + '[1m]')
    result = run_restore(store, op, p, env)
    assert launched(box) == [s2.agent_id, child.agent_id] and result.state == 'complete'
    stages = [e.stage for e in OperationLog(store, op).operation().events]
    assert any(st.startswith('model-unconfirmed') for st in stages), 'the non-blocking note stays'


def test_an_unreadable_default_blocks_too(tmp_path, box):
    s2, child, store, p, op, env = unknown_model_world(box, tmp_path, SONNET, None)
    result = run_restore(store, op, p, env)
    assert box.calls == [] and next(r for r in result.seats if r.seat_id == s2.agent_id).errors[0].code == 'model-mismatch'


def test_allow_default_model_launches_on_the_default_and_says_so_on_the_row(tmp_path, box):
    s2, child, store, p, op, env = unknown_model_world(box, tmp_path, OPUS, SONNET, allow_default_model=True)
    result = run_restore(store, op, p, env)
    assert launched(box) == [s2.agent_id, child.agent_id]
    assert all('--model' not in argv for argv, _ in box.calls), 'the transcript model is never passed (no [1m])'
    row = next(r for r in result.seats if r.seat_id == s2.agent_id)
    assert row.state == 'ready-idle' and [e.code for e in row.errors] == ['model-defaulted']
    assert result.state == 'incomplete'


def test_a_seat_with_a_real_registry_model_is_never_gated(tmp_path, box):
    s2 = seat(2, root=tmp_path, model=OPUS)
    store, p, op = start(tmp_path, manifest(tmp_path, [s2]))
    result = run_restore(store, op, p, box.env(default_model=lambda: SONNET))
    assert launched(box) == [s2.agent_id] and result.state == 'complete'


def test_dry_run_lists_would_block_per_seat(tmp_path, box):
    s2, child, store, p, op, env = unknown_model_world(box, tmp_path, OPUS, SONNET, dry_run=True)
    result = run_restore(store, None, p, env)
    row = next(r for r in result.seats if r.seat_id == s2.agent_id)
    assert row.state == 'blocked' and row.errors[0].code == 'model-mismatch' and row.errors[0].remedy.startswith('would block: ')
    assert box.calls == []
