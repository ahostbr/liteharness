"""Superseding COPY eligibility: exact bridge identity, not OS writer/module proof."""
import json

import pytest

from liteharness import cli
from liteharness.agent_store import StoreError
from test_agent_migration import AID, DID
from test_agent_migration_copy import candidate as candidate_fixture, execute

candidate = candidate_fixture


@pytest.mark.parametrize('status', ['offline', 'retired', 'absent', 'retired-folder'])
def test_offline_retired_or_proven_absent_with_no_exact_pty_eligible(candidate, monkeypatch, status):
    root, registry, _, _ = candidate
    path = registry / 'agents' / (AID + '.json')
    if status == 'absent':
        path.unlink()  # fixture only
    elif status == 'retired-folder':
        path.unlink()
        (registry / 'retired-agents').mkdir()
        (registry / 'retired-agents' / (AID + '.json')).write_text(json.dumps({'agent_id': AID, 'name': 'QuietHelm'}))
    else:
        path.write_text(json.dumps({'agent_id': AID, 'name': 'QuietHelm', 'status': status}))
    calls = []
    def bridge(method, route):
        calls.append((method, route))
        return {'sessions': [{'id': 'other-live-pty', 'pid': 123, 'harnessAgentId': DID}]}
    monkeypatch.setattr(cli, '_bridge_request', bridge)
    result = execute(candidate)
    assert result['status'] == 'copied' and not result['activated']
    assert result['quiescence']['no_exact_live_pty']
    assert 'no_live_pid' not in result['quiescence']
    assert calls and set(calls) == {('GET', '/pty/list')}
    assert (root / '.agents' / 'QuietHelm' / '.agent.initializing').is_file()


@pytest.mark.parametrize('row_status', ['active', 'online', 'unknown', None])
def test_live_or_unknown_registry_status_refuses_before_copy(candidate, row_status):
    root, registry, _, _ = candidate
    (registry / 'agents' / (AID + '.json')).write_text(json.dumps({'agent_id': AID,
        'name': 'QuietHelm', 'status': row_status}))
    with pytest.raises(StoreError, match='live/unknown|not explicitly'):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


def test_exact_live_pty_refuses_even_if_registry_offline(candidate, monkeypatch):
    root, _, _, _ = candidate
    monkeypatch.setattr(cli, '_bridge_request', lambda *a: {'sessions': [
        {'id': 'exact-live', 'pid': 123, 'harnessAgentId': AID}]})
    with pytest.raises(StoreError, match='Exact live PTY'):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


@pytest.mark.parametrize('response', [None, {}, {'error': 'unavailable'}, {'ok': False, 'sessions': []},
    {'sessions': None}, {'sessions': {}}, {'sessions': [None]},
    {'sessions': [{'id': 'pty', 'pid': None}]}, {'sessions': [{'id': '', 'pid': 123}]},
    {'sessions': [{'id': 'pty', 'pid': 123, 'harnessAgentId': 'malformed'}]},
    {'sessions': [{'id': 'pty', 'pid': 123}, {'id': 'pty', 'pid': 456}]},
])
def test_bridge_unavailable_or_malformed_never_means_no_pty(candidate, monkeypatch, response):
    root, _, _, _ = candidate
    monkeypatch.setattr(cli, '_bridge_request', lambda *a: response)
    with pytest.raises(StoreError):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()


def test_other_pty_aliases_not_exact_agent_identity_claim(candidate, monkeypatch):
    _, _, _, _ = candidate
    monkeypatch.setattr(cli, '_bridge_request', lambda *a: {'sessions': [
        {'id': 'other', 'pid': 123, 'name': 'QuietHelm', 'agent_id': AID, 'paneId': AID}]})
    result = execute(candidate)
    assert result['quiescence']['no_exact_live_pty']
    assert result['quiescence']['bridge_rows_without_agent_identity'] == 1
    assert 'not absence of all OS writers' in result['quiescence']['caveat']


def test_uninspectable_registry_is_not_proven_absent(candidate):
    root, registry, _, _ = candidate
    (registry / 'agents' / (AID + '.json')).unlink()  # fixture only
    (registry / 'agents').rmdir()
    with pytest.raises(StoreError, match='cannot be inspected'):
        execute(candidate)
    assert not (root / '.agents-migration-staging').exists()
