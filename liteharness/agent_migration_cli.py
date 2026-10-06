"""Explicit named COPY operator boundary. Dry-run default; no runtime activation.

Run reviewed merged code only for --apply. Merge approval is independently
verified by the operator; its receipt is audit evidence, not self-authorization.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent_migration import _object
from .agent_migration_copy import copy_named_agent
from .agent_migration_policy import named_plan
from .agent_store import StoreError, _unlinked, valid_name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Named-agent COPY only; dry-run unless --apply')
    parser.add_argument('--copy-agent', required=True, type=valid_name)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--registry-root', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--merge-receipt', type=Path)
    args = parser.parse_args(argv)
    if args.apply != bool(args.manifest and args.merge_receipt) or bool(args.manifest) != bool(args.merge_receipt):
        parser.error('--apply requires BOTH --manifest and --merge-receipt; neither is valid in dry-run')
    try:
        root, registry = _unlinked(args.root), _unlinked(args.registry_root)
        if not root.is_absolute() or not registry.is_absolute() or '..' in root.parts or '..' in registry.parts:
            raise StoreError('Absolute unlinked root and registry-root required')
        if args.apply:
            manifest = _object(args.manifest)
            receipt = _object(args.merge_receipt)
            if manifest.get('root') != str(root):
                raise StoreError('Approved manifest root disagrees with --root')
        else:
            manifest = named_plan(root, names=_object(registry / 'names.json'),
                                  selected_agent=args.copy_agent)
        agents = manifest.get('agents')
        if not isinstance(agents, list) or any(not isinstance(row, dict) for row in agents):
            raise StoreError('Invalid named-policy agents')
        rows = [row for row in agents if row.get('name') == args.copy_agent]
        if len(rows) != 1:
            raise StoreError('Named agent absent or ambiguous in policy plan')
        agent = rows[0]
        if args.apply:
            result = copy_named_agent(manifest, agent_id=agent['agent_id'], registry_root=registry,
                                      merge_receipt=receipt)
        else:
            result = {'status': 'dry-run', 'apply': False, 'activated': False,
                      'agent': agent, 'plan_digest': manifest['plan_digest'],
                      'writer_gate': 'UNVERIFIED; --apply rechecks registry/exact live PTY and full selected source hashes',
                      'manifest': manifest}
        print(json.dumps(result, indent=2))
    except (StoreError, OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f'COPY deferred: {exc}\n')


if __name__ == '__main__':
    main()
