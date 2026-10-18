"""Supported source policy against real canonical FTS, never live user data."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch
import uuid

import pytest

SKILL = Path(__file__).resolve().parents[1] / 'liteharness/catalog/skills/ls-conversation-lookup'
sys.path.insert(0, str(SKILL))
import memory_sources as sources
import conversation_sources


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv('LITEHARNESS_CONVO_HOME', str(tmp_path / 'index'))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    spec = importlib.util.spec_from_file_location('memory_fixture_engine', SKILL / 'find_conversation.py')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return tmp_path, engine


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    return path


def owned(root, name='Seat', cid=None):
    cid = cid or str(uuid.uuid4())
    directory = root / '.agents' / name
    write(directory / 'settings.json', [{'schema_version': 1, 'name': name,
        'agent_id': str(uuid.uuid4()), 'execution': {'backend': 'codex', 'model': 'fixture', 'thinking_level': 'high'}}])
    return directory / 'conversations' / cid / 'convo.jsonl'


def test_config_revision_preserves_disabled_and_multiple_folders(corpus):
    root, _ = corpus
    config = sources.defaults()
    config['sources'] += [{'id': f'folder-{i}', 'kind': 'folder', 'enabled': bool(i), 'path': str(root / f'folder{i}')} for i in range(2)]
    saved = sources.save_config(config)
    assert saved['revision'] == 1
    assert sources.load_config() == saved
    with pytest.raises(ValueError, match='stale'):
        sources.save_config(config)
    assert sources.load_config() == saved


def test_invalid_config_fails_closed_before_discovery(corpus):
    root, _ = corpus
    sources.home().mkdir()
    (sources.home() / 'sources.json').write_text('{"version":1,"revision":0,"sources":[{"kind":"copilot"}]}')
    with pytest.raises(ValueError):
        list(conversation_sources.conversation_files())
    assert not (sources.home() / 'convo_index.db').exists()


def test_owned_logical_replay_archive_tool_raw_and_hidden_exclusion(corpus):
    root, engine = corpus
    config = sources.defaults()
    config['sources'].append({'id': 'owned', 'kind': 'litetui', 'enabled': True, 'path': str(root / 'tui')})
    sources.save_config(config)
    transcript = owned(root / 'tui')
    write(transcript, [
        {'type': 'meta', 'id': transcript.parent.name},
        {'type': 'msg', 'message': {'role': 'user', 'content': 'removedoriginal'}},
        {'type': 'snapshot', 'messages': [{'role': 'system', 'content': 'hiddensystem'}, {'role': 'user', 'content': 'snapshotold'}]},
        {'type': 'edit', 'index': 1, 'message': {'role': 'user', 'content': 'editedvisible'}},
        {'type': 'msg', 'message': {'role': 'assistant', 'content': 'retainedtail', 'reasoning_content': 'hiddenreasoning'}},
        {'type': 'truncate', 'keep_from': 2, 'keep_system': True, 'prepend': [{'role': 'user', 'content': 'survivingprepend'}]},
    ])
    write(transcript.with_name('tool-raw.jsonl'), [{'type': 'msg', 'message': {'role': 'user', 'content': 'rawsecret'}}])
    write(root / 'tui/.convos' / transcript.parent.name / 'convo.jsonl', [{'type': 'msg', 'message': {'role': 'user', 'content': 'archivesecret'}}])
    with contextlib.redirect_stdout(io.StringIO()):
        engine.cmd_index()
    with contextlib.closing(engine.get_db(read=True)) as db:
        texts = [r[0] for r in db.execute('SELECT content FROM messages_fts')]
        assert texts == ['survivingprepend', 'retainedtail']
        assert db.execute('SELECT count(*) FROM messages WHERE line_number IS NOT NULL').fetchone()[0] == 0


def test_sources_share_canonical_db_and_disabled_rows_retained(corpus):
    root, engine = corpus
    write(root / 'claude/projects/demo/session.jsonl', [{'type': 'user', 'message': {'content': 'amber fixture'}}])
    write(root / 'codex/sessions/rollout-fixture.jsonl', [{'type': 'session_meta', 'payload': {'id': 'codexfixture'}}, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'amber fixture'}}])
    docs = root / 'docs'
    docs.mkdir()
    (docs / 'note.txt').write_text('amber document')
    write(docs / '.convos/archive/convo.jsonl', [{'type': 'user', 'message': {'content': 'forbidden'}}])
    (docs / '.convos/forbidden.md').write_text('archivesecret')
    config = sources.defaults()
    config['sources'].append({'id': 'docs', 'kind': 'folder', 'enabled': True, 'path': str(docs)})
    config = sources.save_config(config)
    with contextlib.redirect_stdout(io.StringIO()):
        engine.cmd_index()
        engine.cmd_index_memory()
    with contextlib.closing(engine.get_db(read=True)) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM memory_content').fetchone()[0] == 1
    config['sources'][0]['enabled'] = False
    sources.save_config(config)
    with contextlib.closing(engine.get_db(read=True)) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 1
    with contextlib.closing(engine.get_db()) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 2


def test_reparse_source_rejected_without_real_link(corpus):
    root, _ = corpus
    original = Path.lstat
    def linked(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == root:
            class Linked:
                st_mode = info.st_mode
                st_file_attributes = 0x400
            return Linked()
        return info
    with patch.object(Path, 'lstat', linked):
        with pytest.raises(ValueError, match='reparse'):
            sources.validate({'version': 1, 'revision': 0, 'sources': [{'id': 'x', 'kind': 'folder', 'enabled': True, 'path': str(root)}]})


def test_archive_retained_rows_are_hidden_without_deletion(corpus):
    root, engine = corpus
    with contextlib.closing(engine.get_db(create=True)) as db:
        db.execute("INSERT INTO messages (conversation_id,project,file_path) VALUES ('old','old',?)", (str(root / '.convos/archive/convo.jsonl'),))
        db.execute("INSERT INTO messages_fts(rowid,content) VALUES (1,'archivesecret')")
        db.commit()
    with contextlib.closing(engine.get_db(read=True)) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
    with contextlib.closing(engine.get_db()) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 1
