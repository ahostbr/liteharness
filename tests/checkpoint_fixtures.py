"""Synthetic restart contract fixtures; never use local fleet files."""
from datetime import datetime, timedelta
import json
from uuid import UUID

from liteharness.checkpoint_contract import (Coordinator, Decision, Decisions,
    Execution, ExpectedSeat, Manifest, RECOMMENDED_DECISIONS, Receipt, Request, Seat)

STAMP = '2026-01-01T00:00:00+00:00'


def uid(n):
    return str(UUID(int=n))


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def seat(root, n=1, cli='litetui', selection='explicit-offline'):
    return Seat(f'Seat-{n}', uid(n), uid(n + 100), cli,
                Execution(cli if cli != 'litetui' else 'codex', 'fixture-model', 'high'),
                str(root), str(root), 'worker', 'fixture-method', None, None,
                selection, 'idle', 'Read handoff; do not replay work', 'revision-1', None)


def manifest(root, n=500, *, complete=True):
    s = seat(root)
    decisions = Decisions(**{k: Decision(v, 'unconfirmed-recommendation')
                             for k, v in RECOMMENDED_DECISIONS.items()})
    receipts = [Receipt(uid(600 + i), uid(n), s.agent_id, kind, s.source_revision,
                        str(root / f'{kind}.json'), 'a' * 64, True, 'native', STAMP)
                for i, kind in enumerate(('handoff', 'compact', 'boundary'))]
    return Manifest(1, uid(n), (datetime.fromisoformat(STAMP) + timedelta(seconds=n - 500)).isoformat(), 'fixture-host', 'fixture-boot', {'harness': 'fixture-v1'},
                    decisions, Coordinator(s.agent_id, 'external-manual-return',
                    str(root / 'read-first.md'), 'b' * 64),
                    [ExpectedSeat(s.agent_id, s.name, s.selection)], [s], [], receipts, [], [],
                    'complete' if complete else 'incomplete')


def request(n=700, action='restore'):
    return Request(1, action, uid(n), uid(500) if action == 'restore' else None, None, [], False)


def owned(root, n=1):
    s = seat(root, n)
    home = root / '.agents' / s.name
    put(home / 'settings.json', {'schema_version': 1, 'name': s.name, 'agent_id': s.agent_id,
                               'execution': {'backend': 'codex', 'model': 'home-model', 'thinking_level': 'high'}})
    convo = home / 'conversations' / s.conversation_id
    put(convo / 'settings.json', {'seat_id': s.agent_id})
    (convo / 'convo.jsonl').write_text('{"type":"meta"}\n', encoding='utf-8')
    return s


def native(root, harness, n, cli, index):
    s = seat(root, n, cli, 'selected-current')
    root.mkdir(parents=True, exist_ok=True)
    transcript = root / f'{s.conversation_id}.jsonl'
    transcript.write_text('{"type":"meta"}\n', encoding='utf-8')
    put(harness / 'agents' / f'{s.agent_id}.json', {'agent_id': s.agent_id, 'name': s.name,
        'cli': cli, 'role': s.role, 'thinking_level': s.execution.thinking_level, 'transcript_path': str(transcript)})
    index[s.name] = {'agent_id': s.agent_id, 'convo_id': s.conversation_id,
                     'cwd': s.cwd, 'backend': cli, 'model': s.execution.model}
    return s
