"""Roster discovery against a synthetic harness root: mixed Claude/LiteTUI rows."""
import json

import pytest

from liteharness.checkpoint_roster import discover_current, roster_census
from checkpoint_fixtures import put, uid
from checkpoint_world import World


def test_live_claude_seats_are_named_with_launch_contract_from_the_transcript(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    owner = w.claude(1, role='owner', names_cwd=None)
    child = w.claude(2, parent=owner)  # no names row at all
    w.claude(3, alive=False)           # dead row: not part of the roster
    put(w.harness / 'agents' / 'pty-14-123.json', {'agent_id': 'pty-14-123'})
    found = w.discover()
    assert [c.expected.agent_id for c in found.candidates] == [owner, child]
    seats = {c.expected.agent_id: c.seat for c in found.candidates}
    assert all(s is not None for s in seats.values())
    assert seats[owner].cwd == str(tmp_path / 'work1') and seats[owner].cli == 'claude'
    assert seats[owner].owner_profile.title == 'Claude-1'
    assert seats[child].parent_id == owner and seats[child].owner_profile is None
    assert seats[child].slot.pane == 'pane-2' and seats[child].disposition == 'held'
    assert seats[child].execution.thinking_level == 'unrecorded'
    assert ('pty-14-123', 'not-a-seat-row') in found.skipped


def test_roster_census_accepts_claude_seats_with_absent_or_null_names_rows(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.claude(1, names_cwd=None)
    w.claude(2)
    found = w.discover()
    result = roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path])
    assert result.expected_count == 2 and not result.blockers
    assert all(row.seat is not None for row in result.rows)


def test_unresolvable_and_unsupported_rows_stay_in_the_denominator(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    good = w.claude(1)
    lost = w.claude(2, transcript=False)
    odd = w.claude(3, cli='mystery')
    found = w.discover()
    result = roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path])
    assert result.expected_count == 3
    blocked = {r.expected.agent_id for r in result.rows if r.seat is None}
    assert blocked == {lost, odd} and good not in blocked
    assert all(b.code == 'seat-unresolved' for b in result.blockers)


def test_offline_litetui_is_selected_only_by_explicit_id(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.claude(1)
    tui = w.litetui(10)
    w.litetui(11)  # dormant retired home: never woken, never listed
    assert [c.expected.agent_id for c in w.discover().candidates] == [uid(1)]
    found = w.discover(include_offline=[tui.agent_id])
    chosen = next(c for c in found.candidates if c.expected.agent_id == tui.agent_id)
    assert chosen.expected.selection == 'explicit-offline'
    assert chosen.seat.cli == 'litetui' and chosen.seat.execution.model == 'home-model'
    assert chosen.seat.conversation_id == tui.conversation_id
    result = roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path])
    assert result.expected_count == 2 and not result.blockers


