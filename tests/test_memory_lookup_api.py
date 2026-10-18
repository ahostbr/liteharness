"""Installed machine API and bounded telemetry, using isolated canonical fixtures."""
import concurrent.futures
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from test_memory_lookup_sources import corpus, owned, write, SKILL
from liteharness import memory_lookup
import lookup_events as events
import memory_sources as sources


def run_api(root, action, request, env=None):
    process_env = {**os.environ, 'LITEHARNESS_CONVO_HOME': str(root / 'index'),
                   'CLAUDE_CONFIG_DIR': str(root / 'claude'), 'CODEX_HOME': str(root / 'codex'),
                   'PYTHONPATH': str(SKILL.parents[3]), **(env or {})}
    return subprocess.run([sys.executable, '-m', 'liteharness.memory_lookup', action], input=json.dumps(request),
                          capture_output=True, text=True, env=process_env, timeout=30)


def test_no_model_fresh_keyword_all_sources_machine_contract(corpus):
    root, _ = corpus
    write(root / 'claude/projects/demo/fixture.jsonl', [{'type': 'user', 'message': {'content': 'canary amber'}}])
    write(root / 'codex/sessions/rollout-fixture.jsonl', [{'type': 'session_meta', 'payload': {'id': 'codexfixture'}}, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'canary amber'}}])
    transcript = owned(root / 'tui')
    write(transcript, [{'type': 'msg', 'message': {'role': 'user', 'content': 'canary amber'}}])
    config = sources.defaults()
    config['sources'].append({'id': 'tui', 'kind': 'litetui', 'enabled': True, 'path': str(root / 'tui')})
    sources.save_config(config)
    # A broken/missing semantic stack cannot affect any read/keyword path.
    def deny_model(name, *args, **kwargs):
        if name.startswith(('onnxruntime', 'numpy', 'tokenizers', 'huggingface_hub', 'liteharness.embed_service')):
            raise AssertionError('Unexpected model import ' + name)
        return original_import(name, *args, **kwargs)
    import builtins
    original_import = builtins.__import__
    with patch.object(builtins, '__import__', deny_model):
        for action in ('get-config', 'detect', 'status', 'recent'):
            memory_lookup.dispatch(action, {})
        updates = []
        result = memory_lookup.dispatch('index', {}, updates.append)
        assert result['semantic']['state'] == 'not-installed'
        assert [p['state'] for p in updates] == ['running', 'complete']
        result = memory_lookup.dispatch('search', {'query': 'canary'})
        assert len(result['results']) == 3
    assert {hit['source'] for hit in events.recent()[0]['hits']} == {'claude', 'codex', 'litetui'}
    assert not (sources.home() / 'models').exists()
    proc = run_api(root, 'search', {'query': 'canary'})
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [line['type'] for line in lines] == ['result']
    assert len(lines[0]['data']['results']) == 3


def test_validation_one_terminal_error_and_no_io(corpus):
    root, _ = corpus
    proc = run_api(root, 'search', {'query': 'x', 'mode': 'injected; shell'})
    assert proc.returncode == 1
    assert [json.loads(line)['type'] for line in proc.stdout.splitlines()] == ['error']
    assert not sources.home().exists()
    proc = run_api(root, 'index', {'sourceIds': ['unknown']})
    assert proc.returncode == 1
    assert not (sources.home() / 'convo_index.db').exists()


def test_keyword_fallback_is_truthful_without_constructing_model(corpus):
    root, _ = corpus
    write(root / 'claude/projects/demo/fixture.jsonl', [{'type': 'user', 'message': {'content': 'amber canary'}}])
    memory_lookup.dispatch('index', {})
    result = memory_lookup.dispatch('search', {'query': 'amber', 'mode': 'semantic'})
    assert result['results']
    event = events.recent()[0]
    assert event['mode'] == 'keyword' and event['fallback'] == 'keyword'


def test_events_complete_empty_error_and_spool_failure_preserves_cli(corpus):
    root, engine = corpus
    write(root / 'claude/projects/demo/fixture.jsonl', [{'type': 'user', 'message': {'content': 'amber canary'}}])
    memory_lookup.dispatch('index', {})
    for query, expected in [('amber', 'complete'), ('absentword', 'empty')]:
        memory_lookup.dispatch('search', {'query': query})
        assert events.recent()[0]['status'] == expected
    with pytest.raises(RuntimeError), events.capture('bad'):
        raise RuntimeError('bounded failure')
    assert events.recent()[0]['status'] == 'error'
    args = ['find_conversation.py', '--search', 'amber', '--no-refresh']
    with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()) as baseline:
        engine.main()
    with patch.object(events, 'append', side_effect=OSError('disk full')), patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()) as failed:
        engine.main()
    assert failed.getvalue() == baseline.getvalue()


