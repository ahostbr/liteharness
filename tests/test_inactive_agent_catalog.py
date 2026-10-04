"""Inactive COPY identity reserves collisions without disabling unrelated ready agents."""
import json

import pytest

from liteharness.agent_store import AgentStore, StoreError, read_agent

AID = '11111111-1111-4111-8111-111111111111'
BID = '22222222-2222-4222-8222-222222222222'


def folder(root, name, identity, *, inactive=False):
    directory = root / '.agents' / name
    directory.mkdir(parents=True)
    (directory / 'settings.json').write_text(json.dumps({'schema_version': 1, 'name': name,
        'agent_id': identity, 'execution': {'backend': 'codex', 'model': 'fixture', 'thinking_level': 'high'}}))
    if inactive:
        (directory / '.agent.initializing').write_text('Copy inactive; do not activate')
    return directory


def test_unrelated_inactive_sibling_reserves_identity_but_ready_seat_still_resolves(tmp_path):
    ready = folder(tmp_path, 'ReadySeat', AID)
    copied = folder(tmp_path, 'CopiedSeat', BID, inactive=True)
    store = AgentStore(tmp_path)
    assert [agent.name for agent in store.list_agents()] == ['ReadySeat']
    assert store.find_agent(agent_id=AID).directory == ready
    assert store.find_agent(name='ReadySeat').directory == ready
    with pytest.raises(StoreError, match='absent'):
        store.find_agent(name='CopiedSeat')
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(copied)
    assert (copied / '.agent.initializing').is_file()


def test_inactive_ready_same_id_collision_still_failclosed(tmp_path):
    folder(tmp_path, 'ReadySeat', AID)
    folder(tmp_path, 'CopiedSeat', AID, inactive=True)
    with pytest.raises(StoreError, match='ambiguous'):
        AgentStore(tmp_path).find_agent(name='ReadySeat')


def test_inactive_ready_metadata_name_conflict_not_silently_skipped(tmp_path):
    folder(tmp_path, 'ReadySeat', AID)
    copied = folder(tmp_path, 'CopiedSeat', BID, inactive=True)
    settings = json.loads((copied / 'settings.json').read_text())
    settings['name'] = 'ReadySeat'
    (copied / 'settings.json').write_text(json.dumps(settings))
    with pytest.raises(StoreError, match='disagrees'):
        AgentStore(tmp_path).find_agent(name='ReadySeat')


@pytest.mark.parametrize('metadata', ['missing', 'malformed', 'invalid-id', 'invalid-execution'])
def test_unreadable_or_invalid_inactive_metadata_defers_catalog(tmp_path, metadata):
    folder(tmp_path, 'ReadySeat', AID)
    copied = folder(tmp_path, 'CopiedSeat', BID, inactive=True)
    path = copied / 'settings.json'
    if metadata == 'missing':
        path.unlink()  # fixture only
    elif metadata == 'malformed':
        path.write_text('{')
    else:
        settings = json.loads(path.read_text())
        if metadata == 'invalid-id':
            settings['agent_id'] = 'not-an-id'
        else:
            settings['execution'] = {}
        path.write_text(json.dumps(settings))
    with pytest.raises(StoreError):
        AgentStore(tmp_path).find_agent(name='ReadySeat')
