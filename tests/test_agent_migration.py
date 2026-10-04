"""T0308 dry run and copy-only manifest fixtures, never actual legacy stores."""
import json

import pytest

from liteharness import agent_migration as migration
from liteharness.agent_store import StoreError

AID = '11111111-1111-4111-8111-111111111111'
CID = '33333333-3333-4333-8333-333333333333'
DID = '44444444-4444-4444-8444-444444444444'
EXECUTION = {'backend': 'codex', 'model': 'fixture', 'thinking_level': 'high'}


def legacy(root, cid=CID, memory=b'original memory', *, settings=True):
    directory = root / '.convos' / cid
    directory.mkdir(parents=True)
    if settings:
        (directory / 'settings.json').write_text(json.dumps({'seat_id': AID,
            'backend': 'codex', 'model': 'fixture', 'thinking_level': 'high'}), encoding='utf-8')
    (directory / 'memory.md').write_bytes(memory)
    (directory / 'unknown.bin').write_bytes(b'\x00\xffunknown\r\n')
    (directory / 'tool-raw').mkdir()
    (directory / 'tool-raw' / 'raw.bin').write_bytes(b'raw tool bytes')
    return directory


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def quiescence(manifest):
    return {'source_digest': manifest['source_digest'],
            'folders': sorted(f['source'] for f in manifest['folders']),
            'live_writers': [], 'unknown_writers': [], 'evidence': 'fixture-owned root, no other processes'}


def test_dryrun_hashes_all_companions_never_writes_source_destination_or_locks(tmp_path):
    legacy(tmp_path)
    before = snapshot(tmp_path)
    first = migration.plan(tmp_path)
    second = migration.plan(tmp_path)
    assert first == second
    assert first['conflicts'] == []
    assert set(first['folders'][0]['files']) == {'settings.json', 'memory.md', 'unknown.bin', 'tool-raw/raw.bin'}
    assert snapshot(tmp_path) == before
    assert not (tmp_path / '.agents').exists()
    assert not list(tmp_path.rglob('*.lock'))


def test_missing_metadata_folder_is_included_and_conflict_not_dropped(tmp_path):
    legacy(tmp_path, settings=False)
    manifest = migration.plan(tmp_path)
    assert len(manifest['folders']) == 1
    assert manifest['folders'][0]['files']['unknown.bin']['bytes'] == 11
    assert manifest['conflicts']
    selected = migration.plan(tmp_path, decisions={CID: {'execution': EXECUTION}})
    assert selected['conflicts'] == []
    assert len(selected['agents']) == 1


def test_unnamed_conversations_each_own_generated_name_and_id_even_shared_history(tmp_path):
    legacy(tmp_path)
    legacy(tmp_path, DID)
    manifest = migration.plan(tmp_path)
    assert len(manifest['agents']) == 2
    assert len({a['agent_id'] for a in manifest['agents']}) == 2
    for agent in manifest['agents']:
        assert agent['name'] == migration.generate_name(agent['agent_id'])
    assert all(f['historical_agent_id'] == AID for f in manifest['folders'])


def test_named_group_conflict_requires_explicit_canonical_preserves_all_versions(tmp_path):
    legacy(tmp_path)
    legacy(tmp_path, DID, b'different original')
    names = {'QuietHelm': {'agent_id': AID}}
    manifest = migration.plan(tmp_path, names=names)
    assert len(manifest['agents']) == 1 and len(manifest['folders']) == 2
    assert any('Conflicting grouped' in c['reason'] for c in manifest['conflicts'])
    approved = migration.plan(tmp_path, names=names, decisions={'canonical_sources': {AID: DID}})
    assert approved['conflicts'] == []
    assert approved['agents'][0]['canonical_source'] == DID
    assert snapshot(tmp_path)[f'.convos/{CID}/memory.md'] == b'original memory'


def test_generated_name_collision_fails_closed_no_suffix_guess(tmp_path, monkeypatch):
    legacy(tmp_path)
    legacy(tmp_path, DID)
    monkeypatch.setattr(migration, 'generate_name', lambda _: 'SameName')
    result = migration.plan(tmp_path)
    assert any('collision' in row['reason'] for row in result['conflicts'])
    assert not (tmp_path / '.agents').exists()


def test_active_unknown_writer_or_wrong_plan_gate_refuses_before_destination_writes(tmp_path):
    legacy(tmp_path)
    manifest = migration.plan(tmp_path)
    for evidence in ({}, {**quiescence(manifest), 'live_writers': ['fixture-pid']},
                     {**quiescence(manifest), 'unknown_writers': ['uninspectable']},
                     {**quiescence(manifest), 'source_digest': 'wrong'}):
        with pytest.raises(StoreError):
            migration.copy_verified(manifest, quiescence=evidence)
        assert not (tmp_path / '.agents').exists()


def test_source_change_after_dryrun_refuses_without_destination_writes(tmp_path):
    legacy(tmp_path)
    manifest = migration.plan(tmp_path)
    (tmp_path / '.convos' / CID / 'unknown.bin').write_bytes(b'changed')
    with pytest.raises(StoreError):
        migration.copy_verified(manifest, quiescence=quiescence(manifest))
    assert not (tmp_path / '.agents').exists()


def test_old_copy_route_unconditionally_disabled_even_with_valid_fixture_gate(tmp_path, monkeypatch):
    legacy(tmp_path)
    manifest = migration.plan(tmp_path)
    monkeypatch.setattr(migration, '_verify_sources', lambda *_: pytest.fail('disabled route reached source verification'))
    with pytest.raises(StoreError, match='DISABLED'):
        migration.copy_verified(manifest, quiescence=quiescence(manifest))
    assert not (tmp_path / '.agents').exists()
    assert not (tmp_path / '.agents-migration-staging').exists()


