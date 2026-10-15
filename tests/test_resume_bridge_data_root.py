"""A normal shell discovers owned homes without importing LiteTUI or guessing cwd."""
import builtins
import json
from unittest.mock import Mock

import pytest

from liteharness import agent_names, cli, config, fleet_policy
from liteharness.agent_ownership import AgentSession
from liteharness.agent_store import AgentStore

AID = '11111111-1111-4111-8111-111111111111'
CID = '22222222-2222-4222-8222-222222222222'
PARENT = '33333333-3333-4333-8333-333333333333'
NAME = 'Owned-Only'


@pytest.fixture
def world(tmp_path, monkeypatch):
    data = tmp_path / 'authoritative'
    data.mkdir()
    harness = tmp_path / 'harness'
    (harness / 'agents').mkdir(parents=True)
    registry = harness / 'agents' / f'{AID}.json'
    registry.write_text(json.dumps(dict(agent_id=AID, name=NAME, cli='litetui',
        role='builder', backend='codex', model='registry-model', thinking_level='high')))
    with AgentSession.create_fresh(AgentStore(data), name=NAME, agent_id=AID,
            backend='codex', model='folder-model', thinking_level='high') as session:
        transcript = session.conversation_directory(CID) / 'convo.jsonl'
        transcript.parent.mkdir()
        transcript.write_text('{"type":"meta"}\n')
    monkeypatch.setattr(config, 'get_root', lambda: harness)
    monkeypatch.delenv('LITETUI_DATA_ROOT', raising=False)
    original_import = builtins.__import__

    def without_litetui(name, *args, **kwargs):
        if name == 'litetui.paths':
            raise ImportError('LiteTUI is not installed in the caller Python')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_litetui)
    monkeypatch.setattr(fleet_policy, 'gate', lambda *a: None)
    monkeypatch.setattr(fleet_policy, 'verify_seat', lambda *a, **kw: None)
    monkeypatch.setattr(fleet_policy, 'load', lambda: ({}, None))
    monkeypatch.setattr(fleet_policy, 'floor_for', lambda *a: None)
    calls = []

    def bridge(method, path, body=None):
        calls.append((method, path, body))
        if path == '/pty/list':
            return {'sessions': []}
        assert (method, path) == ('POST', '/harness/spawn/resolve')
        return {'ok': True, 'agentId': PARENT, 'liteTuiDataRoot': str(data),
                'request': {'args': [], 'env': {'LITETUI_DATA_ROOT': str(data)},
                            'harnessAgentId': PARENT}}

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    place = Mock(return_value={'newSessionId': 'new-session'})
    monkeypatch.setattr(cli, '_place_split', place)
    kwargs = dict(split_mode=True, resume_agent_id=NAME, cwd=str(tmp_path),
                  tier='builder', spawned_by=PARENT)
    return data, registry, calls, place, kwargs, bridge


@pytest.mark.parametrize('selector', [NAME.lower(), AID])
@pytest.mark.parametrize('retired', [False, True])
def test_plain_shell_resume_uses_bridge_root_and_folder_execution(world, monkeypatch, tmp_path,
                                                                selector, retired):
    data, registry, calls, place, kwargs, _ = world
    if retired:
        target = registry.parent.parent / 'retired-agents' / registry.name
        target.parent.mkdir()
        registry.rename(target)
        registry = target
    caller = tmp_path / 'unrelated-cwd'
    caller.mkdir()
    monkeypatch.chdir(caller)
    before = registry.read_bytes()
    settings = (data / '.agents' / NAME / 'settings.json').read_bytes()
    assert agent_names.resolve_name(NAME) is None
    kwargs['resume_agent_id'] = selector
    cli.cmd_spawn(**kwargs)
    resolutions = [body for _, path, body in calls if path == '/harness/spawn/resolve']
    assert len(resolutions) == 2
    assert resolutions[0]['backend'] == 'codex'
    assert resolutions[0]['model'] == 'registry-model'
    assert resolutions[1]['model'] == 'folder-model'  # stale presence never executes
    launch = place.call_args.kwargs['launch']
    assert launch['harnessAgentId'] == AID  # discovery's fresh ID is discarded
    assert launch['env']['LITETUI_DATA_ROOT'] == str(data)
    for flag, expected in (('--agent', NAME), ('--convo', CID), ('--model', 'folder-model')):
        assert launch['args'][launch['args'].index(flag) + 1] == expected
    assert '--create-agent' not in launch['args']
    assert registry.read_bytes() == before
    assert (data / '.agents' / NAME / 'settings.json').read_bytes() == settings
    assert agent_names.resolve_name(NAME) is None


def test_without_registry_discovery_uses_only_explicit_execution(world):
    _, registry, calls, place, kwargs, _ = world
    registry.unlink()
    cli.cmd_spawn(**kwargs, backend='codex', model='folder-model', thinking_level='high')
    assert calls[0][2]['model'] == 'folder-model'
    place.assert_called_once()


