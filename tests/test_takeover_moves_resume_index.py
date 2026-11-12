"""`register --takeover` must move the name in the resume index too, not only in the registry.

Measured 2026-11-12 on a real orchestrator: the registry gave the name to the live seat, and
names.json (what a resume by name reads) still pointed at the previous holder, an agent whose
process was long gone. The command printed "name ... already belongs to agent <dead id> ... resume
it instead" and left the index naming the dead agent, so resuming the orchestrator by name would
have reopened a conversation nobody was in.
"""
import json
import os
from datetime import datetime, timezone

import pytest

from liteharness import agent_names, cli, config, hooks, seat_lifecycle

SEAT = 'aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa'
SEAT_CONVO = 'bbbbbbbb-1111-4bbb-8bbb-bbbbbbbbbbbb'
GONE = 'cccccccc-1111-4ccc-8ccc-cccccccccccc'
GONE_CONVO = 'dddddddd-1111-4ddd-8ddd-dddddddddddd'
NAME = 'Lead-Takeover'


@pytest.fixture
def seat(tmp_path, monkeypatch, capsys):
    """A live Claude seat that the registry calls NAME, while the index gives NAME to GONE."""
    for key in tuple(os.environ):
        if key.startswith(('LITEHARNESS', 'LITESUITE', 'LITETUI', 'CLAUDE', 'CODEX', 'COPILOT', 'LITECODE')):
            monkeypatch.delenv(key)
    home, root, project, data = (tmp_path / part for part in ('home', 'registry', 'project', 'data'))
    for path in (home, root, project, data):
        path.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('LITETUI_DATA_ROOT', str(data))
    monkeypatch.setenv('LITESUITE_FLEET_POLICY', str(tmp_path / 'absent-policy.json'))
    monkeypatch.setenv('LITEHARNESS_AGENT_ID', SEAT)
    monkeypatch.setenv('LITEHARNESS_REQUESTED_NAME', NAME)
    monkeypatch.setenv('LITEHARNESS_MODEL', 'claude-opus')
    monkeypatch.setenv('LITEHARNESS_CLI', 'claude-code')
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', SEAT_CONVO)
    monkeypatch.setattr(config, 'HARNESS_ROOT', root)
    monkeypatch.setattr(config, 'CONFIG_PATH', root / 'config.json')
    monkeypatch.setattr(hooks, '_LAST_PRESENCE', {})
    monkeypatch.setattr(hooks, '_resolve_session_pid', lambda existing=None: os.getpid())
    monkeypatch.setattr(seat_lifecycle, 'log_path', lambda: tmp_path / 'seat-lifecycle.jsonl')
    monkeypatch.chdir(project)
    encoded = ''.join(char if char.isalnum() else '-' for char in str(project))
    transcript = home / '.claude' / 'projects' / encoded / f'{SEAT_CONVO}.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({
        'type': 'user', 'sessionId': SEAT_CONVO, 'cwd': str(project),
        'message': {'role': 'user', 'content': 'first turn'}}) + '\n', encoding='utf-8')
    agent_names.record_name(NAME, GONE, GONE_CONVO, str(project), backend='claude')
    hooks._apply_hook_context({'hook_event_name': 'SessionStart', 'source': 'resume',
                               'session_id': SEAT_CONVO, 'transcript_path': str(transcript),
                               'cwd': str(project), 'model': 'claude-opus'})
    hooks.register_presence()
    row = json.loads((root / 'agents' / f'{SEAT}.json').read_text(encoding='utf-8'))
    assert row['name'] == NAME, 'fixture: the registry must already call this seat by the name'
    assert agent_names.resolve_name(NAME)['agent_id'] == GONE, (
        'fixture: a session hook is not an explicit takeover and must leave the index alone')
    capsys.readouterr()
    return root


def register(**options):
    cli.cmd_register(SEAT, cli='claude-code', model='claude-opus', name=NAME,
                     tier='orchestrator', session_pid=os.getpid(), **options)


def holder_row(root, **changes):
    now = datetime.now(timezone.utc).isoformat()
    row = {'agent_id': GONE, 'name': 'Someone-Else', 'cli': 'claude-code', 'session_pid': os.getppid(),
           'registered_at': now, 'last_seen': now}
    row.update(changes)
    (root / 'agents' / f'{GONE}.json').write_text(json.dumps(row), encoding='utf-8')


@pytest.mark.parametrize('holder', ['no row', 'exited row'])
def test_a_takeover_moves_the_name_off_a_holder_that_is_not_live(seat, capsys, holder):
    if holder == 'exited row':
        holder_row(seat, exited_at='gone', status='offline')
    register(takeover=True)
    captured = capsys.readouterr()
    entry = agent_names.resolve_name(NAME)
    assert (entry['agent_id'], entry['convo_id']) == (SEAT, SEAT_CONVO), (
        f'the resume index still names {entry["agent_id"]}')
    assert 'resume index unavailable' not in captured.err
    assert agent_names.name_for_agent(GONE) is None
    assert [e['name'] for e in agent_names.list_names()] == [NAME], 'one name, one entry'


def test_without_takeover_the_index_is_left_alone_and_the_refusal_is_printed(seat, capsys):
    """CONTROL, one flag apart: replacing a saved binding stays an explicit act."""
    register()
    assert agent_names.resolve_name(NAME)['agent_id'] == GONE
    assert f'already belongs to agent {GONE}' in capsys.readouterr().err


def test_a_takeover_never_takes_the_name_from_a_live_holder(seat, capsys):
    """CONTROL: the index holder is alive (under another registry name, so the registry's own
    name check does not stop this first). Its saved conversation is not handed to anyone."""
    holder_row(seat)
    register(takeover=True)
    entry = agent_names.resolve_name(NAME)
    assert (entry['agent_id'], entry['convo_id']) == (GONE, GONE_CONVO)
    assert f'already belongs to agent {GONE}' in capsys.readouterr().err


def test_a_takeover_by_the_seat_that_already_holds_the_name_only_refreshes_it(seat):
    register(takeover=True)
    created = agent_names.resolve_name(NAME)['created_at']
    register(takeover=True)
    entry = agent_names.resolve_name(NAME)
    assert (entry['agent_id'], entry['convo_id'], entry['created_at']) == (SEAT, SEAT_CONVO, created)
