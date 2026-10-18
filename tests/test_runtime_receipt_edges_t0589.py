"""Real launch boundaries: ambiguity and an exit during the success readback."""
import json

import pytest

from liteharness import cli
from test_restore_runtime_receipt_t0589 import AID, PARENT, SESSION, launch, world  # noqa: F401


@pytest.mark.parametrize('entry', ['fresh', 'owned-resume'])
def test_owner_exiting_before_final_report_never_reports_success(world, monkeypatch, capsys, entry):
    _, _, _, state = world
    bridge = cli._bridge_request
    reads = 0

    def transport(method, path, body=None, **kwargs):
        nonlocal reads
        if path == '/pty/list' and state['created']:
            reads += 1
            if reads == 2:
                state['fault'] = 'dead'
                state['child'].release()
        return bridge(method, path, body, **kwargs)

    monkeypatch.setattr(cli, '_bridge_request', transport)
    assert launch(world, entry) == 2
    output = capsys.readouterr().out
    assert 'Launched owned seat' not in output
    assert 'SPAWN REFUSED' in output
    assert not [call for call in state['calls'] if call[0] == 'DELETE']


@pytest.mark.parametrize('fault', ['ambiguous-presence', 'foreign-native', 'duplicate-native'])
def test_exact_receipt_refuses_ambiguous_or_foreign_runtime_without_killing(world, monkeypatch, capsys, fault):
    _, _, registry, state = world
    bridge = cli._bridge_request

    def transport(method, path, body=None, **kwargs):
        result = bridge(method, path, body, **kwargs)
        if method == 'POST' and path == '/pty/create' and fault == 'ambiguous-presence':
            row = {'agent_id': PARENT, 'canvas_session_id': SESSION}
            (registry / 'agents' / f'{PARENT}.json').write_text(json.dumps(row), encoding='utf-8')
        if path == '/pty/list' and state['created']:
            if fault == 'foreign-native':
                result['sessions'][0]['harnessAgentId'] = PARENT
            elif fault == 'duplicate-native':
                result['sessions'].append(dict(result['sessions'][0]))
        return result

    monkeypatch.setattr(cli, '_bridge_request', transport)
    assert launch(world, 'fresh') == 2
    assert 'SPAWN REFUSED' in capsys.readouterr().out
    assert not [call for call in state['calls'] if call[0] == 'DELETE']


def test_policy_changing_during_canvas_rename_is_rechecked_before_success(world, monkeypatch, capsys):
    _, _, registry, state = world

    def rename(*args):
        path = registry / 'agents' / f'{AID}.json'
        row = json.loads(path.read_text(encoding='utf-8'))
        row['model'] = 'wrong-pinned-model'
        path.write_text(json.dumps(row), encoding='utf-8')

    monkeypatch.setattr(cli, '_rename_canvas_seat_retrying', rename)
    assert launch(world, 'fresh-split') == 2
    assert 'MISMATCH' in capsys.readouterr().out
    assert not [call for call in state['calls'] if call[0] == 'DELETE']
