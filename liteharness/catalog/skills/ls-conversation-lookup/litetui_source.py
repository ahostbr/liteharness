"""Read-only owned LiteTUI adapter. Replay contract: ConversationRepository.read.

Ownership uses the shipped canonical agent store, not a runtime LiteTUI import.
Logical edits have no single physical message record: provenance stays unknown.
"""
from datetime import datetime, timezone
from pathlib import Path


def owned_files(root):
    from liteharness.agent_store import AgentStore, _unlinked
    store = AgentStore(root)
    files, ids = [], set()
    for agent in store.list_agents():
        for directory in store.list_conversations(agent):
            if directory.name in ids:
                raise ValueError('Conversation has ambiguous owned membership')
            ids.add(directory.name)
            path = _unlinked(directory / 'convo.jsonl')
            if path.is_file():
                files.append(path)
    return files


def replay(file, budget=None):
    from conversation_sources import records
    messages = []
    for rec in records(file, budget):
        kind = rec.get('type')
        if kind == 'snapshot':
            candidate = rec.get('messages')
            if isinstance(candidate, list) and all(isinstance(m, dict) and isinstance(m.get('role'), str) for m in candidate):
                messages = list(candidate)
        elif kind == 'edit':
            index = rec.get('index')
            if isinstance(index, int) and 0 <= index < len(messages) and isinstance(rec.get('message'), dict):
                messages[index] = rec['message']
        elif kind == 'truncate':
            keep = rec.get('keep_from')
            if not isinstance(keep, int) or keep < 0:
                continue
            head = [messages[0]] if rec.get('keep_system') and messages and messages[0].get('role') == 'system' else []
            prepend = [m for m in (rec.get('prepend') or []) if isinstance(m, dict)]
            messages = head + prepend + messages[keep:]
        elif kind == 'msg' and isinstance(rec.get('message'), dict):
            messages.append(rec['message'])
    for index, msg in enumerate(messages):
        role = msg.get('role')
        if role not in ('user', 'assistant', 'tool'):
            continue  # Never index system/developer instructions or hidden reasoning.
        content = msg.get('content', '')
        if isinstance(content, list):
            content = [{'type': 'text', 'text': b.get('text', '')} for b in content
                       if isinstance(b, dict) and b.get('type') in ('text', 'input_text', 'output_text')]
        if not isinstance(content, (str, list)):
            continue
        # No physical span claims after replay; stable logical ordinal is not a line.
        yield {'type': 'user' if role == 'tool' else role,
               'message': {'content': content}, 'uuid': f'logical:{index}',
               'timestamp': msg.get('timestamp', ''), '_line_number': None,
               '_byte_offset': None, '_byte_length': None, '_record_hash': None,
               '_source_signature': None}
