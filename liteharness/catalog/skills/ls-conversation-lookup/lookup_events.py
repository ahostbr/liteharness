"""Optional bounded cinematic telemetry. This boundary alone may drop failures."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

MAX_SPOOL = 4 * 1024 * 1024
MAX_RECORD = 64 * 1024
_active = ContextVar('memory_lookup_event', default=None)


def add_hit(path, identity, text, document=False, source=None):
    try:
        _add_hit(path, identity, text, document, source)
    except Exception:
        pass  # Nonessential telemetry only.


def _add_hit(path, identity, text, document=False, source=None):
    event = _active.get()
    if event is None or len(event['hits']) >= 10:
        return
    from memory_sources import classify
    memberships = classify(path, document) if path else []
    kind = source or (memberships[0]['kind'] if memberships else ('claude-memory' if document else 'claude'))
    # Stable source-qualified identity, separate from the execution UUID.
    key = f'{kind}:{identity}'
    if any(h['id'] == key for h in event['hits']):
        return
    tokens = [t[:32] for t in re.findall(r'[^\W_]+', str(text), re.UNICODE)
              if len(t) < 128][:24]
    event['hits'].append({'source': kind, 'id': key, 'rank': len(event['hits']) + 1, 'tokens': tokens})


def fallback_keyword():
    if _active.get() is not None:
        _active.get()['fallback'] = 'keyword'
        _active.get()['mode'] = 'keyword'


@contextmanager
def capture(query, mode='keyword', seat=None):
    if _active.get() is not None:
        yield
        return
    from memory_sources import query_snapshot
    with query_snapshot(), _capture(query, mode, seat):
        yield


@contextmanager
def _capture(query, mode='keyword', seat=None):
    if _active.get() is not None:
        yield
        return
    start = time.monotonic()
    event = {'version': 1, 'id': str(uuid.uuid4()), 'ts': datetime.now(timezone.utc).isoformat(),
             'seat': (seat or os.environ.get('LITEHARNESS_AGENT_ID') or None),
             'query': str(query)[:2048], 'status': 'empty',
             'mode': mode, 'fallback': None, 'hits': [], 'durationMs': 0}
    if event['seat'] is not None:
        event['seat'] = event['seat'][:120]
    token = _active.set(event)
    try:
        yield
        event['status'] = 'complete' if event['hits'] else 'empty'
    except BaseException as error:
        if not isinstance(error, SystemExit) or error.code not in (None, 0):
            event.update(status='error', hits=[], error=str(error)[:512] or type(error).__name__)
        raise
    finally:
        _active.reset(token)
        event['durationMs'] = round((time.monotonic() - start) * 1000, 3)
        try:
            append(event)
        except Exception:
            pass  # UI/spool failure must never change retrieval stdout/exit.


def append(event):
    from memory_sources import home
    encoded = (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    if len(encoded) > MAX_RECORD:
        return False
    directory = home()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / 'lookup-events.lock', 'a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        try:
            path = directory / 'lookup-events.jsonl'
            if path.exists() and path.stat().st_size + len(encoded) > MAX_SPOOL:
                temporary = directory / f'.lookup-events-{uuid.uuid4()}.tmp'
                try:
                    temporary.write_bytes(b'')
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            with open(path, 'ab', buffering=0) as stream:
                return stream.write(encoded) == len(encoded)
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def valid_event(row):
    from memory_sources import KINDS
    def text(value, limit):
        return isinstance(value, str) and len(value) <= limit
    if not isinstance(row, dict) or type(row.get('version')) is not int or row['version'] != 1:
        return False
    if not text(row.get('id'), 256) or not row['id'] or not text(row.get('ts'), 64) or not text(row.get('query'), 2048):
        return False
    if 'seat' not in row or not (row['seat'] is None or text(row['seat'], 120)) or row.get('mode') not in ('keyword', 'semantic', 'hybrid', 'all', 'exact'):
        return False
    if 'fallback' not in row or row['fallback'] not in (None, 'keyword') or row.get('status') not in ('complete', 'empty', 'error'):
        return False
    duration = row.get('durationMs')
    import math
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        return False
    if 'error' in row and not text(row['error'], 2048):
        return False
    hits = row.get('hits')
    if not isinstance(hits, list) or len(hits) > 10 or (row['status'] != 'complete' and hits):
        return False
    for rank, hit in enumerate(hits, 1):
        if not isinstance(hit, dict) or hit.get('source') not in KINDS or not text(hit.get('id'), 8192) or type(hit.get('rank')) is not int or hit['rank'] != rank:
            return False
        tokens = hit.get('tokens')
        if not isinstance(tokens, list) or len(tokens) > 24 or not all(text(token, 32) for token in tokens):
            return False
    try:
        timestamp = datetime.fromisoformat(row['ts'].replace('Z', '+00:00'))
        return timestamp.tzinfo is not None and timestamp.utcoffset() == timedelta(0)
    except ValueError:
        return False


def recent():
    from memory_sources import home
    path = home() / 'lookup-events.jsonl'
    if not path.exists():
        return []
    with open(path, 'rb') as stream:
        # Bound memory even if an external writer corrupted/expanded the spool.
        size = os.fstat(stream.fileno()).st_size
        stream.seek(max(0, size - MAX_SPOOL))
        data = stream.read(MAX_SPOOL)
    lines = data.split(b'\n')[:-1]  # Torn final records never become events.
    if size > MAX_SPOOL:
        lines = lines[1:]
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    seen, events = set(), []
    for line in reversed(lines):
        if len(line) > MAX_RECORD:
            continue
        try:
            row = json.loads(line)
            if not valid_event(row):
                continue
            timestamp = datetime.fromisoformat(row['ts'].replace('Z', '+00:00'))
            if timestamp < cutoff or row['id'] in seen:
                continue
            seen.add(row['id'])
            events.append(row)
            if len(events) == 50:
                break
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return events
