"""Unknown unselected archives do not veto an exact indexed source."""
import json

import pytest

from liteharness import agent_migration_policy as policy
from test_agent_migration import DID
from test_agent_migration_policy import NAMES, conversation


@pytest.mark.parametrize('state', ['missing', 'malformed', 'inaccessible', 'race', 'named-no-id'])
def test_unknown_sibling_left_in_legacy_not_inferred_or_global_block(tmp_path, monkeypatch, state):
    conversation(tmp_path)
    sibling = conversation(tmp_path, DID, ts=20)
    path = sibling / 'settings.json'
    if state == 'missing':
        path.unlink()  # fixture only
    elif state == 'malformed':
        path.write_text('{')
    elif state == 'named-no-id':
        path.write_text(json.dumps({'seat_name': 'QuietHelm'}))
    else:
        original = policy._object
        def broken(current, **kwargs):
            if current == path:
                if state == 'inaccessible':
                    raise PermissionError('fixture denied')
                result = original(current, **kwargs)
                path.write_text(json.dumps({**result, 'model': 'changed'}))
                return result
            return original(current, **kwargs)
        monkeypatch.setattr(policy, '_object', broken)
    result = policy.named_plan(tmp_path, names=NAMES)
    assert result['counts']['candidate_agents'] == 1
    assert result['counts']['candidate_conversations'] == 1
    record = next(r for r in result['left_in_legacy'] if r.get('source') == DID)
    assert record['status'] == policy.LEFT_LEGACY and record['reasons']
    folder = next(f for f in result['folders'] if f['source'] == DID)
    assert 'agent_id' not in folder and folder['files'] == {}


def test_readable_unindexed_archive_is_not_unknown_lineage(tmp_path):
    conversation(tmp_path)
    sibling = conversation(tmp_path, DID)
    settings = json.loads((sibling / 'settings.json').read_text())
    settings.pop('seat_id')
    settings.pop('seat_name')
    (sibling / 'settings.json').write_text(json.dumps(settings))
    result = policy.named_plan(tmp_path, names=NAMES)
    assert result['counts']['candidate_agents'] == 1
    assert result['left_in_legacy'][0]['source'] == DID
