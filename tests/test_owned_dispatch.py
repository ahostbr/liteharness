"""Actual dispatcher contracts: owned first, Claude legacy narrowly preserved."""
import json
from pathlib import Path

import pytest
from liteharness import resume_seat, owned_launch, owned_resume, agent_names
from liteharness.agent_ownership import AgentSession
from liteharness.agent_store import AgentStore, StoreError

AID = '11111111-1111-4111-8111-111111111111'
CID = '33333333-3333-4333-8333-333333333333'


def dispatch(value='QuietHelm', convo=None):
    return resume_seat.spawn_resume(agent_id=value, convo_id=convo, pane=None, direction=None,
        cwd=None, name=None, tier='worker', model=None, backend=None,
        thinking_level=None, spawned_by='parent', kill_old=False, prompt=None)


def test_explicit_claude_convo_is_not_owned_membership(tmp_path, monkeypatch):
    monkeypatch.setenv('LITETUI_DATA_ROOT', str(tmp_path))
    entry = {'agent_id': AID, 'convo_id': CID, 'name': 'ClaudeSeat', 'backend': 'claude'}
    monkeypatch.setattr(agent_names, 'resolve_name', lambda _: entry)
    monkeypatch.setattr(resume_seat, '_registry', lambda _: {})
    monkeypatch.setattr(owned_resume, 'spawn', lambda **_: pytest.fail('wrong owned route'))
    seen = []
    monkeypatch.setattr(resume_seat, '_spawn_claude_resume', lambda row, **kw: seen.append((row, kw)))
    dispatch('ClaudeSeat', CID)
    assert seen[0][0] == entry
    assert seen[0][1]['convo_id'] == CID


def test_default_canonical_root_finds_owned_before_poisoned_index(tmp_path, monkeypatch):
    with AgentSession.create_fresh(AgentStore(tmp_path), name='QuietHelm', agent_id=AID,
                                  backend='codex', model='fixture', thinking_level='high'):
        pass
    monkeypatch.delenv('LITETUI_DATA_ROOT', raising=False)
    # Canonical root resolution itself is covered separately; this test observes
    # actual dispatch ordering when no environment override exists.
    monkeypatch.setattr(owned_launch, 'data_root', lambda: tmp_path)
    monkeypatch.setattr(agent_names, 'resolve_name', lambda _: pytest.fail('legacy index consulted'))
    seen = []
    monkeypatch.setattr(owned_resume, 'spawn', lambda **kw: seen.append(kw))
    dispatch()
    assert seen[0]['value'] == 'QuietHelm'


def test_unknown_root_claude_route_survives_but_corrupt_root_never_falls_back(monkeypatch):
    def unknown():
        raise owned_launch.RootUnknown('uninstalled')
    monkeypatch.setattr(owned_launch, 'data_root', unknown)
    entry = {'agent_id': AID, 'convo_id': CID, 'name': 'ClaudeSeat', 'backend': 'claude'}
    monkeypatch.setattr(agent_names, 'resolve_name', lambda _: entry)
    monkeypatch.setattr(resume_seat, '_registry', lambda _: {})
    seen = []
    monkeypatch.setattr(resume_seat, '_spawn_claude_resume', lambda *a, **kw: seen.append(kw))
    dispatch('ClaudeSeat', CID)
    assert len(seen) == 1
    def corrupt():
        raise StoreError('linked/corrupt authority')
    monkeypatch.setattr(owned_launch, 'data_root', corrupt)
    with pytest.raises(StoreError, match='corrupt authority'):
        dispatch('ClaudeSeat', CID)
    assert len(seen) == 1
