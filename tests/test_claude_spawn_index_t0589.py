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
