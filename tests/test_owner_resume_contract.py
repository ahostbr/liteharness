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
    def resolve(method, path, body=None):
        if path == '/pty/list':
            return {'sessions': []}
        assert path == '/harness/spawn/resolve'
        args = ['--role-prompt-file', 'fixture-role.md']
        if 'systemPromptFile' in body:
            args.extend(['--system-prompt-file', body['systemPromptFile']])
        return {'ok': True, 'agentId': AID, 'liteTuiDataRoot': str(data),
                'request': {'args': args, 'env': {'LITEHARNESS_SPAWNED_BY': body['spawnedBy']},
                            'harnessAgentId': AID}}
    bridge = Mock(side_effect=resolve)
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


@pytest.mark.parametrize('explicit_cli', [False, True])
def test_named_owner_resume_custom_body_preserves_owned_launch(launched, tmp_path, explicit_cli):
    kwargs, bridge, place, data, _ = launched
    if not explicit_cli:
        kwargs.pop('spawn_cli')
    source = tmp_path / 'new caller & body.md'
    source.write_text('\ufeffNew owner domain rules.\r\nUnicode: \u03bb\n', encoding='utf-8')
    before = {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    original = source.read_bytes()
    cli.cmd_spawn(**kwargs, resume_agent_id='Alpha', system_prompt_file=str(source), cognitive='linus')
    resolution = next(call.args[2] for call in bridge.call_args_list if call.args[1] == '/harness/spawn/resolve')
    assert resolution['systemPromptFile'] == str(source)
    assert resolution['cognitive'] == 'linus'
    assert resolution['spawnedBy'] == PARENT and resolution['tier'] == 'leader'
    assert 'prompt' not in resolution
    launch = place.call_args.kwargs['launch']
    args = launch['args']
    for flag, value in (('--system-prompt-file', str(source)), ('--owner', 'Alpha'),
                        ('--memory', kwargs['memory_dir']), ('--convo', CID)):
        assert args.count(flag) == 1
        assert args[args.index(flag) + 1] == value
    assert not {'--agent', '--create-agent', '--agent-id'} & set(args)
    assert launch['harnessAgentId'] == AID and launch['env']['LITEHARNESS_AGENT_ID'] == AID
    assert launch['env']['LITEHARNESS_SPAWNED_BY'] == PARENT
    assert before == {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    assert source.read_bytes() == original
    with AgentSession.acquire_existing(AgentStore(data), name='Alpha'):
        pass  # parent preflight retained no lease after launch construction


@pytest.mark.parametrize('kind', ['missing', 'relative', 'directory', 'utf8', 'nul-body', 'nul-path'])
def test_named_owner_invalid_custom_source_refuses_before_bridge(launched, tmp_path, capsys, kind):
    kwargs, bridge, place, data, _ = launched
    source = tmp_path / 'caller.md'
    if kind == 'relative':
        source = 'caller.md'
    elif kind == 'directory':
        source = tmp_path
    elif kind == 'utf8':
        source.write_bytes(b'\xff')
    elif kind == 'nul-body':
        source.write_bytes(b'caller\0body')
    elif kind == 'nul-path':
        source = str(source) + '\0'
    before = {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    with pytest.raises(SystemExit) as error:
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha', system_prompt_file=str(source))
    assert error.value.code == 2
    assert '--system-prompt-file' in capsys.readouterr().out
    bridge.assert_not_called()
    place.assert_not_called()
    assert before == {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}


def test_named_owner_live_custom_refuses_without_message_or_duplicate(launched, tmp_path, capsys):
    kwargs, bridge, place, data, _ = launched
    source = tmp_path / 'caller.md'
    source.write_text('New body', encoding='utf-8')
    bridge.side_effect = lambda *a: {'sessions': [{'harnessAgentId': AID, 'id': 'existing'}]}
    before = {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    with pytest.raises(SystemExit) as error:
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha', system_prompt_file=str(source))
    assert error.value.code == 2
    output = capsys.readouterr().out.lower()
    assert 'live' in output and '--system-prompt-file was not applied' in output
    assert [call.args[:2] for call in bridge.call_args_list] == [('GET', '/pty/list')]
    place.assert_not_called()
    assert before == {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}


@pytest.mark.parametrize('kind', ['ignored', 'wrong-path', 'duplicate', 'inline', 'inline-equals', 'inline-after-brief'])
def test_named_owner_custom_refuses_old_or_conflicting_bridge(launched, tmp_path, capsys, kind):
    kwargs, bridge, place, data, _ = launched
    source = tmp_path / 'caller.md'
    source.write_text('New body', encoding='utf-8')
    original = bridge.side_effect
    def resolve(method, path, body=None):
        result = original(method, path, body)
        if path == '/harness/spawn/resolve':
            args = ['--system-prompt-file', str(source)]
            if kind == 'ignored':
                args = ['--role-prompt-file', 'old-role.md']
            elif kind == 'wrong-path':
                args[1] = str(tmp_path / 'wrong.md')
            elif kind == 'duplicate':
                args += args
            elif kind == 'inline-after-brief':
                args += ['--prompt', '--prompt', '--system-prompt', 'inline body']
            else:
                args += ['--system-prompt', 'inline body'] if kind == 'inline' else ['--system-prompt=inline body']
            result['request']['args'] = args
        return result
    bridge.side_effect = resolve
    before = {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    with pytest.raises(SystemExit) as error:
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha', system_prompt_file=str(source))
    assert error.value.code == 2
    assert '--system-prompt-file' in capsys.readouterr().out
    place.assert_not_called()
    assert before == {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    assert not [call for call in bridge.call_args_list if call.args[0] == 'DELETE']


def test_named_owner_custom_refuses_held_lease(launched, tmp_path, capsys):
    kwargs, bridge, place, data, _ = launched
    source = tmp_path / 'caller.md'
    source.write_text('New body', encoding='utf-8')
    with AgentSession.acquire_existing(AgentStore(data), name='Alpha'):
        with pytest.raises(SystemExit) as error:
            cli.cmd_spawn(**kwargs, resume_agent_id='Alpha', system_prompt_file=str(source))
        assert error.value.code == 2
    assert '--system-prompt-file was not applied' in capsys.readouterr().out
    bridge.assert_not_called()
    place.assert_not_called()


@pytest.mark.parametrize('explicit_cli', ['claude', 'litetui'])
def test_custom_resume_refuses_actual_legacy_claude_target(launched, tmp_path, monkeypatch, capsys, explicit_cli):
    _, bridge, place, _, root = launched
    source = tmp_path / 'caller.md'
    source.write_text('New body', encoding='utf-8')
    legacy_id = '44444444-4444-4444-8444-444444444444'
    (root / 'agents' / f'{legacy_id}.json').write_text(json.dumps({
        'agent_id': legacy_id, 'name': 'LegacyClaude', 'cli': 'claude', 'role': 'owner'}), encoding='utf-8')
    monkeypatch.setattr(agent_names, 'resolve_name', lambda _: {
        'name': 'LegacyClaude', 'agent_id': legacy_id, 'convo_id': CID, 'cwd': str(tmp_path), 'backend': 'claude'})
    with pytest.raises(SystemExit) as error:
        cli.cmd_spawn(spawn_cli=explicit_cli, split_mode=True, resume_agent_id='LegacyClaude',
                      cwd=str(tmp_path), system_prompt_file=str(source))
    assert error.value.code == 2
    assert 'Claude targets are unsupported' in capsys.readouterr().out
    bridge.assert_not_called()
    place.assert_not_called()


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
