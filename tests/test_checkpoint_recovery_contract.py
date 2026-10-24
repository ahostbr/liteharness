"""Saved model assertions traverse the existing reserved-slot transport, never a split."""
import pytest

from liteharness import cli, recovered_destination


@pytest.mark.parametrize('model,accepted', [('claude-x[1m]', True), ('claude-x', False)])
def test_reserved_slot_accepts_only_an_exact_current_model_assertion(monkeypatch, model, accepted):
    requests = []

    def bridge(method, path, body=None, **kwargs):
        requests.append((path, body))
        if path.startswith('/harness/recovered-agent/locate?'):
            return {'ok': True, 'found': True, 'contractVersion': 2, 'agentId': 'seat', 'paneId': 'pane',
                    'slotId': 'slot', 'leafId': 'leaf', 'cwd': '/work', 'requestId': 'request'}
        assert path == '/harness/recovered-agent/restore'
        return {'ok': True, 'status': 'bound', 'agentId': 'seat', 'sessionId': 'new', 'leafId': 'leaf'}

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    monkeypatch.setattr(recovered_destination, 'prepare', lambda *args: {
        'model': 'claude-x[1m]', 'authorityFingerprint': 'current-authority'})
    call = lambda: recovered_destination.recover_if_reserved(
        'seat', 'actor', requested_cwd='/work', overrides={'--model': model})
    if accepted:
        assert call()
        assert requests[-1][1]['expectedAuthorityFingerprint'] == 'current-authority'
    else:
        with pytest.raises(ValueError, match='disagrees'):
            call()
        assert len(requests) == 1


def test_older_host_refuses_execution_assertion_before_any_mutation(monkeypatch):
    calls = []

    def bridge(method, path, body=None, **kwargs):
        calls.append(method)
        return {'ok': True, 'found': True, 'agentId': 'seat'}

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    with pytest.raises(ValueError, match='Host cannot assert'):
        recovered_destination.recover_if_reserved('seat', 'actor', overrides={'--model': 'claude-x'})
    assert calls == ['GET']


@pytest.mark.parametrize('flag,field', [('--backend', 'backend'), ('--thinking-level', 'thinking_level')])
def test_owned_assertions_cannot_drift_between_local_validation_and_host(monkeypatch, flag, field):
    calls = []

    def bridge(method, path, body=None, **kwargs):
        calls.append(method)
        return {'ok': True, 'found': True, 'contractVersion': 2, 'agentId': 'seat', 'cwd': '/work'}

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    monkeypatch.setattr(recovered_destination, 'prepare', lambda *args: {field: 'changed'})
    with pytest.raises(ValueError, match='disagrees'):
        recovered_destination.recover_if_reserved('seat', 'actor', overrides={flag: 'saved'})
    assert calls == ['GET']


def test_model_assertion_does_not_enable_unsupported_prompt_override(monkeypatch):
    monkeypatch.setattr(cli, '_bridge_request', lambda *a, **kw: {
        'ok': True, 'found': True, 'agentId': 'seat'})
    with pytest.raises(ValueError, match='explicit --prompt'):
        recovered_destination.recover_if_reserved(
            'seat', 'actor', overrides={'--model': 'claude-x', '--prompt': 'work'})
