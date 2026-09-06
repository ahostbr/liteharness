"""Regression coverage for Claude/Codex shared conversation retrieval."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / 'liteharness/catalog/skills/ls-conversation-lookup'
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location('convo_shared_test', SKILL / 'find_conversation.py')
lookup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lookup)


class SharedLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
            'CODEX_HOME': str(self.root / 'codex'),
            'LITEHARNESS_CONVO_HOME': str(self.root / 'shared'),
        })
        self.env.start()
        self.globals = patch.multiple(lookup, DATA_DIR=self.root / 'shared',
                                     DB_PATH=self.root / 'shared/convo_index.db',
                                     INDEX_STALENESS_FILE=self.root / 'shared/.last_indexed')
        self.globals.start()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.globals.stop()
        self.env.stop()
        self.temp.cleanup()

    def write(self, relative, records):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        return path

    def claude(self):
        return self.write('claude/projects/demo/claude-id.jsonl', [
            {'type': 'user', 'timestamp': '2026-01-01T00:00:00Z', 'message': {'content': 'amber squirrel'}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'OK'}]}},
        ])

    def codex(self):
        return self.write('codex/archived_sessions/rollout-2026-01-02-codex-id.jsonl', [
            {'type': 'session_meta', 'payload': {'id': 'codex-id', 'cwd': 'C:/Projects/example'}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'violet badger'}},
            {'type': 'response_item', 'timestamp': '2026-01-02T00:00:00Z', 'payload': {
                'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'violet badger'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer', 'content': 'hidden instructions'}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'read', 'arguments': '{"path":"asset.glb"}'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'mesh ready'}},
        ])

    def test_codex_visible_messages_tools_and_no_event_duplicates(self):
        file = self.codex()
        with file.open('a') as stream:
            stream.write('{"partial":')
        messages = list(lookup.parse_jsonl_messages(file))
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]['text'], 'violet badger')
        self.assertIn('[tool:read]', messages[1]['text'])
        self.assertEqual(lookup.extract_transcript(str(file))['total_tool_calls'], 1)

    def test_short_claude_message_survives(self):
        self.assertEqual([m['text'] for m in lookup.parse_jsonl_messages(self.claude())], ['amber squirrel', 'OK'])

    def test_archived_codex_lookup_uses_metadata_id_and_project(self):
        self.codex()
        found = lookup.find_conversations('codex-i')
        self.assertEqual(found[0]['uuid'], 'codex-id')
        self.assertEqual(found[0]['project'], 'C:/Projects/example')
        self.assertEqual(found[0]['provider'], 'codex')

    def test_both_providers_searchable_incremental_replacement(self):
        claude = self.claude()
        self.codex()
        lookup.cmd_index()
        lookup.cmd_index()
        conn = lookup.get_db()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 5)
        for word, expected in [('amber', 'claude-id'), ('violet', 'codex-id')]:
            row = conn.execute('SELECT m.conversation_id FROM messages m JOIN messages_fts f ON f.rowid=m.id WHERE messages_fts MATCH ?', (word,)).fetchone()
            self.assertEqual(row[0], expected)
        conn.close()
        claude.write_text(json.dumps({'type': 'user', 'message': {'content': 'replacement heron'}}) + '\n')
        os.utime(claude, (claude.stat().st_mtime + 2,) * 2)
        lookup.cmd_index()
        conn = lookup.get_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'amber'").fetchone()[0], 0)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 4)
        conn.close()

    def test_failed_embedding_does_not_mark_file_complete(self):
        self.claude()
        class BrokenEmbedder:
            def encode(self, *args, **kwargs):
                raise RuntimeError('fixture failure')
        with patch.object(lookup, '_load_embedder', return_value=BrokenEmbedder()):
            with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                lookup.cmd_index_embeddings()
        conn = lookup.get_db()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM embedded_files').fetchone()[0], 0)
        conn.close()

    def test_hybrid_ties_have_stable_session_order(self):
        for cid in ('z-session', 'a-session'):
            self.write(f'claude/projects/demo/{cid}.jsonl', [
                {'type': 'user', 'message': {'content': 'identical retrieval phrase'}}])
        lookup.cmd_index()
        with patch.object(lookup, '_print_search_results') as results:
            lookup.cmd_search_hybrid('retrieval', top_n=2)
        self.assertEqual([row[0] for row in results.call_args.args[0]],
                         ['a-session', 'z-session'])


if __name__ == '__main__':
    unittest.main()