def test_no_registry_or_explicit_execution_never_fabricates_a_provider(world, capsys):
    _, registry, calls, place, kwargs, _ = world
    registry.unlink()
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs)
    assert 'Owned-store data root unknown' in capsys.readouterr().out
    assert calls == []
    place.assert_not_called()


@pytest.mark.parametrize('bad_root', ['', 'relative', 42, None, 'missing', 'traversal'])
def test_invalid_bridge_root_refuses_before_placement(world, monkeypatch, tmp_path, bad_root):
    _, _, calls, place, kwargs, original = world
    if bad_root == 'missing':
        bad_root = str(tmp_path / 'absent')
    elif bad_root == 'traversal':
        bad_root = str(tmp_path / '..' / tmp_path.name)

    def bridge(*args):
        result = original(*args)
        result['liteTuiDataRoot'] = bad_root
        return result

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs)
    assert len(calls) == 1
    place.assert_not_called()


@pytest.mark.parametrize('response', [None, {}, {'ok': False, 'error': 'offline'},
                                      {'ok': True, 'liteTuiDataRoot': None}])
def test_failed_discovery_never_launches(world, monkeypatch, response):
    _, _, _, place, kwargs, _ = world
    monkeypatch.setattr(cli, '_bridge_request', lambda *a: response)
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs)
    place.assert_not_called()


def test_second_resolver_must_keep_discovered_root(world, monkeypatch, tmp_path, capsys):
    _, _, calls, place, kwargs, original = world
    other = tmp_path / 'other-root'
    other.mkdir()

    def bridge(method, path, body=None):
        result = original(method, path, body)
        if path == '/harness/spawn/resolve' and len(calls) > 1:
            result['liteTuiDataRoot'] = str(other)
            result['request']['env']['LITETUI_DATA_ROOT'] = str(other)
        return result

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs)
    assert 'Resolver root differs' in capsys.readouterr().out
    place.assert_not_called()


@pytest.mark.parametrize('corruption', ['catalog', 'explicit-root', 'duplicate-registry'])
def test_corruption_keeps_fail_closed_rules(world, monkeypatch, corruption):
    data, registry, calls, place, kwargs, _ = world
    if corruption == 'catalog':
        (data / '.agents' / NAME / 'settings.json').write_text('{bad')
    elif corruption == 'explicit-root':
        monkeypatch.setenv('LITETUI_DATA_ROOT', 'relative')
    else:
        row = json.loads(registry.read_text())
        row['agent_id'] = PARENT
        (registry.parent / f'{PARENT}.json').write_text(json.dumps(row))
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs)
    assert len(calls) == (1 if corruption == 'catalog' else 0)
    place.assert_not_called()


def test_claude_resume_without_root_does_not_discover_litetui(world, monkeypatch):
    _, registry, calls, place, kwargs, _ = world
    registry.unlink()
    agent_names.record_name(NAME, AID, CID, kwargs['cwd'], backend='claude', model='claude-opus')
    entrypoint = cli.cmd_spawn
    launches = []
    monkeypatch.setattr(cli, 'cmd_spawn', lambda **kw: launches.append(kw))
    entrypoint(**kwargs)
    assert calls == []
    place.assert_not_called()
    assert launches[0]['spawn_cli'] == 'claude'
    assert launches[0]['additional_args'] == f'--resume {CID}'


@pytest.mark.parametrize('role_args', [
    ['--system-prompt-file', 'role.md'], ['--system-prompt', 'original role text'],
    ['--system-prompt-file=role.md'],
])
def test_resume_preserves_complete_launch_text_and_first_turn(world, monkeypatch, role_args):
    data, _, _, place, kwargs, original = world
    transcript = data / '.agents' / NAME / 'conversations' / CID / 'convo.jsonl'
    before = transcript.read_bytes()

    def bridge(method, path, body=None):
        result = original(method, path, body)
        if path == '/harness/spawn/resolve':
            result['request']['args'] = list(role_args)
            if body.get('cognitive'):
                result['request']['args'] += ['--cognitive-file', 'feynman.md']
            if body.get('prompt'):
                result['request']['args'] += ['--prompt', body['prompt']]
        return result

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    cli.cmd_spawn(**kwargs, cognitive='feynman', prompt='Reply READY')
    args = place.call_args.kwargs['launch']['args']
    assert args[:len(role_args)] == role_args
    assert args[args.index('--cognitive-file') + 1] == 'feynman.md'
    assert args[args.index('--prompt') + 1] == 'Reply READY'
    assert args[args.index('--convo') + 1] == CID
    assert transcript.read_bytes() == before  # child validates; parent never rewrites


@pytest.mark.parametrize('flag', ['--system-prompt-file', '--system-prompt'])
def test_bare_role_flag_refused_before_resume_placement(world, monkeypatch, flag):
    _, _, _, place, kwargs, original = world

    def bridge(method, path, body=None):
        result = original(method, path, body)
        if path == '/harness/spawn/resolve':
            result['request']['args'] = [flag]
        return result

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs)
    place.assert_not_called()
