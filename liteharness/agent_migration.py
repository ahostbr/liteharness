"""Copy-only agent migration: immutable source manifest, explicit conflicts.

Dry run creates NO source/destination/catalog/lock files. The optional CLI report
must live outside both stores. Real copy requires a previously reviewed exact
manifest and independent quiescence evidence; hashes are not proof of no writers.
All legacy companion bytes survive under the owned conversation. Canonical agent
memory/settings are additional copies/selections, never a destructive merge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import NAMESPACE_URL, uuid5

from .agent_store import (
    AGENTS_DIR, INITIALIZING_NAME, MEMORY_FILES, MEMORIES_DIR, SCHEMA_VERSION,
    SETTINGS_NAME, StoreError, _unlinked, name_key, valid_id, valid_name,
)
from .naming import generate_name

MANIFEST_VERSION = 1


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def file_fact(path: Path) -> dict:
    _unlinked(path)
    before = path.stat()
    hashed = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            hashed.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise StoreError('Source changed while hashing')
    return {'bytes': after.st_size, 'sha256': hashed.hexdigest(),
            'mtime_ns': after.st_mtime_ns}


def _object(path: Path, *, missing_ok: bool = False) -> dict:
    _unlinked(path)
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise StoreError('Required metadata is absent')
    except (OSError, ValueError) as exc:
        raise StoreError('Metadata is unreadable') from exc
    if not isinstance(result, dict):
        raise StoreError('Metadata must be an object')
    return result


def _execution(settings: dict) -> dict:
    saved = settings.get('execution')
    saved = saved if isinstance(saved, dict) else {}
    return {'backend': settings.get('backend') or saved.get('backend'),
            'model': settings.get('model') or saved.get('default_model'),
            'thinking_level': settings.get('reasoning_effort') or settings.get('thinking_level')
            or saved.get('thinking_level')}


def plan(data_root: Path | str, *, names: dict | None = None,
         decisions: dict | None = None) -> dict:
    """Hash all folders, including missing settings/transcript and unknown files.

    decisions[legacy-folder] may explicitly supply name/agent_id/execution and
    decisions['canonical_sources'][agent-id] selects ONE legacy folder for grouped
    non-identical agent memory/execution. No latest-wins. Unnamed UUID allocation
    is deterministic per source folder, so retries preserve generated identity.
    """
    root = _unlinked(Path(data_root))
    if not root.is_absolute() or '..' in root.parts:
        raise StoreError('Migration root must be absolute')
    source = _unlinked(root / '.convos')
    destination = _unlinked(root / AGENTS_DIR)
    names, decisions = names or {}, decisions or {}
    by_id: dict[str, list[str]] = {}
    for name, row in names.items():
        if isinstance(row, dict) and isinstance(row.get('agent_id'), str):
            by_id.setdefault(row['agent_id'], []).append(name)
    result = {'manifest_version': MANIFEST_VERSION, 'root': str(root),
              'source': str(source), 'destination': str(destination),
              'agents': [], 'folders': [], 'root_files': {}, 'conflicts': [],
              'writer_gate': 'UNVERIFIED: require independent quiescence before real copy'}
    if not source.is_dir():
        raise StoreError('Legacy source directory is absent')
    groups: dict[str, dict] = {}
    for name, row in names.items():
        try:
            valid_name(name)
            if not isinstance(row, dict):
                raise StoreError('Name catalog row must be an object')
            valid_id(row.get('agent_id'))
        except StoreError as exc:
            result['conflicts'].append({'name': name, 'reason': str(exc)})
    for directory in sorted(source.iterdir(), key=lambda p: p.name):
        try:
            _unlinked(directory)
            if not directory.is_dir():
                if directory.is_file():
                    result['root_files'][directory.name] = file_fact(directory)
                result['conflicts'].append({'source': directory.name,
                    'reason': 'Non-folder legacy companion: destination scope decision required'})
                continue
        except (StoreError, OSError) as exc:
            result['conflicts'].append({'source': directory.name, 'reason': str(exc)})
            continue
        folder = {'source': directory.name, 'files': {}, 'directories': [],
                  'agent_id': None, 'name': None}
        result['folders'].append(folder)
        try:
            valid_id(directory.name)
            folder['files'], folder['directories'] = _inventory(directory)
            settings = _object(directory / SETTINGS_NAME, missing_ok=True)
            if SETTINGS_NAME in folder['files'] and file_fact(directory / SETTINGS_NAME) != folder['files'][SETTINGS_NAME]:
                raise StoreError('Settings changed while resolving identity')
            prior_id = settings.get('seat_id')
            folder['historical_agent_id'] = prior_id
            choice = decisions.get(directory.name, {})
            if not isinstance(choice, dict):
                raise StoreError('Source decision must be an object')
            known_names = by_id.get(prior_id, []) if isinstance(prior_id, str) else []
            saved_name = settings.get('seat_name')
            if saved_name:
                valid_name(saved_name)
                if known_names and known_names != [saved_name]:
                    raise StoreError('Legacy folder name contradicts name catalog')
                row = names.get(saved_name)
                if row is not None and row.get('agent_id') != prior_id:
                    raise StoreError('Legacy name catalog assigns a different identity')
                if not known_names:
                    known_names = [saved_name]
            if len(known_names) > 1:
                raise StoreError('Historical identity joins multiple names')
            named = bool(known_names)
            if named and choice.get('agent_id', prior_id) != prior_id:
                raise StoreError('Explicit choice contradicts historical named identity')
            if named and choice.get('name', known_names[0]) != known_names[0]:
                raise StoreError('Explicit choice contradicts historical name')
            identity = choice.get('agent_id') or (prior_id if named else
                        str(uuid5(NAMESPACE_URL, 'litetui:agent-migration:' + directory.name)))
            identity = valid_id(identity)
            name = choice.get('name') or (known_names[0] if named else generate_name(identity))
            name = valid_name(name)
            execution = choice.get('execution') or _execution(settings)
            if not isinstance(execution, dict) or any(
                    not isinstance(execution.get(k), str) or not execution[k].strip()
                    for k in ('backend', 'model', 'thinking_level')):
                raise StoreError('Missing execution authority; explicit source decision required')
            folder['agent_id'], folder['name'] = identity, name
            folder['execution'] = execution
            scope = _preference_scope(settings)
            folder['unresolved_preference_scope'] = scope
            if scope:
                result['conflicts'].append({'source': directory.name, 'fields': scope,
                    'reason': 'Preference scope unresolved; retained conversation bytes, no broad agent inheritance'})
            group = groups.setdefault(identity, {'agent_id': identity, 'name': name,
                                                'sources': [], 'canonical_source': None})
            if group['name'] != name:
                raise StoreError('One agent identity has contradictory names')
            group['sources'].append(folder)
        except (OSError, StoreError) as exc:
            result['conflicts'].append({'source': directory.name, 'reason': str(exc)})
    keys: dict[str, str] = {}
    for identity, group in groups.items():
        key = name_key(group['name'])
        if key in keys and keys[key] != identity:
            result['conflicts'].append({'name': group['name'], 'reason': 'Generated/existing name collision'})
        keys[key] = identity
        for catalog_name, row in names.items():
            if isinstance(row, dict) and isinstance(catalog_name, str):
                try:
                    catalog_key = name_key(catalog_name)
                except StoreError:
                    continue
                if catalog_key == key and (catalog_name != group['name'] or row.get('agent_id') != identity):
                    result['conflicts'].append({'name': group['name'],
                        'reason': 'Name collides with existing catalog spelling/identity'})
        folders = group.pop('sources')
        signatures = []
        for folder in folders:
            memories = {p: fact['sha256'] for p, fact in folder['files'].items()
                        if p in MEMORY_FILES or p.startswith(MEMORIES_DIR + '/')}
            signatures.append(digest({'memory': memories, 'execution': folder['execution']}))
        selected = decisions.get('canonical_sources', {}).get(identity)
        if selected is not None and selected not in [f['source'] for f in folders]:
            result['conflicts'].append({'agent_id': identity, 'reason': 'Canonical source choice is not owned'})
        elif len(set(signatures)) > 1 and selected is None:
            result['conflicts'].append({'agent_id': identity, 'sources': [f['source'] for f in folders],
                                       'reason': 'Conflicting grouped memory/execution; explicit canonical source required'})
        else:
            selected = selected or folders[0]['source']
            canonical = next(f for f in folders if f['source'] == selected)
            group['canonical_source'] = selected
            group['settings'] = {'schema_version': SCHEMA_VERSION, 'agent_id': identity,
                                 'name': group['name'], 'execution': canonical['execution']}
        group['conversations'] = [f['source'] for f in folders]
        result['agents'].append(group)
        target = destination / group['name']
        if target.exists():
            # Never assume an existing destination is ours or silently overwrite.
            result['conflicts'].append({'name': group['name'], 'reason': 'Destination already exists; verify prior receipt for rerun'})
    result['source_digest'] = digest({'folders': result['folders'], 'root_files': result['root_files']})
    result['plan_digest'] = digest(result)
    return result


def _inventory(directory: Path) -> tuple[dict, list[str]]:
    _unlinked(directory)
    files, directories = {}, []
    # Walk without following links, inspect every entry including empty dirs.
    for current, dirs, entries in os.walk(directory, followlinks=False,
                                          onerror=lambda exc: _raise_inventory(exc)):
        for name in sorted(dirs + entries):
            path = _unlinked(Path(current) / name)
            relative = path.relative_to(directory).as_posix()
            if path.is_file():
                files[relative] = file_fact(path)
            elif path.is_dir():
                directories.append(relative)
            else:
                raise StoreError('Unsupported source entry')
    return dict(sorted(files.items())), sorted(directories)


def _raise_inventory(exc: OSError) -> None:
    raise StoreError('Source inventory cannot be inspected') from exc


def _preference_scope(settings: dict) -> list[str]:
    known = {'schema_version', 'seat_id', 'seat_name', 'seat_tier', 'seat_spawned',
             'backend', 'model', 'thinking_level', 'reasoning_effort', 'execution'}
    nested = settings.get('execution')
    fields = [key for key in settings if key not in known]
    if isinstance(nested, dict):
        fields.extend('execution.' + key for key in nested
                      if key not in {'backend', 'default_model', 'thinking_level'})
    return sorted(fields)


def _verify_sources(manifest: dict) -> None:
    source = _unlinked(Path(manifest['source']))
    expected = {folder['source'] for folder in manifest['folders']} | set(manifest['root_files'])
    entries = list(source.iterdir())
    if {p.name for p in entries} != expected:
        raise StoreError('Source entry inventory changed')
    for path in entries:
        _unlinked(path)
    for name, fact in manifest['root_files'].items():
        if file_fact(source / name) != fact:
            raise StoreError('Root companion changed')
    for folder in manifest['folders']:
        directory = source / folder['source']
        files, directories = _inventory(directory)
        if files != folder['files'] or directories != folder['directories']:
            raise StoreError('Source manifest no longer matches; regenerate dry run')


def _relative(value: str) -> Path:
    path = Path(value)
    if (not isinstance(value, str) or not value or path.is_absolute()
            or path.drive or '..' in path.parts or path.as_posix() != value):
        raise StoreError('Invalid manifest relative path')
    for component in path.parts:
        valid_name(component)
    return path


def _validate_manifest(manifest: dict) -> None:
    root = _unlinked(Path(manifest['root']))
    if not root.is_absolute() or '..' in root.parts:
        raise StoreError('Invalid manifest root')
    if (Path(manifest['source']) != root / '.convos'
            or Path(manifest['destination']) != root / AGENTS_DIR):
        raise StoreError('Manifest stores must belong to exact data root')
    names, ids, folders = set(), set(), set()
    for agent in manifest['agents']:
        name, identity = name_key(agent['name']), valid_id(agent['agent_id'])
        if name in names or identity in ids:
            raise StoreError('Ambiguous manifest agent')
        names.add(name)
        ids.add(identity)
        if (agent['settings'].get('name') != agent['name']
                or agent['settings'].get('agent_id') != identity):
            raise StoreError('Manifest settings contradict identity')
        if agent['canonical_source'] not in agent['conversations']:
            raise StoreError('Canonical source is not owned')
    for folder in manifest['folders']:
        valid_id(folder['source'])
        if folder['source'] in folders or folder['agent_id'] not in ids:
            raise StoreError('Ambiguous manifest conversation')
        folders.add(folder['source'])
        for relative in [*folder['files'], *folder['directories']]:
            _relative(relative)
        owners = [a for a in manifest['agents'] if folder['source'] in a['conversations']]
        if len(owners) != 1 or owners[0]['agent_id'] != folder['agent_id'] or owners[0]['name'] != folder['name']:
            raise StoreError('Manifest conversation ownership disagreement')


def copy_verified(manifest: dict, *, quiescence: dict) -> dict:
    """Execute only validated copy after caller supplies explicit writer evidence.

    The caller's independent evidence must list every source folder, no live or
    unknown writers, and exact source_digest. This function cannot quiesce old
    processes; it never stops a seat or changes/chmods/deletes a source.
    Publication uses destination-volume staged directories, same-plan receipts,
    and Windows no-overwrite rename. Unsupported POSIX publication is refused
    rather than rely on overwrite-prone rename for an existing empty directory.
    """
    raise StoreError('Old all-source migration copy route DISABLED by named-index-only policy; use reviewed named-policy tool after merge')
    if manifest.get('manifest_version') != MANIFEST_VERSION or manifest.get('conflicts'):
        raise StoreError('Migration manifest is incompatible or has unresolved conflicts')
    unsigned = {k: v for k, v in manifest.items() if k != 'plan_digest'}
    if digest(unsigned) != manifest.get('plan_digest'):
        raise StoreError('Migration plan digest mismatch')
    if (quiescence.get('source_digest') != manifest['source_digest']
            or quiescence.get('folders') != sorted(f['source'] for f in manifest['folders'])
            or quiescence.get('live_writers') != [] or quiescence.get('unknown_writers') != []
            or not quiescence.get('evidence')):
        raise StoreError('Independent all-source quiescence evidence is required')
    if os.name != 'nt':
        raise StoreError('No-overwrite directory publication currently requires Windows')
    _validate_manifest(manifest)
    _verify_sources(manifest)
    destination = _unlinked(Path(manifest['destination']))
    staging = _unlinked(destination.parent / '.agents-migration-staging' / manifest['plan_digest'])
    staging.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=True)
    copied = []
    for agent in manifest['agents']:
        target = _unlinked(destination / valid_name(agent['name']))
        receipt = {'plan_digest': manifest['plan_digest'], 'agent_id': agent['agent_id'],
                   'source_digest': manifest['source_digest']}
        if target.exists():
            if _object(target / '.migration-receipt.json') != receipt:
                raise StoreError('Existing destination is not this verified publication')
            # Rerun validates complete destination hashes, not just a marker.
            _verify_agent_copy(target, agent, manifest)
            copied.append(agent['name'])
            continue
        temporary = _unlinked(staging / agent['name'])
        temporary.mkdir(exist_ok=True)
        marker = temporary / INITIALIZING_NAME
        if not marker.exists():
            marker.write_text('Copy incomplete; never activate.\n', encoding='utf-8')
        for folder in manifest['folders']:
            if folder['agent_id'] != agent['agent_id']:
                continue
            for relative in folder['directories']:
                _unlinked(temporary / 'conversations' / folder['source'] / relative).mkdir(parents=True, exist_ok=True)
            for relative, fact in folder['files'].items():
                _copy_file(Path(manifest['source']) / folder['source'] / relative,
                           temporary / 'conversations' / folder['source'] / relative, fact)
            (temporary / 'conversations' / folder['source']).mkdir(parents=True, exist_ok=True)
        canonical = next(f for f in manifest['folders'] if f['source'] == agent['canonical_source'])
        for relative in canonical['directories']:
            if relative == MEMORIES_DIR or relative.startswith(MEMORIES_DIR + '/'):
                _unlinked(temporary / relative).mkdir(parents=True, exist_ok=True)
        for relative, fact in canonical['files'].items():
            if relative in MEMORY_FILES or relative.startswith(MEMORIES_DIR + '/'):
                _copy_file(Path(manifest['source']) / canonical['source'] / relative,
                           temporary / relative, fact)
        _write_once_json(temporary / SETTINGS_NAME, agent['settings'])
        _write_once_json(temporary / '.migration-receipt.json', receipt)
        _verify_agent_copy(temporary, agent, manifest)
        _verify_sources(manifest)  # changes anywhere block publication
        marker.unlink()  # only NEW staging marker, never source/legacy data
        temporary.rename(target)  # Windows MoveFile no replace, fails if target exists
        copied.append(agent['name'])
    _verify_sources(manifest)
    return {'copied_agents': copied, 'plan_digest': manifest['plan_digest'],
            'source_unchanged': True, 'activated': False}


def _copy_file(source: Path, target: Path, fact: dict) -> None:
    _unlinked(source)
    _unlinked(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        current = file_fact(target)
        if (current['bytes'], current['sha256']) == (fact['bytes'], fact['sha256']):
            return
        raise StoreError('Staging copy conflict; never overwrite partial/original data')
    with source.open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    current = file_fact(target)
    if (current['bytes'], current['sha256']) != (fact['bytes'], fact['sha256']):
        raise StoreError('Copy hash verification failed')


def _write_once_json(path: Path, value: dict) -> None:
    if path.exists():
        if _object(path) != value:
            raise StoreError('Staging metadata conflict')
        return
    with _unlinked(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def _verify_agent_copy(directory: Path, agent: dict, manifest: dict) -> None:
    for folder in manifest['folders']:
        if folder['agent_id'] != agent['agent_id']:
            continue
        copied_files, copied_dirs = _inventory(directory / 'conversations' / folder['source'])
        if set(copied_files) != set(folder['files']) or copied_dirs != folder['directories']:
            raise StoreError('Published conversation inventory disagrees with manifest')
        for relative, fact in folder['files'].items():
            current = file_fact(directory / 'conversations' / folder['source'] / relative)
            if (current['bytes'], current['sha256']) != (fact['bytes'], fact['sha256']):
                raise StoreError('Published conversation bytes disagree with source manifest')
    canonical = next(f for f in manifest['folders'] if f['source'] == agent['canonical_source'])
    for relative, fact in canonical['files'].items():
        if relative in MEMORY_FILES or relative.startswith(MEMORIES_DIR + '/'):
            current = file_fact(directory / relative)
            if (current['bytes'], current['sha256']) != (fact['bytes'], fact['sha256']):
                raise StoreError('Published agent memory bytes disagree with canonical source')
    if _object(directory / SETTINGS_NAME) != agent['settings']:
        raise StoreError('Published settings disagree with manifest')


def main() -> None:
    parser = argparse.ArgumentParser(description='T0308 dry-run-first copy-only migration')
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--names', type=Path)
    parser.add_argument('--decisions', type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    root = _unlinked(args.root.absolute())
    report = _unlinked(args.report.absolute())
    if '..' in report.parts or '..' in root.parts:
        parser.error('Traversal in root/report is not supported')
    for store in (root / '.convos', root / AGENTS_DIR):
        if report == store or store in report.parents:
            parser.error('Report must live outside source/destination stores')
    result = plan(root, names=_object(args.names) if args.names else None,
                  decisions=_object(args.decisions) if args.decisions else None)
    with report.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps({'plan_digest': result['plan_digest'], 'folders': len(result['folders']),
                      'agents': len(result['agents']), 'conflicts': len(result['conflicts'])}))


if __name__ == '__main__':
    main()
