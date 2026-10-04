"""Adversarial draft-policy fixtures only; no user stores or process launches."""
import json

import pytest

from liteharness import agent_migration_copy as copy_tool
from liteharness import agent_migration_policy as policy
from liteharness.agent_store import StoreError
from test_agent_migration import AID, CID, DID
from test_agent_migration_copy import candidate as candidate_fixture, execute

candidate = candidate_fixture
from test_agent_migration_policy import NAMES, conversation


def resign(manifest, receipt):
    manifest['plan_digest'] = copy_tool.digest({k: v for k, v in manifest.items() if k != 'plan_digest'})
    receipt['plan_digest'] = manifest['plan_digest']


@pytest.mark.parametrize('change', ['settings', 'duplicate-folder', 'memory', 'selection'])
def test_self_consistent_forged_policy_is_not_copy_authority(candidate, change):
    root, _, manifest, receipt = candidate
    if change == 'settings':
        manifest['agents'][0]['selected_settings_snapshot']['model'] = 'forged'
    elif change == 'duplicate-folder':
        manifest['folders'].append(dict(manifest['folders'][0]))
    elif change == 'memory':
        manifest['folders'][0]['memory_signature'] = 'forged'
    else:
        manifest['agents'][0]['settings_source'] = AID
    resign(manifest, receipt)
    with pytest.raises(StoreError):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


@pytest.mark.parametrize('collision', ['alias', 'case', 'invalid', 'conversation'])
def test_current_shared_index_conflicts_block_copy(candidate, collision):
    root, registry, _, _ = candidate
    names = dict(NAMES)
    alias_name = 'quiethelm' if collision == 'case' else 'Bad/Name' if collision == 'invalid' else 'Alias'
    names[alias_name] = {'agent_id': DID, 'convo_id': CID} if collision == 'conversation' else {'agent_id': AID}
    (registry / 'names.json').write_text(json.dumps(names))
    with pytest.raises(StoreError):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


@pytest.mark.parametrize('change', ['identity', 'name', 'exit-time', 'contradictory'])
def test_ambiguous_registry_evidence_never_counts_as_offline(candidate, change):
    root, registry, _, _ = candidate
    path = registry / 'agents' / (AID + '.json')
    row = json.loads(path.read_text())
    if change == 'identity':
        row.pop('agent_id')
    elif change == 'name':
        row['name'] = 'AnotherName'
    elif change == 'exit-time':
        row['exited_at'] = 'not a timestamp'
    else:
        row['status'] = 'active'
    path.write_text(json.dumps(row))
    with pytest.raises(StoreError):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


@pytest.mark.parametrize('extra', ['extra.md', 'memories/unapproved.md', 'conversations/unapproved/extra.txt'])
def test_existing_destination_requires_exact_inventory(candidate, extra):
    root, _, _, _ = candidate
    execute(candidate)
    path = root / '.agents' / 'QuietHelm' / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('fixture contamination')
    with pytest.raises(StoreError, match='inventory mismatch'):
        execute(candidate)


def test_settings_read_hash_race_is_archive_only(tmp_path, monkeypatch):
    source = conversation(tmp_path)
    original = policy._object
    def race(path, **kwargs):
        result = original(path, **kwargs)
        if path == source / 'settings.json':
            path.write_text(json.dumps({**result, 'model': 'changed-after-read'}))
        return result
    monkeypatch.setattr(policy, '_object', race)
    result = policy.named_plan(tmp_path, names=NAMES)
    assert result['counts']['candidate_agents'] == 0
    assert any('Settings changed' in reason for reason in result['reason_counts'])
