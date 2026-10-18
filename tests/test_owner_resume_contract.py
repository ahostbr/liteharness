"""Owner launch/resume retains explicit memory without persisting launch authority."""
import json
import os
import subprocess
import sys
from pathlib import Path
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


@pytest.mark.parametrize('memory_backed', [False, True], ids=['agent-home-owner', 'owner-memory'])
@pytest.mark.parametrize('selector', ['name', 'convo'])
def test_emitted_owner_resume_argv_passes_real_consumer(launched, tmp_path, monkeypatch, memory_backed, selector):
    """Receiver proof, not only a green sender/bridge mock.

    Set LITETUI_CONSUMER_SRC to the explicitly pinned immutable consumer src.
    The harness imports from this checkout; the child imports that consumer,
    using the same interpreter and isolated HOME/data/lore, with no model call.
    """
    from contextlib import redirect_stdout
    from io import StringIO

    consumer = os.environ.get('LITETUI_CONSUMER_SRC')
    if not consumer:
        pytest.skip('explicit LITETUI_CONSUMER_SRC required for cross-repo receiver contract')
    consumer = Path(consumer).resolve()
    assert (consumer / 'litetui' / 'cli.py').is_file()
    kwargs, bridge, place, data, _ = launched
    workspace = tmp_path / 'receiver-workspace'
    workspace.mkdir()
    role = tmp_path / 'role.md'
    role.write_text('# OWNER ROLE\nOLD SYNTHETIC DEFAULT BODY', encoding='utf-8')
    method = tmp_path / 'method.md'
    method.write_text('# POLYMATHIC LINUS\nSYNTHETIC METHOD', encoding='utf-8')
    custom = tmp_path / 'caller.md'
    custom.write_text('\ufeffNEW SYNTHETIC CALLER BODY\nUnicode: \u03bb', encoding='utf-8')
    lore = tmp_path / 'synthetic-lore.md'
    lore.write_text('FROZEN SYNTHETIC LORE', encoding='utf-8')
    state_file = tmp_path / 'receiver-state.json'
    output = tmp_path / 'receiver-receipt.json'
    helper = Path(__file__).with_name('_owner_resume_consumer.py').resolve()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('LITETUI_', 'LITEHARNESS_', 'LITESUITE_'))
           and key not in ('CLAUDECODE', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED')}
    env.update(PYTHONPATH=str(consumer), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               LITETUI_CONSUMER_SRC=str(consumer), LITETUI_DATA_ROOT=str(data), LITETUI_NO_HARNESS='1',
               HOME=str(tmp_path / 'profile'), USERPROFILE=str(tmp_path / 'profile'),
               LITEHARNESS_LORE_FILE=str(lore), CLAUDE_SKILL_DIR=str(tmp_path / 'skills'))
    envelope = {'LITETUI_SPAWN_IDENTITY': '1', 'LITEHARNESS_AGENT_ID': AID,
                'LITEHARNESS_AGENT_NAME': 'Alpha', 'LITEHARNESS_TIER': 'leader',
                'LITEHARNESS_SPAWNED_BY': PARENT}
    env.update(envelope)

    def receive(phase, args, launch_env):
        completed = subprocess.run([sys.executable, str(helper), phase, str(state_file), str(output), *args],
                                   env={**env, **launch_env}, cwd=workspace, capture_output=True,
                                   text=True, encoding='utf-8', timeout=45, check=False)
        # Exception output is sanitized: never dump complete SYSTEM or home/lore.
        assert completed.returncode == 0, ('consumer receiver failed', phase, completed.returncode,
                                          completed.stderr[-3000:])
        return json.loads(output.read_text(encoding='utf-8'))

    selection = ['--owner', 'Alpha', '--memory', kwargs['memory_dir']] if memory_backed else ['--agent', 'Alpha']
    receive('seed', [*selection, '--role-prompt-file', str(role), '--cognitive-file', str(method)], envelope)
    state = json.loads(state_file.read_text(encoding='utf-8'))
    old_cid = state['cid']
    # BOTH catalog entries really exist: bare named resume must refuse rather
    # than guess. Only after proving that boundary move the meta-only fixture
    # placeholder (not genuine user history) outside this synthetic catalog.
    refusal = StringIO()
    with redirect_stdout(refusal), pytest.raises(SystemExit) as ambiguous:
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha', system_prompt_file=str(custom), cognitive='linus')
    assert ambiguous.value.code == 2 and 'Multiple owned conversations' in refusal.getvalue()
    bridge.assert_not_called()
    place.assert_not_called()
    (AgentStore(data).agent_directory('Alpha') / 'conversations' / CID).rename(tmp_path / 'unused-placeholder')
    original = bridge.side_effect

    def resolve(request_method, path, body=None):
        result = original(request_method, path, body)
        if path == '/harness/spawn/resolve':
            assert body['tier'] == 'leader' and body['spawnedBy'] == PARENT
            result['request']['args'] = ['--role-prompt-file', str(role), '--cognitive-file', str(method),
                                         '--system-prompt-file', body['systemPromptFile']]
            # Exact typed identity envelope emitted by Suite's LiteTUI adapter,
            # not a repair of malformed owner/agent flags after the producer.
            result['request']['env'].update(envelope)
        return result

    bridge.side_effect = resolve
    if not memory_backed:
        kwargs.pop('owner_title')
        kwargs.pop('memory_dir')
    kwargs['cwd'] = str(workspace)
    source_bytes = custom.read_bytes()
    before = {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}
    target = {'resume_agent_id': 'Alpha'}
    if selector == 'convo':
        target['resume_convo_id'] = old_cid  # custom body requires an owned named target
    cli.cmd_spawn(**kwargs, **target, system_prompt_file=str(custom), cognitive='linus')
    launch = place.call_args.kwargs['launch']
    args = launch['args']
    assert args[args.index('--convo') + 1] == old_cid
    assert ('--owner' in args) is memory_backed
    assert ('--agent' in args) is not memory_backed
    assert before == {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}, 'producer wrote consumer HOME'
    # Only the receiver may publish. Change disk lore after seeding to prove that
    # resume retains the original frozen suffix rather than reconstructing it.
    lore.write_text('CHANGED DISK LORE MUST NOT REPLACE FROZEN SNAPSHOT', encoding='utf-8')
    receipt = receive('resume', args, launch['env'])
    assert receipt['argv'] == args  # exact producer argv reached the real consumer
    assert receipt['same_owned_home'] and receipt['requested_old_cid_active']
    assert receipt['frozen_suffix_preserved'] and receipt['history_preserved']
    assert receipt['audit_before_request'] and receipt['system_messages'] == 1
    assert receipt['actual_owner_headers'] == receipt['selected_method_headers'] == receipt['custom_body_count'] == 1
    assert custom.read_bytes() == source_bytes
    print('REAL_CONSUMER_RECEIPT=' + json.dumps({key: value for key, value in receipt.items() if key != 'argv'}, sort_keys=True))
