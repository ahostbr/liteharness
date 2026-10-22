"""Direct LiteTUI launches have native PID evidence but no canvas-session env."""
import json
import sys
from types import SimpleNamespace

import psutil
import pytest

from liteharness import cli
from liteharness.agent_ownership import AgentSession
from liteharness.agent_store import AgentStore
from test_restore_runtime_receipt_t0589 import (
    AID, PARENT, PID, SESSION, launch, world,  # noqa: F401
)


@pytest.mark.parametrize('entry', ['fresh-split', 'owned-resume'])
@pytest.mark.parametrize('native', ['owner', 'launcher', 'unrelated'])
def test_missing_canvas_link_uses_exact_native_process(world, monkeypatch, capsys, entry, native):
    _, _, registry, state = world
    bridge = cli._bridge_request
    native_pid = PID if native == 'owner' else PID + (1 if native == 'launcher' else 2)

    def process(pid):
        assert pid in (PID, PID + 1)
        return SimpleNamespace(pid=pid, create_time=lambda: 1 if pid == PID else 0.5,
                               parent=lambda: process(PID + 1) if pid == PID else None,
                               is_running=lambda: True, status=lambda: psutil.STATUS_RUNNING)

    monkeypatch.setattr(psutil, 'Process', process)
    if sys.platform == 'win32':
        from ancestry_boundary import windows_processes
        windows_processes(monkeypatch, {PID: (PID + 1, 1), PID + 1: (0, 0.5)})

    def transport(method, path, body=None, **kwargs):
        result = bridge(method, path, body, **kwargs)
        if method == 'POST' and path in ('/pty/create', '/canvas/split'):
            row_path = registry / 'agents' / f'{AID}.json'
            row = json.loads(row_path.read_text())
            row.pop('canvas_session_id')
            row_path.write_text(json.dumps(row))
        if path == '/pty/list' and state['created']:
            result['sessions'][0]['pid'] = native_pid
        return result

    monkeypatch.setattr(cli, '_bridge_request', transport)
    assert launch(world, entry) == (2 if native == 'unrelated' else 0)
    output = capsys.readouterr().out
    if native != 'unrelated':
        assert AID in output and SESSION in output
    # Evidence is read, not repaired into a fabricated registration.
    assert 'canvas_session_id' not in json.loads((registry / 'agents' / f'{AID}.json').read_text())
    assert not [call for call in state['calls'] if call[0] == 'DELETE']


@pytest.mark.parametrize('record,expected', [
    ({'tier': 'worker'}, 'worker'),
    ({'role': 'critic', 'tier': 'worker'}, 'reviewer'),
    ({}, None),
    ({'tier': 'invalid'}, None),
    ({'agent_id': PARENT, 'tier': 'worker'}, None),
])
def test_owned_resume_recovers_only_valid_matching_saved_role(world, capsys, record, expected):
    data, project, registry, state = world
    with AgentSession.create_fresh(AgentStore(data), name='ReceiptSeat', agent_id=AID,
                                   backend='local', model='fixture-local-model', thinking_level='high'):
        pass
    agents = registry / 'agents'
    agents.mkdir()
    (agents / f'{AID}.json').write_text(json.dumps({'agent_id': AID, **record}))
    kwargs = dict(split_mode=True, resume_agent_id='ReceiptSeat', cwd=str(project),
                  spawned_by=PARENT, split_pane='pane-parent')
    if expected:
        cli.cmd_spawn(**kwargs)
        resolution = next(body for _, path, body in state['calls'] if path == '/harness/spawn/resolve')
        assert resolution['tier'] == expected
        assert 'Launched owned seat' in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as exc:
            cli.cmd_spawn(**kwargs)
        assert exc.value.code == 2
        assert not state['created']
        assert '--tier' in capsys.readouterr().out
