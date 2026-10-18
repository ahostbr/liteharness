"""T0589 semantic REDs at real fresh/resume success boundaries.

Only bridge/native-process inspection is substituted. Real AgentStore ownership,
owned launch construction, split selection, policy gate and verify_seat execute.
The proposed recovery destination adapter is `owned_resume recovery destination`;
these first REDs deliberately retain the ordinary launch entry to expose the
existing readiness defect independently of the later placement coordinator.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import psutil
import pytest

from liteharness import cli, config, fleet_policy
from liteharness.agent_ownership import AgentSession
from liteharness.agent_store import AgentStore

AID = '11111111-1111-4111-8111-111111111111'
PARENT = '22222222-2222-4222-8222-222222222222'
CID = '33333333-3333-4333-8333-333333333333'
PID = 580589
SESSION = 'pty-t0589-new'
MODEL = 'fixture-local-model'


@pytest.fixture
def world(tmp_path, monkeypatch):
    data, project, registry = (tmp_path / part for part in ('data', 'project', 'registry'))
    for path in (data, project, registry):
        path.mkdir()
    monkeypatch.setenv('LITETUI_DATA_ROOT', str(data))
    monkeypatch.setenv('LITEHARNESS_AGENT_ID', PARENT)
    monkeypatch.setenv('LITESUITE_FLEET_POLICY', str(tmp_path / 'absent-policy.json'))
    monkeypatch.setattr(config, 'get_root', lambda: registry)
    monkeypatch.setattr(cli, 'OWNED_RUNTIME_WAIT_SECONDS', 0)
    # Rename is an external renderer effect, not readiness/policy/placement.
    monkeypatch.setattr(cli, '_rename_canvas_seat_retrying', lambda *a: None)
    state = {'fault': 'live', 'created': False, 'child': None, 'calls': []}
    # Substitute OS inspection, NOT the liveness/reuse decisions in hooks/CLI.
    monkeypatch.setattr(psutil, 'pid_exists', lambda pid: pid == PID and state['fault'] != 'dead')

    def process(pid):
        assert pid == PID, 'test must not inspect an unrelated real process'
        if state['fault'] == 'inaccessible':
            raise psutil.AccessDenied(pid)
        # A reused PID started AFTER this row registered; an exact owner predates it.
        started = datetime.now(timezone.utc).timestamp() + 60 if state['fault'] == 'reused-pid' else 1
        return SimpleNamespace(create_time=lambda: started)

    monkeypatch.setattr(psutil, 'Process', process)

    def bridge(method, path, body=None, **kwargs):
        state['calls'].append((method, path, body))
        if path == '/harness/spawn/resolve':
            return {'ok': True, 'agentId': AID, 'liteTuiDataRoot': str(data), 'request': {
                'shell': 'litetui.exe', 'args': ['--backend', 'local', '--model', MODEL,
                                               '--thinking-level', 'high'],
                'env': {'LITETUI_DATA_ROOT': str(data)}, 'harnessAgentId': AID}}
        if path.startswith('/context?'):
            return {'activePanes': [{'id': 'pane-parent', 'leafCount': 1,
                                    'leaves': [{'id': 'parent-leaf', 'sessionIds': ['pty-parent']}]}]}
        if method == 'POST' and path in ('/pty/create', '/canvas/split'):
            assert not state['created'], 'test transport must never hide duplicate creation'
            state['created'] = True
            store = AgentStore(data)
            if not (data / '.agents' / 'ReceiptSeat').exists():
                child = AgentSession.create_fresh(store, name='ReceiptSeat', agent_id=AID,
                    backend='local', model=MODEL, thinking_level='high')
            else:
                child = AgentSession.acquire_existing(store, agent_id=AID)
            state['child'] = child
            if state['fault'] in ('dead', 'reused-pid'):
                child.release()
            # A matching policy row can outlive its owner or name another PTY.
            (registry / 'agents').mkdir(exist_ok=True)
            now = datetime.now(timezone.utc).isoformat()
            row = {'agent_id': AID, 'name': 'ReceiptSeat', 'backend': 'local',
                   'model': MODEL, 'thinking_level': 'high', 'session_pid': PID,
                   'session_process_started_at': 1000,
                   'started_at': now, 'registered_at': now, 'last_seen': now,
                   'canvas_session_id': 'pty-t0589-old' if state['fault'] == 'wrong-session' else SESSION}
            (registry / 'agents' / f'{AID}.json').write_text(json.dumps(row), encoding='utf-8')
            return {'newSessionId': SESSION, 'newLeafId': 'new-leaf'} if path == '/canvas/split' else {
                'session_id': SESSION, 'pid': PID}
        if path == '/pty/list':
            return {'sessions': ([{'id': SESSION, 'pid': PID, 'harnessAgentId': AID}]
                                 if state['created'] and state['fault'] != 'dead' else [])}
        if method == 'DELETE' and path == f'/pty/{SESSION}':
            return {'success': True}
        raise AssertionError(f'unexpected external bridge call: {method} {path}')

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    yield data, project, registry, state
    if state['child'] is not None:
        state['child'].release()


def launch(world, entry):
    data, project, _, _ = world
    if entry == 'owned-resume':
        with AgentSession.create_fresh(AgentStore(data), name='ReceiptSeat', agent_id=AID,
                backend='local', model=MODEL, thinking_level='high') as owner:
            transcript = owner.conversation_directory(CID) / 'convo.jsonl'
            transcript.parent.mkdir()
            transcript.write_text('{"type":"meta"}\n', encoding='utf-8')
        kwargs = dict(split_mode=True, resume_agent_id='ReceiptSeat', resume_convo_id=CID,
                      split_pane='pane-parent')
    else:
        kwargs = dict(spawn_cli='litetui', pty_mode=entry != 'fresh-split',
                      split_mode=entry == 'fresh-split', split_pane='pane-parent', name='ReceiptSeat',
                      backend='local', model=MODEL, thinking_level='high')
    try:
        cli.cmd_spawn(cwd=str(project), tier='worker', spawned_by=PARENT, **kwargs)
    except SystemExit as exc:
        return exc.code
    return 0


@pytest.mark.parametrize('entry', ['fresh', 'owned-resume'])
@pytest.mark.parametrize('fault', ['dead', 'reused-pid', 'wrong-session', 'inaccessible'])
def test_matching_policy_row_is_not_an_exact_live_runtime_receipt(world, capsys, entry, fault):
    _, _, registry, state = world
    state['fault'] = fault
    code = launch(world, entry)
    output = capsys.readouterr().out
    # Positive control for the distinction: policy REALLY passes, unmocked.
    assert fleet_policy.verify_seat(AID, registry, wait=0, backend='local',
        expect_model=MODEL, expect_thinking='high') is None
    if fault in ('dead', 'reused-pid'):
        assert not cli._agent_record_live(AID), 'real reuse-checked liveness rejects this owner'
    elif fault == 'wrong-session':
        row = json.loads((registry / 'agents' / f'{AID}.json').read_text(encoding='utf-8'))
        assert row['canvas_session_id'] != SESSION
    assert state['created']
    assert code != 0, (f'SEMANTIC RED: {entry} reported success for {fault} exact new runtime; '
                       f'policy agreement is not runtime readiness. stdout={output!r}')
    assert 'Launched owned seat' not in output


@pytest.mark.parametrize('entry', ['fresh', 'owned-resume'])
def test_CONTROL_live_exact_runtime_and_matching_policy_can_succeed(world, capsys, entry):
    assert launch(world, entry) == 0
    output = capsys.readouterr().out
    assert AID in output and SESSION in output
    assert cli._agent_record_live(AID)
    _, _, registry, state = world
    row = json.loads((registry / 'agents' / f'{AID}.json').read_text(encoding='utf-8'))
    assert row['canvas_session_id'] == SESSION
    assert state['child'] is not None
    assert not [call for call in state['calls'] if call[0] == 'DELETE']
