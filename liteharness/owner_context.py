"""Transfer host-reported context usage from the status line to an owner hook.

One JSON record holds the percentage and reminder latch. Both processes use the
same kernel lease while updating it; atomic replacement alone would lose the
latch when a status refresh races parallel tool hooks. This is advisory telemetry,
not a handoff or a compaction gate. Inaccessible state simply skips this sample.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from . import config
from .agent_ownership import _KernelLease

# The status line normally refreshes in seconds. Two minutes allows scheduling
# and tool latency but refuses an old observation after an idle/resumed session.
MAX_AGE_SECONDS = 120


def _path(session_id) -> Path | None:
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
        return None
    return config.get_root() / 'context-usage' / f'{session_id}.json'


def _number(value) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(data: dict) -> None:
    """Record explicit usage or invalidate old usage; never infer capacity."""
    if not isinstance(data, dict):
        return
    context = data.get('context_window')
    used = context.get('used_percentage') if isinstance(context, dict) else None
    if not _number(used) or not 0 <= used <= 100:
        used = None
    path = _path(data.get('session_id'))
    if path is None:
        return
    try:
        if used is None and not path.exists():
            return  # No old observation to invalidate.
        path.parent.mkdir(parents=True, exist_ok=True)
        with _KernelLease(path.with_suffix('.lock')):
            previous = _read(path)
            # An unusable latest sample invalidates the observation, not the
            # latch. Only a measured below-threshold value rearms the reminder.
            latched = (previous.get('session_id') == data['session_id']
                       and previous.get('reminded') is True and (used is None or used >= 70))
            config.atomic_write_json(path, {
                'session_id': data['session_id'], 'used_percentage': used,
                'updated_at': time.time(), 'reminded': latched,
            })
    except OSError:
        pass


def reminder(session_id, role: str) -> str:
    """Claim one reminder per above-threshold interval for this exact session."""
    if role != 'owner':
        return ''
    path = _path(session_id)
    if path is None or not path.is_file():
        return ''
    try:
        with _KernelLease(path.with_suffix('.lock')):
            data = _read(path)
            stamp, used = data.get('updated_at'), data.get('used_percentage')
            if (data.get('session_id') != session_id or not _number(stamp)
                    or not 0 <= time.time() - stamp <= MAX_AGE_SECONDS
                    or not _number(used) or not 70 <= used <= 100
                    or data.get('reminded') is not False):
                return ''
            data['reminded'] = True
            config.atomic_write_json(path, data)
    except OSError:
        return ''
    return ('Owner context is at or above 70%. Rewrite NOW.md, then run '
            '`liteharness owner-handoff --memory <your folder>` and follow its reload pointer. '
            'This is a reminder only; no handoff or compaction has been performed.')
