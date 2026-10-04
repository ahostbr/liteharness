"""Postmerge named-policy COPY fixtures only; no live/user stores."""
import json
import os

import pytest

from liteharness import agent_migration_copy as copy_tool
from liteharness.agent_migration_policy import named_plan
from liteharness.agent_store import StoreError
from test_agent_migration import AID, CID, snapshot
from test_agent_migration_policy import conversation, NAMES


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    source = conversation(tmp_path)
    (source / 'empty').mkdir()
    settings_path = source / 'settings.json'
    settings = json.loads(settings_path.read_text())
    settings['execution'] = {'backend': 'codex', 'default_model': 'fixture', 'thinking_level': 'high',
                             'temperature': 0.3, 'tool_policy_profile': 'interactive'}
    settings_path.write_text(json.dumps(settings))
    registry = tmp_path / 'fixture-registry'
    registry.mkdir()
    (registry / 'names.json').write_text(json.dumps(NAMES))
    (registry / 'agents').mkdir()
    (registry / 'agents' / (AID + '.json')).write_text(json.dumps({
        'agent_id': AID, 'name': 'QuietHelm', 'session_pid': 123,
        'status': 'offline', 'exited_at': '2026-10-01T00:00:00+00:00'}))
    manifest = named_plan(tmp_path, names=NAMES)
    receipt = {'plan_digest': manifest['plan_digest'], 'authority': 'fixture leader',
               'merged_commit': 'a' * 40, 'review_evidence': 'fixture-only approval'}
    from liteharness import cli
    monkeypatch.setattr(cli, '_bridge_request', lambda method, route: {'sessions': []})
    return tmp_path, registry, manifest, receipt


def execute(candidate, **kwargs):
    root, registry, manifest, receipt = candidate
    return copy_tool.copy_named_agent(manifest, agent_id=AID, registry_root=registry,
        merge_receipt=kwargs.get('merge_receipt', receipt))


def test_no_merge_approval_blocks_before_destination_and_process_checks(candidate, monkeypatch):
    root, _, _, _ = candidate
    monkeypatch.setattr(copy_tool, '_offline', lambda *a, **k: pytest.fail('gate must precede side effects'))
    with pytest.raises(StoreError, match='approval receipt'):
        execute(candidate, merge_receipt={})
    assert not (root / '.agents').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows publication required')
def test_copy_verifies_every_source_byte_preserves_legacy_and_reruns(candidate):
    root, _, _, _ = candidate
    before = snapshot(root / '.convos')
    result = execute(candidate)
    assert result['source_unchanged'] and not result['activated']
    assert result['copied_source_bytes'] == sum(len(b) for b in before.values())
    assert snapshot(root / '.agents' / 'QuietHelm' / 'conversations' / CID) == snapshot(root / '.convos' / CID)
    assert (root / '.agents' / 'QuietHelm' / 'conversations' / CID / 'empty').is_dir()
    settings = json.loads((root / '.agents' / 'QuietHelm' / 'settings.json').read_text())
    assert settings['execution']['temperature'] == 0.3
    assert settings['execution']['tool_policy_profile'] == 'interactive'
    assert settings['execution']['model'] == 'fixture'
    assert snapshot(root / '.convos') == before
    assert execute(candidate) == result
    target = root / '.agents' / 'QuietHelm' / 'conversations' / CID / 'unknown.bin'
    target.write_bytes(b'corrupt')
    with pytest.raises(StoreError, match='hash mismatch'):
        execute(candidate)
    assert snapshot(root / '.convos') == before


def test_changed_source_or_index_blocks(candidate):
    root, registry, _, _ = candidate
    (root / '.convos' / CID / 'unknown.bin').write_bytes(b'changed')
    with pytest.raises(StoreError, match='changed'):
        execute(candidate)
    (registry / 'names.json').write_text('{}')
    with pytest.raises(StoreError, match='index'):
        execute(candidate)
    assert not (root / '.agents').exists()


def test_archive_agent_never_copied(candidate):
    root, _, manifest, receipt = candidate
    manifest['agents'][0]['disposition'] = 'archive-only'
    manifest['plan_digest'] = copy_tool.digest({k: v for k, v in manifest.items() if k != 'plan_digest'})
    receipt['plan_digest'] = manifest['plan_digest']
    with pytest.raises(StoreError, match='archive-only'):
        execute(candidate)
    assert not (root / '.agents').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows publication required')
def test_partial_copy_retry_never_overwrites_and_complete_file_resume(candidate, monkeypatch):
    root, _, _, _ = candidate
    original = copy_tool._copy_file
    count = 0
    def interrupt(*args):
        nonlocal count
        original(*args)
        count += 1
        if count == 1:
            raise OSError('fixture complete-file interruption')
    with monkeypatch.context() as patch:
        patch.setattr(copy_tool, '_copy_file', interrupt)
        with pytest.raises(OSError):
            execute(candidate)
    assert not (root / '.agents').exists()
    assert list((root / '.agents-migration-staging').rglob('.agent.initializing'))
    assert execute(candidate)['source_unchanged']
