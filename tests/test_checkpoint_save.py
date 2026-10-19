"""One-call save: real store, contract and roster; only the process boundary is faked."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from liteharness.checkpoint_contract import Receipt, Request, wire
from liteharness.checkpoint_save import Saver, SaveEnv, SaveParams, run_save
from liteharness.checkpoint_store import CheckpointStore
from checkpoint_fixtures import put, uid
from checkpoint_world import World

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0
        self.on_sleep = None

    def now(self):
        self.t += timedelta(seconds=1)
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)
        if self.on_sleep:
            self.on_sleep()


class Fleet:
    """Seats that answer by inbox, a compact command that really appends a boundary record."""

    def __init__(self, world, clock):
        self.w, self.clock = world, clock
        self.inbox, self.sent, self.ran = [], [], []
        self.silent, self.bad_path, self.compact_rc = set(), set(), 0
        self.handoffs, self.disposition = {}, {}
        self.litetui_calls = []
        self.compact_delay = 0  # seconds until an ASYNC compactor's boundary lands; 0 = inside run()
        self.pending = {}

    def flush_due(self):
        for agent_id, due in list(self.pending.items()):
            if self.clock.t >= due:
                del self.pending[agent_id]
                transcript = next(self.w.projects.glob(f'*/{agent_id}.jsonl'))
                with transcript.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'type': 'system', 'subtype': 'compact_boundary', 'uuid': uid(9100),
                                             'timestamp': self.clock.t.isoformat()}) + chr(10))

    def handoff_for(self, agent_id):
        path = self.w.tmp / f'handoff-{agent_id[-4:]}.md'
        path.write_text(f'handoff of {agent_id}', encoding='utf-8')
        return path

    def send(self, sender, recipient, body):
        self.sent.append((sender, recipient, body))
        if recipient in self.silent:
            return 'm'
        path = self.handoff_for(recipient) if recipient not in self.bad_path else self.w.tmp / 'absent.md'
        self.inbox.append({'from': recipient, 'to': sender,
                           'body': f'CHECKPOINT-HANDOFF {path} {self.disposition.get(recipient, "idle")}'})
        return 'm'

    def poll(self, mailbox):
        got, self.inbox = [m for m in self.inbox if m['to'] == mailbox], [m for m in self.inbox if m['to'] != mailbox]
        return got

    def run(self, argv, timeout):
        self.ran.append(argv)
        agent_id = argv[-1]
        if self.compact_delay and self.compact_rc == 0:
            self.pending[agent_id] = self.clock.t + timedelta(seconds=self.compact_delay)  # returns at once, like self_compact
            return 0
        transcript = next(self.w.projects.glob(f'*/{agent_id}.jsonl'))
        with transcript.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'system', 'subtype': 'compact_boundary', 'uuid': uid(9000),
                                     'timestamp': self.clock.now().isoformat()}) + '\n')
        return self.compact_rc

    def litetui(self, agent_id, command):
        self.litetui_calls.append(command['type'])
        if command['type'].endswith('flush'):
            boundary = Receipt(uid(7000), command['checkpoint_id'], agent_id, 'boundary', 'tui-rev-1',
                               str(self.w.owned_root / 'boundary.json'), 'c' * 64, True, 'native',
                               self.clock.now().isoformat())
            (self.w.owned_root / 'boundary.json').write_text('{}')
            return {'boundary_ready': True, 'receipt': wire(boundary), 'disposition': 'idle', 'blockers': []}
        return {'accepted': True}


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.clock = Clock()
    w.fleet = Fleet(w, w.clock)
    w.clock.on_sleep = w.fleet.flush_due
    return w


def coordinator(w, n=1):
    agent_id = w.claude(n, role='orchestrator')
    handoff = w.tmp / 'sentinel-handoff.md'
    handoff.write_text('SAVE: read first', encoding='utf-8')
    return agent_id, handoff


def compacted(w, agent_id, handoff):
    """The coordinator compacts itself after its handoff (the caller's own step)."""
    transcript = next(w.projects.glob(f'*/{agent_id}.jsonl'))
    with transcript.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'system', 'subtype': 'compact_boundary', 'uuid': uid(9001),
                                 'timestamp': (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}) + '\n')


