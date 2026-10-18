"""Owner launch/resume retains explicit memory without persisting launch authority."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from liteharness import agent_names, cli, config, fleet_policy, owner
from liteharness.agent_ownership import AgentSession
from liteharness.agent_store import AgentStore

AID = '11111111-1111-4111-8111-111111111111'
CID = '22222222-2222-4222-8222-222222222222'
PARENT = '33333333-3333-4333-8333-333333333333'


@pytest.fixture
def launched(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    root = tmp_path / 'harness'
    memory = tmp_path / 'memory'
    memory.mkdir()
    subprocess.run(['git', 'init', '-q', str(memory)], check=True)
    for filename in owner.OWNER_FILES:
        (memory / filename).write_text('widgets\n', encoding='utf-8')
    monkeypatch.setenv('LITETUI_DATA_ROOT', str(data))
    monkeypatch.setattr(config, 'get_root', lambda: root)
    monkeypatch.setattr(agent_names, 'resolve_name', lambda _: None)
    monkeypatch.setattr(fleet_policy, 'gate', lambda *a: None)
    monkeypatch.setattr(fleet_policy, 'verify_seat', lambda *a, **kw: None)
    monkeypatch.setattr(cli, '_rename_canvas_seat', lambda *a: None)
    bridge = Mock(side_effect=lambda method, path, body=None: {
        '/pty/list': {'sessions': []},
        '/harness/spawn/resolve': {'ok': True, 'agentId': AID, 'liteTuiDataRoot': str(data),
                                  'request': {'args': [], 'env': {}, 'harnessAgentId': AID}},
    }[path])
    place = Mock(return_value={'newSessionId': 'fixture'})
    monkeypatch.setattr(cli, '_bridge_request', bridge)
    monkeypatch.setattr(cli, '_place_split', place)
    kwargs = dict(spawn_cli='litetui', split_mode=True, cwd=str(tmp_path),
                  owner_title='Alpha', memory_dir=str(memory), spawned_by=PARENT,
                  backend='codex', model='fixture', thinking_level='high')
    cli.cmd_spawn(**kwargs)
    first = place.call_args.kwargs['launch']
    assert first['args'][first['args'].index('--owner') + 1] == 'Alpha'
    # Emulate the launched child's owned home, transcript and registration, then stop it.
    with AgentSession.create_fresh(AgentStore(data), name='Alpha', agent_id=AID,
                                  backend='codex', model='fixture', thinking_level='high') as session:
        target = session.conversation_directory(CID) / 'convo.jsonl'
        target.parent.mkdir()
        target.write_text('{"type":"meta"}\n', encoding='utf-8')
    (root / 'agents').mkdir(parents=True)
    (root / 'agents' / f'{AID}.json').write_text(json.dumps({'agent_id': AID, 'name': 'Alpha', 'role': 'owner'}))
    bridge.reset_mock()
    place.reset_mock()
    return kwargs, bridge, place, data, root


@pytest.mark.parametrize('selector', [{'resume_agent_id': 'Alpha'}, {'resume_convo_id': CID}])
@pytest.mark.parametrize('explicit_cli', [False, True])
def test_owner_resume_keeps_conversation_and_memory(launched, selector, explicit_cli):
    kwargs, _, place, data, _ = launched
    if not explicit_cli:
        kwargs.pop('spawn_cli')
    settings = {p: p.read_bytes() for p in data.rglob('settings.json')}
    cli.cmd_spawn(**kwargs, **selector)
    args = place.call_args.kwargs['launch']['args']
    for flag, value in (('--convo', CID), ('--owner', 'Alpha'), ('--memory', kwargs['memory_dir'])):
        assert args.count(flag) == 1
        assert args[args.index(flag) + 1] == value
    # LiteTUI refuses --owner with --agent/--create-agent (cli.py: "--owner opens the agent
    # home by its own name") and exits 2 a second after launch; --owner + --convo is its resume.
    assert not {'--agent', '--create-agent', '--agent-id'} & set(args)
    assert settings == {p: p.read_bytes() for p in data.rglob('settings.json')}


@pytest.mark.parametrize('retired', [False, True])
def test_bare_owner_resume_refuses_without_downgrading(launched, capsys, retired):
    kwargs, bridge, place, _, root = launched
    if retired:
        (root / 'retired-agents').mkdir()
        (root / 'agents' / f'{AID}.json').rename(root / 'retired-agents' / f'{AID}.json')
    kwargs.pop('owner_title')
    kwargs.pop('memory_dir')
    kwargs['tier'] = 'builder'
    with pytest.raises(SystemExit) as error:
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert '--owner' in output and '--memory' in output
    bridge.assert_not_called()
    place.assert_not_called()


def test_owner_saved_without_owner_memory_resumes_as_owner_without_it(launched):
    # A seat spawned with a plain `--tier owner` saved its conversation in its agent home.
    # LiteTUI refuses to reopen that conversation under --owner/--memory ("Store mismatch:
    # ... resume without --owner and --memory"), so the bare resume keeps the owner role
    # and leaves the store to the folder: --agent + --convo, no owner flags.
    kwargs, _, place, _, _ = launched
    kwargs.pop('owner_title')
    kwargs.pop('memory_dir')
    cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    launch = place.call_args.kwargs['launch']
    args = launch['args']
    assert not {'--owner', '--memory'} & set(args)
    assert args[args.index('--agent') + 1] == 'Alpha'
    assert args[args.index('--convo') + 1] == CID


def test_resume_validates_memory_like_first_launch(launched, tmp_path, capsys):
    kwargs, bridge, place, _, _ = launched
    incomplete = tmp_path / 'incomplete'
    incomplete.mkdir()
    kwargs['memory_dir'] = str(incomplete)
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    assert 'is missing SOUL.md' in capsys.readouterr().out
    bridge.assert_not_called()
    place.assert_not_called()
