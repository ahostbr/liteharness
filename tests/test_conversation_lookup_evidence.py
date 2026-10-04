"""Opt-in evidence contracts; every archive and index lives in a temp directory."""
import contextlib
import io
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

from test_conversation_lookup_shared import SharedLookupTests, lookup, SKILL


class EvidenceTests(SharedLookupTests):
    def run_cli(self, *args):
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(SKILL / 'find_conversation.py'), *args, '--no-refresh']), contextlib.redirect_stdout(output):
            lookup.main()
        return output.getvalue()

    def test_worktree_import_and_physical_lines(self):
        self.assertEqual(Path(lookup.__file__).resolve(), SKILL / 'find_conversation.py')
        file = self.codex()
        text = file.read_text(encoding='utf-8')
        file.write_text('\n{bad json}\n' + text, encoding='utf-8')
        messages = list(lookup.parse_jsonl_messages(file))
        self.assertEqual([m['line_number'] for m in messages], [5, 7, 8])

    def test_exact_first_preserves_punctuation_and_case(self):
        self.write('claude/projects/demo/literal.jsonl', [
            {'type': 'user', 'message': {'content': 'Discuss X.y! exactly, with many filler words ' * 10}}])
        self.write('claude/projects/demo/token.jsonl', [
            {'type': 'user', 'message': {'content': 'X y X y X y'}}])
        lookup.cmd_index()
        packet = json.loads(self.run_cli('--search', 'X.y!', '--packet', '-n', '1'))
        self.assertEqual(packet['results'][0]['conversation_id'], 'literal')
        self.assertEqual(packet['results'][0]['tier'], 'exact')
        self.assertTrue(packet['truncated'])
        self.assertEqual(packet['total_matches'], 2)
        lower = json.loads(self.run_cli('--search', 'x.y!', '--packet'))
        self.assertFalse(any(r['tier'] == 'exact' for r in lower['results']))

    def test_citations_filters_and_no_match_coverage(self):
        path = self.codex()
        lookup.cmd_index()
        packet = json.loads(self.run_cli('--search', 'violet badger', '--packet', '--date', '2026-01-02', '--type', 'user'))
        hit = packet['results'][0]
        self.assertEqual(hit['source']['line'], 3)
        self.assertEqual(hit['citation'], f'{path.resolve()}:3')
        self.assertIn('Source:', self.run_cli('--search', 'violet', '--citations'))
        empty = json.loads(self.run_cli('--search', 'nonexistent-literal', '--packet'))
        self.assertEqual(empty['results'], [])
        self.assertEqual(empty['coverage']['indexed_files'], 1)
        self.assertTrue(empty['unknowns'])
        filtered = json.loads(self.run_cli('--search', 'violet badger', '--packet', '--date', '2026-01-01'))
        self.assertEqual(filtered['results'], [])

    def test_old_schema_plain_bytes_and_lazy_backfill(self):
        path = self.claude()
        lookup.cmd_index()
        before = self.run_cli('--search', 'amber')
        conn = lookup.get_db()
        # Recreate precisely the old messages schema while preserving row ids/FTS.
        conn.executescript('ALTER TABLE messages RENAME TO new_messages; '
            'CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, project TEXT NOT NULL, message_uuid TEXT, timestamp TEXT, msg_type TEXT, file_path TEXT NOT NULL); '
            'INSERT INTO messages SELECT id,conversation_id,project,message_uuid,timestamp,msg_type,file_path FROM new_messages; DROP TABLE new_messages;')
        conn.close()
        self.assertEqual(before, self.run_cli('--search', 'amber'))
        packet = json.loads(self.run_cli('--search', 'amber', '--packet'))
        self.assertIsNone(packet['results'][0]['source']['line'])
        self.assertIn('provenance unknown', packet['results'][0]['citation'])
        conn = lookup.get_db(create=True)
        conn.close()
        self.assertEqual(before, self.run_cli('--search', 'amber'))
        lookup.cmd_index()  # unchanged old rows must NOT be swept/backfilled
        packet = json.loads(self.run_cli('--search', 'amber', '--packet'))
        self.assertIsNone(packet['results'][0]['source']['line'])
        import os
        os.utime(path, (path.stat().st_mtime + 2,) * 2)
        lookup.cmd_index()
        packet = json.loads(self.run_cli('--search', 'amber', '--packet'))
        self.assertEqual(packet['results'][0]['source']['line'], 1)
        self.assertEqual(before, self.run_cli('--search', 'amber'))

    def test_old_row_lazy_scan_and_explicit_backfill(self):
        path = self.write('claude/projects/demo/old.jsonl', [
            {'type': 'system'},
            {'type': 'user', 'uuid': 'old-message', 'message': {'content': 'historic evidence'}}])
        lookup.cmd_index()
        conn = lookup.get_db()
        try:
            conn.execute('UPDATE messages SET line_number=NULL')
            conn.commit()
        finally:
            conn.close()
        packet = json.loads(self.run_cli('--search', 'historic', '--packet'))
        self.assertEqual(packet['results'][0]['source']['line'], 2)
        self.assertEqual(packet['coverage']['lazy_resolved'], 1)
        with patch.object(lookup, 'EVIDENCE_SCAN_MAX_BYTES', 0):
            limited = json.loads(self.run_cli('--search', 'historic', '--packet'))
        self.assertIsNone(limited['results'][0]['source']['line'])
        self.assertEqual(limited['coverage']['provenance_unknown'], 1)
        self.run_cli('--backfill-provenance')
        conn = lookup.get_db()
        self.assertEqual(conn.execute('SELECT line_number FROM messages').fetchone()[0], 2)
        conn.close()
        self.assertEqual(packet['results'][0]['citation'], f'{path.resolve()}:2')

    def test_stubbed_vectors_spans_old_chunks_and_hybrid(self):
        import numpy as np
        path = self.codex()
        lookup.cmd_index()
        class Embedder:
            def encode(self, text, **kwargs):
                if isinstance(text, list):
                    return np.array([[1.0, 0.0] for _ in text])
                return np.array([1.0, 0.0])
        with patch.object(lookup, '_load_embedder', return_value=Embedder()):
            lookup.cmd_index_embeddings()
            for mode in ('semantic', 'hybrid'):
                packet = json.loads(self.run_cli('--search', 'concept', '--mode', mode, '--packet', '--date', '2026-01-02'))
                self.assertEqual(packet['results'][0]['source'], {'path': str(path.resolve()), 'line': 3, 'end_line': 6})
            conn = lookup.get_db()
            try:
                conn.execute('UPDATE embeddings SET file_path=NULL,line_start=NULL,line_end=NULL')
                conn.commit()
            finally:
                conn.close()
            packet = json.loads(self.run_cli('--search', 'concept', '--mode', 'semantic', '--packet'))
            self.assertIsNone(packet['results'][0]['source']['path'])
            self.assertIsNone(packet['results'][0]['source']['line'])
            self.run_cli('--backfill-provenance')
            conn = lookup.get_db()
            try:
                self.assertEqual(conn.execute('SELECT line_start,line_end FROM embeddings').fetchone(), (None, None))
            finally:
                conn.close()

    def test_missing_or_changed_source_never_fabricates_line(self):
        path = self.claude()
        lookup.cmd_index()
        path.unlink()
        packet = json.loads(self.run_cli('--search', 'amber', '--packet'))
        self.assertEqual(packet['results'][0]['source']['path'], str(path.resolve()))
        self.assertIsNone(packet['results'][0]['source']['line'])
        self.assertEqual(packet['coverage']['current_files'], 0)

    def test_older_replaced_source_is_unknown(self):
        import os
        path = self.claude()
        lookup.cmd_index()
        previous = path.stat().st_mtime
        path.write_text('changed source\n', encoding='utf-8')
        os.utime(path, (previous - 10,) * 2)
        packet = json.loads(self.run_cli('--search', 'amber', '--packet'))
        self.assertIsNone(packet['results'][0]['source']['line'])
        self.assertEqual(packet['coverage']['current_files'], 0)

    def test_ambiguous_embedding_backfill_stays_unknown(self):
        import numpy as np
        for folder in ('sessions', 'archived_sessions'):
            self.write(f'codex/{folder}/rollout-same.jsonl', [
                {'type': 'session_meta', 'payload': {'id': 'same', 'cwd': 'demo'}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'identical chunk'}}])
        class Embedder:
            def encode(self, text, **kwargs):
                return np.array([[1.0, 0.0] for _ in text])
        lookup.cmd_index()
        with patch.object(lookup, '_load_embedder', return_value=Embedder()):
            lookup.cmd_index_embeddings()
        conn = lookup.get_db()
        try:
            conn.execute('UPDATE embeddings SET file_path=NULL,line_start=NULL,line_end=NULL')
            conn.commit()
        finally:
            conn.close()
        self.run_cli('--backfill-provenance')
        conn = lookup.get_db()
        try:
            self.assertIsNone(conn.execute('SELECT line_start FROM embeddings').fetchone()[0])
        finally:
            conn.close()

    def test_additive_migration_concurrent_open(self):
        self.claude()
        lookup.cmd_index()
        from concurrent.futures import ThreadPoolExecutor
        def open_schema(_):
            conn = lookup.get_db(create=True)
            try:
                return conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(open_schema, range(4))), [2] * 4)

    def test_bare_argument_is_still_prefix_lookup(self):
        self.claude()
        self.assertIn('UUID:     claude-id', self.run_cli('claude-i'))
        self.assertNotIn('schema_version', self.run_cli('claude-i'))

    def test_all_memory_evidence_and_legacy_output(self):
        self.claude()
        memory = self.root / 'claude/projects/demo/memory/topic.md'
        memory.parent.mkdir(parents=True)
        memory.write_text('---\nname: amber\n---\namber squirrel\n', encoding='utf-8')
        lookup.cmd_index()
        with patch.object(lookup, 'PROJECTS_DIR', self.root / 'claude/projects'):
            lookup.cmd_index_memory()
        legacy = self.run_cli('--search', 'amber', '--mode', 'all')
        self.assertIn('=== Conversation Results ===', legacy)
        packet = json.loads(self.run_cli('--search', 'amber', '--mode', 'all', '--packet'))
        self.assertEqual({r['kind'] for r in packet['results']}, {'conversation', 'memory'})
        hit = next(r for r in packet['results'] if r['kind'] == 'memory')
        self.assertEqual(hit['source']['path'], str(memory.resolve()))
        self.assertIsNone(hit['source']['line'])

    def test_parent_segmentation_and_codex_generated_ids(self):
        # Verbatim records() from 0597794; no git/archive dependency in CI.
        def parent_records(file):
            with open(file, encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue  # Active logs can end in a torn JSON record.
                    if isinstance(record, dict):
                        yield record
        import conversation_sources
        records = [
            {'type': 'session_meta', 'payload': {'id': 'native', 'cwd': 'demo'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'first'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': 'second'}}]
        path = self.root / 'segmentation.jsonl'
        for separator in ('\n', '\r\n', '\r'):
            path.write_bytes(separator.join(json.dumps(r) for r in records).encode('utf-8'))
            def old_adapter(file, **kwargs):
                for number, record in enumerate(parent_records(file), 1):
                    record['_line_number'] = number
                    yield record
            with patch.object(conversation_sources, 'records', old_adapter):
                previous = list(conversation_sources.normalized_records(path))
            current = list(conversation_sources.normalized_records(path))
            for old, new in zip(previous, current):
                self.assertEqual({k: new[k] for k in old}, old)
            self.assertEqual(len(current), len(previous))

    def test_same_mtime_replacement_and_read_race(self):
        import os
        path = self.write('claude/projects/demo/proof.jsonl', [
            {'type': 'user', 'uuid': 'proof-id', 'message': {'content': 'original evidence'}}])
        lookup.cmd_index()
        stamp = path.stat().st_mtime
        path.write_text(json.dumps({'type': 'user', 'uuid': 'proof-id', 'message': {'content': 'replacement text'}}) + '\n', encoding='utf-8')
        os.utime(path, (stamp, stamp))
        packet = json.loads(self.run_cli('--search', 'original', '--packet'))
        self.assertIsNone(packet['results'][0]['source']['line'])
        path.write_text(json.dumps({'type': 'user', 'uuid': 'proof-id', 'message': {'content': 'original evidence'}}) + '\n', encoding='utf-8')
        os.utime(path, (stamp + 2,) * 2)
        lookup.cmd_index()
        import lookup_evidence
        original_open = open
        def racing_open(file, *args, **kwargs):
            if Path(file) == path:
                path.write_text('{}\n', encoding='utf-8')
            return original_open(file, *args, **kwargs)
        with patch.object(lookup_evidence, 'open', side_effect=racing_open, create=True):
            raced = json.loads(self.run_cli('--search', 'original', '--packet'))
        self.assertIsNone(raced['results'][0]['source']['line'])

    def test_legacy_uuid_text_unique_and_ambiguous(self):
        path = self.write('claude/projects/demo/duplicates.jsonl', [
            {'type': 'user', 'uuid': 'same', 'message': {'content': 'first text'}},
            {'type': 'user', 'uuid': 'same', 'message': {'content': 'second text'}}])
        lookup.cmd_index()
        conn = lookup.get_db()
        try:
            conn.execute('UPDATE messages SET line_number=NULL,byte_offset=NULL,byte_length=NULL,record_hash=NULL,source_signature=NULL')
            conn.commit()
        finally:
            conn.close()
        with patch.object(lookup, 'EVIDENCE_SCAN_MAX_BYTES', len(path.read_bytes())):
            capped = json.loads(self.run_cli('--search', 'second', '--packet'))
        self.assertIsNone(capped['results'][0]['source']['line'])
        unique = json.loads(self.run_cli('--search', 'second', '--packet'))
        self.assertEqual(unique['results'][0]['source']['line'], 2)
        self.run_cli('--backfill-provenance')
        conn = lookup.get_db()
        try:
            self.assertEqual(conn.execute('SELECT line_number FROM messages ORDER BY id').fetchall(), [(1,), (2,)])
        finally:
            conn.close()
        with path.open('ab') as stream:
            stream.write((json.dumps({'type': 'user', 'uuid': 'same', 'message': {'content': 'second text'}}) + '\n').encode())
        import os
        os.utime(path, (path.stat().st_mtime + 2,) * 2)
        lookup.cmd_index()
        conn = lookup.get_db()
        try:
            conn.execute('UPDATE messages SET line_number=NULL,byte_offset=NULL,byte_length=NULL,record_hash=NULL,source_signature=NULL')
            conn.commit()
        finally:
            conn.close()
        ambiguous = json.loads(self.run_cli('--search', 'second', '--packet'))
        self.assertIsNone(ambiguous['results'][0]['source']['line'])

    def test_memory_channels_absent_empty_nonmatching(self):
        self.claude()
        lookup.cmd_index()
        conn = lookup.get_db()
        conn.executescript('DROP TABLE memory_content; DROP TABLE memory_files; DROP TABLE memory_fts;')
        conn.close()
        absent = json.loads(self.run_cli('--search', 'unrelated', '--mode', 'all', '--packet'))
        self.assertEqual(absent['coverage']['channels']['memory']['status'], 'unavailable')
        conn = lookup.get_db(create=True)
        conn.close()
        empty = json.loads(self.run_cli('--search', 'unrelated', '--mode', 'memory', '--packet'))
        self.assertEqual(empty['coverage']['channels']['memory']['status'], 'empty')
        memory = self.root / 'claude/projects/demo/memory/memo.md'
        memory.parent.mkdir(parents=True)
        memory.write_text('different content', encoding='utf-8')
        with patch.object(lookup, 'PROJECTS_DIR', self.root / 'claude/projects'):
            lookup.cmd_index_memory()
        nonmatching = json.loads(self.run_cli('--search', 'unrelated', '--mode', 'memory', '--packet'))
        self.assertEqual(nonmatching['coverage']['channels']['memory']['status'], 'searched')
        self.assertEqual(nonmatching['coverage']['channels']['memory']['indexed_files'], 1)
        self.assertEqual(nonmatching['results'], [])

    def test_legacy_changed_text_and_after_deadline_unknown(self):
        import os
        path = self.write('claude/projects/demo/legacy.jsonl', [
            {'type': 'user', 'uuid': 'legacy-id', 'message': {'content': 'original text'}}])
        lookup.cmd_index()
        conn = lookup.get_db()
        try:
            conn.execute('UPDATE messages SET line_number=NULL,byte_offset=NULL,byte_length=NULL,record_hash=NULL,source_signature=NULL')
            conn.commit()
        finally:
            conn.close()
        import lookup_evidence
        real_records = lookup_evidence.normalized_records
        now = [0.0]
        def delayed(*args, **kwargs):
            for record in real_records(*args, **kwargs):
                now[0] = 2.0
                yield record
        with patch.object(lookup_evidence.time, 'monotonic', side_effect=lambda: now[0]), patch.object(lookup_evidence, 'normalized_records', side_effect=delayed):
            expired = json.loads(self.run_cli('--search', 'original', '--packet'))
        self.assertIsNone(expired['results'][0]['source']['line'])
        stamp = path.stat().st_mtime
        path.write_text(json.dumps({'type': 'user', 'uuid': 'legacy-id', 'message': {'content': 'changed text'}}) + '\n', encoding='utf-8')
        os.utime(path, (stamp, stamp))
        changed = json.loads(self.run_cli('--search', 'original', '--packet'))
        self.assertIsNone(changed['results'][0]['source']['line'])

    def test_large_record_linear_scan_and_segmentation(self):
        import time
        import conversation_sources
        path = self.root / 'large.jsonl'
        large = json.dumps({'type': 'user', 'message': {'content': 'x' * (20 * 1024 * 1024)}}).encode()
        path.write_bytes(large + b'\r\n' + b'{"type":"assistant","message":{"content":"tail"}}\r')
        scanned = [0]
        pattern = conversation_sources._RECORD_END
        class CountingPattern:
            def search(self, buffer, position=0):
                match = pattern.search(buffer, position)
                scanned[0] += (match.end() if match else len(buffer)) - position
                return match
        start = time.perf_counter()
        with patch.object(conversation_sources, '_RECORD_END', CountingPattern()):
            rows = list(conversation_sources.records(path))
        elapsed = time.perf_counter() - start
        with contextlib.redirect_stdout(sys.__stdout__):
            print(f'20MiB fixture: elapsed={elapsed:.3f}s scanned_bytes={scanned[0]} source_bytes={path.stat().st_size}')
        self.assertLess(elapsed, 5.0)
        self.assertLess(scanned[0], path.stat().st_size * 2)
        self.assertEqual([r['_line_number'] for r in rows], [1, 2])
        self.assertEqual(rows[1]['message']['content'], 'tail')
        self.assertEqual(rows[1]['_byte_offset'], len(large) + 2)

    def _equal_size_prefix_edit_limit(self, vector=False):
        """Option C pins an excluded mutation, not a never-wrong-line guarantee."""
        import hashlib
        import os
        import conversation_sources
        import lookup_evidence
        path = self.root / 'claude/projects/demo/prefix-limit.jsonl'
        path.parent.mkdir(parents=True)
        target = json.dumps({'type': 'user', 'uuid': 'prefix-target',
                             'message': {'content': 'prefix limit target'}}).encode('utf-8') + b'\n'
        original = b'{}\n{}\n' + target
        path.write_bytes(original)
        stamp = path.stat().st_mtime_ns
        signature = conversation_sources.source_signature(path)
        # Model Windows creation-time semantics deterministically on every OS.
        with patch.object(conversation_sources, 'source_signature', return_value=signature), patch.object(lookup_evidence, 'source_signature', return_value=signature):
            lookup.cmd_index()
            if vector:
                import numpy as np
                class Embedder:
                    def encode(self, text, **kwargs):
                        if isinstance(text, list):
                            return np.array([[1.0, 0.0] for _ in text])
                        return np.array([1.0, 0.0])
                embedder = Embedder()
                with patch.object(lookup, '_load_embedder', return_value=embedder):
                    lookup.cmd_index_embeddings()
            conn = lookup.get_db()
            try:
                table = 'embeddings' if vector else 'messages'
                proof = conn.execute(f'SELECT byte_offset,byte_length,record_hash FROM {table}').fetchone()
                self.assertEqual(proof, (6, len(target), hashlib.sha256(target).hexdigest()))
            finally:
                conn.close()
            with path.open('r+b') as stream:
                stream.write(b'{}   \n')  # six bytes still, but one delimiter instead of two
            os.utime(path, ns=(stamp, stamp))
            self.assertEqual(path.stat().st_mtime_ns, stamp)
            self.assertEqual(path.stat().st_size, len(original))
            self.assertEqual(path.read_bytes()[6:], target)
            actual = list(lookup.parse_jsonl_messages(path))
            self.assertEqual(actual[0]['line_number'], 2)
            self.assertEqual(actual[0]['byte_offset'], proof[0])
            self.assertEqual(actual[0]['record_hash'], proof[2])
            if vector:
                with patch.object(lookup, '_load_embedder', return_value=embedder):
                    packet = json.loads(self.run_cli('--search', 'unrelated concept', '--mode', 'semantic', '--packet'))
            else:
                packet = json.loads(self.run_cli('--search', 'prefix limit target', '--packet'))
            # Accepted option-C limit: selected bytes still verify, stored line is stale.
            self.assertEqual(packet['results'][0]['source']['line'], 3)
            self.assertEqual(packet['results'][0]['source']['end_line'], 3)
            self.assertEqual(packet['results'][0]['citation'], f'{path.resolve()}:3')
        docs = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('line verified unless the file was edited in place with its timestamps restored', docs)
        self.assertIn('st_ctime is creation time', docs)
        self.assertIn('append-only', docs)
        self.assertIn('out of scope', docs)
        self.assertIn('line verified unless the file was edited in place with its timestamps restored', lookup_evidence._verified.__doc__)

    def test_message_equal_size_prefix_edit_documented_limit(self):
        self._equal_size_prefix_edit_limit()

    def test_vector_equal_size_prefix_edit_documented_limit(self):
        self._equal_size_prefix_edit_limit(vector=True)
