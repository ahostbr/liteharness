"""Behavioral regressions from independent Dijkstra review of 4e901fd."""
import contextlib
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_memory_lookup_sources import corpus, owned, write
from liteharness import memory_lookup, memory_semantic
import memory_sources as sources
import lookup_events as events


def test_real_logical_chunker_and_vector_writer_keep_null_provenance(corpus):
    import numpy as np
    root, engine = corpus
    config = sources.defaults()
    config['sources'].append({'id': 'owned', 'kind': 'litetui', 'enabled': True, 'path': str(root / 'tui')})
    sources.save_config(config)
    path = owned(root / 'tui')
    write(path, [{'type': 'snapshot', 'messages': [{'role': 'user', 'content': 'oldtext'}]},
                 {'type': 'edit', 'index': 0, 'message': {'role': 'user', 'content': 'replayedcanary'}},
                 {'type': 'msg', 'message': {'role': 'assistant', 'content': 'tailcanary'}}])
    class Encoder:
        def encode(self, texts, **kwargs):
            assert texts == ['[USER]: replayedcanary\n[ASSISTANT]: tailcanary']
            return np.asarray([[1., 0.] for text in texts])
    with patch.object(engine, '_load_embedder', return_value=Encoder()), contextlib.redirect_stdout(io.StringIO()):
        engine.cmd_index_embeddings()
        engine.cmd_index_embeddings()
    with contextlib.closing(engine.get_db()) as db:
        row = db.execute('SELECT chunk_text,line_start,line_end,byte_offset,byte_length,record_hash,source_signature FROM embeddings').fetchone()
        assert row == ('[USER]: replayedcanary\n[ASSISTANT]: tailcanary', None, None, None, None, None, None)
        assert db.execute('SELECT count(*) FROM embeddings').fetchone()[0] == 1


def test_successful_hybrid_does_not_reopen_database_for_telemetry(corpus):
    root, engine = corpus
    write(root / 'claude/projects/demo/fixture.jsonl', [{'type': 'user', 'message': {'content': 'amber canary'}}])
    with contextlib.redirect_stdout(io.StringIO()):
        engine.cmd_index()
    original = engine.get_db
    calls = []
    def once(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) > 1:
            raise OSError('enrichment database unavailable')
        return original(*args, **kwargs)
    with contextlib.redirect_stdout(io.StringIO()) as baseline:
        engine.cmd_search_hybrid('amber')
    with patch.object(engine, 'get_db', once), contextlib.redirect_stdout(io.StringIO()) as checked:
        engine.cmd_search_hybrid('amber')
    assert len(calls) == 1
    assert checked.getvalue() == baseline.getvalue()


def test_known_download_systemexit_becomes_progress_and_one_terminal_error(corpus):
    root, _ = corpus
    helper = SimpleNamespace(download_model=lambda _directory: sys.exit(1))
    def install(_emit):
        memory_semantic.download_artifacts(root / 'never-downloaded')
    stdin = SimpleNamespace(buffer=io.BytesIO(b'{}'))
    with patch.dict(sys.modules, {'liteharness.embed_service': helper}), patch.object(memory_semantic, 'install', install), patch.object(sys, 'stdin', stdin), patch.object(sys, 'argv', ['memory_lookup', 'install-semantic']), contextlib.redirect_stdout(io.StringIO()) as output:
        assert memory_lookup.main() == 1
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r['type'] for r in records] == ['progress', 'progress', 'error']
    assert records[-2]['data']['state'] == 'error'
    assert 'exit 1' in records[-1]['message']
    assert not (root / 'never-downloaded').exists()


def test_recent_machine_api_skips_invalid_v1_and_preserves_valid_neighbors(corpus):
    root, _ = corpus
    with events.capture('valid canary'):
        events.add_hit(None, 'real-hit', 'canary')
    valid = events.recent()[0]
    variants = []
    for field in ('seat', 'query', 'mode', 'fallback', 'durationMs'):
        row = {**valid, 'id': field}
        row.pop(field)
        variants.append(row)
    variants += [{**valid, 'id': 'rank', 'hits': [{**valid['hits'][0], 'rank': 2}]},
                 {**valid, 'id': 'tokens', 'hits': [{**valid['hits'][0], 'tokens': ['x' * 33]}]},
                 {**valid, 'id': 'source', 'hits': [{**valid['hits'][0], 'source': 'copilot'}]},
                 {**valid, 'id': 'bad-duration', 'durationMs': True}]
    with (sources.home() / 'lookup-events.jsonl').open('a', encoding='utf-8') as stream:
        for row in variants:
            stream.write(json.dumps(row) + '\n')
        stream.write(json.dumps({**valid, 'id': 'neighbor'}) + '\n')
    assert [row['id'] for row in memory_lookup.dispatch('recent', {})] == ['neighbor', valid['id']]
