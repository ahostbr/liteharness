"""Final narrow blockers: registry enumeration and exact audit receipt verification."""
import json
from pathlib import Path

import pytest

from liteharness import agent_migration_copy as copy_tool
from liteharness.agent_store import StoreError, read_agent
from test_agent_migration_copy import candidate as candidate_fixture, execute

candidate = candidate_fixture


@pytest.mark.parametrize('directory', ['agents', 'retired-agents'])
def test_existing_registry_scan_denied_is_not_proven_absent(candidate, monkeypatch, directory):
    root, registry, _, _ = candidate
    denied = registry / directory
    denied.mkdir(exist_ok=True)
    original = Path.iterdir
    def scan(path):
        if path == denied:
            raise PermissionError('fixture registry scan denied')
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', scan)
    with pytest.raises(StoreError, match='enumeration unavailable'):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


def test_registry_partial_scan_error_not_accepted_as_complete(candidate, monkeypatch):
    root, registry, _, _ = candidate
    original = Path.iterdir
    def scan(path):
        if path == registry / 'agents':
            def partial():
                yield path / 'unrelated.txt'
                raise PermissionError('fixture partial scan denied')
            return partial()
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', scan)
    with pytest.raises(StoreError, match='enumeration unavailable'):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


def test_postrename_receipt_tamper_defers_with_marker_retained(candidate, monkeypatch):
    root, _, _, _ = candidate
    original = Path.rename
    def tamper(path, target):
        result = original(path, target)
        receipt = target / '.migration-receipt.json'
        row = json.loads(receipt.read_text())
        row['agent_id'] = 'tampered'
        receipt.write_text(json.dumps(row))
        return result
    monkeypatch.setattr(Path, 'rename', tamper)
    with pytest.raises(StoreError, match='receipt mismatch'):
        execute(candidate)
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(root / '.agents' / 'QuietHelm')


def test_staging_receipt_readback_tamper_defers_before_publication(candidate, monkeypatch):
    root, _, _, _ = candidate
    original = copy_tool._write_once_json
    def tamper(path, value):
        original(path, value)
        if path.name == '.migration-receipt.json':
            path.write_text(json.dumps({**value, 'settings_source': 'tampered'}))
    monkeypatch.setattr(copy_tool, '_write_once_json', tamper)
    with pytest.raises(StoreError, match='receipt mismatch'):
        execute(candidate)
    assert not (root / '.agents' / 'QuietHelm').exists()
    assert list((root / '.agents-migration-staging').rglob('.agent.initializing'))
