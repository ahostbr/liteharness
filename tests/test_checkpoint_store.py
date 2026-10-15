from dataclasses import replace
import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from liteharness import checkpoint_store as storage
from liteharness.checkpoint_contract import ContractError
from liteharness.checkpoint_store import CheckpointStore, JournalEvent
from checkpoint_fixtures import manifest, request, uid


def publish(store, value):
    store.publish(value, authorized_reference_roots=[store.root.parents[1]])


def test_complete_publication_partial_cannot_advance_or_rewrite(tmp_path):
    store = CheckpointStore(tmp_path)
    assert not store.root.exists()  # construction/read never initializes storage
    assert store.latest_complete() is None
    complete = manifest(tmp_path)
    publish(store, complete)
    assert store.latest_complete() == complete
    publish(store, manifest(tmp_path, 501, complete=False))
    assert store.latest_complete() == complete
    assert store.read_manifest(uid(501)).state == 'incomplete'
    with pytest.raises(ContractError):
        publish(store, replace(complete, state='incomplete'))
    assert store.latest_complete() == complete
    assert not (store.root / '.writer-lock').exists()


@pytest.mark.parametrize('failure', ['torn-write', 'disk-full', 'manifest-replace', 'pointer-replace'])
def test_publication_fault_preserves_previous_complete(tmp_path, monkeypatch, failure):
    store = CheckpointStore(tmp_path)
    old = manifest(tmp_path)
    publish(store, old)
    old_bytes = (store.root / old.checkpoint_id / 'manifest.json').read_bytes()
    pointer_bytes = (store.root / 'latest-complete.json').read_bytes()
    new = manifest(tmp_path, 501)
    original_replace = os.replace
    if failure == 'disk-full':
        def full(_fd):
            raise OSError(errno.ENOSPC, 'synthetic disk full')
        monkeypatch.setattr(storage.os, 'fsync', full)
    elif failure == 'torn-write':
        original_fdopen = os.fdopen
        class TornWriter:
            def __init__(self, fd, mode):
                self.stream = original_fdopen(fd, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, data):
                self.stream.write(data[:17])
                self.stream.flush()
                raise OSError(errno.ENOSPC, 'synthetic partial write')
        monkeypatch.setattr(storage.os, 'fdopen', TornWriter)
    else:
        def fail_replace(source, target):
            if Path(target).name == ('manifest.json' if failure == 'manifest-replace' else 'latest-complete.json'):
                raise PermissionError('synthetic replace failure')
            return original_replace(source, target)
        monkeypatch.setattr(storage.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        publish(store, new)
    assert store.latest_complete() == old
    assert (store.root / old.checkpoint_id / 'manifest.json').read_bytes() == old_bytes
    assert (store.root / 'latest-complete.json').read_bytes() == pointer_bytes
    assert not list(store.root.rglob('.pending-*'))
    if failure == 'pointer-replace':
        assert store.read_manifest(new.checkpoint_id) == new  # durable orphan, never false acceptance


def test_idempotent_request_payload_and_actor_binding(tmp_path):
    store = CheckpointStore(tmp_path)
    r = request()
    operation = store.submit(r, actor_id='host-authorized-fixture')
    reopened = CheckpointStore(tmp_path)
    assert reopened.submit(r, actor_id='host-authorized-fixture') == operation
    assert reopened.operation_for_request(r.request_id) == operation
    assert reopened.operation(operation.operation_id) == operation
    for changed, actor in ((replace(r, checkpoint_id=uid(999)), 'host-authorized-fixture'),
                           (r, 'different-actor')):
        with pytest.raises(ContractError):
            reopened.submit(changed, actor_id=actor)
    assert len(list((store.root / 'operations').glob('*.json'))) == 1


def test_submit_failure_does_not_acknowledge_or_bind_request(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(storage.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('uncertain')))
        with pytest.raises(OSError):
            store.submit(request(), actor_id='host-authorized-fixture')
    assert store.operation_for_request(uid(700)) is None
    assert store.submit(request(), actor_id='host-authorized-fixture').request.request_id == uid(700)


def test_journal_sequence_and_interrupted_append_keep_previous(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path)
    operation = store.submit(request(), actor_id='fixture')
    first = JournalEvent(0, 'launch-intent', uid(1), None)
    saved = store.append(operation.operation_id, first, expected_sequence=0)
    assert saved.events == [first]
    with pytest.raises(ContractError):
        store.append(operation.operation_id, first, expected_sequence=0)
    with monkeypatch.context() as patch:
        patch.setattr(storage.os, 'fsync', lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC, 'full')))
        with pytest.raises(OSError):
            store.append(operation.operation_id, JournalEvent(1, 'unknown', uid(1), None), expected_sequence=1)
    assert CheckpointStore(tmp_path).operation(operation.operation_id) == saved


def test_read_only_requests_and_reads_create_nothing(tmp_path):
    store = CheckpointStore(tmp_path)
    assert store.operation_for_request(uid(700)) is None
    assert store.operation(uid(800)) is None
    with pytest.raises(ContractError):
        store.submit(replace(request(), dry_run=True), actor_id='fixture')
    assert not store.root.exists()


def test_lock_is_not_reaped_and_no_second_writer(tmp_path):
    store = CheckpointStore(tmp_path)
    store.root.mkdir(parents=True)
    lock = store.root / '.writer-lock'
    lock.write_text('existing owner; crash requires reconciliation')
    with pytest.raises(ContractError, match='locked'):
        publish(store, manifest(tmp_path))
    assert lock.read_text() == 'existing owner; crash requires reconciliation'
    assert not (store.root / uid(500)).exists()


