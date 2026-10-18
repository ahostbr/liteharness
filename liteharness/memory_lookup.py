"""Installed machine interface to the one canonical conversation lookup engine.

One request on stdin; progress plus exactly one terminal NDJSON envelope. No
model import/installation on read or keyword paths. Human CLI output is unchanged.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

ACTIONS = {'get-config', 'save-config', 'detect', 'status', 'index', 'install-semantic', 'recent', 'search'}
SKILL = Path(__file__).parent / 'catalog/skills/ls-conversation-lookup'
MAX_REQUEST = 64 * 1024


def engine():
    # Resolve only shipped package data, never a plugin cache or checkout registry.
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location('liteharness_canonical_memory', SKILL / 'find_conversation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_request(action, request):
    if action not in ACTIONS or not isinstance(request, dict):
        raise ValueError('Unsupported action or request')
    allowed = {'save-config': {'config'}, 'index': {'sourceIds'}, 'search': {'query', 'mode', 'limit', 'seat'}}.get(action, set())
    if set(request) - allowed:
        raise ValueError('Unknown request fields')
    if action == 'save-config' and not isinstance(request.get('config'), dict):
        raise ValueError('Expected config')
    if action == 'index' and 'sourceIds' in request:
        ids = request['sourceIds']
        if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(v, str) for v in ids) or len(set(ids)) != len(ids):
            raise ValueError('Invalid source IDs')
    if action == 'search':
        if not isinstance(request.get('query'), str) or not request['query'].strip() or len(request['query']) > 2048:
            raise ValueError('Expected nonempty query up to 2048 characters')
        if request.get('mode', 'keyword') not in ('keyword', 'all', 'exact', 'semantic', 'hybrid'):
            raise ValueError('Invalid search mode')
        if type(request.get('limit', 10)) is not int or not 1 <= request.get('limit', 10) <= 50:
            raise ValueError('Limit must be 1..50')
        if 'seat' in request and (not isinstance(request['seat'], str) or len(request['seat']) > 120):
            raise ValueError('Invalid seat')


def source_statuses(api):
    import memory_sources as sources
    with sources.query_snapshot():
        return _source_statuses(api)


def _source_statuses(api):
    import memory_sources as sources
    rows = []
    config = sources.load_config()
    counts = []
    if api.DB_PATH.exists():
        with contextlib.closing(api.get_db()) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, document in [('messages', False), ('memory_content', True)]:
                if table in tables:
                    counts += [(path, count, document) for path, count in db.execute(f'SELECT file_path,count(*) FROM {table} GROUP BY file_path')]
    indexed = None
    if api.INDEX_STALENESS_FILE.exists():
        from datetime import datetime, timezone
        indexed = datetime.fromtimestamp(api.INDEX_STALENESS_FILE.stat().st_mtime, timezone.utc).isoformat()
    for source in config['sources']:
        row = {**source, 'indexedCount': 0, 'lastIndexedAt': indexed}
        root = sources.source_path(source)
        try:
            sources.unlinked(root)
            if not root.is_dir():
                raise ValueError('Source folder is missing or unreadable')
            if source['kind'] == 'litetui':
                from litetui_source import owned_files
                owned_files(root)
            else:
                next(root.iterdir(), None)  # Check readability, not successful zero.
            for path, count, document in counts:
                if any(m['id'] == source['id'] for m in sources.classify(path, document, config)):
                    row['indexedCount'] += count
        except (OSError, ValueError) as error:
            row['error'] = str(error)[:512]
        rows.append(row)
    return rows


def semantic_status(api):
    import memory_sources as sources
    directory = sources.home() / 'models/all-MiniLM-L6-v2'
    from liteharness.memory_semantic import readiness
    ready, error = readiness()
    count = 0
    if api.DB_PATH.exists():
        with contextlib.closing(api.get_db(read=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='embeddings'").fetchone():
                count = db.execute('SELECT count(*) FROM embeddings').fetchone()[0]
    return {'state': 'ready' if ready else 'not-installed',
            'backend': 'cpu' if ready else None, 'vectorCount': count}


def status(api):
    # Main enriches read-only hardware fields; CPU is the one supported path.
    return {'keyword': 'ready' if api.DB_PATH.exists() else 'unavailable',
            'sources': source_statuses(api), 'semantic': semantic_status(api),
            'recommendation': {'recommended': 'cpu', 'reason': 'CPU MiniLM ONNX is the shared lightweight optional path; GPU proxy requires an additional server and GGUF. Runtime measurements are unmeasured.', 'ramBytes': 0}}


def dispatch(action, request, emit=lambda data: None, *, semantic_child=False):
    validate_request(action, request)  # No I/O before structural validation.
    api = engine()
    import memory_sources as sources
    import lookup_events
    if action == 'get-config':
        return sources.load_config()
    if action == 'save-config':
        return sources.save_config(request['config'])
    if action == 'detect':
        return source_statuses(api)
    if action == 'status':
        return status(api)
    if action == 'recent':
        return lookup_events.recent()
    if action == 'index':
        config = sources.load_config()
        ids = request.get('sourceIds')
        if ids is not None and set(ids) - {s['id'] for s in config['sources']}:
            raise ValueError('Unknown source IDs')
        operation = 'index'
        emit({'operation': operation, 'state': 'running', 'percent': None, 'message': 'Updating canonical keyword index'})
        try:
            with sources.index_selection(ids), contextlib.redirect_stdout(io.StringIO()):
                api.cmd_index()
                api.cmd_index_memory()
            emit({'operation': operation, 'state': 'complete', 'percent': 100, 'message': 'Keyword index updated'})
        except Exception as error:
            emit({'operation': operation, 'state': 'error', 'percent': None, 'message': str(error)[:512]})
            raise
        return status(api)
    if action == 'install-semantic':
        return install_semantic(api, emit)
    if action == 'search':
        from lookup_evidence import search_evidence
        mode = request.get('mode', 'keyword')
        actual = {'keyword': 'bm25', 'exact': 'bm25'}.get(mode, mode)
        if semantic_child is not False:
            if not isinstance(semantic_child, dict):
                raise ValueError('Semantic child requires the validated adopted identity')
            from liteharness.memory_semantic import validate_child
            validate_child(semantic_child)
        if mode in ('semantic', 'hybrid') and semantic_child is False and semantic_status(api)['state'] == 'ready':
            from liteharness.memory_semantic import run_owned, base_identity
            return run_owned('search', request, base_identity())
        with lookup_events.capture(request['query'], mode, request.get('seat')):
            if mode in ('semantic', 'hybrid') and semantic_child is False and semantic_status(api)['state'] != 'ready':
                actual = 'bm25'
                lookup_events.fallback_keyword()
            with contextlib.redirect_stdout(io.StringIO()):
                return search_evidence(api, request['query'], request.get('limit', 10), None, None, actual, None, None, exact_first=mode == 'exact', packet=True)


def install_semantic(api, emit):
    """Called only by the explicit install action; never by status or retrieval."""
    operation = 'install-semantic'
    emit({'operation': operation, 'state': 'running', 'percent': None, 'message': 'Checking optional CPU semantic dependencies'})
    try:
        from liteharness.memory_semantic import install
        install(emit)
        emit({'operation': operation, 'state': 'complete', 'percent': 100, 'message': 'Optional semantic index ready'})
        return status(api)
    except Exception as error:
        emit({'operation': operation, 'state': 'error', 'percent': None, 'message': str(error)[:512]})
        raise


def main():
    terminal = None
    def progress(data):
        print(json.dumps({'type': 'progress', 'data': data}), flush=True)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError('Request exceeds 64 KiB')
        request = json.loads(raw)
        if len(sys.argv) != 2:
            raise ValueError('Expected one action')
        data = dispatch(sys.argv[1], request, progress)
        terminal = {'type': 'result', 'data': data}
    except Exception as error:
        terminal = {'type': 'error', 'message': str(error)[:2048]}
    print(json.dumps(terminal, ensure_ascii=False), flush=True)
    return 1 if terminal['type'] == 'error' else 0


if __name__ == '__main__':
    sys.exit(main())
