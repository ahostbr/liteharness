"""Readonly exact name-index conversation COPY plan; unrelated legacy stays put.

The current names.json agent_id/convo_id pair is the selection authority, not
historical sibling discovery. Selected source identity and full inventory must
still validate. Unselected/unknown archives never acquire ownership by inference.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path

from .agent_migration import _execution, _inventory, _object, digest, file_fact
from .agent_store import AGENTS_DIR, MEMORY_FILES, MEMORIES_DIR, StoreError, _unlinked, name_key, valid_id, valid_name

POLICY = 'named-index-conversation-only/v2'
CANDIDATE = 'candidate-awaiting-per-agent-quiescence'
LEFT_LEGACY = 'left in legacy, not migrated'


def _timestamp(value) -> float:
    if type(value) in (int, float):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as exc:
            raise StoreError('Event timestamp missing or invalid') from exc
        if stamp.tzinfo is None:
            raise StoreError('Event timestamp has no timezone')
        parsed = stamp.timestamp()
    else:
        raise StoreError('Event timestamp missing or invalid')
    if not math.isfinite(parsed) or parsed <= 0:
        raise StoreError('Event timestamp missing or invalid')
    return parsed


def _last_event(path: Path) -> dict:
    before = file_fact(path)
    last = None
    meta = None
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise StoreError('Transcript has invalid JSON event') from exc
            if not isinstance(row, dict):
                raise StoreError('Transcript event is not an object')
            if row.get('type') == 'meta':
                meta = row
            last = row
    if before != file_fact(path):
        raise StoreError('Transcript changed during timestamp inspection')
    if last is None:
        raise StoreError('Transcript has no persisted event')
    stamp = last.get('ts')
    if stamp is None and last.get('type') == 'meta':
        stamp = last.get('created')
    return {'last_event_timestamp': _timestamp(stamp), 'last_event_type': last.get('type'),
            'transcript_fact': before, 'meta': meta or {}}


def named_plan(root: Path | str, *, names: dict) -> dict:
    if not isinstance(names, dict):
        raise StoreError('Name catalog must be an object')
    root = _unlinked(Path(root))
    if not root.is_absolute() or '..' in root.parts:
        raise StoreError('Absolute unlinked root required')
    source = _unlinked(root / '.convos')
    destination = _unlinked(root / AGENTS_DIR)
    result = {'policy': POLICY, 'root': str(root), 'source': str(source),
              'destination': str(destination), 'folders': [], 'agents': [],
              'catalog_conflicts': [], 'root_companions': [], 'left_in_legacy': [],
              'writer_gate': 'UNVERIFIED; per-agent independent quiescence required',
              'activated': False, 'copy_enabled': False}
    indexed = []
    by_id, by_name, by_convo = defaultdict(list), defaultdict(list), defaultdict(list)
    for name, row in names.items():
        item = {'name': name, 'agent_id': None, 'conversations': [], 'index_selection': {},
                'reasons': [], 'disposition': 'archive-only'}
        result['agents'].append(item)
        # Independently record valid associations even in malformed rows: a
        # bad name or missing pointer cannot hide a shared identity/conversation.
        try:
            valid_name(name)
            by_name[name_key(name)].append(item)
        except StoreError as exc:
            item['reasons'].append(str(exc))
        if not isinstance(row, dict):
            item['reasons'].append('Invalid name catalog row')
        else:
            try:
                identity = valid_id(row.get('agent_id'))
                item['agent_id'] = identity
                by_id[identity].append(item)
            except StoreError as exc:
                item['reasons'].append(str(exc))
            try:
                convo = valid_id(row.get('convo_id'))
                by_convo[convo].append(item)
                item.update(conversations=[convo], index_selection={'convo_id': convo})
            except StoreError as exc:
                item['reasons'].append(str(exc))
        if not item['reasons']:
            indexed.append(item)
        else:
            result['catalog_conflicts'].extend({'name': name, 'reason': reason}
                                                for reason in item['reasons'])
    # Validate readable metadata for identity collisions, but never assign an
    # unselected archive or let unknown ownership block another selected source.
    observed_names = defaultdict(set)
    entries = sorted(source.iterdir(), key=lambda p: p.name)
    folders = {}
    for directory in entries:
        folder = {'source': directory.name, 'disposition': 'archive-only', 'reasons': [],
                  'files': {}, 'directories': [], 'ownership_observed': False,
                  'migration_status': LEFT_LEGACY}
        result['folders'].append(folder)
        folders[directory.name] = folder
        try:
            _unlinked(directory)
            if not directory.is_dir():
                folder['ownership_observed'] = True
                result['root_companions'].append(directory.name)
                raise StoreError('Root companion retained archive-only')
            valid_id(directory.name)
            settings = _object(directory / 'settings.json')
            identity, saved_name = settings.get('seat_id'), settings.get('seat_name')
            folder.update(historical_agent_id=identity, historical_name=saved_name, ownership_observed=True)
            if isinstance(saved_name, str) and saved_name:
                try:
                    observed_names[name_key(saved_name)].add(valid_id(identity))
                except StoreError:
                    folder['reasons'].append('Historical owner ID unknown; no inferred ownership')
            if directory.name not in by_convo:
                folder['reasons'].append('Not selected by current name-index conversation; no inferred ownership')
        except (OSError, ValueError, StoreError) as exc:
            folder['reasons'].append(str(exc))
    for agent in indexed:
        name, identity = agent['name'], agent['agent_id']
        convo = agent['index_selection']['convo_id']
        reasons = agent['reasons']
        if (len(by_id[identity]) != 1 or len(by_name[name_key(name)]) != 1
                or len(by_convo[convo]) != 1):
            reasons.append('Name index identity/name/conversation collision')
        if observed_names[name_key(name)] - {identity}:
            reasons.append('Historical named lineage collides across agent identities')
        folder = folders.get(convo)
        if folder is None:
            reasons.append('Selected index conversation folder missing')
        else:
            try:
                directory = _unlinked(source / convo)
                if not directory.is_dir():
                    raise StoreError('Selected index conversation is not a directory')
                settings_before = file_fact(directory / 'settings.json')
                settings = _object(directory / 'settings.json')
                if settings_before != file_fact(directory / 'settings.json'):
                    raise StoreError('Settings changed during identity inspection')
                if settings.get('seat_id') != identity:
                    raise StoreError('Selected conversation agent ID disagrees with name index')
                saved_name = settings.get('seat_name')
                if saved_name and saved_name != name:
                    raise StoreError('Historical name disagrees with indexed identity')
                files, directories = _inventory(directory)
                if settings_before != files.get('settings.json'):
                    raise StoreError('Settings changed during identity inspection')
                event = _last_event(directory / 'convo.jsonl')
                if event['transcript_fact'] != files.get('convo.jsonl'):
                    raise StoreError('Transcript changed during inventory')
                meta = event.pop('meta')
                if meta.get('id') not in (None, convo):
                    raise StoreError('Transcript identity disagrees with folder')
                if meta.get('agent_id') not in (None, identity):
                    raise StoreError('Transcript agent identity disagrees with settings')
                execution = _execution(settings)
                if any(not isinstance(execution.get(k), str) or not execution[k].strip()
                       for k in ('backend', 'model', 'thinking_level')):
                    raise StoreError('Selected settings missing execution; no defaults guessed')
                folder.update(name=name, agent_id=identity, files=files, directories=directories,
                              settings_snapshot=settings, execution=execution, **event)
                memory = {p: v['sha256'] for p, v in files.items()
                          if p in MEMORY_FILES or p.startswith(MEMORIES_DIR + '/')}
                folder['memory_signature'] = digest(memory)
                agent.update(settings_source=convo, settings_last_event=event['last_event_timestamp'],
                             selected_settings_snapshot=settings, memory_conflict=False,
                             mtime_comparison_sources=[convo], mtime_selection_differs=False)
                if (destination / name).exists():
                    reasons.append('Destination exists; no overwrite')
            except (OSError, ValueError, StoreError) as exc:
                reasons.append(str(exc))
        agent['disposition'] = 'archive-only' if reasons else CANDIDATE
        if folder is not None and len(by_convo[convo]) == 1:
            folder['disposition'] = agent['disposition']
            folder['reasons'] = list(dict.fromkeys([*folder['reasons'], *reasons]))
            if not reasons:
                folder['migration_status'] = 'selected named-index source; pending offline proof'
    result['left_in_legacy'] = [{'kind': 'legacy-entry', 'source': f['source'], 'status': LEFT_LEGACY,
                                 'reasons': f['reasons'] or ['Not an eligible selected source']}
                                for f in result['folders'] if f['disposition'] != CANDIDATE]
    # Report named deferrals even when there is no source folder to enumerate.
    # indexed_convo_id records the catalog claim, not ownership of an archive.
    result['left_in_legacy'].extend({'kind': 'named-deferral', 'name': a['name'],
        'agent_id': a['agent_id'], 'indexed_convo_id': a['index_selection'].get('convo_id'),
        'status': LEFT_LEGACY, 'reasons': list(a['reasons'])}
        for a in result['agents'] if a['disposition'] != CANDIDATE)
    result['counts'] = {'legacy_entries': len(result['folders']), 'named_candidates': len(result['agents']),
        'candidate_agents': sum(a['disposition'] == CANDIDATE for a in result['agents']),
        'candidate_conversations': sum(f['disposition'] == CANDIDATE for f in result['folders']),
        'candidate_bytes': sum(v['bytes'] for f in result['folders'] if f['disposition'] == CANDIDATE for v in f['files'].values()),
        'archive_only': sum(f['disposition'] != CANDIDATE for f in result['folders']),
        'named_deferrals': sum(a['disposition'] != CANDIDATE for a in result['agents']),
        'left_in_legacy_records': len(result['left_in_legacy']),
        'actual_copy_eligible': 0, 'mtime_selection_differences': 0}
    result['reason_counts'] = dict(Counter(reason for f in result['left_in_legacy'] for reason in f['reasons']))
    result['plan_digest'] = digest(result)
    return result