def save(w, coord, handoff, *, compact=True, only=None, include_offline=(), op=800, layout='present',
         compact_command=None, compact_timeout=900.0):
    put(w.harness / 'names.json', w.names)
    store = CheckpointStore(w.harness)
    request = Request(1, 'save', uid(op), None, None, [], False)
    operation = store.submit(request, actor_id=coord)
    fleet = w.fleet
    layout_value = None
    if layout == 'present':
        leaves = {f'leaf-{n}' for n in range(1, 20)}
        layout_value = ('rev-1', leaves)
    env = SaveEnv(now=w.clock.now, sleep=w.clock.sleep, send=fleet.send, poll=fleet.poll, run=fleet.run,
                  litetui=fleet.litetui, layout=lambda: layout_value, live=lambda row: row['agent_id'] in w.live,
                  handoff_timeout=30, poll_interval=1, compact_timeout=compact_timeout,
                  compact_command=(compact_command or ['compact-tool', '{name}', '{agent_id}']) if compact else None)
    params = SaveParams(str(w.harness), coord, str(handoff), uid(850 + op - 800),
                        include_offline=list(include_offline), litetui_root=str(w.owned_root), cwd_roots=[str(w.tmp)])
    result = run_save(store, operation.operation_id, params, env, only=only)
    return store, params, result, operation


def test_save_collects_every_receipt_and_publishes_complete(world):
    w = world
    coord, handoff = coordinator(w)
    peer_a, peer_b = w.claude(2, parent=coord), w.claude(3, parent=coord)
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff)
    manifest = store.read_manifest(params.checkpoint_id)
    assert manifest.state == 'complete' and result.state == 'complete' and not manifest.blockers
    assert store.latest_complete().checkpoint_id == params.checkpoint_id
    assert {s.agent_id for s in manifest.seats} == {coord, peer_a, peer_b}
    kinds = {(r.seat_id, r.kind) for r in manifest.receipts}
    assert {(s, k) for s in (peer_a, peer_b) for k in ('handoff', 'compact', 'boundary', 'layout')} <= kinds
    assert all(s.disposition == 'idle' for s in manifest.seats if s.agent_id != coord)
    assert w.fleet.ran and all(argv[0] == 'compact-tool' for argv in w.fleet.ran)
    assert not any(coord in argv for argv in w.fleet.ran), 'the coordinator is never compacted'
    assert result.counts.expected == 3 and result.counts.failed == 0


def test_roster_is_published_before_any_seat_is_asked_anything(world):
    w = world
    coord, handoff = coordinator(w)
    w.claude(2, parent=coord)
    compacted(w, coord, handoff)
    published = []
    real_send = w.fleet.send

    def watching(sender, recipient, body):
        store = CheckpointStore(w.harness)
        published.append(store.read_manifest(uid(850)).state)
        return real_send(sender, recipient, body)
    w.fleet.send = watching
    save(w, coord, handoff)
    assert published == ['incomplete']


def test_no_compact_command_is_a_named_unsupported_blocker_not_a_silent_pass(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff, compact=False)
    manifest = store.read_manifest(params.checkpoint_id)
    assert manifest.state == 'incomplete' and result.state == 'incomplete'
    assert [b.code for b in manifest.blockers] == ['compact-unsupported']
    assert manifest.blockers[0].seat_id == peer
    compact = next(r for r in manifest.receipts if r.seat_id == peer and r.kind == 'compact')
    assert compact.assurance == 'unsupported' and not compact.durable
    assert store.latest_complete() is None
    assert next(s for s in result.seats if s.seat_id == peer).state == 'blocked'


