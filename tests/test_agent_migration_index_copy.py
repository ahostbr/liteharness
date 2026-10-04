"""COPY v2 selected-source safety and explicit unselected archive retention."""
import json

import pytest

from liteharness import agent_migration_copy as copy_tool
from liteharness.agent_migration_policy import named_plan
from liteharness.agent_store import StoreError, read_agent
from test_agent_migration import CID, DID, snapshot
from test_agent_migration_copy import candidate as candidate_fixture, execute
from test_agent_migration_policy import NAMES, conversation

candidate = candidate_fixture


@pytest.mark.parametrize('kind', ['same-agent', 'unknown'])
def test_only_indexed_source_copied_unselected_bytes_untouched(candidate, kind):
    root, _, _, receipt = candidate
    if kind == 'same-agent':
        conversation(root, DID, ts=99, memory=b'unselected memory')
    else:
        sibling = root / '.convos' / DID
        sibling.mkdir()
        (sibling / 'opaque.bin').write_bytes(b'unknown source')
    manifest = named_plan(root, names=NAMES)
    receipt['plan_digest'] = manifest['plan_digest']
    before = snapshot(root / '.convos')
    result = execute((root, candidate[1], manifest, receipt))
    target = root / '.agents' / 'QuietHelm'
    assert result['conversation_count'] == 1
    assert set(result['source_files']) == {CID}
    assert sorted(p.name for p in (target / 'conversations').iterdir()) == [CID]
    assert snapshot(root / '.convos') == before
    actual_receipt = json.loads((target / '.migration-receipt.json').read_text())
    assert actual_receipt['policy'] == copy_tool.POLICY
    assert actual_receipt['index_selection'] == {'convo_id': CID}
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(target)


@pytest.mark.parametrize('change', ['bytes', 'empty-directory', 'settings'])
def test_after_rename_selected_mutation_never_returns_copied(candidate, monkeypatch, change):
    root, _, _, _ = candidate
    original = copy_tool.Path.rename
    def late(path, target):
        result = original(path, target)
        directory = root / '.convos' / CID
        if change == 'bytes':
            (directory / 'unknown.bin').write_bytes(b'late selected change')
        elif change == 'empty-directory':
            (directory / 'late-empty').mkdir()
        else:
            settings = json.loads((directory / 'settings.json').read_text())
            (directory / 'settings.json').write_text(json.dumps({**settings, 'model': 'late'}))
        return result
    monkeypatch.setattr(copy_tool.Path, 'rename', late)
    with pytest.raises(StoreError, match='changed'):
        execute(candidate)
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(root / '.agents' / 'QuietHelm')


@pytest.mark.parametrize('change', ['receipt', 'conversation-bytes', 'marker'])
def test_after_rename_destination_tampering_never_returns_copied(candidate, monkeypatch, change):
    root, _, _, _ = candidate
    original = copy_tool.Path.rename
    def late(path, target):
        result = original(path, target)
        if change == 'receipt':
            receipt_path = target / '.migration-receipt.json'
            receipt = json.loads(receipt_path.read_text())
            receipt['index_selection'] = {'convo_id': DID}
            receipt_path.write_text(json.dumps(receipt))
        elif change == 'conversation-bytes':
            (target / 'conversations' / CID / 'unknown.bin').write_bytes(b'corrupted')
        else:
            (target / '.agent.initializing').unlink()  # adversarial fixture only
        return result
    monkeypatch.setattr(copy_tool.Path, 'rename', late)
    before = snapshot(root / '.convos')
    with pytest.raises(StoreError):
        execute(candidate)
    assert snapshot(root / '.convos') == before
