"""Actual spawn/resume entrypoints with bridge transport substituted, real homes."""
import json
import pytest

from liteharness import cli, fleet_policy, owned_launch, agent_names
from liteharness.agent_store import AgentStore, StoreError
from liteharness.agent_ownership import AgentSession

AID = '11111111-1111-4111-8111-111111111111'
BID = '22222222-2222-4222-8222-222222222222'
CID = '33333333-3333-4333-8333-333333333333'


@pytest.fixture
def world(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setenv('LITETUI_DATA_ROOT', str(data))
    monkeypatch.setenv('LITEHARNESS_AGENT_ID', BID)
    monkeypatch.setattr(fleet_policy, 'gate', lambda *a: None)
    monkeypatch.setattr(fleet_policy, 'verify_seat', lambda *a, **kw: None)
    monkeypatch.setattr(cli, '_rename_canvas_seat', lambda *a: None)
    monkeypatch.setattr(agent_names, 'resolve_name', lambda _: None)
    calls = []
    def bridge(method, path, body=None):
        calls.append((method, path, body))
        if path == '/pty/list':
            return {'sessions': []}
        if path == '/harness/spawn/resolve':
            return {'ok': True, 'agentId': AID, 'liteTuiDataRoot': str(data), 'request': {
                'shell': 'litetui.exe', 'args': ['--backend', 'codex', '--model', 'fixture',
                                               '--thinking-level', 'high'],
                'env': {'LITETUI_DATA_ROOT': str(data)}, 'harnessAgentId': AID}}
        if path == '/pty/create':
            return {'session_id': 'fixture-session'}
        raise AssertionError(path)
    monkeypatch.setattr(cli, '_bridge_request', bridge)
    return data, project, calls


def test_actual_fresh_spawn_binds_child_birth_and_never_scans_archive(world, monkeypatch):
    data, project, calls = world
    monkeypatch.setattr(cli, '_record_fresh_name', lambda *a, **kw: pytest.fail('legacy archive scan'))
    cli.cmd_spawn(spawn_cli='litetui', pty_mode=True, name='QuietHelm', tier='worker',
                  cwd=str(project), backend='codex', model='fixture', thinking_level='high')
    body = next(body for _, path, body in calls if path == '/pty/create')
    assert body['args'][-4:] == ['--create-agent', 'QuietHelm', '--agent-id', AID]
    assert body['env']['LITEHARNESS_AGENT_ID'] == AID
    # Parent request construction grants no AgentSession and creates no home.
    assert not (data / '.agents').exists()
    assert not (data / '.convos').exists()


def test_actual_owned_resume_ignores_poisoned_registry_index_and_uses_home(world, monkeypatch):
    data, project, calls = world
    with AgentSession.create_fresh(AgentStore(data), name='QuietHelm', agent_id=AID,
                                  backend='codex', model='fixture', thinking_level='high') as session:
        target = session.conversation_directory(CID) / 'convo.jsonl'
        target.parent.mkdir()
        target.write_text('{"type":"meta"}\n')
    monkeypatch.setattr(agent_names, 'resolve_name', lambda _: pytest.fail('legacy authority consulted'))
    placed = []
    monkeypatch.setattr(cli, '_place_split', lambda *a, **kw: placed.append(kw) or {'newSessionId': 'fixture'})
    cli.cmd_spawn(split_mode=True, resume_agent_id='QuietHelm', resume_convo_id=CID,
                  cwd=str(project), tier='worker', spawned_by=BID)
    launch = placed[0]['launch']
    assert launch['args'][-4:] == ['--agent', 'QuietHelm', '--convo', CID]
    assert launch['harnessAgentId'] == AID
    assert launch['env']['LITETUI_DATA_ROOT'] == str(data)
    assert not (data / '.convos').exists()


def test_owned_missing_convo_refuses_archive_twin(world, monkeypatch):
    data, project, _ = world
    with AgentSession.create_fresh(AgentStore(data), name='QuietHelm', agent_id=AID,
                                  backend='codex', model='fixture', thinking_level='high'):
        pass
    archive = data / '.convos' / CID
    archive.mkdir(parents=True)
    (archive / 'convo.jsonl').write_bytes(b'foreign archive')
    monkeypatch.setattr(cli, '_place_split', lambda *a, **kw: pytest.fail('foreign launch'))
    with pytest.raises(SystemExit):
        cli.cmd_spawn(split_mode=True, resume_agent_id='QuietHelm', resume_convo_id=CID,
                      cwd=str(project), tier='worker', spawned_by=BID)
    assert (archive / 'convo.jsonl').read_bytes() == b'foreign archive'


def test_malformed_identity_and_execution_fail_before_creation(world, monkeypatch):
    data, _, _ = world
    resolution = {'agentId': 'not-an-owned-id', 'liteTuiDataRoot': str(data),
                  'request': {'args': [], 'env': {}, 'harnessAgentId': 'not-an-owned-id'}}
    with pytest.raises(StoreError):
        owned_launch.request(resolution, root=data, name='QuietHelm', agent_id='not-an-owned-id',
                             backend='codex', model='fixture', thinking_level='high', fresh=True)
    assert not (data / '.agents').exists()


@pytest.mark.parametrize('guard,reason', [
    ('owned', 'Resource owned or inaccessible'),
    ('live-pty', 'Live owned seat exists'),
    ('kill-old', 'never closes a seat'),
    ('override', 'override disagrees'),
    ('floor', 'fixture floor refusal'),
])
def test_actual_owned_guards_refuse_before_resolution_and_never_close(world, monkeypatch, capsys, guard, reason):
    from liteharness.agent_ownership import OwnershipError
    data, project, calls = world
    owned = AgentSession.create_fresh(AgentStore(data), name='QuietHelm', agent_id=AID,
                                     backend='codex', model='fixture', thinking_level='high')
    if guard != 'owned':
        owned.release()
    if guard == 'live-pty':
        original = cli._bridge_request
        def bridge(method, path, body=None):
            if path == '/pty/list':
                calls.append((method, path, body))
                return {'sessions': [{'id': 'existing', 'pid': 123, 'harnessAgentId': AID}]}
            return original(method, path, body)
        monkeypatch.setattr(cli, '_bridge_request', bridge)
    if guard == 'floor':
        monkeypatch.setattr(fleet_policy, 'gate', lambda *a: 'fixture floor refusal')
    kwargs = {'kill_old': True} if guard == 'kill-old' else ({'model': 'wrong'} if guard == 'override' else {})
    try:
        if guard == 'owned':
            with pytest.raises(OwnershipError, match=reason):
                cli.cmd_spawn(split_mode=True, resume_agent_id='QuietHelm', cwd=str(project),
                              tier='worker', spawned_by=BID, **kwargs)
        else:
            with pytest.raises(SystemExit) as exc:
                cli.cmd_spawn(split_mode=True, resume_agent_id='QuietHelm', cwd=str(project),
                              tier='worker', spawned_by=BID, **kwargs)
            assert exc.value.code == 2
            assert reason in capsys.readouterr().out
        assert not [path for _, path, _ in calls if path in ('/harness/spawn/resolve', '/canvas/split')]
        assert not [method for method, _, _ in calls if method == 'DELETE']
        assert [path for _, path, _ in calls] == (['/pty/list'] if guard == 'live-pty' else [])
    finally:
        owned.release()



def test_unchosen_named_resume_refuses_before_policy_bridge_or_lease(world, monkeypatch, capsys):
    data, project, calls = world
    with AgentSession.create_fresh(AgentStore(data), name='QuietHelm', agent_id=AID,
                                  backend='codex', model=None, thinking_level='high',
                                  allow_unchosen=True):
        pass
    before = {str(p.relative_to(data)): p.read_bytes() for p in data.rglob('*') if p.is_file()}
    monkeypatch.setattr(fleet_policy, 'gate', lambda *a: pytest.fail('unchosen reached policy'))
    with pytest.raises(SystemExit) as error:
        cli.cmd_spawn(split_mode=True, resume_agent_id='QuietHelm', cwd=str(project),
                      tier='worker', spawned_by=BID)
    assert error.value.code == 2
    assert 'model is unchosen' in capsys.readouterr().out
    assert calls == []
    assert {str(p.relative_to(data)): p.read_bytes() for p in data.rglob('*') if p.is_file()} == before