def test_silent_and_bad_path_seats_are_blocked_rows_and_fail_safe_to_held(world):
    w = world
    coord, handoff = coordinator(w)
    silent, liar = w.claude(2, parent=coord), w.claude(3, parent=coord)
    w.fleet.silent.add(silent)
    w.fleet.bad_path.add(liar)
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff)
    manifest = store.read_manifest(params.checkpoint_id)
    codes = {b.seat_id: b.code for b in manifest.blockers}
    assert codes == {silent: 'handoff-missing', liar: 'handoff-invalid'}
    assert {s.agent_id: s.disposition for s in manifest.seats}[silent] == 'held'
    assert {s.agent_id for s in manifest.seats} == {coord, silent, liar}, 'blocked seats stay in the roster'
    assert not w.fleet.ran, 'no compaction without a verified handoff'


def test_midwork_seat_records_its_handoff_is_not_compacted_and_blocks_completion(world):
    w = world
    coord, handoff = coordinator(w)
    busy = w.claude(2, parent=coord)
    w.fleet.disposition[busy] = 'held'
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff)
    manifest = store.read_manifest(params.checkpoint_id)
    assert manifest.state == 'incomplete' and result.state == 'incomplete'
    assert [(b.seat_id, b.code) for b in manifest.blockers] == [(busy, 'held-not-compacted')]
    assert {s.agent_id: s.disposition for s in manifest.seats}[busy] == 'held'
    assert not any(busy in argv for argv in w.fleet.ran), 'a mid-work seat is never compacted'
    kinds = {r.kind: r for r in manifest.receipts if r.seat_id == busy}
    assert kinds['handoff'].durable and kinds['compact'].assurance == 'unsupported'


def test_held_litetui_seat_is_put_on_hold_but_never_compacted_or_flushed(world):
    w = world
    coord, handoff = coordinator(w)
    tui = w.litetui(10, role='worker')
    home_handoff = w.owned_root / '.agents' / tui.name / 'handoff.md'
    home_handoff.write_text('owned handoff', encoding='utf-8')
    plain = w.fleet.handoff_for
    w.fleet.handoff_for = lambda agent_id: home_handoff if agent_id == tui.agent_id else plain(agent_id)
    w.fleet.disposition[tui.agent_id] = 'held'
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff, include_offline=[tui.agent_id])
    manifest = store.read_manifest(params.checkpoint_id)
    assert w.fleet.litetui_calls == ['gui.checkpoint.prepare']
    assert (tui.agent_id, 'held-not-compacted') in [(b.seat_id, b.code) for b in manifest.blockers]


