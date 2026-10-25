"""Current layout is location authority; registry pane coordinates are not."""
from copy import deepcopy
import json

import pytest

from liteharness.checkpoint_roster import roster_census
from checkpoint_world import World
from checkpoint_fixtures import put


def inventory(agent_id, *, session='current-tab', leaf='current-leaf', identity=True):
    return {'ok': True, 'schemaVersion': 1, 'panes': [{
        'revision': 'current-revision', 'pane': 'current-pane', 'workspace': 'current-workspace',
        'thread': 'current-thread', 'leaves': [{'leaf': leaf, 'tabs': [session]}],
        'slots': {session: {'version': 1, 'agentId': agent_id if identity else None,
                            'sessionId': session, 'slotId': 'slot', 'state': 'attached'}},
    }]}


def registry(w, aid, **changes):
    path = w.harness / 'agents' / f'{aid}.json'
    row = json.loads(path.read_text())
    row.update(changes)
    put(path, row)


def test_current_identity_replaces_stale_registry_location_including_tab(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    aid = w.claude(1)
    found = w.discover(current_slots=inventory(aid), layout_revision='frozen')
    slot = found.candidates[0].seat.slot
    assert (slot.workspace, slot.thread, slot.pane, slot.leaf, slot.tab, slot.layout_revision) == (
        'current-workspace', 'current-thread', 'current-pane', 'current-leaf', 'current-tab', 'frozen')
    assert not roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path]).blockers


def test_exact_current_session_joins_legacy_unlabelled_claude(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    aid = w.claude(1)
    registry(w, aid, canvas_session_id='current-tab')
    assert w.discover(current_slots=inventory(aid, identity=False)).candidates[0].seat.slot.tab == 'current-tab'


def test_legacy_current_leaf_with_one_tab_joins_without_stale_pane_guess(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    aid = w.claude(1)
    registry(w, aid, spatial={'leaf_id': 'current-leaf'})
    assert w.discover(current_slots=inventory(aid, identity=False)).candidates[0].seat.slot.pane == 'current-pane'


@pytest.mark.parametrize('failure', ['unavailable', 'missing', 'duplicate', 'ownerless', 'multi-tab',
                                      'stale-session', 'foreign-identity', 'conflicting-session', 'malformed'])
def test_unknown_or_ambiguous_slot_preserves_seat_and_blocker(tmp_path, monkeypatch, failure):
    w = World(tmp_path, monkeypatch)
    aid = w.claude(1)
    snapshot = inventory(aid)
    pane = snapshot['panes'][0]
    if failure == 'unavailable': snapshot = {'ok': False}
    elif failure == 'missing': snapshot['panes'] = []
    elif failure == 'duplicate': snapshot['panes'].append({**deepcopy(pane), 'pane': 'mirror'})
    elif failure == 'ownerless': pane['thread'] = None
    elif failure == 'multi-tab':
        pane['slots'] = {}
        pane['leaves'][0]['tabs'].append('other-tab')
        registry(w, aid, leaf_id='current-leaf')
    elif failure == 'stale-session':
        pane['slots'] = {}
        registry(w, aid, canvas_session_id='gone', leaf_id='current-leaf')
    elif failure == 'foreign-identity':
        pane['slots']['current-tab']['agentId'] = 'foreign-agent'
        registry(w, aid, canvas_session_id='current-tab')
    elif failure == 'conflicting-session':
        pane['leaves'].append({'leaf': 'other-leaf', 'tabs': ['other-tab']})
        registry(w, aid, canvas_session_id='other-tab')
    elif failure == 'malformed': pane['leaves'] = None
    found = w.discover(current_slots=snapshot)
    candidate = found.candidates[0]
    assert candidate.seat is not None and candidate.seat.slot is None
    assert candidate.blocker.code == 'layout-unresolved'
    result = roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path])
    assert result.expected_count == 1 and any(b.code == 'layout-unresolved' for b in result.blockers)
