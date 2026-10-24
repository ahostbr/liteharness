"""Real homes/save/restore; positive digest checks substitute the owner-helper boundary.

No LiteTUI package is available in the current Harness interpreter. These mocked
helper contracts do NOT establish full effective LiteTUI composition or live use.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from importlib.util import find_spec

import pytest

from liteharness.checkpoint_cli import render
from liteharness.checkpoint_contract import Request, ContractError, wire
from liteharness import checkpoint_profile as profile
from liteharness.checkpoint_restore import RestoreParams, run_restore
from checkpoint_fixtures import put, uid
from test_checkpoint_restore import Box
from test_checkpoint_save import coordinator, compacted, save, world


@pytest.fixture
def owner_helpers(monkeypatch):
    """Stub only the unavailable external API; no copied production validator."""
    def metadata_path(home, name):
        return home / 'prompts' / f'{name}.json'

    def metadata(home, name):
        path = metadata_path(home, name)
        return json.loads(path.read_text()) if path.exists() else None

    def scoped_file(home, name):
        row = metadata(home, name)
        if row and not row['active']:
            return None
        path = home / 'prompts' / name
        return path if path.exists() else None

    def stamp(path):
        info = path.stat() if path.exists() else None
        return (str(path), info.st_mtime_ns, info.st_size) if info else (str(path), None)

    helpers = SimpleNamespace(__file__=__file__, metadata=metadata, metadata_path=metadata_path,
                              scoped_file=scoped_file, stamp=stamp,
                              read_text=lambda p: p.read_text(encoding='utf-8'),
                              sha=lambda text: hashlib.sha256(text.encode()).hexdigest())
    monkeypatch.setattr(profile, 'import_module', lambda name: helpers)
    return helpers


def managed_save(world):
    coord, handoff = coordinator(world)
    compacted(world, coord, handoff)
    seat = world.litetui(10)
    home = world.owned_root / '.agents' / seat.name
    prompts = home / 'prompts'
    prompts.mkdir()
    (prompts / 'role.md').write_text('Current role\n', encoding='utf-8')
    (prompts / 'method.md').write_text('Current method\n', encoding='utf-8')
    # Original launch seed is deliberately different from current managed content.
    put(home / '.prompt-launch.json', {'version': 2, 'sources': {
        'role.md': 'a' * 64, 'method.md': 'b' * 64}, 'role_source': 'default'})
    store, params, result, _ = save(world, coord, handoff, include_offline=[seat.agent_id])
    assert seat.agent_id in {s.agent_id for s in store.read_manifest(params.checkpoint_id).seats}
    return seat, home, store, params, result


def payload(receipt):
    content = Path(receipt.reference).read_bytes()
    assert hashlib.sha256(content).hexdigest() == receipt.sha256
    return json.loads(content)


def test_save_retains_current_managed_profile_not_original_seed(world, owner_helpers):
    seat, home, store, params, result = managed_save(world)
    assert result.state == 'complete'
    saved = store.read_manifest(params.checkpoint_id)
    receipts = [r for r in saved.receipts if r.kind == 'profile' and r.seat_id == seat.agent_id]
    assert len(receipts) == 1, 'SAVE must retain hash-checked managed-profile evidence'
    data = payload(receipts[0])
    assert data['home'] == str(home)
    assert data['saved_digest'] == data['current_digest']
    assert data['saved_digest'] is not None
    assert data['saved_digest'] not in ('a' * 64, 'b' * 64)
    assert data['helper_source'] == str(Path(__file__).absolute())
    assert data['notice'] is None


@pytest.mark.parametrize('changed', [False, True])
def test_restore_reports_managed_edits_once_without_reseed_or_failure(world, owner_helpers, changed):
    seat, home, store, params, _ = managed_save(world)
    if changed:
        (home / 'prompts' / 'method.md').write_text('Legitimate edited method\n', encoding='utf-8')
    before = {p.name: p.read_bytes() for p in (home / 'prompts').iterdir()}
    box = Box(world.tmp)
    operation = store.submit(Request(1, 'restore', uid(990), params.checkpoint_id, None, [], False),
                             actor_id=params.coordinator_id)
    result = run_restore(store, operation.operation_id,
                         RestoreParams(str(world.harness), params.coordinator_id, params.checkpoint_id,
                                       keep_shells=True), box.env())
    assert result.state == 'complete'
    row = next(s for s in result.seats if s.seat_id == seat.agent_id)
    assert not row.errors
    receipts = [r for r in row.evidence if r.kind == 'profile']
    assert len(receipts) == 1, 'RESTORE must report the current managed profile'
    data = payload(receipts[0])
    assert (data['saved_digest'] != data['current_digest']) is changed
    assert data['notice'] == (profile.NOTICE if changed else None)
    assert render(result, {}).count('profile changed since save') == int(changed)
    assert before == {p.name: p.read_bytes() for p in (home / 'prompts').iterdir()}
    assert len(box.calls) == 1
    argv, environment = box.calls[0]
    assert '--cognitive' not in argv and '--cognitive-file' not in argv
    assert 'LITEHARNESS_COGNITIVE_FILE' not in environment


def restore_saved(world, store, params):
    box = Box(world.tmp)
    operation = store.submit(Request(1, 'restore', uid(991), params.checkpoint_id, None, [], False),
                             actor_id=params.coordinator_id)
    result = run_restore(store, operation.operation_id,
                         RestoreParams(str(world.harness), params.coordinator_id, params.checkpoint_id,
                                       keep_shells=True), box.env())
    return result, box


@pytest.mark.parametrize('phase', ['save', 'restore'])
@pytest.mark.parametrize('error', [OSError, ContractError])
def test_profile_only_retention_failure_is_nonblocking_and_journalled(world, monkeypatch, phase, error):
    world.claude(3, parent=uid(1))
    def unavailable(*args, **kwargs):
        raise error('profile-only destination inaccessible')
    if phase == 'save':
        monkeypatch.setattr(profile, 'retain', unavailable)
    seat, home, store, params, saved_result = managed_save(world)
    if phase == 'restore':
        monkeypatch.setattr(profile, 'retain', unavailable)
        before = {p.name: p.read_bytes() for p in (home / 'prompts').iterdir()}
        result, box = restore_saved(world, store, params)
        assert len(box.calls) == 2
        assert before == {p.name: p.read_bytes() for p in (home / 'prompts').iterdir()}
    else:
        result = saved_result
    assert result.state == 'complete' and all(not row.errors for row in result.seats)
    assert store.read_manifest(params.checkpoint_id).state == 'complete'
    diagnostics = [e for e in store.operation(result.operation_id).events
                   if e.stage.startswith('profile-evidence-write-failed:')]
    assert len(diagnostics) == 1 and diagnostics[0].seat_id == seat.agent_id
    assert 'cognitive role+method digest' in diagnostics[0].stage
    assert 'profile-only destination inaccessible' in diagnostics[0].stage
    row = next(row for row in result.seats if row.seat_id == seat.agent_id)
    assert not any(r.kind == 'profile' for r in row.evidence), 'Never fabricate a retained receipt'
    assert 'profile-evidence-write-failed' not in render(result), 'Retention failure is journal-only'


@pytest.mark.parametrize('phase', ['save', 'restore'])
@pytest.mark.parametrize('error', [OSError, ContractError])
def test_profile_retention_diagnostic_core_journal_failure_still_propagates(world, monkeypatch, phase, error):
    from liteharness import checkpoint_store
    if phase == 'restore':
        _, _, store, params, _ = managed_save(world)
    def unavailable(*args, **kwargs):
        raise OSError('profile-only destination inaccessible')
    monkeypatch.setattr(profile, 'retain', unavailable)
    original = checkpoint_store._atomic_json
    def fail_diagnostic_write(path, value):
        events = value.get('events', [])
        if events and events[-1]['stage'].startswith('profile-evidence-write-failed:'):
            raise error('core diagnostic journal write refused')
        return original(path, value)
    monkeypatch.setattr(checkpoint_store, '_atomic_json', fail_diagnostic_write)
    with pytest.raises(error, match='core diagnostic journal write refused'):
        if phase == 'save':
            managed_save(world)
        else:
            restore_saved(world, store, params)


def test_normal_interpreter_missing_helper_is_explicit_and_nonblocking(world):
    if find_spec('litetui') is not None:
        pytest.skip('This negative measures an interpreter without the optional owner package')
    seat, home, store, params, _ = managed_save(world)
    result, box = restore_saved(world, store, params)
    row = next(s for s in result.seats if s.seat_id == seat.agent_id)
    data = payload(next(r for r in row.evidence if r.kind == 'profile'))
    assert result.state == 'complete' and not row.errors and len(box.calls) == 1
    assert data['home'] == str(home)
    assert data['helper_source'] is None
    assert data['current_digest'] is None and data['saved_digest'] is None and data['notice'] is None
    assert all(p['state'] == 'unavailable' and 'helper unavailable' in p['diagnostic']
               for p in data['parts'].values())
    assert 'cognitive role+method digest unavailable' in render(result)


@pytest.mark.parametrize('state', ['missing', 'inactive', 'empty'])
def test_missing_inactive_and_empty_are_distinct(world, owner_helpers, state):
    seat, home, store, params, _ = managed_save(world)
    method = home / 'prompts' / 'method.md'
    if state == 'missing':
        method.unlink()
    elif state == 'inactive':
        put(home / 'prompts' / 'method.md.json', {'active': False})
    else:
        method.write_bytes(b'')
    observation = profile.observe(seat, params.checkpoint_id)
    assert observation.parts['method.md'].state == ('active' if state == 'empty' else state)
    assert (observation.current_digest is not None) == (state == 'empty')
    assert observation.notice is None


def test_owner_normalization_makes_crlf_and_lf_equal(world, owner_helpers):
    seat, home, store, params, _ = managed_save(world)
    before = profile.observe(seat, params.checkpoint_id)
    (home / 'prompts' / 'method.md').write_bytes(b'Current method\r\n')
    after = profile.observe(seat, params.checkpoint_id, uid(991), before.current_digest)
    assert after.current_digest == before.current_digest and after.notice is None


def test_owner_invalid_metadata_diagnostic_is_nonblocking(world, owner_helpers, monkeypatch):
    seat, home, store, params, _ = managed_save(world)
    def invalid(*args):
        raise ValueError('Active scoped prompt is missing or hash disagrees')
    monkeypatch.setattr(owner_helpers, 'scoped_file', invalid)
    result, box = restore_saved(world, store, params)
    row = next(s for s in result.seats if s.seat_id == seat.agent_id)
    data = payload(next(r for r in row.evidence if r.kind == 'profile'))
    assert result.state == 'complete' and not row.errors and len(box.calls) == 1
    assert data['saved_digest'] is not None and data['current_digest'] is None
    assert data['notice'] is None
    assert all(p['state'] == 'invalid' for p in data['parts'].values())


def test_mid_observation_body_change_cannot_get_a_digest(world, owner_helpers, monkeypatch):
    seat, home, store, params, _ = managed_save(world)
    original = owner_helpers.read_text
    reads = 0
    def changing(path):
        nonlocal reads
        reads += 1
        if reads == 2:
            (home / 'prompts' / 'method.md').write_text('Changed while reading', encoding='utf-8')
        return original(path)
    monkeypatch.setattr(owner_helpers, 'read_text', changing)
    observation = profile.observe(seat, params.checkpoint_id)
    assert observation.current_digest is None
    assert all(p.state == 'invalid' for p in observation.parts.values())


def test_older_checkpoint_no_digest_still_uses_current_home(world, owner_helpers):
    from checkpoint_fixtures import owned
    from test_checkpoint_restore import manifest, seat as restore_seat, start
    owned_seat = owned(world.tmp, 10)
    home = world.tmp / '.agents' / owned_seat.name
    (home / 'prompts').mkdir()
    for name in profile.NAMES:
        (home / 'prompts' / name).write_text('Current managed text', encoding='utf-8')
    seat = replace(restore_seat(10, root=world.tmp), cli='litetui', execution=owned_seat.execution)
    m = manifest(world.tmp, [seat])  # deliberately old manifest: no profile receipt
    store, params, operation = start(world.tmp, m, keep_shells=True)
    box = Box(world.tmp)
    result = run_restore(store, operation, params, box.env())
    row = next(s for s in result.seats if s.seat_id == seat.agent_id)
    data = payload(next(r for r in row.evidence if r.kind == 'profile'))
    assert result.state == 'complete' and not row.errors
    assert data['saved_digest'] is None and data['current_digest'] is not None and data['notice'] is None
    assert 'profile changed since save' not in render(result)


@pytest.mark.parametrize('corruption', ['bytes', 'checkpoint', 'seat', 'operation'])
def test_corrupt_saved_profile_is_not_legacy_absence_and_sibling_continues(world, owner_helpers, corruption):
    sibling = world.claude(3, parent=uid(1))
    seat, home, store, params, _ = managed_save(world)
    saved = store.read_manifest(params.checkpoint_id)
    receipt = next(r for r in saved.receipts if r.kind == 'profile' and r.seat_id == seat.agent_id)
    if corruption == 'bytes':
        Path(receipt.reference).write_text('{}', encoding='utf-8')  # deliberate byte corruption
    else:
        data = payload(receipt)
        data[{'checkpoint': 'checkpoint_id', 'seat': 'seat_id', 'operation': 'operation_id'}[corruption]] = uid(999)
        put(Path(receipt.reference), data)
        bad_receipt = replace(receipt, sha256=hashlib.sha256(Path(receipt.reference).read_bytes()).hexdigest())
        # Fault injection ONLY: bypass immutable publication to isolate the inner identity join.
        altered = replace(saved, receipts=[bad_receipt if r == receipt else r for r in saved.receipts])
        put(store.root / params.checkpoint_id / 'manifest.json', wire(altered))
    result, box = restore_saved(world, store, params)
    rows = {s.seat_id: s for s in result.seats}
    assert result.state == 'complete'
    assert rows[seat.agent_id].state == 'ready-idle' and not rows[seat.agent_id].errors
    assert rows[sibling].state == 'ready-idle' and not rows[sibling].errors
    assert len(box.calls) == 2
    data = payload(next(r for r in rows[seat.agent_id].evidence if r.kind == 'profile'))
    assert data['saved_digest'] is None and data['current_digest'] is not None
    assert data['notice'] is None and 'profile-evidence-invalid' in data['saved_diagnostic']
    assert render(result).count('profile-evidence-invalid') == 1
    assert 'profile changed since save' not in render(result)


@pytest.mark.parametrize('axis', ['checkpoint', 'seat', 'operation', 'hash'])
def test_profile_receipt_identity_and_hash_are_checked(world, owner_helpers, axis):
    seat, home, store, params, _ = managed_save(world)
    observation = profile.observe(seat, params.checkpoint_id, uid(991))
    receipt = profile.retain(store, seat, observation, world.clock.now().isoformat())
    checkpoint, identity, operation = params.checkpoint_id, seat.agent_id, uid(991)
    if axis == 'checkpoint':
        checkpoint = uid(999)
    elif axis == 'seat':
        identity = uid(999)
    elif axis == 'operation':
        operation = uid(999)
    else:
        receipt = replace(receipt, sha256='0' * 64)
    with pytest.raises(ContractError):
        profile.read(receipt, checkpoint, identity, operation)


def test_unsupported_owner_api_is_explicit_not_empty(world, owner_helpers, monkeypatch):
    seat, home, store, params, _ = managed_save(world)
    monkeypatch.delattr(owner_helpers, 'stamp')
    observation = profile.observe(seat, params.checkpoint_id)
    assert observation.current_digest is None
    assert all(p.state == 'unavailable' and 'API unsupported' in p.diagnostic for p in observation.parts.values())
    assert observation.helper_source == str(Path(__file__).absolute())


def test_receipt_rejects_digest_contradicting_its_components(world, owner_helpers):
    seat, home, store, params, _ = managed_save(world)
    observation = profile.observe(seat, params.checkpoint_id)
    with pytest.raises(ContractError, match='disagrees with components'):
        wire(replace(observation, saved_digest='0' * 64, current_digest='0' * 64))


def test_operation_evidence_never_overwrites_saved_or_other_operation(world, owner_helpers):
    seat, home, store, params, _ = managed_save(world)
    saved = next(r for r in store.read_manifest(params.checkpoint_id).receipts
                 if r.kind == 'profile' and r.seat_id == seat.agent_id)
    original = Path(saved.reference).read_bytes()
    a = profile.retain(store, seat, profile.observe(seat, params.checkpoint_id, uid(991)), world.clock.now().isoformat())
    b = profile.retain(store, seat, profile.observe(seat, params.checkpoint_id, uid(992)), world.clock.now().isoformat())
    assert len({saved.reference, a.reference, b.reference}) == 3
    assert Path(saved.reference).read_bytes() == original
    assert profile.read(a, params.checkpoint_id, seat.agent_id, uid(991)).operation_id == uid(991)
    assert profile.read(b, params.checkpoint_id, seat.agent_id, uid(992)).operation_id == uid(992)
