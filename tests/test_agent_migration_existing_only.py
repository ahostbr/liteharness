"""Verification-only COPY seam: never creates when an outer existence check races."""
import pytest

from liteharness import agent_migration_copy as copy_tool
from liteharness.agent_store import StoreError
from test_agent_migration import AID, snapshot
from test_agent_migration_copy import candidate as candidate_fixture, execute

candidate = candidate_fixture


def verify(candidate, **kwargs):
    _, registry, manifest, receipt = candidate
    return copy_tool.copy_named_agent(manifest, agent_id=AID, registry_root=registry,
        merge_receipt=receipt, existing_only=kwargs.get('existing_only', True))


def forbid_writes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('existing-only verification attempted a write')
    for name in ('_copy_file', '_write_once_json'):
        monkeypatch.setattr(copy_tool, name, forbidden)
    monkeypatch.setattr(copy_tool.Path, 'mkdir', forbidden)
    monkeypatch.setattr(copy_tool.Path, 'rename', forbidden)
    original = copy_tool.Path.open
    def readonly(path, mode='r', *args, **kwargs):
        if any(flag in mode for flag in 'wax+'):
            forbidden()
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(copy_tool.Path, 'open', readonly)


@pytest.mark.parametrize('state', ['initially-absent', 'removed-after-outer-check'])
def test_existing_only_missing_destination_never_creates(candidate, monkeypatch, state):
    root, _, _, _ = candidate
    target = root / '.agents' / 'QuietHelm'
    if state == 'removed-after-outer-check':
        execute(candidate)
        assert target.exists()  # caller's outer check cannot authorize recreation
        target.rename(root / 'preserved-fixture-copy')  # fixture only, no deletion
    before_sources = snapshot(root / '.convos')
    before_tree = snapshot(root)
    forbid_writes(monkeypatch)
    with pytest.raises(StoreError, match='verification cannot create'):
        verify(candidate)
    assert not target.exists()
    assert snapshot(root) == before_tree
    assert snapshot(root / '.convos') == before_sources


def test_existing_only_valid_destination_verifies_without_writes(candidate, monkeypatch):
    root, _, _, _ = candidate
    expected = execute(candidate)
    before = snapshot(root)
    forbid_writes(monkeypatch)
    assert verify(candidate) == expected
    assert snapshot(root) == before
    assert (root / '.agents' / 'QuietHelm' / '.agent.initializing').exists()


@pytest.mark.parametrize('value', [None, 0, 1, 'true'])
def test_existing_only_requires_actual_boolean_before_observation(candidate, monkeypatch, value):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid flag reached observation')
    monkeypatch.setattr(copy_tool, '_offline', forbidden)
    with pytest.raises(StoreError, match='must be a boolean'):
        verify(candidate, existing_only=value)
