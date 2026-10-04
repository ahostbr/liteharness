"""Exact singular indexed-conversation policy; no inferred historical lineage."""
import json

import pytest

from liteharness.agent_migration_policy import CANDIDATE, LEFT_LEGACY, POLICY, named_plan
from test_agent_migration import AID, CID, DID, legacy

NAMES = {'QuietHelm': {'agent_id': AID, 'convo_id': CID}}


def conversation(root, cid=CID, ts=10, memory=b'original memory'):
    source = legacy(root, cid, memory)
    settings = json.loads((source / 'settings.json').read_text())
    settings['seat_name'] = 'QuietHelm'
    (source / 'settings.json').write_text(json.dumps(settings))
    (source / 'convo.jsonl').write_text(json.dumps({'type': 'meta', 'id': cid, 'agent_id': AID, 'created': 1}) + '\n' + json.dumps({'type': 'msg', 'ts': ts}) + '\n')
    return source


def test_exact_index_selection_not_latest_event_or_mtime(tmp_path):
    first = conversation(tmp_path, ts=10)
    second = conversation(tmp_path, DID, ts=30, memory=b'unselected differing memory')
    settings = json.loads((first / 'settings.json').read_text())
    settings['model'] = 'index-selected-model'
    (first / 'settings.json').write_text(json.dumps(settings))
    (second / 'convo.jsonl').touch()
    result = named_plan(tmp_path, names=NAMES)
    assert result['policy'] == POLICY
    assert result['counts']['candidate_agents'] == result['counts']['candidate_conversations'] == 1
    agent = result['agents'][0]
    assert agent['settings_source'] == CID and agent['conversations'] == [CID]
    assert agent['index_selection'] == {'convo_id': CID}
    assert agent['selected_settings_snapshot']['model'] == 'index-selected-model'
    assert not agent['memory_conflict']
    assert result['left_in_legacy'][0]['source'] == DID
    assert not (tmp_path / '.agents').exists()


def test_unindexed_no_generated_agent_and_sources_unchanged(tmp_path):
    source = conversation(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    result = named_plan(tmp_path, names={})
    assert result['agents'] == [] and result['counts']['archive_only'] == 1
    assert result['left_in_legacy'][0]['status'] == LEFT_LEGACY
    assert {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()} == before


def test_unknown_sibling_never_blocks_exact_named_selection(tmp_path):
    conversation(tmp_path)
    unknown = tmp_path / '.convos' / DID
    unknown.mkdir()
    (unknown / 'opaque.bin').write_bytes(b'unknown ownership left untouched')
    result = named_plan(tmp_path, names=NAMES)
    assert result['agents'][0]['disposition'] == CANDIDATE
    assert result['counts']['candidate_conversations'] == 1
    assert result['left_in_legacy'][0]['source'] == DID
    assert result['left_in_legacy'][0]['reasons']


def test_unknown_selected_target_defers_only_its_agent(tmp_path):
    conversation(tmp_path)
    unknown = tmp_path / '.convos' / DID
    unknown.mkdir()
    names = {**NAMES, 'KindGrid': {'agent_id': DID, 'convo_id': DID}}
    result = named_plan(tmp_path, names=names)
    rows = {a['name']: a for a in result['agents']}
    assert rows['QuietHelm']['disposition'] == CANDIDATE
    assert rows['KindGrid']['disposition'] == 'archive-only'
    assert rows['KindGrid']['reasons']


def test_selected_missing_timestamp_or_execution_still_deferred(tmp_path):
    directory = conversation(tmp_path)
    (directory / 'convo.jsonl').write_text(json.dumps({'type': 'msg'}) + '\n')
    result = named_plan(tmp_path, names=NAMES)
    assert result['counts']['candidate_agents'] == 0
    assert any('timestamp missing' in reason for reason in result['agents'][0]['reasons'])


def test_old_same_name_distinct_id_collision_still_deferred(tmp_path):
    conversation(tmp_path)
    other = conversation(tmp_path, DID, ts=20)
    settings = json.loads((other / 'settings.json').read_text())
    settings['seat_id'] = DID
    (other / 'settings.json').write_text(json.dumps(settings))
    result = named_plan(tmp_path, names=NAMES)
    assert result['counts']['candidate_agents'] == 0
    assert any('lineage collides' in r for r in result['reason_counts'])


@pytest.mark.parametrize('state', ['folder-missing', 'settings-missing', 'id-mismatch', 'transcript-id', 'execution'])
def test_selected_unsafe_source_has_explicit_named_deferral(tmp_path, state):
    conversation(tmp_path)
    names = {**NAMES, 'KindGrid': {'agent_id': DID, 'convo_id': DID}}
    if state != 'folder-missing':
        selected = conversation(tmp_path, DID)
        settings = json.loads((selected / 'settings.json').read_text())
        settings['seat_name'] = 'KindGrid'
        settings['seat_id'] = DID
        if state == 'id-mismatch':
            settings['seat_id'] = AID
        if state == 'execution':
            settings.pop('backend', None)
        (selected / 'settings.json').write_text(json.dumps(settings))
        (selected / 'convo.jsonl').write_text(json.dumps({
            'type': 'meta', 'id': CID if state == 'transcript-id' else DID,
            'agent_id': DID, 'created': 1}) + '\n')
        if state == 'settings-missing':
            (selected / 'settings.json').unlink()  # fixture only
    result = named_plan(tmp_path, names=names)
    rows = {a['name']: a for a in result['agents']}
    assert rows['QuietHelm']['disposition'] == CANDIDATE
    assert rows['KindGrid']['disposition'] == 'archive-only'
    record = next(r for r in result['left_in_legacy'] if r.get('name') == 'KindGrid')
    assert record['kind'] == 'named-deferral' and record['status'] == LEFT_LEGACY
    assert record['indexed_convo_id'] == DID and record['reasons']
    assert 'source' not in record  # catalog claim, not fabricated ownership


@pytest.mark.parametrize('collision', ['casefold-name', 'agent-id', 'convo-id', 'missing-pointer', 'invalid-name', 'invalid-agent'])
def test_shared_index_associations_fail_closed_even_partial_rows(tmp_path, collision):
    conversation(tmp_path)
    alias_name = 'quiethelm' if collision == 'casefold-name' else 'Bad/Name' if collision == 'invalid-name' else 'Alias'
    alias = {'agent_id': DID, 'convo_id': DID}
    if collision in ('agent-id', 'missing-pointer', 'invalid-name'):
        alias['agent_id'] = AID
    if collision in ('convo-id', 'invalid-agent'):
        alias['convo_id'] = CID
    if collision == 'missing-pointer':
        alias.pop('convo_id')
    if collision == 'invalid-agent':
        alias['agent_id'] = 'not-a-uuid'
    result = named_plan(tmp_path, names={**NAMES, alias_name: alias})
    row = next(a for a in result['agents'] if a['name'] == 'QuietHelm')
    assert row['disposition'] == 'archive-only'
    assert 'Name index identity/name/conversation collision' in row['reasons']


def test_selected_reparse_fails_closed_without_following_target(tmp_path):
    conversation(tmp_path)
    target = tmp_path / 'external'
    target.mkdir()
    link = tmp_path / '.convos' / CID / 'linked'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'OS does not permit fixture symlink: {exc}')
    result = named_plan(tmp_path, names=NAMES)
    assert result['counts']['candidate_agents'] == 0
    assert result['agents'][0]['reasons']
