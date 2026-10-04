"""Opt-in retrieval/evidence envelope. Legacy text retrieval stays untouched."""
import json
import hashlib
import re
import sys
import time
from contextlib import closing, redirect_stdout
from pathlib import Path

from conversation_sources import normalized_records, serialized_index, source_roots, source_signature


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}


def _hit(cid, project, timestamp, role, text, path, line, end=None, uuid=None):
    return {'kind': 'conversation', 'conversation_id': cid, 'project': project,
            'timestamp': timestamp, 'role': role, 'snippet': text[:300],
            'tier': 'ranked', 'score': 0.0, 'source': {'path': str(Path(path).resolve()) if path else None,
            'line': line, 'end_line': end or line}, 'message_uuid': uuid}


def _messages(api, conn, query, project, role, hours, date, exact, mode, top_n):
    cols = _columns(conn, 'messages')
    line = 'm.line_number' if 'line_number' in cols else 'NULL'
    select = f'SELECT m.conversation_id,m.project,m.timestamp,m.msg_type,f.content,m.file_path,{line},m.message_uuid'
    proof = ','.join('m.' + name if name in cols else 'NULL' for name in ('byte_offset', 'byte_length', 'record_hash', 'source_signature'))
    select += ',' + proof
    base = ' FROM messages_fts f JOIN messages m ON m.id=f.rowid WHERE '
    filters, params = '', []
    after, before = api._time_cutoff(hours, date)
    for clause, value in [('m.project LIKE ?', f'%{project}%' if project else None),
                          ('m.msg_type = ?', role), ('m.timestamp >= ?', after), ('m.timestamp <= ?', before)]:
        if value is not None:
            filters += ' AND ' + clause
            params.append(value)
    exact_hits = []
    if exact and query:
        for row in conn.execute(select + base + 'instr(f.content, ?) > 0' + filters + ' ORDER BY m.file_path,m.id', [query, *params]):
            hit = _hit(*row[:7], uuid=row[7])
            hit['_proof'] = row[8:12]
            hit['_indexed_text'] = row[4]
            hit['tier'] = 'exact'
            exact_hits.append(hit)
    ranked = []
    if mode != 'semantic':
        terms = re.sub(r'[^\w\s]', ' ', query).split()
        if terms:
            join = ' OR ' if mode == 'hybrid' else ' AND '
            match = join.join('"' + t + '"' for t in terms)
            limit = 200 if mode == 'hybrid' else top_n * 3
            sql = select + ',bm25(messages_fts)' + base + 'messages_fts MATCH ?' + filters + ' ORDER BY bm25(messages_fts) LIMIT ?'
            try:
                rows = conn.execute(sql, [match, *params, limit]).fetchall()
            except Exception as error:
                raise RuntimeError(f'Keyword retrieval failed: {error}') from error
            for row in rows:
                hit = _hit(*row[:7], uuid=row[7])
                hit['_proof'] = row[8:12]
                hit['_indexed_text'] = row[4]
                hit['score'] = abs(row[-1])
                ranked.append(hit)
    return exact_hits, ranked


def _vectors(api, conn, query, project, hours, date, unknowns):
    if 'embeddings' not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        unknowns.append('No embedding index available.')
        return []
    cols = _columns(conn, 'embeddings')
    provenance = 'file_path,line_start,line_end' if 'line_start' in cols else 'NULL,NULL,NULL'
    proof = ','.join(name if name in cols else 'NULL' for name in ('byte_offset', 'byte_length', 'record_hash', 'source_signature'))
    sql = 'SELECT conversation_id,project,timestamp_start,chunk_text,embedding,chunk_index,' + provenance + ',' + proof + ' FROM embeddings'
    clauses, params = [], []
    after, before = api._time_cutoff(hours, date)
    for clause, value in [('project LIKE ?', f'%{project}%' if project else None), ('timestamp_start >= ?', after), ('timestamp_start <= ?', before)]:
        if value is not None:
            clauses.append(clause)
            params.append(value)
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        unknowns.append('No vectors match this scope; semantic coverage is unavailable.')
        return []
    import numpy as np
    # Backend startup messages must not corrupt packet JSON.
    with redirect_stdout(sys.stderr):
        model = api._load_embedder()
        query_vec = model.encode(query, convert_to_numpy=True)
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    hits = []
    for cid, proj, ts, text, blob, chunk, path, start, end, offset, length, digest, signature in rows:
        hit = _hit(cid, proj, ts, None, text, path, start, end)
        vec = np.array(api._blob_to_vec(blob), dtype=np.float32)
        hit['score'] = float(np.dot(query_norm, vec / (np.linalg.norm(vec) + 1e-8)))
        hit['_proof'] = (offset, length, digest, signature)
        hit['_chunk_index'] = chunk
        hit['_chunk_text'] = text
        hits.append(hit)
    return sorted(hits, key=lambda h: (-h['score'], h['conversation_id']))


