"""COPY safety ruling: source retained; snapshots, not launcher maintenance."""

import pytest

from liteharness import agent_migration_copy as copy_tool
from liteharness.agent_store import StoreError, read_agent
from test_agent_migration import AID, CID
from test_agent_migration_copy import candidate as candidate_fixture, execute

candidate = candidate_fixture


def absent(candidate):
    _, registry, _, _ = candidate
    (registry / 'agents' / (AID + '.json')).unlink()  # fixture only


def test_proven_absent_named_agent_requires_exact_pty_and_full_hash_proof(candidate):
    root, _, _, _ = candidate
    absent(candidate)
    result = execute(candidate)
    assert result['quiescence']['registry_proven_absent']
    assert result['quiescence']['no_exact_live_pty']
    assert 'no_live_pid' not in result['quiescence']
    assert result['before_after_destination_equal'] and result['status'] == 'copied'
    assert not result['activated'] and result['copy_inactive']
    assert (root / '.agents' / 'QuietHelm' / '.agent.initializing').is_file()
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(root / '.agents' / 'QuietHelm')


def test_source_change_during_copy_never_promotes_or_returns_copied(candidate, monkeypatch):
    root, _, _, _ = candidate
    original = copy_tool._copy_file
    changed = False
    def race(source, target, fact):
        nonlocal changed
        original(source, target, fact)
        if not changed:
            changed = True
            (root / '.convos' / CID / 'unknown.bin').write_bytes(b'new legacy bytes retained')
    monkeypatch.setattr(copy_tool, '_copy_file', race)
    with pytest.raises(StoreError):
        execute(candidate)
    assert not (root / '.agents' / 'QuietHelm').exists()
    assert (root / '.convos' / CID / 'unknown.bin').read_bytes() == b'new legacy bytes retained'
    assert list((root / '.agents-migration-staging').rglob('.agent.initializing'))


def test_source_change_after_destination_rename_stays_inactive_and_deferred(candidate, monkeypatch):
    root, _, _, _ = candidate
    original = copy_tool.Path.rename
    def race(path, target):
        result = original(path, target)
        (root / '.convos' / CID / 'unknown.bin').write_bytes(b'late legacy write retained')
        return result
    monkeypatch.setattr(copy_tool.Path, 'rename', race)
    with pytest.raises(StoreError, match='changed'):
        execute(candidate)
    assert (root / '.convos' / CID / 'unknown.bin').read_bytes() == b'late legacy write retained'
    with pytest.raises(StoreError, match='incomplete'):
        read_agent(root / '.agents' / 'QuietHelm')