def test_route_inventory_once_per_logical_query(corpus):
    root, engine = corpus
    write(root / 'claude/projects/demo/fixture-id.jsonl', [{'type': 'user', 'message': {'content': 'amber canary'}}])
    note = root / 'claude/projects/demo/memory/note.md'
    note.parent.mkdir()
    note.write_text('amber memory')
    memory_lookup.dispatch('index', {})
    routes = [
        ['--search', 'amber'], ['--search', 'amber', '--mode', 'memory'],
        ['--search', 'amber', '--mode', 'all'], ['--search', 'amber', '--exact-first'],
        ['--search', 'amber', '--packet'], ['--search', 'amber', '--mode', 'hybrid'],
        ['fixture-id'], ['fixture-id', '--extract'],
    ]
    for route in routes:
        before = len(events.recent())
        with patch.object(sys, 'argv', ['find_conversation.py', *route, '--no-refresh']), contextlib.redirect_stdout(io.StringIO()):
            engine.main()
        assert len(events.recent()) == before + 1, route
        assert events.recent()[0]['status'] == 'complete', route
    with patch.object(sys, 'argv', ['find_conversation.py', '--stats']), contextlib.redirect_stdout(io.StringIO()):
        engine.main()
    assert len(events.recent()) == len(routes)


def test_corrupt_partial_duplicate_and_bounded_concurrent_spool(corpus):
    root, _ = corpus
    def emit(index):
        with events.capture('query' * 1000, seat='seat' * 100):
            for hit in range(100):
                events.add_hit(None, str(hit), 'token ' * 1000)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(emit, range(100)))
    path = sources.home() / 'lookup-events.jsonl'
    assert 0 < path.stat().st_size <= events.MAX_SPOOL
    for event in events.recent():
        assert len(event['query']) <= 2048 and len(event['seat']) <= 120
        assert len(event['hits']) <= 10
        assert all(len(h['tokens']) <= 24 and all(len(t) <= 32 for t in h['tokens']) for h in event['hits'])
        assert len(json.dumps(event).encode()) <= events.MAX_RECORD
    last = events.recent()[0]
    with path.open('ab') as stream:
        stream.write(b'corrupt\n')
        stream.write(json.dumps(last).encode() + b'\n')
        stream.write(b'{"type":')
    assert len({e['id'] for e in events.recent()}) == len(events.recent())
    assert events.recent()[0]['id'] == last['id']
    # Writer rotates before exceeding its hard bound, replacing actual file identity.
    before = path.stat().st_ino
    path.write_bytes(b'x' * events.MAX_SPOOL)
    emit(0)
    assert path.stat().st_size <= events.MAX_SPOOL
    assert path.stat().st_ino != before


def test_optional_install_error_progress_no_global_pip(corpus):
    root, _ = corpus
    updates = []
    with patch.object(memory_lookup.importlib.util, 'find_spec', return_value=None), patch.object(sys, 'prefix', sys.base_prefix), patch('subprocess.run') as run:
        with pytest.raises(RuntimeError, match='adopted installed'):
            memory_lookup.dispatch('install-semantic', {}, updates.append)
    run.assert_not_called()
    assert [p['state'] for p in updates] == ['running', 'error']
    assert not (sources.home() / 'models').exists()


def test_query_snapshot_does_not_hide_next_config_disable_or_invalid_edit(corpus):
    root, _ = corpus
    write(root / 'claude/projects/demo/fixture.jsonl', [{'type': 'user', 'message': {'content': 'snapshotcanary'}}])
    memory_lookup.dispatch('index', {})
    assert memory_lookup.dispatch('search', {'query': 'snapshotcanary'})['results']
    config = sources.load_config()
    config['sources'][0]['enabled'] = False
    sources.save_config(config)
    assert not memory_lookup.dispatch('search', {'query': 'snapshotcanary'})['results']
    (sources.home() / 'sources.json').write_text('{invalid json')
    with pytest.raises(ValueError):
        memory_lookup.dispatch('search', {'query': 'snapshotcanary'})
