"""Canonical source policy and atomic configuration. No model or UI dependencies."""
import json
import os
from pathlib import Path
import stat
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar

_selected = ContextVar('memory_index_source_ids', default=None)
_query = ContextVar('memory_query_source_snapshot', default=None)


@contextmanager
def query_snapshot():
    """One coherent source policy per query; never cache across queries/saves."""
    token = _query.set({'checked': set(), 'config': None})
    try:
        yield
    finally:
        _query.reset(token)


@contextmanager
def index_selection(ids):
    token = _selected.set(None if ids is None else set(ids))
    try:
        yield
    finally:
        _selected.reset(token)


def index_sources(config=None):
    return [s for s in (config or load_config())['sources']
            if _selected.get() is None or s['id'] in _selected.get()]


KINDS = ('claude', 'codex', 'claude-memory', 'litetui', 'obsidian', 'folder')


def home():
    return Path(os.environ.get('LITEHARNESS_CONVO_HOME', Path.home() / '.liteharness/conversations'))


def unlinked(path):
    path = Path(path)
    checked = _query.get()['checked'] if _query.get() is not None else set()
    for part in (path, *path.parents):
        if part in checked:
            continue
        try:
            info = part.lstat()
        except FileNotFoundError:
            checked.add(part)
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked/reparse source paths are not supported')
        checked.add(part)
    if any(p.casefold() == '.convos' for p in path.parts):
        raise ValueError('.convos archives are not a supported memory source')
    return path


def validate(config):
    if not isinstance(config, dict) or type(config.get('version')) is not int or config['version'] != 1 or type(config.get('revision')) is not int or config['revision'] < 0:
        raise ValueError('Expected source config version 1 and nonnegative revision')
    sources = config.get('sources')
    if not isinstance(sources, list) or len(sources) > 100:
        raise ValueError('Expected at most 100 sources')
    result, ids = [], set()
    for entry in sources:
        if not isinstance(entry, dict):
            raise ValueError('Invalid source')
        identity, kind = entry.get('id'), entry.get('kind')
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 120 or identity in ids:
            raise ValueError('Source IDs must be unique nonempty strings')
        if kind not in KINDS or type(entry.get('enabled')) is not bool:
            raise ValueError('Unsupported source kind or enabled flag')
        row = {'id': identity, 'kind': kind, 'enabled': entry['enabled']}
        if 'path' in entry:
            raw = entry['path']
            if not isinstance(raw, str) or not raw.strip() or '\x00' in raw or len(raw) > 4096:
                raise ValueError('Invalid source path')
            path = Path(raw)
            if not path.is_absolute() or '..' in path.parts:
                raise ValueError('Source path must be absolute without traversal')
            unlinked(path)
            row['path'] = str(path)
        elif kind in ('litetui', 'obsidian', 'folder'):
            raise ValueError('This source requires an explicit path')
        ids.add(identity)
        result.append(row)
    return {'version': 1, 'revision': config['revision'], 'sources': result}


def defaults():
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude'))
    codex = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    return {'version': 1, 'revision': 0, 'sources': [
        {'id': 'claude', 'kind': 'claude', 'enabled': True, 'path': str(claude / 'projects')},
        {'id': 'codex', 'kind': 'codex', 'enabled': True, 'path': str(codex)},
        {'id': 'claude-memory', 'kind': 'claude-memory', 'enabled': True, 'path': str(claude / 'projects')},
    ]}


def load_config():
    snapshot = _query.get()
    if snapshot is not None and snapshot['config'] is not None:
        return snapshot['config']
    path = home() / 'sources.json'
    result = validate(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else validate(defaults())
    if snapshot is not None:
        snapshot['config'] = result
    return result


def save_config(config):
    config = validate(config)  # Validate before creating anything.
    from conversation_sources import serialized_index

    @serialized_index
    def persist():
        if config['revision'] != load_config()['revision']:
            raise ValueError('Source configuration revision is stale; reload before saving')
        result = {**config, 'revision': config['revision'] + 1}
        descriptor, temporary = tempfile.mkstemp(prefix='sources-', suffix='.tmp', dir=home())
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, home() / 'sources.json')
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return result
    return persist()


def source_path(source):
    if 'path' in source:
        return Path(source['path'])
    return Path(next(row['path'] for row in defaults()['sources'] if row['kind'] == source['kind']))


def conversation_roots(config=None):
    result = []
    for source in index_sources(config):
        if not source['enabled']:
            continue
        root = source_path(source)
        if source['kind'] == 'claude':
            result.append(('claude', root))
        elif source['kind'] == 'codex':
            result.extend(('codex', root / name) for name in ('sessions', 'archived_sessions'))
        elif source['kind'] == 'litetui':
            result.append(('litetui', root))
    return result


def walk_files(root, extensions):
    """Never descend links or .convos, including links inside a configured folder."""
    root = unlinked(root)
    if not root.is_dir():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d.casefold() != '.convos' and _safe(Path(directory) / d)]
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() in extensions and _safe(path):
                yield path


def _safe(path):
    try:
        unlinked(path)
        return True
    except (OSError, ValueError):
        return False


def documents():
    seen = set()
    for source in index_sources():
        if not source['enabled']:
            continue
        root, kind = source_path(source), source['kind']
        if kind == 'claude-memory':
            for path in walk_files(root, {'.md'}) or []:
                if path.parent.name == 'memory' and path.name != 'MEMORY.md':
                    key = str(path).casefold() if os.name == "nt" else str(path)
                    if key not in seen:
                        seen.add(key)
                        yield path, path.parent.parent.name
        elif kind in ('obsidian', 'folder'):
            for path in walk_files(root, {'.md', '.txt'}) or []:
                key = str(path).casefold() if os.name == "nt" else str(path)
                if key not in seen:
                    seen.add(key)
                    yield path, source['id']


def classify(path, document=False, config=None):
    """Return all matching configured memberships, disabled entries included."""
    if not path:
        return []
    path = unlinked(Path(path))
    result = []
    for source in (config or load_config())['sources']:
        kind = source['kind']
        if document != (kind in ('claude-memory', 'obsidian', 'folder')):
            continue
        try:
            path.relative_to(source_path(source))
        except ValueError:
            continue
        result.append(source)
    return result


def policy(config=None):
    config = config or load_config()
    owned = {}

    def allowed(path, document=0):
        if not path:
            return True  # Retained unknown provenance is not silently erased.
        try:
            matches = classify(path, bool(document), config)
            if not matches:
                # Preserve unknown legacy provenance, but reject all archive paths.
                return not (home() / 'sources.json').exists()
            for source in matches:
                if not source['enabled']:
                    continue
                if source['kind'] == 'litetui':
                    from litetui_source import owned_files
                    key = source['id']
                    if key not in owned:
                        owned[key] = {str(p) for p in owned_files(source_path(source))}
                    if str(path) not in owned[key]:
                        continue
                return True
            return False
        except (ValueError, OSError):
            return False
    return allowed
