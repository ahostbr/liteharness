"""Current-slot CLI destination REDs: never ordinary split after recovery lookup.

The host is the external authority boundary here. Its issuer/native/binder
pipeline has separate Suite coverage; this is the real owned resume entry.
"""
import pytest

from liteharness import cli
from test_restore_runtime_receipt_t0589 import AID, SESSION, launch, world  # noqa: F401


@pytest.mark.parametrize('outcome', ['bound', 'unknown', 'refused'])
def test_owned_cli_reserved_slot_uses_host_recovery_and_never_an_ordinary_split(world, monkeypatch, capsys, outcome):
    _, _, _, state = world
    transport = cli._bridge_request
    calls = []

    def bridge(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if path.startswith('/harness/recovered-agent/locate?'):
            return {'ok': True, 'found': True, 'paneId': 'saved-pane', 'slotId': 'saved-slot',
                    'leafId': 'saved-agent-leaf', 'agentId': AID}
        if path == '/harness/recovered-agent/restore':
            return {'ok': outcome == 'bound', 'status': outcome, 'requestId': 'request-589',
                    'agentId': AID, 'sessionId': SESSION, 'paneId': 'saved-pane',
                    'slotId': 'saved-slot', 'leafId': 'saved-agent-leaf'}
        return transport(method, path, body, **kwargs)

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    code = launch(world, 'owned-resume')
    assert not [item for item in state['calls'] if item[1] in ('/canvas/split', '/pty/create')], (
        'SEMANTIC RED: owned CLI resume created another leaf instead of consuming the reserved-slot destination')
    assert len([item for item in calls if item[1] == '/harness/recovered-agent/restore']) == 1
    assert code == (0 if outcome == 'bound' else 2)
    output = capsys.readouterr().out
    assert 'Launched owned seat' not in output
    if outcome == 'bound':
        assert 'saved-agent-leaf' in output