@pytest.mark.skipif(os.name != 'nt', reason='case-insensitive paths')
def test_handoff_root_containment_compares_resolved_paths(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    compacted(w, coord, handoff)
    plain = w.fleet.handoff_for

    def shouting(agent_id):
        path = plain(agent_id)
        return Path(str(path).upper()) if agent_id == peer else path
    w.fleet.handoff_for = shouting
    store, params, result, _ = save(w, coord, handoff)
    assert store.read_manifest(params.checkpoint_id).state == 'complete'


def test_failed_compact_command_blocks_with_failed_code(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    w.fleet.compact_rc = 3
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff)
    manifest = store.read_manifest(params.checkpoint_id)
    assert [(b.seat_id, b.code) for b in manifest.blockers] == [(peer, 'compact-failed')]
    assert manifest.state == 'incomplete'


def test_uncompacted_coordinator_leaves_it_pending_and_retry_can_finish(world):
    w = world
    coord, handoff = coordinator(w)
    w.claude(2, parent=coord)
    store, params, result, op = save(w, coord, handoff)
    manifest = store.read_manifest(params.checkpoint_id)
    assert [(b.seat_id, b.code) for b in manifest.blockers] == [(coord, 'coordinator-compaction-pending')]
    assert result.state == 'incomplete'
    # The caller compacts itself, then retries ONLY its own seat; the peer is not asked again.
    compacted(w, coord, handoff)
    sent_before = len(w.fleet.sent)
    store2 = CheckpointStore(w.harness)
    env = SaveEnv(now=w.clock.now, sleep=w.clock.sleep, send=w.fleet.send, poll=w.fleet.poll, run=w.fleet.run,
                  layout=lambda: ('rev-1', {f'leaf-{n}' for n in range(1, 20)}), live=lambda row: True,
                  handoff_timeout=30, poll_interval=1, compact_command=['compact-tool', '{name}', '{agent_id}'])
    final = run_save(store2, op.operation_id, params, env, only=[coord])
    assert len(w.fleet.sent) == sent_before
    assert final.state == 'complete' and store2.read_manifest(params.checkpoint_id).state == 'complete'


def test_activity_after_boundary_invalidates_the_seat(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    compacted(w, coord, handoff)
    put(w.harness / 'names.json', w.names)
    store = CheckpointStore(w.harness)
    operation = store.submit(Request(1, 'save', uid(802), None, None, [], False), actor_id=coord)
    env = SaveEnv(now=w.clock.now, sleep=w.clock.sleep, send=w.fleet.send, poll=w.fleet.poll, run=w.fleet.run,
                  layout=lambda: ('rev-1', {f'leaf-{n}' for n in range(1, 20)}), live=lambda row: row['agent_id'] in w.live,
                  handoff_timeout=30, poll_interval=1, compact_command=['compact-tool', '{name}', '{agent_id}'])
    params = SaveParams(str(w.harness), coord, str(handoff), uid(852), litetui_root=str(w.owned_root),
                        cwd_roots=[str(w.tmp)])

    class Busy(Saver):
        def layout_receipt(self, manifest, seat, revision=None):
            if seat.agent_id == peer:  # the seat speaks after its boundary evidence was taken
                with next(w.projects.glob(f'*/{peer}.jsonl')).open('a', encoding='utf-8') as stream:
                    stream.write('{"type":"assistant"}\n')
            return super().layout_receipt(manifest, seat, revision)

    result = Busy(store, params, env, operation.operation_id).run()
    manifest = store.read_manifest(params.checkpoint_id)
    assert manifest.state == 'incomplete' and result.state == 'incomplete'
    assert [(b.seat_id, b.code) for b in manifest.blockers] == [(peer, 'activity-after-boundary')]
    assert store.latest_complete() is None


def test_offline_litetui_seat_uses_native_verbs_and_adopts_its_revision(world):
    w = world
    coord, handoff = coordinator(w)
    tui = w.litetui(10, role='worker')
    home_handoff = w.owned_root / '.agents' / tui.name / 'handoff.md'
    home_handoff.write_text('owned handoff', encoding='utf-8')
    plain = w.fleet.handoff_for
    w.fleet.handoff_for = lambda agent_id: home_handoff if agent_id == tui.agent_id else plain(agent_id)
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff, include_offline=[tui.agent_id])
    manifest = store.read_manifest(params.checkpoint_id)
    assert manifest.state == 'complete', manifest.blockers
    seat = next(s for s in manifest.seats if s.agent_id == tui.agent_id)
    assert seat.source_revision == 'tui-rev-1'
    assert w.fleet.litetui_calls == ['gui.checkpoint.prepare', 'gui.conversations.compact', 'gui.checkpoint.flush']
    by_kind = {r.kind: r for r in manifest.receipts if r.seat_id == tui.agent_id}
    assert by_kind['boundary'].assurance == 'native' and by_kind['handoff'].assurance == 'native'


def test_unobserved_layout_keeps_a_slotted_seat_incomplete(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff, layout=None)
    manifest = store.read_manifest(params.checkpoint_id)
    assert manifest.state == 'incomplete'
    layout = next(r for r in manifest.receipts if r.seat_id == peer and r.kind == 'layout')
    assert layout.assurance == 'unsupported' and not layout.durable


def test_save_never_runs_git_or_process_control(world):
    w = world
    coord, handoff = coordinator(w)
    w.claude(2, parent=coord)
    compacted(w, coord, handoff)
    save(w, coord, handoff)
    flat = [' '.join(a) for a in w.fleet.ran] + [b for _, _, b in w.fleet.sent]
    assert not any(word in text for text in flat for word in ('git ', 'taskkill', 'shutdown'))
    assert all('do not commit, push, stash' in b for _, _, b in w.fleet.sent)


def test_missing_coordinator_or_handoff_is_refused_before_anything_is_written(world):
    w = world
    w.claude(2)
    handoff = w.tmp / 'h.md'
    handoff.write_text('x')
    with pytest.raises(Exception, match='Coordinator is not a live seat'):
        save(w, uid(404), handoff)
    assert not (w.harness / 'sessions' / 'checkpoints' / uid(850)).exists()


def test_a_model_derived_from_the_transcript_is_labelled_in_the_journal(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    row_path = w.harness / 'agents' / f'{peer}.json'
    row = json.loads(row_path.read_text())
    row['model'] = 'unknown'
    row_path.write_text(json.dumps(row))
    transcript = next(w.projects.glob(f'*/{peer}.jsonl'))
    with transcript.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'assistant', 'message': {'model': 'claude-opus-5-5'}}) + chr(10))
    compacted(w, coord, handoff)
    store, params, result, op = save(w, coord, handoff)
    manifest = store.read_manifest(params.checkpoint_id)
    assert next(s for s in manifest.seats if s.agent_id == peer).execution.model == 'claude-opus-5-5'
    stages = [e.stage for e in store.operation(op.operation_id).events]
    assert f'model-source seat={peer} source=transcript model=claude-opus-5-5' in stages
    assert not any(st.startswith('model-source') and coord in st for st in stages), 'registry-sourced models are not noted'


