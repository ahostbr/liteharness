"""Exact cognitive-method seed (T0558-F): SAVE records which method a seat was launched with,
RESTORE hands the bounded reference to the governed recovery path. A seed is evidence, never a
launch grant, and legacy or unjoinable markers stay unsupported.
"""
import hashlib
import json
from pathlib import Path

import pytest

from liteharness import checkpoint_seed as seed
from liteharness.checkpoint_contract import ContractError, Request, SeedEvidence, decode, wire
from liteharness.checkpoint_restore import RestoreParams, run_restore
from liteharness.recovered_destination import SEED_ENV
from checkpoint_fixtures import put, uid
from test_checkpoint_restore import Box
from test_checkpoint_save import coordinator, compacted, save, world  # noqa: F401

PID, BIRTH = 4242, 1792890574.885


def method_file(world, text='# POLYMATHIC TESLA\nMethod body\n'):
    path = world.tmp / 'library' / 'tesla.md'
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path, hashlib.sha256(text.encode('utf-8')).hexdigest()


def joined_seat(world, parent=None, n=2, **marker):
    """A live Claude seat whose registry row and v2 marker name the same process."""
    agent_id = world.claude(n, parent=parent)
    row_path = world.harness / 'agents' / f'{agent_id}.json'
    row = json.loads(row_path.read_text(encoding='utf-8'))
    row.update(session_pid=PID, session_process_started_at=BIRTH * 1000)
    put(row_path, row)
    path, body = method_file(world)
    event = {'version': 2, 'pid': PID, 'birth': BIRTH, 'session': agent_id, 'method': str(path), 'body': body}
    event.update(marker)
    put(world.harness / '.cog_printed' / agent_id, event)
    return agent_id, path, body


def seat_of(world, agent_id):
    return next(c.seat for c in world.discover().candidates if c.expected.agent_id == agent_id)


def test_observe_joins_the_marker_to_the_seats_process(world):
    agent_id, path, body = joined_seat(world)
    found = seed.observe(seat_of(world, agent_id), uid(900), world.harness)
    assert found == SeedEvidence(1, 'cognitive-seed-v1', uid(900), agent_id, str(path), body, PID, BIRTH, agent_id)
    assert decode(SeedEvidence, wire(found)) == found


def test_observe_is_silent_when_no_method_was_ever_printed(world):
    coord, _ = coordinator(world)
    assert seed.observe(seat_of(world, coord), uid(900), world.harness) is None


@pytest.mark.parametrize('what, change', [
    ('legacy text marker', lambda w, a: (w.harness / '.cog_printed' / a).write_text('printed', encoding='utf-8')),
    ('version 1 json', lambda w, a: put(w.harness / '.cog_printed' / a, {'version': 1})),
    ('another process', lambda w, a: _marker(w, a, pid=PID + 1)),
    ('another birth', lambda w, a: _marker(w, a, birth=BIRTH + 60)),
    ('unknown birth', lambda w, a: _marker(w, a, birth=None)),
    ('body changed', lambda w, a: _marker(w, a, body='0' * 64)),
    ('method missing', lambda w, a: _marker(w, a, method=str(w.tmp / 'gone.md'))),
    ('relative method', lambda w, a: _marker(w, a, method='tesla.md')),
])
def test_unjoinable_markers_are_unavailable_with_a_reason_not_a_seed(world, what, change):
    agent_id, *_ = joined_seat(world)
    change(world, agent_id)
    result = seed.observe(seat_of(world, agent_id), uid(900), world.harness)
    assert isinstance(result, str) and result, what


def _marker(world, agent_id, **fields):
    path = world.harness / '.cog_printed' / agent_id
    event = json.loads(path.read_text(encoding='utf-8'))
    event.update(fields)
    put(path, event)


def test_the_contract_is_closed_and_digest_checked():
    absolute = str(Path.cwd() / 'library' / 'tesla.md')
    good = SeedEvidence(1, 'cognitive-seed-v1', uid(900), uid(2), absolute, 'a' * 64, 1, 1.5, None)
    wire(good)
    with pytest.raises(ContractError):
        wire(SeedEvidence(1, 'cognitive-seed-v1', uid(900), uid(2), absolute, 'A' * 64, 1, 1.5, None))
    with pytest.raises(ContractError):
        wire(SeedEvidence(1, 'cognitive-seed-v1', uid(900), uid(2), 'relative.md', 'a' * 64, 1, 1.5, None))
    with pytest.raises(ContractError):
        decode(SeedEvidence, {**wire(good), 'prompt_text': 'smuggled'})


def saved(world, **marker):
    coord, handoff = coordinator(world)
    agent_id, path, body = joined_seat(world, coord, **marker)
    compacted(world, coord, handoff)
    store, params, result, _ = save(world, coord, handoff)
    return agent_id, path, body, store, params, result


def test_save_retains_a_hash_checked_seed_receipt(world):
    agent_id, path, body, store, params, result = saved(world)
    manifest = store.read_manifest(params.checkpoint_id)
    receipts = [r for r in manifest.receipts if r.kind == 'seed']
    assert [r.seat_id for r in receipts] == [agent_id], 'one seed receipt, for the polymath seat only'
    data = json.loads(Path(receipts[0].reference).read_bytes())
    assert hashlib.sha256(Path(receipts[0].reference).read_bytes()).hexdigest() == receipts[0].sha256
    assert (data['method_path'], data['body_sha256']) == (str(path), body)
    assert 'Method body' not in json.dumps(data), 'a reference, never prompt text'


def test_save_journals_why_a_seed_is_unavailable_and_still_completes(world):
    agent_id, *_rest, store, params, result = saved(world, pid=PID + 1)
    assert result.state == 'complete'
    assert not [r for r in store.read_manifest(params.checkpoint_id).receipts if r.kind == 'seed']
    events = [e.stage for e in store.operation(result.operation_id).events if e.seat_id == agent_id]
    assert any(s.startswith('cognitive-seed-unavailable') and 'recorded process' in s for s in events)


def restore(world, store, params, parent_env=None, monkeypatch=None):
    box = Box(world.tmp)
    operation = store.submit(Request(1, 'restore', uid(991), params.checkpoint_id, None, [], False),
                             actor_id=params.coordinator_id)
    result = run_restore(store, operation.operation_id,
                         RestoreParams(str(world.harness), params.coordinator_id, params.checkpoint_id,
                                       keep_shells=True), box.env())
    return result, box


def test_restore_hands_the_reference_to_the_spawn_environment(world):
    agent_id, path, body, store, params, _ = saved(world)
    # the saved seat must be dead for the plan to launch it
    world.live.discard(agent_id)
    result, box = restore(world, store, params)
    assert len(box.calls) == 1, result
    argv, env = box.calls[0]
    assert json.loads(env[SEED_ENV]) == {'version': 1, 'path': str(path), 'bodySha256': body}
    assert '--cognitive' not in argv, 'the seed is not an operator override'


def test_restore_never_inherits_a_stale_seed_from_its_own_environment(world, monkeypatch):
    coord, handoff = coordinator(world)
    agent_id = world.claude(2, parent=coord)  # no marker: no seed was saved
    compacted(world, coord, handoff)
    store, params, _, _ = save(world, coord, handoff)
    world.live.discard(agent_id)
    monkeypatch.setenv(SEED_ENV, json.dumps({'version': 1, 'path': '/x/other.md', 'bodySha256': 'f' * 64}))
    _, box = restore(world, store, params)
    assert len(box.calls) == 1 and SEED_ENV not in box.calls[0][1]
