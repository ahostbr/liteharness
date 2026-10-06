"""Actual temporary COPY->ACTIVATE path; never a live/operator store."""
import os

import pytest

from liteharness import agent_activation as activation
from liteharness.agent_migration import digest
from liteharness.agent_store import AgentStore, StoreError
from liteharness.agent_ownership import AgentSession
from test_agent_migration_copy import candidate, execute
from test_agent_migration import AID, CID, snapshot


def approval(manifest, copy_receipt):
    return {'operation': 'activate-verified-copy', 'agent_id': AID,
            'plan_digest': manifest['plan_digest'], 'copy_approval_digest': digest(copy_receipt),
            'authority': 'fixture operator', 'merged_commit': 'b' * 40,
            'review_evidence': 'fixture reviewed activation'}


def activate(candidate):
    root, registry, manifest, copy_receipt = candidate
    return activation.activate(manifest, agent_id=AID, registry_root=registry,
        copy_approval=copy_receipt, activation_approval=approval(manifest, copy_receipt))


@pytest.mark.skipif(os.name != 'nt', reason='COPY exclusive publication requires Windows')
def test_verified_copy_activates_then_owned_launch_consumes(candidate):
    root, _, _, _ = candidate
    before = snapshot(root / '.convos')
    execute(candidate)
    assert AgentStore(root).list_agents() == []
    result = activate(candidate)
    assert result['status'] == 'activated'
    with AgentSession.acquire_existing(AgentStore(root), name='QuietHelm') as owned:
        assert owned.authority.agent_id == AID
        assert owned.authority.model == 'fixture'
        assert owned.conversation_directory(CID).is_dir()
        (owned.memory_root / 'memory.md').write_text('new owned memory')
    assert snapshot(root / '.convos') == before


@pytest.mark.skipif(os.name != 'nt', reason='COPY exclusive publication requires Windows')
@pytest.mark.parametrize('change', ['source', 'destination', 'receipt', 'extra', 'live'])
def test_stale_partial_foreign_or_live_copy_never_activates(candidate, monkeypatch, change):
    root, registry, manifest, copy_receipt = candidate
    execute(candidate)
    home = root / '.agents' / 'QuietHelm'
    if change == 'source':
        (root / '.convos' / CID / 'unknown.bin').write_bytes(b'late write')
    elif change == 'destination':
        (home / 'conversations' / CID / 'unknown.bin').write_bytes(b'foreign changed copy')
    elif change == 'receipt':
        (home / '.migration-receipt.json').write_text('{}')
    elif change == 'extra':
        (home / 'foreign-extra').write_bytes(b'not silently ignored')
    else:
        from liteharness import cli
        monkeypatch.setattr(cli, '_bridge_request', lambda *a: {'sessions': [
            {'id': 'live', 'pid': 123, 'harnessAgentId': AID}]})
    before = snapshot(home)
    with pytest.raises(StoreError):
        activate(candidate)
    assert snapshot(home) == before
    assert (home / '.agent.initializing').is_file()
    assert not (home / '.activation-receipt.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='COPY exclusive publication requires Windows')
def test_final_boundary_mutation_keeps_blocking_marker_and_exact_lease(candidate, monkeypatch):
    root, _, _, _ = candidate
    execute(candidate)
    home = root / '.agents' / 'QuietHelm'
    original = activation._offline
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (home / 'memory.md').write_text('changed at final external boundary')
        return result
    monkeypatch.setattr(activation, '_offline', changed)
    with pytest.raises(StoreError, match='final activation boundary'):
        activate(candidate)
    assert (home / '.agent.initializing').exists()
    assert not (home / '.activation-receipt.json').exists()
    with activation._KernelLease(home / '.agent.lease'):
        pass
    with pytest.raises(StoreError, match='Prior activation evidence'):
        activate(candidate)


@pytest.mark.skipif(os.name != 'nt', reason='COPY exclusive publication requires Windows')
def test_destination_disappearing_before_copy_verification_is_not_recreated(candidate, monkeypatch):
    root, _, _, _ = candidate
    execute(candidate)
    home = root / '.agents' / 'QuietHelm'
    displaced = root / 'displaced-fixture-copy'
    before_source = snapshot(root / '.convos')
    before_copy = snapshot(home)
    original = activation.copy_named_agent

    def missing(*args, **kwargs):
        assert kwargs['existing_only'] is True
        home.rename(displaced)
        return original(*args, **kwargs)

    monkeypatch.setattr(activation, 'copy_named_agent', missing)
    with pytest.raises(StoreError):
        activate(candidate)
    assert not home.exists()
    assert snapshot(displaced) == before_copy
    assert snapshot(root / '.convos') == before_source
    assert (displaced / '.agent.initializing').is_file()
    assert not (displaced / '.activation-receipt.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='COPY exclusive publication requires Windows')
def test_receipt_publication_mutation_keeps_copy_inactive(candidate, monkeypatch):
    root, _, _, _ = candidate
    execute(candidate)
    home = root / '.agents' / 'QuietHelm'
    original = activation._write_once_json

    def changed(path, value):
        result = original(path, value)
        if path.name == activation.ACTIVATION_RECEIPT:
            (home / 'memory.md').write_text('changed during activation receipt publication')
        return result

    monkeypatch.setattr(activation, '_write_once_json', changed)
    with pytest.raises(StoreError, match='receipt publication'):
        activate(candidate)
    assert (home / '.agent.initializing').is_file()
    assert (home / '.activation-receipt.json').is_file()
    with pytest.raises(StoreError, match='Prior activation evidence'):
        activate(candidate)


def test_no_activation_receipt_blocks_without_copy_or_writes(candidate, monkeypatch):
    root, registry, manifest, copy_receipt = candidate
    monkeypatch.setattr(activation, 'copy_named_agent', lambda *a, **k: pytest.fail('copy before approval'))
    with pytest.raises(StoreError, match='approval'):
        activation.activate(manifest, agent_id=AID, registry_root=registry,
                            copy_approval=copy_receipt, activation_approval={})
    assert not (root / '.agents').exists()