def test_path_traversal_and_link_refusal(tmp_path, monkeypatch):
    with pytest.raises(ContractError):
        CheckpointStore(tmp_path / '..' / 'outside')
    store = CheckpointStore(tmp_path)
    with pytest.raises(ValueError):
        store.read_manifest('../outside')
    original = type(tmp_path).lstat
    monkeypatch.setattr(type(tmp_path), 'lstat', lambda p, *a, **kw:
                        SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                        if p == tmp_path else original(p, *a, **kw))
    with pytest.raises(ContractError, match='Linked'):
        publish(store, manifest(tmp_path))


def test_pointer_digest_and_directory_identity_detect_corruption(tmp_path):
    store = CheckpointStore(tmp_path)
    m = manifest(tmp_path)
    publish(store, m)
    path = store.root / m.checkpoint_id / 'manifest.json'
    path.write_text(path.read_text().replace('fixture-host', 'changed-host'))
    with pytest.raises(ContractError, match='content mismatch'):
        store.latest_complete()


def test_duplicate_json_keys_are_corrupt_evidence(tmp_path):
    path = tmp_path / 'evidence.json'
    path.write_text('{"agent_id":"first","agent_id":"second"}')
    with pytest.raises(ContractError):
        storage.read_json(path)


def test_republishing_old_complete_cannot_regress_latest(tmp_path):
    store = CheckpointStore(tmp_path)
    old = manifest(tmp_path)
    newer = replace(manifest(tmp_path, 501), created_at='2026-01-01T00:00:01+00:00')
    publish(store, old)
    publish(store, newer)
    publish(store, old)
    assert store.latest_complete() == newer


def test_partial_retry_cannot_shrink_frozen_expected_roster(tmp_path):
    from liteharness.checkpoint_contract import Blocker, ExpectedSeat
    store = CheckpointStore(tmp_path)
    base = manifest(tmp_path, complete=False)
    partial = replace(base, expected=[*base.expected, ExpectedSeat(uid(9), 'Offline', 'explicit-offline')],
                      blockers=[Blocker('missing', uid(9), 'Reconcile offline seat')])
    publish(store, partial)
    with pytest.raises(ContractError):
        publish(store, base)
    assert store.read_manifest(base.checkpoint_id) == partial


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_nonstandard_json_constants_refuse(tmp_path, constant):
    path = tmp_path / 'evidence.json'
    path.write_text('{"value":' + constant + '}')
    with pytest.raises(ContractError):
        storage.read_json(path)


def test_publication_requires_authorized_reference_roots(tmp_path):
    store = CheckpointStore(tmp_path / 'store')
    authorized = tmp_path / 'authorized'
    authorized.mkdir()
    with pytest.raises(ContractError):
        store.publish(manifest(tmp_path), authorized_reference_roots=[authorized])
    assert not store.root.exists()


def test_result_append_cannot_drop_expected_row(tmp_path):
    from liteharness.checkpoint_contract import Blocker, Counts, ExpectedSeat, Result, SeatResult
    store = CheckpointStore(tmp_path)
    m = manifest(tmp_path, complete=False)
    m = replace(m, expected=[*m.expected, ExpectedSeat(uid(9), 'Offline', 'explicit-offline')],
                blockers=[Blocker('missing', uid(9), 'Reconcile')])
    publish(store, m)
    operation = store.submit(request(), actor_id='fixture')
    result = Result(1, operation.operation_id, m.checkpoint_id, 'complete', Counts(1, 1, 0, 0, 0),
                    [SeatResult(uid(1), 'ready-idle', [], [], [])], [], [])
    with pytest.raises(ContractError):
        store.append(operation.operation_id, JournalEvent(0, 'complete', None, result), expected_sequence=0)
    assert not store.operation(operation.operation_id).events


def test_created_directory_parents_are_synchronized_before_record_ack(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path)
    synchronized = []
    monkeypatch.setattr(storage, '_sync_directory', synchronized.append)
    store.submit(request(), actor_id='fixture')
    assert synchronized == [tmp_path, tmp_path / 'sessions', store.root, store.root / 'operations']


def test_partial_retry_cannot_change_resolved_conversation(tmp_path):
    store = CheckpointStore(tmp_path)
    m = manifest(tmp_path, complete=False)
    publish(store, m)
    changed = replace(m, seats=[replace(m.seats[0], conversation_id=uid(999))])
    with pytest.raises(ContractError):
        publish(store, changed)


def test_restore_operation_rejects_result_from_another_checkpoint(tmp_path):
    from liteharness.checkpoint_contract import Counts, Result, SeatResult, wire
    from liteharness.checkpoint_store import Operation
    r = request()
    result = Result(1, uid(800), uid(501), 'complete', Counts(1, 1, 0, 0, 0),
                    [SeatResult(uid(1), 'ready-idle', [], [], [])], [], [])
    with pytest.raises(ContractError):
        wire(Operation(1, uid(800), 'fixture', r, [JournalEvent(0, 'complete', None, result)]))


def test_orphan_complete_can_be_retried_under_same_checkpoint_id(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path)
    old, new = manifest(tmp_path), manifest(tmp_path, 501)
    publish(store, old)
    original = os.replace
    def interrupt(source, target):
        if Path(target).name == 'latest-complete.json':
            raise OSError('synthetic pointer interruption')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(storage.os, 'replace', interrupt)
        with pytest.raises(OSError):
            publish(store, new)
    assert store.latest_complete() == old
    publish(store, new)
    assert store.latest_complete() == new


def test_reference_link_is_rejected_before_publication(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path)
    m = manifest(tmp_path)
    reference = Path(m.coordinator.read_first)
    original = type(reference).lstat
    monkeypatch.setattr(type(reference), 'lstat', lambda p, *a, **kw:
                        SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
                        if p == reference else original(p, *a, **kw))
    with pytest.raises(ContractError, match='Linked'):
        publish(store, m)
    assert not store.root.exists()
