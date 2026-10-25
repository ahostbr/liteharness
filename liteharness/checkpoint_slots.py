"""Join a selected registry identity to a current renderer slot, never a stale pane.

This inventory is observation only, not launch authority or a durable layout receipt.
"""
from __future__ import annotations

from .checkpoint_contract import ContractError, Slot


def current_slots() -> dict:
    from . import cli
    try:
        result = cli._bridge_request('GET', '/harness/checkpoint/layout/snapshot')
        return result if isinstance(result, dict) else {'ok': False}
    except Exception:
        return {'ok': False}


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def resolve_slot(row: dict, snapshot: dict, layout_revision: str) -> Slot:
    """Require one current binding. Legacy leaf-only Claude needs exactly one tab.

    Exact session and retained identity claims must agree when both are present.
    A stale explicit session never falls back to a leaf or a pane.
    """
    if (not isinstance(snapshot, dict) or snapshot.get('ok') is not True
            or snapshot.get('schemaVersion') != 1 or not isinstance(snapshot.get('panes'), list)):
        raise ContractError('Current slot inventory unavailable')
    locations = []
    for pane in snapshot['panes']:
        if (not isinstance(pane, dict) or not _text(pane.get('pane'))
                or not isinstance(pane.get('leaves'), list) or not isinstance(pane.get('slots'), dict)):
            raise ContractError('Current slot inventory malformed')
        bindings = list(pane['slots'].values())
        if any(not isinstance(s, dict) or not _text(s.get('sessionId'))
               or (s.get('agentId') is not None and not _text(s.get('agentId'))) for s in bindings):
            raise ContractError('Current slot bindings malformed')
        for leaf in pane['leaves']:
            if (not isinstance(leaf, dict) or not _text(leaf.get('leaf'))
                    or not isinstance(leaf.get('tabs'), list) or not all(_text(t) for t in leaf['tabs'])):
                raise ContractError('Current slot leaves malformed')
            for tab in leaf['tabs']:
                owners = {s.get('agentId') for s in bindings if s['sessionId'] == tab and s.get('agentId')}
                locations.append((pane, leaf, tab, owners))
    aid, session = row['agent_id'], row.get('canvas_session_id')
    by_identity = [loc for loc in locations if aid in loc[3]]
    by_session = [loc for loc in locations if _text(session) and loc[2] == session]
    if _text(session) and not by_session:
        raise ContractError('Recorded session is absent from current slots')
    if by_identity or by_session:
        matches = [loc for loc in locations if aid in loc[3] or (_text(session) and loc[2] == session)]
    else:
        if row.get('cli') not in ('claude', 'claude-code'):
            raise ContractError('Current identity/session binding is absent')
        spatial = row.get('spatial') if isinstance(row.get('spatial'), dict) else {}
        leaf_id = spatial.get('leaf_id') or row.get('leaf_id')
        matches = [loc for loc in locations if _text(leaf_id) and loc[1]['leaf'] == leaf_id]
        if any(len(loc[1]['tabs']) != 1 for loc in matches):
            raise ContractError('Legacy leaf has multiple tabs; exact session unresolved')
    if len(matches) != 1:
        raise ContractError('Current slot absent or ambiguous')
    pane, leaf, tab, owners = matches[0]
    if owners - {aid}:
        raise ContractError('Current slot belongs to another identity')
    if not all(_text(pane.get(key)) for key in ('workspace', 'thread')):
        raise ContractError('Current slot has no durable workspace/thread owner')
    return Slot(pane['workspace'], pane['thread'], pane['pane'], leaf['leaf'], tab, layout_revision)
