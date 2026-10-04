"""Post-publication index/collision checks, without historical sibling inference."""
import json

import pytest

from liteharness import agent_migration_copy as copy_tool
from liteharness.agent_store import StoreError, read_agent
from test_agent_migration import CID, DID
from test_agent_migration_copy import candidate as candidate_fixture, execute
from test_agent_migration_policy import NAMES, conversation

candidate = candidate_fixture


@pytest.mark.parametrize('sibling', ['same-agent', 'unknown-owner', 'empty-directory'])
def test_after_rename_unselected_sibling_does_not_join_copy(candidate, monkeypatch, sibling):
    root, _, _, _ = candidate
    original = copy_tool.Path.rename
    def late(path, target):
        result = original(path, target)
        if sibling == 'same-agent':
            conversation(root, DID, ts=30)
        elif sibling == 'unknown-owner':
            directory = root / '.convos' / DID
            directory.mkdir()
            (directory / 'settings.json').write_text('{')
        else:
            (root / '.convos' / DID).mkdir()
        return result
    monkeypatch.setattr(copy_tool.Path, 'rename', late)
    result = execute(candidate)
    assert result['status'] == 'copied' and result['conversation_count'] == 1
    assert not (root / '.agents' / 'QuietHelm' / 'conversations' / DID).exists()
    assert (root / '.convos' / DID).exists()
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(root / '.agents' / 'QuietHelm')


@pytest.mark.parametrize('timing', ['before-copy', 'before-rename', 'after-rename'])
def test_index_rewire_defers_before_or_after_publication(candidate, monkeypatch, timing):
    root, registry, _, _ = candidate
    conversation(root, DID, ts=30)
    def rewire():
        (registry / 'names.json').write_text(json.dumps({
            'QuietHelm': {**NAMES['QuietHelm'], 'convo_id': DID}}))
    if timing == 'before-copy':
        rewire()
    elif timing == 'before-rename':
        original = copy_tool._offline
        calls = 0
        def late(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                rewire()
            return result
        monkeypatch.setattr(copy_tool, '_offline', late)
    else:
        original = copy_tool.Path.rename
        def late(path, target):
            result = original(path, target)
            rewire()
            return result
        monkeypatch.setattr(copy_tool.Path, 'rename', late)
    with pytest.raises(StoreError):
        execute(candidate)
    if timing == 'after-rename':
        with pytest.raises(StoreError, match='incomplete'):
            read_agent(root / '.agents' / 'QuietHelm')
    else:
        assert not (root / '.agents' / 'QuietHelm').exists()
    assert (root / '.convos' / CID).exists()


def test_after_rename_known_historical_name_id_collision_defers(candidate, monkeypatch):
    root, _, _, _ = candidate
    original = copy_tool.Path.rename
    def late(path, target):
        result = original(path, target)
        directory = conversation(root, DID)
        settings = json.loads((directory / 'settings.json').read_text())
        (directory / 'settings.json').write_text(json.dumps({**settings, 'seat_id': DID}))
        return result
    monkeypatch.setattr(copy_tool.Path, 'rename', late)
    with pytest.raises(StoreError, match='policy'):
        execute(candidate)
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(root / '.agents' / 'QuietHelm')


def test_readable_unrelated_archive_change_does_not_join_selected_agent(candidate, monkeypatch):
    root, _, _, _ = candidate
    directory = root / '.convos' / DID
    directory.mkdir()
    (directory / 'settings.json').write_text(json.dumps({'seat_id': DID, 'seat_name': 'OtherSeat'}))
    original = copy_tool.Path.rename
    def late(path, target):
        result = original(path, target)
        (directory / 'unrelated.bin').write_bytes(b'not selected agent lineage')
        return result
    monkeypatch.setattr(copy_tool.Path, 'rename', late)
    result = execute(candidate)
    assert result['status'] == 'copied' and result['source_unchanged']
    assert result['conversation_count'] == 1
