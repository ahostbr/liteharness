"""Explicit reviewed activation of ONE verified inactive COPY, never a repair.

Operator approval is audit evidence, not self-authorization. No startup/picker
calls this module. COPY and ACTIVATE are separate actions; legacy is read-only.
Any interrupted or stale activation remains blocked for operator inspection.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from .agent_migration import _inventory, _object, _write_once_json, digest, file_fact
from .agent_migration_copy import copy_named_agent, _offline, _verify_sources, _verify_policy
from .agent_ownership import _KernelLease, AGENT_LEASE_NAME, CATALOG_LEASE_NAME
from .agent_store import AgentStore, INITIALIZING_NAME, StoreError, _unlinked, valid_id, valid_name

ACTIVATION_RECEIPT = '.activation-receipt.json'


def _same_files(current: dict, expected: dict) -> bool:
    return set(current) == set(expected) and all(
        (current[key]['bytes'], current[key]['sha256']) == (fact['bytes'], fact['sha256'])
        for key, fact in expected.items())


def _owned_inventory(directory: Path, lease) -> tuple[dict, list[str]]:
    """Inventory all entries except the exact current kernel-locked lease.

    Windows rejects reading the locked byte through another handle. Validate its
    current inode against the owned handle instead; no broad lease-name ignore.
    """
    files, directories = {}, []
    lock = directory / AGENT_LEASE_NAME
    def inventory_error(exc):
        raise StoreError('Activation inventory cannot be inspected') from exc
    for current, dirs, entries in os.walk(_unlinked(directory), followlinks=False,
                                        onerror=inventory_error):
        for name in sorted(dirs + entries):
            path = _unlinked(Path(current) / name)
            relative = path.relative_to(directory).as_posix()
            if path == lock:
                actual, owned = path.stat(), os.fstat(lease.handle.fileno())
                if (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
                    raise StoreError('Activation lease was replaced')
                continue
            if path.is_file():
                files[relative] = file_fact(path)
            elif path.is_dir():
                directories.append(relative)
            else:
                raise StoreError('Unsupported activation inventory entry')
    return dict(sorted(files.items())), sorted(directories)


def activate(manifest: dict, *, agent_id: str, registry_root: Path,
             copy_approval: dict, activation_approval: dict) -> dict:
    """Activate only a current byte-verified copied home under exact ownership.

    copy_named_agent's existing-destination branch validates manifest, policy,
    source/destination hashes and receipt; it cannot be used to create here.
    Under BOTH catalog and agent kernel leases, repeat those proofs before
    publishing activation receipt and removing the exact blocking marker LAST.
    """
    identity = valid_id(agent_id)
    if not isinstance(manifest, dict) or not isinstance(copy_approval, dict):
        raise StoreError('Exact manifest and copy approval objects required')
    registry_root = _unlinked(Path(registry_root))
    if not registry_root.is_absolute() or '..' in registry_root.parts:
        raise StoreError('Activation registry root must be absolute without traversal')
    if (not isinstance(activation_approval, dict)
            or activation_approval.get('operation') != 'activate-verified-copy'
            or activation_approval.get('agent_id') != identity
            or activation_approval.get('plan_digest') != manifest.get('plan_digest')
            or activation_approval.get('copy_approval_digest') != digest(copy_approval)
            or any(not isinstance(activation_approval.get(k), str) or not activation_approval[k].strip()
                   for k in ('authority', 'merged_commit', 'review_evidence'))
            or re.fullmatch('[0-9a-f]{40}', activation_approval['merged_commit']) is None):
        raise StoreError('Exact reviewed activation approval required')
    store = AgentStore(Path(manifest['root']))
    rows = [a for a in manifest['agents'] if a.get('agent_id') == identity]
    if len(rows) != 1:
        raise StoreError('Activation agent absent or ambiguous')
    agent = rows[0]
    target = store.agent_directory(valid_name(agent['name']))
    marker = _unlinked(target / INITIALIZING_NAME)
    if not target.is_dir() or not marker.is_file():
        raise StoreError('Activation requires an existing copy-inactive home; no implicit copy')
    # Existing lease evidence from a previous failed activation is not silently
    # repaired or ignored. Fresh COPY has no lease; known holder creates it below.
    if (target / AGENT_LEASE_NAME).exists() or (target / ACTIVATION_RECEIPT).exists():
        raise StoreError('Prior activation evidence exists; operator inspection required')
    proof = copy_named_agent(manifest, agent_id=identity, registry_root=registry_root,
                             merge_receipt=copy_approval, existing_only=True)
    if (proof.get('status') != 'copied' or proof.get('activated') is not False
            or proof.get('destination') != str(target)):
        raise StoreError('Exact copy proof required')
    before_files, before_dirs = _inventory(target)
    if not _same_files(before_files, proof['copied_files']):
        raise StoreError('Copy changed before ownership acquisition')
    selected_sources = set(agent['conversations'])
    folders = [f for f in manifest['folders'] if f.get('agent_id') == identity
               and f.get('source') in selected_sources]
    with _KernelLease(store.data_root / CATALOG_LEASE_NAME):
        # Do not trust only the selected spelling: validate entire reserved
        # catalog for malformed entries, duplicate names or UUIDs.
        store.list_agents()
        lease_path = _unlinked(target / AGENT_LEASE_NAME)
        if lease_path.exists():
            raise StoreError('Agent lease appeared before activation ownership')
        with _KernelLease(lease_path) as lease:
            if lease.handle is None or lease.pid != os.getpid():
                raise StoreError('Current-process activation ownership required')
            if not lease_path.samefile(Path(lease.path)) or lease.handle.fileno() < 0:
                raise StoreError('Activation lease capability changed')
            # Bind handle identity to current inspected lock inode, not a filename.
            lock_stat = os.fstat(lease.handle.fileno())
            path_stat = _unlinked(lease_path).stat()
            if (lock_stat.st_dev, lock_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
                raise StoreError('Activation lease path replaced')
            current, directories = _owned_inventory(target, lease)
            if not _same_files(current, before_files) or directories != before_dirs:
                raise StoreError('Copy inventory/hash changed under activation ownership')
            _verify_sources(store.data_root, agent, folders)
            _verify_policy(store.data_root, _object(registry_root / 'names.json'), agent, folders)
            _offline(agent, registry_root=registry_root, source_root=store.legacy_root)
            # External observation cannot authorize stale source/destination bytes.
            _verify_sources(store.data_root, agent, folders)
            current, directories = _owned_inventory(target, lease)
            if not _same_files(current, before_files) or directories != before_dirs:
                raise StoreError('Copy changed at final activation boundary')
            receipt = {'operation': 'activate-verified-copy', 'agent_id': identity,
                       'name': agent['name'], 'plan_digest': manifest['plan_digest'],
                       'copy_approval_digest': digest(copy_approval),
                       'activation_approval': activation_approval,
                       'verified_copy_files_digest': digest(before_files)}
            _write_once_json(_unlinked(target / ACTIVATION_RECEIPT), receipt)
            if _object(target / ACTIVATION_RECEIPT) != receipt:
                raise StoreError('Activation receipt changed during publication')
            # Receipt publication is another failure/mutation boundary. Verify
            # exact old bytes again, exempting only our validated new receipt.
            _verify_sources(store.data_root, agent, folders)
            _verify_policy(store.data_root, _object(registry_root / 'names.json'), agent, folders)
            current, directories = _owned_inventory(target, lease)
            published_fact = current.pop(ACTIVATION_RECEIPT, None)
            if (published_fact is None or _object(target / ACTIVATION_RECEIPT) != receipt
                    or not _same_files(current, before_files) or directories != before_dirs):
                raise StoreError('Copy changed during activation receipt publication')
            # Exact marker bytes were included in before_files and final hash.
            # No folder/source removal, permission change, or archive write.
            _unlinked(marker).unlink()
            ready = store.find_agent(agent_id=identity)
            if ready.directory != target:
                raise StoreError('Activated authority location disagrees')
    return {'status': 'activated', 'agent_id': identity, 'name': ready.name,
            'destination': str(target), 'plan_digest': manifest['plan_digest'],
            'source_unchanged': True, 'activation_receipt': receipt}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Activate one verified inactive agent COPY; dry-run default')
    parser.add_argument('--activate-agent', required=True, type=valid_name)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--registry-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--copy-approval', required=True, type=Path)
    parser.add_argument('--activation-approval', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if args.apply != bool(args.activation_approval):
        parser.error('--apply requires --activation-approval; never supply it in dry-run')
    try:
        manifest, copy_approval = _object(args.manifest), _object(args.copy_approval)
        if manifest.get('root') != str(args.root):
            raise StoreError('Manifest root disagrees with --root')
        rows = [a for a in manifest['agents'] if a.get('name') == args.activate_agent]
        if len(rows) != 1:
            raise StoreError('Activation name absent or ambiguous')
        if args.apply:
            result = activate(manifest, agent_id=rows[0]['agent_id'], registry_root=args.registry_root,
                              copy_approval=copy_approval, activation_approval=_object(args.activation_approval))
        else:
            # Read-only inspection does not copy, lock, remove marker or claim ready.
            store = AgentStore(args.root)
            target = store.agent_directory(args.activate_agent)
            result = {'status': 'dry-run', 'activated': False, 'destination': str(target),
                      'copy_inactive': _unlinked(target / INITIALIZING_NAME).is_file(),
                      'plan_digest': manifest['plan_digest'],
                      'files_digest': digest(_inventory(target)[0]),
                      'apply_gate': 'Exact approval plus current copy/source/offline/ownership proofs required'}
        print(json.dumps(result, indent=2))
    except (StoreError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'ACTIVATION deferred: {exc}\n')


if __name__ == '__main__':
    main()
