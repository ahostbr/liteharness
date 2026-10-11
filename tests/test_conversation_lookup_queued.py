"""T0466: a message typed while a turn runs is a user record, found like any other."""
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
spec = importlib.util.spec_from_file_location('convo_queued_test', SKILL / 'find_conversation.py')
lookup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lookup)
import conversation_sources
import ruling_index

IMAGE_DATA = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ'


def queued(uuid, prompt, **fields):
    return {'type': 'attachment', 'uuid': uuid, 'timestamp': f'2026-10-11T01:29:{len(uuid):02d}Z',
            'isSidechain': False, 'attachment': {'type': 'queued_command', 'prompt': prompt, **fields}}


HUMAN = {'commandMode': 'prompt', 'origin': {'kind': 'human'}, 'humanTurn': True}
FIXTURE = [
    queued('q-str', 'somebody placed a bunch under floor', **HUMAN),
    queued('q-image', [{'type': 'text', 'text': 'look at this crouch'},
                       {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': IMAGE_DATA}}],
           **HUMAN),
    queued('q-notification', '<task-notification> <task-id>b1</task-id> monitor event',
           commandMode='task-notification', origin={'kind': 'task-notification'}),
    queued('q-liteharness', '[LITEHARNESS] 2 message(s) received'),
    queued('q-coordinator', 'The user has added context: three runtimes', origin={'kind': 'coordinator'}),
    {'type': 'user', 'uuid': 'u-plain', 'timestamp': '2026-10-11T01:30:00Z',
     'message': {'content': 'ive picked the crouched idle 3 times'}},
    {'type': 'assistant', 'uuid': 'a-reply', 'message': {'content': [{'type': 'text', 'text': 'Moving them up now'}]}},
]
EXPECTED = [('user', 'somebody placed a bunch under floor', 'q-str'),
            ('user', 'look at this crouch', 'q-image'),
            ('user', 'ive picked the crouched idle 3 times', 'u-plain'),
            ('assistant', 'Moving them up now', 'a-reply')]


class QueuedCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
                                      'CODEX_HOME': str(self.root / 'codex'),
                                      'LITEHARNESS_CONVO_HOME': str(self.root / 'shared')})
        env.start()
        self.addCleanup(env.stop)
        names = patch.multiple(lookup, DATA_DIR=self.root / 'shared', DB_PATH=self.root / 'shared/convo_index.db',
                               INDEX_STALENESS_FILE=self.root / 'shared/.last_indexed')
        names.start()
        self.addCleanup(names.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def write(self, name, records):
        path = self.root / 'claude/projects/demo' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        return path

    def rows(self):
        with contextlib.closing(lookup.get_db()) as conn:
            return conn.execute('SELECT m.id, m.message_uuid, m.msg_type, f.content, m.timestamp, m.line_number '
                                'FROM messages m JOIN messages_fts f ON f.rowid = m.id ORDER BY m.id').fetchall()

    def test_human_queued_prompts_are_user_records_and_nothing_else_is(self):
        path = self.write('session.jsonl', FIXTURE)
        messages = list(lookup.parse_jsonl_messages(path))
        self.assertEqual([(m['type'], m['text'], m['uuid']) for m in messages], EXPECTED)
        self.assertEqual(messages[0]['timestamp'], FIXTURE[0]['timestamp'])
        self.assertEqual(messages[0]['line_number'], 1)
        self.assertFalse(any(IMAGE_DATA in m['text'] for m in messages))
        self.assertIn('[USER]: look at this crouch', lookup.extract_transcript(str(path))['transcript'])

    def test_structural_fields_decide_and_prefix_is_only_the_fallback(self):
        unprefixed_notification = queued('q-a', 'monitor event', commandMode='task-notification')
        legacy_human = queued('q-b', 'typed before structural fields existed')
        self.assertIsNone(conversation_sources.queued_human_prompt(unprefixed_notification))
        self.assertEqual(conversation_sources.queued_human_prompt(legacy_human),
                         'typed before structural fields existed')
        for prefix in ('<task-notification>', '[LITEHARNESS]', '[SYSTEM NOTIFICATION', '<system-reminder>'):
            self.assertIsNone(conversation_sources.queued_human_prompt(queued('q-c', prefix + ' x')))

    def test_search_finds_the_queued_prompt_as_user(self):
        self.write('session.jsonl', FIXTURE)
        lookup.cmd_index()
        hits = [(r[1], r[2]) for r in self.rows() if 'bunch under floor' in r[3]]
        self.assertEqual(hits, [('q-str', 'user')])

    def test_reindex_reaches_files_an_older_parser_indexed(self):
        self.write('session.jsonl', FIXTURE)
        plain_file = self.write('plain.jsonl', FIXTURE[5:])
        original = conversation_sources.normalized_records

        def older_parser(file, budget=None):
            return (r for r in original(file, budget) if r.get('attachment') is None)
        with patch.object(lookup, 'normalized_records', older_parser):
            lookup.cmd_index()
        with contextlib.closing(lookup.get_db()) as conn:
            conn.execute("DELETE FROM index_meta WHERE key = 'parser_version'")  # no older index carries it
            conn.commit()
        before = self.rows()
        self.assertFalse(any('bunch under floor' in r[3] for r in before))

        lookup.cmd_index()  # neither mtime moved
        after = self.rows()
        self.assertEqual([r[1] for r in after if 'bunch under floor' in r[3]], ['q-str'])
        with contextlib.closing(lookup.get_db()) as conn:
            plain_ids = [r[0] for r in conn.execute('SELECT id FROM messages WHERE file_path = ? ORDER BY id',
                                                    (str(plain_file),))]
        # A file without a queued_command record keeps its rows: same ids, not re-parsed.
        self.assertEqual(len(plain_ids), 2)
        self.assertTrue(set(plain_ids) <= {r[0] for r in before})
        lookup.cmd_index()
        self.assertEqual(self.rows(), after)  # the version is recorded: no third parse

    def test_embeddings_reach_files_an_older_parser_embedded(self):
        import numpy as np
        self.write('session.jsonl', FIXTURE)

        class Embedder:
            def encode(self, text, **kwargs):
                return np.array([[1.0, 0.0] for _ in text])
        original = conversation_sources.normalized_records

        def older_parser(file, budget=None):
            return (r for r in original(file, budget) if r.get('attachment') is None)
        with patch.object(lookup, '_load_embedder', return_value=Embedder()):
            with patch.object(lookup, 'normalized_records', older_parser):
                lookup.cmd_index_embeddings()
            with contextlib.closing(lookup.get_db()) as conn:
                conn.execute("DELETE FROM index_meta WHERE key = 'embedding_parser_version'")
                conn.commit()
                before = conn.execute('SELECT chunk_text FROM embeddings').fetchone()[0]
            self.assertNotIn('bunch under floor', before)
            lookup.cmd_index_embeddings()  # mtime unchanged
        with contextlib.closing(lookup.get_db()) as conn:
            after = conn.execute('SELECT chunk_text FROM embeddings').fetchone()[0]
        self.assertIn('[USER]: somebody placed a bunch under floor', after)

    def test_ruling_layer_takes_human_queued_prompts(self):
        path = self.write('session.jsonl', FIXTURE)
        rows = ruling_index._extract(path)
        self.assertEqual([(r['record_uuid'], r['origin_class'], r['text']) for r in rows], [
            ('q-str', 'human-typed', 'somebody placed a bunch under floor'),
            ('q-image', 'human-typed', 'look at this crouch'),
            ('u-plain', 'legacy-human-candidate', 'ive picked the crouched idle 3 times')])


if __name__ == '__main__':
    unittest.main()