def test_preexisting_destination_and_linked_source_never_overwritten(tmp_path):
    source = legacy(tmp_path)
    manifest = migration.plan(tmp_path)
    target = tmp_path / '.agents' / manifest['agents'][0]['name']
    target.mkdir(parents=True)
    (target / 'original.bin').write_bytes(b'preexisting')
    rerun = migration.plan(tmp_path)
    assert any('Destination already exists' in c['reason'] for c in rerun['conflicts'])
    with pytest.raises(StoreError):
        migration.copy_verified(manifest, quiescence=quiescence(manifest))
    assert (target / 'original.bin').read_bytes() == b'preexisting'
    try:
        (source / 'linked').symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('host cannot create symlinks')
    result = migration.plan(tmp_path)
    assert any('Linked' in c['reason'] for c in result['conflicts'])


def test_empty_directories_and_nested_unknowns_preserved(tmp_path):
    source = legacy(tmp_path)
    (source / 'empty' / 'nested').mkdir(parents=True)
    (source / 'memories' / 'empty').mkdir(parents=True)
    manifest = migration.plan(tmp_path)
    assert manifest['conflicts'] == []
    assert manifest['folders'][0]['directories'] == ['empty', 'empty/nested', 'memories', 'memories/empty', 'tool-raw']
    with pytest.raises(StoreError, match='DISABLED'):
        migration.copy_verified(manifest, quiescence=quiescence(manifest))
    assert not (tmp_path / '.agents').exists()


def test_root_companion_hashed_and_blocks_copy_until_destination_scope_decided(tmp_path):
    legacy(tmp_path)
    (tmp_path / '.convos' / 'catalog.bin').write_bytes(b'root companion')
    manifest = migration.plan(tmp_path)
    assert manifest['root_files']['catalog.bin']['bytes'] == 14
    assert any('Non-folder' in c['reason'] for c in manifest['conflicts'])
    with pytest.raises(StoreError):
        migration.copy_verified(manifest, quiescence=quiescence(manifest))
    assert not (tmp_path / '.agents').exists()


def test_directory_or_root_companion_added_after_plan_blocks_copy(tmp_path):
    legacy(tmp_path)
    for added in ('new-root.bin', f'{CID}/empty'):
        manifest = migration.plan(tmp_path)
        path = tmp_path / '.convos' / added
        if added.endswith('empty'):
            path.mkdir()
        else:
            path.write_bytes(b'new root companion')
        with pytest.raises(StoreError):
            migration.copy_verified(manifest, quiescence=quiescence(manifest))
        assert not (tmp_path / '.agents').exists()
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()


def test_saved_name_authority_and_catalog_disagreement_explicit_conflict(tmp_path):
    source = legacy(tmp_path)
    path = source / 'settings.json'
    settings = json.loads(path.read_text())
    settings['seat_name'] = 'QuietHelm'
    path.write_text(json.dumps(settings))
    manifest = migration.plan(tmp_path)
    assert manifest['agents'][0]['name'] == 'QuietHelm'
    assert manifest['agents'][0]['agent_id'] == AID
    conflict = migration.plan(tmp_path, names={'OtherName': {'agent_id': AID}})
    assert any('contradicts' in c['reason'] for c in conflict['conflicts'])
    conflict = migration.plan(tmp_path, names={'QuietHelm': {'agent_id': DID}})
    assert any('different identity' in c['reason'] for c in conflict['conflicts'])


def test_preference_scope_never_silently_promoted(tmp_path):
    source = legacy(tmp_path)
    path = source / 'settings.json'
    settings = json.loads(path.read_text())
    settings.update(tool_policy_profile='interactive', execution={'backend': 'codex',
                    'default_model': 'fixture', 'thinking_level': 'high', 'temperature': 0.3})
    path.write_text(json.dumps(settings))
    manifest = migration.plan(tmp_path)
    folder = manifest['folders'][0]
    assert folder['unresolved_preference_scope'] == ['execution.temperature', 'tool_policy_profile']
    assert any('scope unresolved' in c['reason'] for c in manifest['conflicts'])
    assert manifest['agents'][0]['settings']['execution'] == EXECUTION
    assert snapshot(tmp_path)[f'.convos/{CID}/settings.json'] == path.read_bytes()


def test_plan_traversal_and_tampered_manifest_path_refused(tmp_path):
    legacy(tmp_path)
    with pytest.raises(StoreError):
        migration.plan(tmp_path / 'child' / '..')
    manifest = migration.plan(tmp_path)
    manifest['folders'][0]['files']['../escape'] = manifest['folders'][0]['files']['unknown.bin']
    manifest['plan_digest'] = migration.digest({k: v for k, v in manifest.items() if k != 'plan_digest'})
    with pytest.raises(StoreError):
        migration.copy_verified(manifest, quiescence=quiescence(manifest))
    assert not (tmp_path / '.agents').exists()


def test_catalog_casefold_collision_even_without_matching_source_folder(tmp_path, monkeypatch):
    legacy(tmp_path)
    monkeypatch.setattr(migration, 'generate_name', lambda _: 'QuietHelm')
    manifest = migration.plan(tmp_path, names={'quiethelm': {'agent_id': DID}})
    assert any('catalog spelling/identity' in c['reason'] for c in manifest['conflicts'])


def test_linked_root_entry_reported_not_followed(tmp_path):
    legacy(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (tmp_path / '.convos' / DID).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('host cannot create symlinks')
    manifest = migration.plan(tmp_path)
    assert any(c['source'] == DID and 'Linked' in c['reason'] for c in manifest['conflicts'])
    assert len(manifest['folders']) == 1
    assert not (tmp_path / '.agents').exists()