def _key(hit):
    return (hit['kind'], hit['source']['path'], hit['conversation_id'])


def _dedup(hits):
    seen, result = set(), []
    for hit in hits:
        key = _key(hit)
        if key not in seen:
            seen.add(key)
            result.append(hit)
    return result


def _memory(conn, query, role, exact, top_n):
    if not _columns(conn, 'memory_content'):
        return [], []
    select = 'SELECT mc.file_path,mc.name,mc.description,mc.type,mc.body'
    base = ' FROM memory_content mc JOIN memory_fts f ON f.rowid=mc.rowid WHERE '
    filt, params = (' AND mc.type=?', [role]) if role else ('', [])
    exact_rows = conn.execute(select + base + '(instr(mc.name,?)>0 OR instr(mc.description,?)>0 OR instr(mc.body,?)>0)' + filt + ' ORDER BY mc.file_path', [query, query, query, *params]).fetchall() if exact and query else []
    terms = re.sub(r'[^\w\s]', ' ', query).split()
    rows = conn.execute(select + ',bm25(memory_fts)' + base + 'memory_fts MATCH ?' + filt + ' ORDER BY bm25(memory_fts) LIMIT ?', [' OR '.join('"'+t+'"' for t in terms), *params, top_n]).fetchall() if terms else []
    def convert(row, literal):
        path, name, desc, kind, body = row[:5]
        return {'kind': 'memory', 'conversation_id': None, 'project': Path(path).parent.parent.name,
                'role': kind, 'timestamp': None, 'name': name, 'description': desc,
                'snippet': body[:300], 'tier': 'exact' if literal else 'ranked',
                'score': 0.0 if literal else abs(row[5]), 'source': {'path': str(Path(path).resolve()), 'line': None, 'end_line': None}}
    return [convert(r, True) for r in exact_rows], [convert(r, False) for r in rows]


def _within(budget):
    return budget['remaining'] >= 0 and time.monotonic() < budget['deadline']


def _verified(api, hit, budget):
    """Return provenance, or None; never accept after the cooperative deadline.

    Supported append-only log scope: line verified unless the file was edited in place with its timestamps restored.
    That edit pattern is out of scope; the signature does not prove prefix content.
    """
    path = hit['source']['path']
    if hit['kind'] == 'memory' or not path or not Path(path).is_file():
        return None
    before = source_signature(path)
    offset, length, digest, signature = hit.get('_proof', (None,) * 4)
    # A content hash checks only this record/span, not the preceding newline count.
    # Windows st_ctime is creation time: equal-size prefix edits with restored mtime
    # can preserve the signature and leave a stale stored line. Append-only logs
    # are the supported scope; line verified unless the file was edited in place with its timestamps restored.
    if hit['source']['line'] is not None and all(v is not None for v in (offset, length, digest, signature)):
        if before != signature or length > budget['remaining'] or not _within(budget):
            return None
        with open(path, 'rb') as stream:
            stream.seek(offset)
            raw = stream.read(length)
        budget['remaining'] -= len(raw)
        if hashlib.sha256(raw).hexdigest() == digest and source_signature(path) == before and _within(budget):
            return (hit['source']['line'], hit['source']['end_line'], offset, length, digest, before)
        return None
    # Old vectors have no trustworthy origin/span: never infer them from messages.
    if '_chunk_index' in hit:
        return None
    uuid = hit.get('message_uuid')
    if not uuid:
        return None
    matches = []
    for obj in normalized_records(path, budget=budget):
        if obj.get('isMeta') or obj.get('uuid') != uuid:
            continue
        text = api.extract_text_from_content(obj.get('message', {}).get('content', ''))
        if text != hit.get('_indexed_text') or obj.get('type') != hit['role']:
            continue
        if digest and obj['_record_hash'] != digest:
            continue
        matches.append(obj)
        if len(matches) > 1:
            return None
    # Finishing the scan is required to establish uniqueness; cap exhaustion is not EOF.
    if len(matches) != 1 or budget['remaining'] <= 0 or not _within(budget) or source_signature(path) != before:
        return None
    obj = matches[0]
    return (obj['_line_number'], obj['_line_number'], obj['_byte_offset'], obj['_byte_length'], obj['_record_hash'], before)


