"""Named-policy COPY tool. Never called during implementation; merge gate first.

Registry and exact bridge PTY checks are readonly observations. Unavailable
identity authority defers safely. Publication is exclusive on Windows;
legacy source is never written, chmodded, moved or removed. Runtime activation is
not performed here. Review/merge approval is a caller-supplied auditable receipt,
not something this module may manufacture or infer from a clean test run.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .agent_migration import (_copy_file, _inventory, _object, _relative,
                              _write_once_json, digest, file_fact)
from .agent_migration_policy import CANDIDATE, POLICY, _last_event, _timestamp, named_plan
from .agent_store import (AGENTS_DIR, INITIALIZING_NAME, MEMORY_FILES, MEMORIES_DIR,
                          SCHEMA_VERSION, StoreError, _unlinked, valid_id, valid_name)


def _pty_rows() -> list[dict]:
    # Existing authenticated bridge client loads configured URL/token. Readonly
    # GET only; no hardcoded port, registration, mutation or process inspection.
    from .cli import _bridge_request
    response = _bridge_request('GET', '/pty/list')
    if not isinstance(response, dict) or response.get('error') or response.get('ok') is False:
        raise StoreError('Bridge unavailable or failed; defer')
    rows = response.get('sessions')
    if not isinstance(rows, list):
        raise StoreError('Bridge PTY list malformed; defer')
    identities = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']
                or row['id'] in identities or type(row.get('pid')) is not int or row['pid'] <= 0):
            raise StoreError('Bridge PTY row malformed or duplicate; defer')
        identities.add(row['id'])
        identity = row.get('harnessAgentId')
        if identity is not None:
            valid_id(identity)
    return rows


def _offline(agent: dict, *, registry_root: Path, source_root: Path) -> dict:
    # source_root retained in signature for explicit caller context, but writer
    # safety is the full selected source BEFORE/AFTER hashes, not process proofs.
    registry = _unlinked(registry_root / 'agents')
    if not registry.is_dir():
        raise StoreError('Authoritative registry cannot be inspected; defer')
    observed = []
    directories = [registry]
    retired = _unlinked(registry_root / 'retired-agents')
    if retired.exists():
        if not retired.is_dir():
            raise StoreError('Retired registry cannot be inspected; defer')
        directories.append(retired)
    for directory in directories:
        try:
            # Materialize the entire error-propagating scan before absence can
            # be asserted. glob may suppress PermissionError on some versions.
            entries = list(directory.iterdir())
        except OSError as exc:
            raise StoreError('Registry enumeration unavailable; defer') from exc
        for path in sorted(entry for entry in entries if entry.suffix == '.json'):
            row = _object(path)
            row_id, row_name = row.get('agent_id'), row.get('name')
            if row_id == agent['agent_id'] or row_name == agent['name'] or path.stem == agent['agent_id']:
                if row_id != agent['agent_id'] or row_name not in (None, agent['name']):
                    raise StoreError('Agent registry identity ambiguous; defer')
                status = row.get('status')
                exited = row.get('exited_at')
                if exited is not None:
                    _timestamp(exited)
                explicitly_retired = directory == retired
                if status not in (None, 'offline', 'retired', 'exited'):
                    raise StoreError('Agent registry is live/unknown; defer')
                if not (explicitly_retired or status in {'offline', 'retired', 'exited'} or exited):
                    raise StoreError('Agent registry is not explicitly offline/retired; defer')
                observed.append({'registry': str(path), 'status': status,
                                 'retired_registry': explicitly_retired})
    rows = _pty_rows()
    if any(row.get('harnessAgentId') == agent['agent_id'] for row in rows):
        raise StoreError('Exact live PTY exists for this agent ID; defer')
    return {'registry_offline_or_retired': bool(observed), 'registry_proven_absent': not observed,
            'no_exact_live_pty': True, 'bridge_rows_observed': len(rows),
            'bridge_rows_without_agent_identity': sum(row.get('harnessAgentId') is None for row in rows),
            'identity_field': 'harnessAgentId exact canonical UUID only',
            'observed_registry': observed,
            'caveat': 'PTY absence means no exact bridge harnessAgentId match, not absence of all OS writers.'}


def _verify_sources(root: Path, agent: dict, folders: list[dict]) -> None:
    for folder in folders:
        directory = _unlinked(root / '.convos' / valid_id(folder['source']))
        files, directories = _inventory(directory)
        if files != folder['files'] or directories != folder['directories']:
            raise StoreError('Agent source hashes/inventory changed; regenerate readonly manifest')
        event = _last_event(directory / 'convo.jsonl')
        if event['last_event_timestamp'] != folder['last_event_timestamp']:
            raise StoreError('Last persisted event changed')
        settings = _object(directory / 'settings.json')
        if settings != folder['settings_snapshot'] or settings.get('seat_id') != agent['agent_id']:
            raise StoreError('Agent source identity/settings changed')
    # Index selection, not inferred historical siblings, defines the lineage.
    # _verify_policy revalidates that pointer and all shared collision guards.


def _verify_destination(directory: Path, agent: dict, folders: list[dict], settings: dict, receipt: dict) -> None:
    for folder in folders:
        copied_files, copied_dirs = _inventory(directory / 'conversations' / folder['source'])
        if set(copied_files) != set(folder['files']) or copied_dirs != folder['directories']:
            raise StoreError('Copied conversation inventory mismatch')
        for relative, fact in folder['files'].items():
            current = copied_files[relative]
            if (current['bytes'], current['sha256']) != (fact['bytes'], fact['sha256']):
                raise StoreError('Copied conversation hash mismatch')
    canonical = folders[0]  # the sole indexed conversation supplies agent memory
    for relative, fact in canonical['files'].items():
        if relative in MEMORY_FILES or relative.startswith(MEMORIES_DIR + '/'):
            current = file_fact(directory / relative)
            if (current['bytes'], current['sha256']) != (fact['bytes'], fact['sha256']):
                raise StoreError('Copied agent memory hash mismatch')
    if _object(directory / 'settings.json') != settings:
        raise StoreError('Copied agent settings mismatch')
    if _object(directory / '.migration-receipt.json') != receipt:
        raise StoreError('Copied migration receipt mismatch')
    expected_files = {'settings.json', '.migration-receipt.json'}
    expected_dirs = {'conversations'}
    for folder in folders:
        prefix = 'conversations/' + folder['source']
        expected_dirs.add(prefix)
        expected_files.update(prefix + '/' + relative for relative in folder['files'])
        expected_dirs.update(prefix + '/' + relative for relative in folder['directories'])
    expected_files.update(relative for relative in canonical['files']
                          if relative in MEMORY_FILES or relative.startswith(MEMORIES_DIR + '/'))
    expected_dirs.update(relative for relative in canonical['directories']
                        if relative == MEMORIES_DIR or relative.startswith(MEMORIES_DIR + '/'))
    all_files, all_dirs = _inventory(directory)
    # COPY never activates: retain the blocking marker in both staging and
    # destination, even after successful byte verification. Activation is outside
    # this tool and never inferred from a copy receipt.
    if INITIALIZING_NAME not in all_files:
        raise StoreError('Migration destination must remain copy-inactive')
    expected_files.add(INITIALIZING_NAME)
    if set(all_files) != expected_files or set(all_dirs) != expected_dirs:
        raise StoreError('Destination exact inventory mismatch')


def _verify_policy(root: Path, names: dict, agent: dict, folders: list[dict]) -> None:
    current = named_plan(root, names=names)
    matches = [row for row in current['agents'] if row['agent_id'] == agent['agent_id']]
    if len(matches) != 1:
        raise StoreError('Current policy no longer proves candidate')
    actual = matches[0]
    current_folders = [row for row in current['folders'] if row.get('agent_id') == agent['agent_id']]
    # Idempotent rerun only: destination existence is validated separately by
    # exact receipt and inventory, never used to relax any source policy reason.
    for row in [actual, *current_folders]:
        row['reasons'] = [reason for reason in row['reasons'] if reason != 'Destination exists; no overwrite']
        row['disposition'] = 'archive-only' if row['reasons'] else CANDIDATE
    for row in current_folders:
        if row['disposition'] == CANDIDATE:
            row['migration_status'] = 'selected named-index source; pending offline proof'
    if actual != agent or current_folders != folders:
        raise StoreError('Current policy/settings/memory/index changed or manifest forged')


def copy_named_agent(manifest: dict, *, agent_id: str, registry_root: Path,
                     merge_receipt: dict) -> dict:
    """Copy ONE independently offline candidate, after approved code merge.

    Receipt requires explicit authority, merged commit, review evidence and exact
    policy plan digest. These values are audit evidence, not a magic boolean.
    Callers must validate actual merge authority externally before invoking. The
    tool itself rechecks registry/exact bridge PTY/source hashes before staging/publication.
    """
    if not isinstance(manifest, dict):
        raise StoreError('Named-policy manifest must be an object')
    if (not isinstance(merge_receipt, dict) or merge_receipt.get('plan_digest') != manifest.get('plan_digest')
            or any(not isinstance(merge_receipt.get(k), str) or not merge_receipt[k].strip()
                   for k in ('authority', 'merged_commit', 'review_evidence'))
            or re.fullmatch('[0-9a-f]{40}', merge_receipt['merged_commit']) is None):
        raise StoreError('Reviewed merged-code approval receipt required before COPY')
    if manifest.get('policy') != POLICY:
        raise StoreError('Named-policy manifest required')
    if digest({k: v for k, v in manifest.items() if k != 'plan_digest'}) != manifest['plan_digest']:
        raise StoreError('Named policy plan digest mismatch')
    if os.name != 'nt':
        raise StoreError('Exclusive directory publication requires Windows')
    identity = valid_id(agent_id)
    if (not isinstance(manifest.get('agents'), list) or not isinstance(manifest.get('folders'), list)
            or any(not isinstance(row, dict) for row in [*manifest['agents'], *manifest['folders']])):
        raise StoreError('Invalid named-policy collection schema')
    matches = [a for a in manifest['agents'] if a.get('agent_id') == identity]
    if len(matches) != 1 or matches[0]['disposition'] != CANDIDATE:
        raise StoreError('Agent is archive-only/ambiguous, not a policy candidate')
    agent = matches[0]
    root = _unlinked(Path(manifest['root']))
    if not root.is_absolute() or '..' in root.parts or Path(manifest['destination']) != root / AGENTS_DIR:
        raise StoreError('Invalid named copy root/destination')
    name = valid_name(agent['name'])
    registry_root = _unlinked(Path(registry_root))
    if not registry_root.is_absolute():
        raise StoreError('Registry root must be absolute')
    # Current index is membership pointer; never backend/model/settings authority.
    names = _object(registry_root / 'names.json')
    if (not isinstance(names.get(name), dict) or names[name].get('agent_id') != identity
            or agent.get('index_selection') != {'convo_id': names[name].get('convo_id')}
            or agent.get('conversations') != [names[name].get('convo_id')]):
        raise StoreError('Current name index no longer matches candidate selection')
    all_sources = [folder.get('source') for folder in manifest['folders']]
    if any(not isinstance(source, str) for source in all_sources) or len(all_sources) != len(set(all_sources)):
        raise StoreError('Ambiguous duplicate folder source')
    folders = [f for f in manifest['folders'] if f.get('agent_id') == identity
               and f.get('disposition') == CANDIDATE]
    if (not folders or not isinstance(agent.get('conversations'), list)
            or len(agent['conversations']) != len(set(agent['conversations']))
            or {f['source'] for f in folders} != set(agent['conversations'])):
        raise StoreError('Candidate conversation membership mismatch')
    _verify_policy(root, names, agent, folders)
    for folder in folders:
        valid_id(folder['source'])
        for relative in [*folder['files'], *folder['directories']]:
            _relative(relative)
    evidence = _offline(agent, registry_root=registry_root, source_root=root / '.convos')
    _verify_sources(root, agent, folders)
    settings = dict(agent['selected_settings_snapshot'])
    selected_execution = next(f['execution'] for f in folders if f['source'] == agent['settings_source'])
    # Indexed conversation settings remain intact; add owned schema/identity and
    # normalized required authority fields without dropping any nested preference.
    historical_execution = settings.get('execution')
    settings.update(schema_version=SCHEMA_VERSION, name=name, agent_id=identity,
                    execution={**(historical_execution if isinstance(historical_execution, dict) else {}),
                               **selected_execution})
    receipt = {'plan_digest': manifest['plan_digest'], 'policy': POLICY, 'agent_id': identity,
               'index_selection': agent['index_selection'],
               'merge_receipt': merge_receipt, 'settings_source': agent['settings_source']}
    target = _unlinked(root / AGENTS_DIR / name)
    if target.exists():
        if _object(target / '.migration-receipt.json') != receipt:
            raise StoreError('Existing destination is not this publication; no overwrite')
        _verify_destination(target, agent, folders, settings, receipt)
    else:
        staging = _unlinked(root / '.agents-migration-staging' / manifest['plan_digest'] / name)
        staging.mkdir(parents=True, exist_ok=True)
        marker = _unlinked(staging / INITIALIZING_NAME)
        if not marker.exists():
            with marker.open('x', encoding='utf-8') as handle:
                handle.write('Copy incomplete; do not activate.\n')
                handle.flush()
                os.fsync(handle.fileno())
        for folder in folders:
            directory = staging / 'conversations' / folder['source']
            _unlinked(directory).mkdir(parents=True, exist_ok=True)
            for relative in folder['directories']:
                _unlinked(directory / relative).mkdir(parents=True, exist_ok=True)
            for relative, fact in folder['files'].items():
                _copy_file(root / '.convos' / folder['source'] / relative, directory / relative, fact)
        canonical = folders[0]
        for relative in canonical['directories']:
            if relative == MEMORIES_DIR or relative.startswith(MEMORIES_DIR + '/'):
                _unlinked(staging / relative).mkdir(parents=True, exist_ok=True)
        for relative, fact in canonical['files'].items():
            if relative in MEMORY_FILES or relative.startswith(MEMORIES_DIR + '/'):
                _copy_file(root / '.convos' / canonical['source'] / relative, staging / relative, fact)
        _write_once_json(staging / 'settings.json', settings)
        _write_once_json(staging / '.migration-receipt.json', receipt)
        _verify_destination(staging, agent, folders, settings, receipt)
        evidence = _offline(agent, registry_root=registry_root, source_root=root / '.convos')
        _verify_sources(root, agent, folders)
        _verify_policy(root, _object(registry_root / 'names.json'), agent, folders)
        _unlinked(root / AGENTS_DIR).mkdir(exist_ok=True)
        staging.rename(target)  # Windows no replacement; blocking marker retained
    # Full AFTER source inventory/hash must match certified BEFORE manifest;
    # certify destination again against the same exact source bytes/tree. Any
    # failure returns no COPIED result; target remains blocked for later retry.
    _verify_sources(root, agent, folders)
    _verify_policy(root, _object(registry_root / 'names.json'), agent, folders)
    _verify_destination(target, agent, folders, settings, receipt)
    return {'status': 'copied', 'agent_id': identity, 'name': name, 'destination': str(target),
            'conversation_count': len(folders), 'copied_source_bytes': sum(v['bytes'] for f in folders for v in f['files'].values()),
            'source_unchanged': True, 'activated': False, 'quiescence': evidence,
            'copy_inactive': True, 'before_after_destination_equal': True,
            'caveat': 'Late writes after final hash may stale copy; no lost source because legacy source is never modified/moved/deleted.',
            'source_files': {folder['source']: folder['files'] for folder in folders},
            'source_directories': {folder['source']: folder['directories'] for folder in folders},
            'copied_files': _inventory(target)[0],
            'plan_digest': manifest['plan_digest']}
