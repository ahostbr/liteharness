"""Actual fresh Claude producer: registration without native index is incomplete."""
import json
from pathlib import Path

import pytest

from liteharness import agent_names, cli
from test_spawn_placement import spawn, world  # noqa: F401

NAME = 'FreshClaude589'
NATIVE = '44444444-4444-4444-8444-444444444444'


def test_actual_fresh_producer_reports_resumable_only_after_child_index_receipt(world, monkeypatch, capsys):
    # The launcher's own native session must NEVER be indexed as the child.
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    spawn(world, name=NAME, tier='worker')
    entry = agent_names.resolve_name(NAME)
    assert entry['agent_id'] == 'child' and entry['convo_id'] == NATIVE
    assert entry['cwd'] == str(world['root'])
    assert 'Spawned: registered and resumable as child' in capsys.readouterr().out


@pytest.mark.parametrize('fault', ['missing', 'empty', 'collision', 'corrupt'])
def test_actual_fresh_producer_never_reports_resumable_when_index_cannot_persist(world, monkeypatch, capsys, fault):
    bridge = cli._bridge_request

    def transport(method, path, body=None):
        result = bridge(method, path, body)
        if path == '/pty/write':
            row = json.loads((world['root'] / 'agents' / 'child.json').read_text())
            transcript = Path(row['transcript_path'])
            if fault == 'missing':
                transcript.unlink()
            elif fault == 'empty':
                transcript.write_text('', encoding='utf-8')
            elif fault == 'collision':
                agent_names.record_name(NAME, 'foreign-agent', NATIVE, str(world['root']), backend='claude')
            else:
                agent_names.index_path().write_text('{torn', encoding='utf-8')
        return result

    monkeypatch.setattr(cli, '_bridge_request', transport)
    with pytest.raises(SystemExit) as exc:
        spawn(world, name=NAME, tier='worker')
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert 'SPAWN INCOMPLETE' in output
    assert 'Spawned: registered' not in output and 'Launched claude' not in output


@pytest.mark.parametrize('initial', ['missing', 'empty', 'metadata'])
@pytest.mark.parametrize('outcome', ['ready', 'timeout', 'changed', 'dead', 'reused'])
def test_registered_child_waits_only_remaining_budget_for_exact_native_transcript(
        world, monkeypatch, capsys, initial, outcome):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import psutil

    bridge, find = cli._bridge_request, cli._find_spawned_presence
    now = cli.time.time()
    clock = {'elapsed': 0.0, 'waiting': False, 'sleeps': [], 'writes': 0}
    row_path = world['root'] / 'agents' / 'child.json'
    transcript = None
    pid = 580621
    monkeypatch.setattr(cli.time, 'time', lambda: now + clock['elapsed'])
    monkeypatch.setattr(psutil, 'pid_exists', lambda value: value == pid and not (
        clock['waiting'] and clock['sleeps'] and outcome == 'dead'))
    monkeypatch.setattr(psutil, 'Process', lambda value: SimpleNamespace(
        create_time=lambda: 2 if clock['sleeps'] and outcome == 'reused' else 1))

    def transport(method, path, body=None):
        nonlocal transcript
        result = bridge(method, path, body)
        if path == '/pty/write':
            row = json.loads(row_path.read_text())
            row.update(session_pid=pid, session_process_started_at=1000,
                       registered_at=datetime.now(timezone.utc).isoformat(),
                       last_seen=datetime.now(timezone.utc).isoformat())
            row_path.write_text(json.dumps(row))
            transcript = Path(row['transcript_path'])
            if initial == 'missing':
                transcript.unlink()
            else:
                transcript.write_text('' if initial == 'empty' else
                                      json.dumps({'sessionId': NATIVE}) + '\n', encoding='utf-8')
        return result

    def registered(*args):
        result = find(*args)
        # Registration consumed 89 of the producer's existing 90 seconds.
        clock['elapsed'] += 89
        clock['waiting'] = True
        return result

    def sleep(seconds):
        if not clock['waiting']:
            return  # shell setup is outside the registration budget
        clock['sleeps'].append(seconds)
        clock['elapsed'] += seconds
        assert clock['elapsed'] <= 90, 'transcript wait must not start a second budget'
        assert agent_names.resolve_name(NAME) is None, 'pending evidence is not resumable'
        if outcome != 'timeout':
            transcript.write_text(json.dumps({'sessionId': NATIVE, 'cwd': str(world['root'])}) + '\n',
                                  encoding='utf-8')
        if outcome == 'changed':
            row = json.loads(row_path.read_text())
            row['native_session_id'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
            row_path.write_text(json.dumps(row))

    writer = agent_names.record_name

    def record(*args, **kwargs):
        clock['writes'] += 1
        return writer(*args, **kwargs)

    monkeypatch.setattr(cli, '_bridge_request', transport)
    monkeypatch.setattr(cli, '_find_spawned_presence', registered)
    monkeypatch.setattr(cli.time, 'sleep', sleep)
    monkeypatch.setattr(agent_names, 'record_name', record)
    if outcome == 'ready':
        spawn(world, name=NAME, tier='worker')
        assert agent_names.resolve_name(NAME)['convo_id'] == NATIVE
        assert clock['writes'] == 1
        assert 'registered and resumable' in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as exc:
            spawn(world, name=NAME, tier='worker')
        assert exc.value.code == 1
        assert agent_names.resolve_name(NAME) is None
        assert clock['writes'] == 0
        assert 'SPAWN INCOMPLETE' in capsys.readouterr().out
    assert clock['sleeps'], 'pending transcript must be observed again, not immediately refused'
    assert sum(path in ('/canvas/split', '/pty/create') for _, path, _ in world['calls']) == 1
    assert not any(method == 'DELETE' for method, _, _ in world['calls'])