def test_unknown_litetui_root_blocks_the_row_not_the_roster(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.claude(1)
    tui = w.litetui(10)
    put(w.harness / 'names.json', {})
    monkeypatch.delenv('LITETUI_DATA_ROOT', raising=False)
    found = discover_current(harness_root=w.harness, include_offline=[tui.agent_id], live=lambda r: True)
    rows = {c.expected.agent_id: c for c in found.candidates}
    assert rows[uid(1)].seat is not None
    if rows[tui.agent_id].seat is None:  # no canonical root on this machine
        assert 'data root unknown' in rows[tui.agent_id].blocker.remedy


def test_excluded_ids_are_dropped_and_discovery_writes_nothing(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.claude(1)
    w.claude(2)
    put(w.harness / 'names.json', w.names)
    snapshot = lambda: {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    before = snapshot()
    found = w.discover(exclude_ids=[uid(2)])
    assert [c.expected.agent_id for c in found.candidates] == [uid(1)]
    assert snapshot() == before


def assistant(model, n=1):
    return json.dumps({'type': 'assistant', 'uuid': f'u{n}', 'message': {'model': model, 'role': 'assistant'}})


def rewrite_model(w, agent_id, registry_model, records):
    path = w.harness / 'agents' / f'{agent_id}.json'
    row = json.loads(path.read_text())
    row['model'] = registry_model
    path.write_text(json.dumps(row))
    transcript = next(w.projects.glob(f'*/{agent_id}.jsonl'))
    with transcript.open('a', encoding='utf-8') as stream:
        stream.write(chr(10).join(records) + chr(10))


def test_unknown_registry_model_is_derived_from_the_newest_real_assistant_record(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    a, b = w.claude(1), w.claude(2)
    rewrite_model(w, a, 'unknown', [assistant('claude-sonnet-5-5', 1), assistant('claude-opus-5-5', 2), assistant('<synthetic>', 3)])
    found = w.discover()
    seats = {c.expected.agent_id: c.seat for c in found.candidates}
    assert seats[a].execution.model == 'claude-opus-5-5' and found.model_sources[a] == 'transcript'
    assert seats[b].execution.model == 'claude-x' and found.model_sources[b] == 'registry', 'a real registry value is kept'
    result = roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path])
    assert not result.blockers


@pytest.mark.parametrize('registry_model', ['unknown', '', None])
def test_a_model_nobody_can_name_blocks_the_row_instead_of_inventing_one(tmp_path, monkeypatch, registry_model):
    w = World(tmp_path, monkeypatch)
    a = w.claude(1)
    rewrite_model(w, a, registry_model, [assistant('<synthetic>')])
    found = w.discover()
    assert found.candidates[0].seat is None and 'model' in found.candidates[0].blocker.remedy.lower()


def test_derived_model_never_carries_a_guessed_context_suffix(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    a = w.claude(1)
    rewrite_model(w, a, 'unknown', [assistant('claude-opus-5-5')])
    assert '[' not in w.discover().candidates[0].seat.execution.model


# -- seats Ryan started himself are not ours to hand off or compact (Sentinel, 2026-10-19) ----------

def test_a_seat_started_directly_is_excluded_with_its_reason_and_the_coordinator_never_is(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    coordinator = w.claude(1, role='orchestrator')            # top-level too (spawned_by absent)
    child = w.claude(2, parent=coordinator)
    direct = w.claude(3)                                       # Ryan started it by hand: no spawned_by
    found = w.discover(coordinator_id=coordinator)
    assert [c.expected.agent_id for c in found.candidates] == [coordinator, child]
    assert [(e.agent_id, e.reason) for e in found.excluded] == [(direct, 'started-directly')]
    result = roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path])
    assert [e.agent_id for e in result.exclusions] == [direct] and result.expected_count == 2


def test_without_a_known_coordinator_nothing_is_excluded_by_default(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.claude(1)
    w.claude(2)
    found = w.discover()
    assert len(found.candidates) == 2 and found.excluded == []


def test_an_explicit_include_beats_the_started_directly_default(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    coordinator = w.claude(1, role='orchestrator')
    direct = w.claude(2)
    found = w.discover(coordinator_id=coordinator, include_offline=[direct])
    assert direct in [c.expected.agent_id for c in found.candidates] and found.excluded == []


def test_exclude_takes_an_id_or_a_name_and_records_why(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    coordinator = w.claude(1, role='orchestrator')
    a = w.claude(2, parent=coordinator)
    b = w.claude(3, parent=coordinator)
    c = w.claude(4, parent=coordinator)
    found = w.discover(coordinator_id=coordinator, exclude_ids=[a, 'claude-3'])  # an id and a name (any case)
    assert [x.expected.agent_id for x in found.candidates] == [coordinator, c]
    assert {(e.agent_id, e.reason) for e in found.excluded} == {(a, 'excluded-by-request'), (b, 'excluded-by-request')}
    roster_census(found, harness_root=w.harness, cwd_roots=[tmp_path])  # the manifest contract accepts them


def test_excluding_the_coordinator_is_refused_by_ignoring_it(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    coordinator = w.claude(1, role='orchestrator')
    found = w.discover(coordinator_id=coordinator, exclude_ids=[coordinator, 'Claude-1'])
    assert [c.expected.agent_id for c in found.candidates] == [coordinator] and found.excluded == []
