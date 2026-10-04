"""Provider adapters for local conversation archives. Never modifies source logs."""
import json
import hashlib
import os
import re
import time
from functools import wraps
from pathlib import Path


def serialized_index(function):
    """One writer across provider entrypoints; OS releases the lock on a crash."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        directory = Path(os.environ.get('LITEHARNESS_CONVO_HOME', Path.home() / '.liteharness' / 'conversations'))
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / 'index.lock', 'a+b') as lock:
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            deadline = time.monotonic() + 120
            while True:
                try:
                    lock.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Another conversation index update is running; retry after it finishes.')
                    time.sleep(0.2)
            try:
                return function(*args, **kwargs)
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)
    return wrapped


def source_roots():
    home = Path.home()
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude'))
    codex = Path(os.environ.get('CODEX_HOME', home / '.codex'))
    return [('claude', claude / 'projects'),
            ('codex', codex / 'sessions'),
            ('codex', codex / 'archived_sessions')]


def conversation_files():
    seen = set()
    for provider, root in source_roots():
        if not root.exists():
            continue
        for file in root.rglob('*.jsonl'):
            key = str(file.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                yield file


def source_signature(file):
    stat = Path(file).stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"


_RECORD_END = re.compile(br'\r\n|\n|\r(?!$)')


def raw_records(file, budget=None):
    """Linear universal-newline segmentation, retaining offsets and exact bytes."""
    with open(file, 'rb') as stream:
        buffer, offset, number, eof = bytearray(), 0, 0, False
        start, scan_from = 0, 0
        while True:
            # Delay a trailing CR until the next byte disambiguates CRLF.
            match = _RECORD_END.search(buffer, scan_from)
            if match:
                end = match.end()
            elif eof and len(buffer) > start:
                end = len(buffer)
            else:
                end = None
            if end is not None:
                raw = bytes(buffer[start:end])
                number += 1
                yield number, offset + start, raw
                start = scan_from = end
                # Compact only at chunk boundaries, not once per small record.
                if start == len(buffer) or start >= 65536:
                    del buffer[:start]
                    offset += start
                    start = scan_from = 0
                continue
            if eof:
                return
            scan_from = max(start, len(buffer) - 1)
            if budget is not None:
                if budget['remaining'] <= 0 or time.monotonic() >= budget['deadline']:
                    return
                amount = min(65536, budget['remaining'])
            else:
                amount = 65536
            block = stream.read(amount)
            if budget is not None:
                budget['remaining'] -= len(block)
            buffer.extend(block)
            eof = not block


def records(file, budget=None):
    signature = source_signature(file)
    for number, offset, raw in raw_records(file, budget):
        try:
            record = json.loads(raw.decode('utf-8', errors='replace'))
        except (ValueError, TypeError):
            continue  # Active logs can end in a torn JSON record.
        if isinstance(record, dict):
            record.update({'_line_number': number, '_byte_offset': offset,
                           '_byte_length': len(raw), '_record_hash': hashlib.sha256(raw).hexdigest(),
                           '_source_signature': signature})
            yield record


def identity(file):
    file = Path(file)
    provider, project = 'claude', file.parent.name
    for kind, root in source_roots():
        try:
            relative = file.relative_to(root)
        except ValueError:
            continue
        provider = kind
        project = relative.parts[0] if kind == 'claude' else 'unknown'
        break
    if provider == 'codex' or file.name.startswith('rollout-'):
        # Native Codex filenames contain a timestamp before the real session UUID.
        for record in records(file):
            if record.get('type') == 'session_meta':
                meta = record.get('payload', {})
                return 'codex', meta.get('id', file.stem), meta.get('cwd', project)
            # Session metadata is at the beginning; do not scan entire large logs.
            break
        match = re.search(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', file.stem)
        return 'codex', match.group() if match else file.stem, project
    return provider, file.stem, project


def normalized_records(file, budget=None):
    """Adapt Codex response items to Claude's message envelope.

    Ignore Codex event_msg mirrors, system/developer instructions and reasoning
    records. response_item is the authoritative source for visible messages and
    tool exchanges, so messages are not indexed twice.
    """
    for number, record in enumerate(records(file, budget=budget)):
        kind = record.get('type')
        if kind in ('user', 'assistant'):
            yield record
            continue
        if kind != 'response_item':
            continue
        item = record.get('payload', {})
        item_kind = item.get('type')
        role, content = item.get('role'), []
        if item_kind == 'message' and role in ('user', 'assistant'):
            raw = item.get('content', [])
            if isinstance(raw, str):
                content = raw
            else:
                content = [{'type': 'text', 'text': block.get('text', '')}
                           for block in raw if isinstance(block, dict)
                           and block.get('type') in ('input_text', 'output_text', 'text')]
        elif item_kind in ('function_call', 'custom_tool_call'):
            role = 'assistant'
            arguments = item.get('arguments', item.get('input', ''))
            try:
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
            except ValueError:
                arguments = {'input': arguments}
            content = [{'type': 'tool_use', 'name': item.get('name', ''), 'input': arguments}]
        elif item_kind in ('function_call_output', 'custom_tool_call_output'):
            role = 'user'
            content = [{'type': 'tool_result', 'content': item.get('output', '')}]
        else:
            continue
        yield {'type': role, 'message': {'content': content},
               'timestamp': record.get('timestamp', ''),
               **{key: value for key, value in record.items() if key.startswith('_')},
               'uuid': item.get('id') or f"{item.get('call_id', 'message')}:{number}"}