def _resolve(api, conn, hit, budget):
    was_unknown = hit['source']['line'] is None
    proof = _verified(api, hit, budget)
    hit['source']['line'] = proof[0] if proof else None
    hit['source']['end_line'] = proof[1] if proof else None
    return bool(proof and was_unknown)


def search_evidence(api, query, top_n, project, role, mode, hours, date, exact_first=False, packet=False, citations=False):
    if top_n <= 0:
        raise ValueError('-n must be positive')
    unknowns = ['Results describe the local index only; no hit is not proof of historical absence.']
    coverage = {'roots': [{'provider': p, 'path': str(r), 'exists': r.exists()} for p, r in source_roots()],
                'scope': 'Claude/Codex visible messages and tools; memory is Claude-only.',
                'limits': ['Keyword candidates limited to 3*N (hybrid: 200); memory ranked candidates limited to N.',
                           'Literal search is case-sensitive over indexed extracted text, not raw JSON or excluded reasoning.',
                           'Embeddings refreshed explicitly; lazy provenance scans selected hits only, capped per query.'],
                'indexed_files': 0, 'current_files': 0, 'last_indexed': None,
                'embeddings': 0, 'lazy_resolved': 0, 'provenance_unknown': 0}
    coverage['channels'] = {name: {'status': 'unavailable', 'indexed_files': 0, 'current_files': 0}
                            for name in (['memory'] if mode == 'memory' else (['conversation', 'memory'] if mode == 'all' else ['conversation']))}
    results, total = [], 0
    if not api.DB_PATH.exists():
        unknowns.append('Index missing; run --index. Nothing was searched.')
    else:
        with closing(api.get_db()) as conn:
            indexed = conn.execute('SELECT file_path,mtime FROM indexed_files').fetchall()
            coverage['indexed_files'] = len(indexed)
            coverage['current_files'] = sum(Path(p).exists() and Path(p).stat().st_mtime == m for p, m in indexed)
            if 'conversation' in coverage['channels']:
                coverage['channels']['conversation'] = {'status': 'searched' if indexed else 'empty', 'indexed_files': len(indexed), 'current_files': coverage['current_files']}
            if 'memory' in coverage['channels']:
                if _columns(conn, 'memory_content'):
                    count = conn.execute('SELECT COUNT(*) FROM memory_content').fetchone()[0]
                    memories = conn.execute('SELECT file_path,mtime FROM memory_files').fetchall() if _columns(conn, 'memory_files') else []
                    coverage['channels']['memory'] = {'status': 'searched' if count else 'empty', 'indexed_files': count,
                        'current_files': sum(Path(p).exists() and Path(p).stat().st_mtime == m for p, m in memories)}
                else:
                    unknowns.append('Requested memory index unavailable; memory channel was not searched.')
            meta = dict(conn.execute('SELECT key,value FROM index_meta'))
            coverage['last_indexed'] = meta.get('last_indexed')
            coverage['last_embedded'] = meta.get('last_embedded')
            coverage['embeddings'] = conn.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0] if _columns(conn, 'embeddings') else 0
            if coverage['current_files'] < len(indexed):
                unknowns.append('Some indexed sources are missing or changed; their line provenance is unknown.')
            literal, ranked = [], []
            if mode != 'memory':
                literal, ranked = _messages(api, conn, query, project, role, hours, date, exact_first, mode, top_n)
                if mode in ('hybrid', 'semantic'):
                    vectors = _vectors(api, conn, query, project, hours, date, unknowns)
                    if role:
                        unknowns.append('Vector chunks mix roles; --type restricts message candidates, not vector chunks.')
                    if mode == 'semantic':
                        ranked = vectors
                    else:
                        bm = {_key(h): h for h in reversed(ranked)}
                        vec = {_key(h): h for h in reversed(vectors)}
                        bmax = max((h['score'] for h in bm.values()), default=1)
                        vmax = max((h['score'] for h in vec.values()), default=1)
                        ranked = []
                        for key in bm.keys() | vec.keys():
                            hit = dict(bm.get(key) or vec[key])
                            hit['score'] = .3 * (bm[key]['score'] / bmax if key in bm and bmax > 0 else 0) + .7 * (vec[key]['score'] / vmax if key in vec and vmax > 0 else 0)
                            ranked.append(hit)
                        ranked.sort(key=lambda h: (-h['score'], h['conversation_id'], h['source']['path'] or ''))
            if mode in ('memory', 'all'):
                ml, mr = _memory(conn, query, role, exact_first, top_n)
                literal += ml
                ranked += mr
                if project or hours or date:
                    unknowns.append('Memory mode does not support project/date/hour filters (legacy contract).')
            candidates = _dedup(literal + ranked)
            total = len(candidates)
            results = candidates[:top_n]
            budget = {'remaining': api.EVIDENCE_SCAN_MAX_BYTES, 'deadline': time.monotonic() + api.EVIDENCE_SCAN_MAX_SECONDS}
            for hit in results:
                if citations:
                    try:
                        coverage['lazy_resolved'] += int(_resolve(api, conn, hit, budget))
                    except OSError as error:
                        hit['source']['line'] = hit['source']['end_line'] = None
                        unknowns.append(f"Source unavailable: {hit['source']['path']}: {error}")
                source = hit['source']
                line = source['line']
                if line is None:
                    coverage['provenance_unknown'] += 1
                suffix = str(line) if line is not None else '? (provenance unknown)'
                if line is not None and source['end_line'] != line:
                    suffix += '-' + str(source['end_line'])
                hit['citation'] = (source['path'] or '(source path unknown)') + ':' + suffix
                hit.pop('message_uuid', None)
                for key in list(hit):
                    if key.startswith('_'):
                        hit.pop(key)
    if coverage['provenance_unknown']:
        unknowns.append('Some returned lines could not be resolved: missing stable id/source, stale index, or scan byte/time cap.')
    envelope = {'schema_version': 1, 'query': query, 'mode': mode,
                'filters': {'project': project, 'type': role, 'hours': hours, 'date': date},
                'total_matches': total, 'count_scope': 'deduplicated observed candidates, not exhaustive ranked corpus',
                'truncated': total > len(results), 'results': results, 'unknowns': unknowns, 'coverage': coverage}
    if packet:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
    else:
        print(f'Found {len(results)} hits ({mode} search)')
        for i, hit in enumerate(results, 1):
            print(f"#{i} [{hit['tier']}] {hit['conversation_id'] or hit.get('name')} score={hit['score']:.4f}")
            print(f"    Project: {hit['project']}")
            if citations:
                print(f"    Source: {hit['citation']}")
            print(f"    Snippet: {hit['snippet']}")


@serialized_index
def backfill_provenance(api):
    """Human-only verification; never invent hashes or infer vector source paths."""
    with closing(api.get_db(create=True)) as conn:
        updated = 0
        rows = conn.execute('SELECT m.id,m.conversation_id,m.project,m.timestamp,m.msg_type,f.content,m.file_path,m.line_number,m.message_uuid,m.byte_offset,m.byte_length,m.record_hash,m.source_signature FROM messages m JOIN messages_fts f ON f.rowid=m.id WHERE m.line_number IS NULL OR m.record_hash IS NULL').fetchall()
        for row in rows:
            hit = _hit(*row[1:8], uuid=row[8])
            hit['_indexed_text'] = row[5]
            hit['_proof'] = row[9:13]
            budget = {'remaining': float('inf'), 'deadline': float('inf')}
            try:
                proof = _verified(api, hit, budget)
            except OSError:
                proof = None
            if proof:
                with conn:
                    conn.execute('UPDATE messages SET line_number=?,byte_offset=?,byte_length=?,record_hash=?,source_signature=? WHERE id=?', (proof[0], *proof[2:], row[0]))
                updated += 1
        print(f'Backfilled {updated} verified message records; unverified messages and legacy vector origins remain unknown.')
