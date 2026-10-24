"""Checkpoint location assertions attenuate the existing native recovery path."""
import json

import pytest

from liteharness import cli, recovered_destination

REQUIRED = {'version': 1, 'agentId': 'seat', 'workspace': 'ws', 'thread': 'th',
            'paneId': 'pane', 'leafId': 'leaf', 'placeholderSessionId': 'old'}
LOCATED = {'ok': True, 'found': True, 'contractVersion': 3, 'agentId': 'seat',
           'workspace': 'ws', 'thread': 'th', 'paneId': 'pane', 'leafId': 'leaf',
           'sessionId': 'old', 'slotId': 'slot', 'requestId': 'request', 'cwd': '/work'}
BOUND = {'ok': True, 'status': 'bound', 'agentId': 'seat', 'paneId': 'pane',
         'leafId': 'leaf', 'slotId': 'slot', 'requestId': 'request', 'sessionId': 'new'}


def boundary(monkeypatch, located=None, response=None):
    calls = []
    monkeypatch.setenv('LITEHARNESS_REQUIRED_RECOVERY_SLOT', json.dumps(REQUIRED))
    def bridge(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        return dict(LOCATED if located is None else located) if method == 'GET' else dict(BOUND if response is None else response)
    monkeypatch.setattr(cli, '_bridge_request', bridge)
    return calls


@pytest.mark.parametrize('changed', ['absent', 'older-host', 'agentId', 'workspace', 'thread', 'paneId', 'leafId', 'sessionId'])
def test_saved_reservation_must_match_before_any_native_side_effect(monkeypatch, changed):
    located = dict(LOCATED)
    if changed == 'absent': located['found'] = False
    elif changed == 'older-host': located['contractVersion'] = 2
    else: located[changed] = 'other'
    calls = boundary(monkeypatch, located)
    with pytest.raises(ValueError):
        recovered_destination.recover_if_reserved('seat', 'actor')
    assert [c[0] for c in calls] == ['GET']


def test_exact_constraint_is_forwarded_to_the_native_resolver(monkeypatch):
    calls = boundary(monkeypatch)
    assert recovered_destination.recover_if_reserved('seat', 'actor')
    assert calls[-1][2]['expectedSlot'] == {k: v for k, v in REQUIRED.items() if k != 'version'}


@pytest.mark.parametrize('field', ['paneId', 'leafId', 'slotId', 'requestId'])
def test_wrong_bound_receipt_is_unknown_and_is_never_retried(monkeypatch, field):
    calls = boundary(monkeypatch, response={**BOUND, field: 'wrong'})
    with pytest.raises(ValueError):
        recovered_destination.recover_if_reserved('seat', 'actor')
    assert [c[0] for c in calls] == ['GET', 'POST']


def test_partial_native_receipt_never_retries_or_falls_back(monkeypatch):
    calls = boundary(monkeypatch, response={**BOUND, 'ok': False, 'status': 'unknown'})
    with pytest.raises(ValueError):
        recovered_destination.recover_if_reserved('seat', 'actor')
    assert [c[0] for c in calls] == ['GET', 'POST']


@pytest.mark.parametrize('required', [None, [], {}, {**REQUIRED, 'version': True},
    {**REQUIRED, 'version': 2}, {**REQUIRED, 'agentId': 'other'},
    {**REQUIRED, 'leafId': ''}, {**REQUIRED, 'thread': None}, {**REQUIRED, 'extra': 'field'}])
def test_malformed_restriction_never_contacts_host(monkeypatch, required):
    calls = boundary(monkeypatch)
    monkeypatch.setenv(recovered_destination.REQUIRED_SLOT_ENV, json.dumps(required))
    with pytest.raises(ValueError):
        recovered_destination.recover_if_reserved('seat', 'actor')
    assert calls == []


def test_invalid_json_restriction_never_contacts_host(monkeypatch):
    calls = boundary(monkeypatch)
    monkeypatch.setenv(recovered_destination.REQUIRED_SLOT_ENV, '{invalid')
    with pytest.raises(ValueError):
        recovered_destination.recover_if_reserved('seat', 'actor')
    assert calls == []


@pytest.mark.parametrize('session', ['old', ' ', 123, ['new']])
def test_exact_receipt_requires_a_new_nonempty_session_id(monkeypatch, session):
    calls = boundary(monkeypatch, response={**BOUND, 'sessionId': session})
    with pytest.raises(ValueError):
        recovered_destination.recover_if_reserved('seat', 'actor')
    assert [c[0] for c in calls] == ['GET', 'POST']


def test_exact_slot_preserves_independent_execution_assertions(monkeypatch):
    calls = boundary(monkeypatch)
    monkeypatch.setattr(recovered_destination, 'prepare', lambda *args: {
        'model': 'exact-model[1m]', 'backend': 'codex', 'thinking_level': 'high',
        'authorityFingerprint': 'execution-authority'})
    assert recovered_destination.recover_if_reserved('seat', 'actor', overrides={
        '--model': 'exact-model[1m]', '--backend': 'codex', '--thinking-level': 'high'})
    assert calls[-1][2]['expectedAuthorityFingerprint'] == 'execution-authority'
    assert calls[-1][2]['expectedSlot'] == {k: v for k, v in REQUIRED.items() if k != 'version'}
