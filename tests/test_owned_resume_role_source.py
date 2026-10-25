"""A default-source LiteTUI home that LiteTUI would refuse is decided before any PTY exists (T0558-F-E)."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from liteharness import cli, recovered_destination
from liteharness.agent_store import AgentStore
from test_owner_resume_contract import AID, PARENT, launched  # noqa: F401  (fixture)

CURRENT = '# LEADER ROLE\nCURRENT LEADER BODY\n'


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@pytest.fixture
def stale(launched, tmp_path):  # noqa: F811
    """The owner home was initialised with an older leader role; the resolver now hands the current one."""
    kwargs, bridge, place, data, _ = launched
    home = AgentStore(data).agent_directory('Alpha')
    (home / 'prompts').mkdir(exist_ok=True)
    (home / 'prompts' / 'role.md').write_text('OLD LEADER BODY', encoding='utf-8')
    manifest = home / '.prompt-launch.json'
    manifest.write_text(json.dumps({'version': 2, 'role_source': 'default', 'sources': {'role.md': sha('OLD LEADER BODY')}},
                                   sort_keys=True), encoding='utf-8')
    role = tmp_path / 'composed-leader-preamble.md'
    role.write_text(CURRENT, encoding='utf-8')
    original = bridge.side_effect

    def resolve(method, path, body=None):
        result = original(method, path, body)
        if path == '/harness/spawn/resolve':
            result['request']['args'] = ['--role-prompt-file', str(role)] + (
                ['--system-prompt-file', body['systemPromptFile']] if 'systemPromptFile' in body else [])
        return result
    bridge.side_effect = resolve
    return kwargs, bridge, place, data, home, manifest


def resolves(bridge):
    return [call.args[2] for call in bridge.call_args_list if call.args[1] == '/harness/spawn/resolve']


def snapshot(data):
    return {p: p.read_bytes() for p in data.rglob('*') if p.is_file()}


@pytest.fixture
def recovery(monkeypatch, runtime_observer):
    """The restore scope: owned_resume sees an active recovery and places through the governed host."""
    placed = Mock(side_effect=lambda launch: runtime_observer.publish(AID, 'fixture') or {'newSessionId': 'fixture'})
    monkeypatch.setattr(recovered_destination, 'active_scope', lambda: {'launchParent': PARENT})
    monkeypatch.setattr(recovered_destination, 'place', placed)
    return placed


def test_matching_default_home_resumes_unchanged(stale):
    kwargs, bridge, place, data, home, manifest = stale
    manifest.write_text(json.dumps({'version': 2, 'role_source': 'default', 'sources': {'role.md': sha('CURRENT LEADER BODY')}},
                                   sort_keys=True), encoding='utf-8')
    before = snapshot(data)
    cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    assert len(resolves(bridge)) == 1 and 'systemPromptFile' not in resolves(bridge)[0]
    assert '--system-prompt-file' not in place.call_args.kwargs['launch']['args']
    assert snapshot(data) == before


def test_outside_a_restore_a_stale_default_home_is_never_overridden(stale, capsys):
    kwargs, bridge, place, data, home, _ = stale
    before = snapshot(data)
    with pytest.raises(SystemExit) as error:
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert 'Launch prompt source changed' in output and 'Resume by hand' in output
    assert f'--system-prompt-file "{home / "prompts" / "role.md"}"' in output
    place.assert_not_called()  # no PTY, so no 90 s "runtime unverified" stall
    assert len(resolves(bridge)) == 1  # nothing was re-resolved with an override
    assert snapshot(data) == before


def test_in_a_restore_a_stale_default_home_is_resumed_with_its_own_role(stale, recovery):
    kwargs, bridge, place, data, home, _ = stale
    before = snapshot(data)
    cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    own = str(home / 'prompts' / 'role.md')
    first, second = resolves(bridge)
    assert 'systemPromptFile' not in first and second['systemPromptFile'] == own
    args = recovery.call_args.args[0]['args']
    assert args[args.index('--system-prompt-file') + 1] == own and args.count('--system-prompt-file') == 1
    place.assert_not_called()  # the governed placement, not an ordinary split
    assert snapshot(data) == before  # the parent writes nothing; LiteTUI publishes the role when it starts


def test_in_a_restore_an_unusable_own_role_stops_before_any_pty(stale, recovery, capsys):
    kwargs, bridge, place, data, home, _ = stale
    (home / 'prompts' / 'role.md').unlink()
    with pytest.raises(SystemExit):
        cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    output = capsys.readouterr().out
    assert 'Alpha' in output and 'cannot be passed instead' in output and f'spawn --resume {AID}' in output
    recovery.assert_not_called()
    place.assert_not_called()
    assert len(resolves(bridge)) == 1


@pytest.mark.parametrize('source', ['custom', 'seed'])
def test_custom_and_seed_homes_behave_exactly_as_before(stale, recovery, source):
    kwargs, bridge, place, data, home, manifest = stale
    manifest.write_text(json.dumps({'version': 2, 'role_source': source, 'sources': {'role.md': sha('OLD LEADER BODY')}},
                                   sort_keys=True), encoding='utf-8')
    cli.cmd_spawn(**kwargs, resume_agent_id='Alpha')
    assert len(resolves(bridge)) == 1 and 'systemPromptFile' not in resolves(bridge)[0]
    assert '--system-prompt-file' not in recovery.call_args.args[0]['args']


def test_a_caller_supplied_role_file_is_never_second_guessed(stale, tmp_path):
    kwargs, bridge, place, data, home, _ = stale
    source = tmp_path / 'caller.md'
    source.write_text('Caller body', encoding='utf-8')
    cli.cmd_spawn(**kwargs, resume_agent_id='Alpha', system_prompt_file=str(source))
    assert [r.get('systemPromptFile') for r in resolves(bridge)] == [str(source)]