# -- round 5: F3 the only real compactor is asynchronous ----------------------------------------------------------

def test_an_async_compactor_is_polled_until_the_boundary_lands(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    w.fleet.compact_delay = 45
    compacted(w, coord, handoff)
    started = w.clock.t
    store, params, result, _ = save(w, coord, handoff)
    manifest = store.read_manifest(params.checkpoint_id)
    assert manifest.state == 'complete' and not manifest.blockers
    assert len(w.fleet.ran) == 1, 'the compactor ran once; the save polled for its effect'
    assert (w.clock.t - started).total_seconds() >= 45


def test_an_async_compactor_that_never_lands_is_compact_unobserved_after_the_timeout(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    w.fleet.compact_delay = 100_000
    compacted(w, coord, handoff)
    started = w.clock.t
    store, params, result, _ = save(w, coord, handoff, compact_timeout=60)
    manifest = store.read_manifest(params.checkpoint_id)
    assert [(b.seat_id, b.code) for b in manifest.blockers] == [(peer, 'compact-unobserved')]
    assert 60 <= (w.clock.t - started).total_seconds() < 400


def test_a_failing_compactor_stops_at_once_without_polling(world):
    w = world
    coord, handoff = coordinator(w)
    w.claude(2, parent=coord)
    w.fleet.compact_rc = 3
    compacted(w, coord, handoff)
    slept = []
    w.clock.on_sleep = lambda: slept.append(1)
    store, params, result, _ = save(w, coord, handoff)
    assert [b.code for b in store.read_manifest(params.checkpoint_id).blockers] == ['compact-failed']
    assert slept == [] or len(slept) < 40, 'only the handoff wait sleeps; no compaction polling'


def test_compact_command_placeholders_include_the_checkpoint_and_the_seats_handoff(world):
    w = world
    coord, handoff = coordinator(w)
    peer = w.claude(2, parent=coord)
    compacted(w, coord, handoff)
    store, params, result, _ = save(w, coord, handoff,
                                    compact_command=['compactor', '--pointer', 'read {handoff} for {checkpoint_id}', '--name', '{name}', '{agent_id}'])
    argv = w.fleet.ran[0]
    assert argv[2] == f'read {w.tmp / ("handoff-" + peer[-4:] + ".md")} for {params.checkpoint_id}'
    assert argv[-1] == peer
