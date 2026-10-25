"""Roster discovery against a synthetic harness root: mixed Claude/LiteTUI rows."""
import json
import os
import subprocess
import sys

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


def _no_canonical_root(monkeypatch, bridge):
    """Env root unset and `litetui` not importable (RootUnknown); `bridge` stands in for the LiteSuite bridge."""
    from liteharness import owned_launch, resume_seat
    monkeypatch.delenv('LITETUI_DATA_ROOT', raising=False)

    def unknown(resolution=None):
        raise owned_launch.RootUnknown('LiteTUI data root unknown; configure LITETUI_DATA_ROOT')
    monkeypatch.setattr(owned_launch, 'data_root', unknown)
    monkeypatch.setattr(resume_seat, '_bridge_data_root', bridge)


def _discover_unrooted(w):
    put(w.harness / 'names.json', w.names)
    return discover_current(harness_root=w.harness, live=lambda row: row['agent_id'] in w.live)


def test_litetui_root_comes_from_the_bridge_when_the_resolver_is_absent(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.claude(1)
    tui = w.litetui(10, retired=False)
    asked = []

    def bridge(value, **hints):
        asked.append(value)
        return w.owned_root
    _no_canonical_root(monkeypatch, bridge)
    rows = {c.expected.agent_id: c for c in _discover_unrooted(w).candidates}
    assert rows[tui.agent_id].blocker is None and rows[tui.agent_id].seat.data_root == str(w.owned_root)
    assert rows[uid(1)].seat is not None
    assert asked == [tui.agent_id]  # a LiteTUI seat names the root, never the Claude row


@pytest.mark.parametrize('outcome', ['refuses', 'unreachable', 'names-nothing'])
def test_unreachable_or_refusing_bridge_blocks_the_litetui_row_without_raising(tmp_path, monkeypatch, outcome):
    from liteharness.agent_store import StoreError
    w = World(tmp_path, monkeypatch)
    w.claude(1)
    tui = w.litetui(10, retired=False)

    def bridge(value, **hints):
        if outcome == 'refuses':
            raise StoreError('Owned data-root discovery refused: no such tier')
        if outcome == 'unreachable':
            raise OSError('connection refused')
        return None
    _no_canonical_root(monkeypatch, bridge)
    rows = {c.expected.agent_id: c for c in _discover_unrooted(w).candidates}
    assert rows[tui.agent_id].seat is None
    assert 'data root unknown' in rows[tui.agent_id].blocker.remedy
    assert rows[uid(1)].seat is not None


def test_bridge_is_not_asked_when_nothing_needs_a_litetui_root(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.claude(1)
    w.litetui(10)  # retired and not named: not a roster row

    def bridge(value, **hints):
        raise AssertionError('no live LiteTUI seat to ask about')
    _no_canonical_root(monkeypatch, bridge)
    assert [c.expected.agent_id for c in _discover_unrooted(w).candidates] == [uid(1)]


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


# -- seats the user started themselves are not ours to hand off or compact (2026-10-19) ----------

def test_a_seat_started_directly_is_excluded_with_its_reason_and_the_coordinator_never_is(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    coordinator = w.claude(1, role='orchestrator')            # top-level too (spawned_by absent)
    child = w.claude(2, parent=coordinator)
    direct = w.claude(3)                                       # the user started it by hand: no spawned_by
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


def _pty(agent_id, cwd, **extra):
    return {'id': 'pty-1-1', 'harnessAgentId': agent_id, 'cwd': str(cwd), 'pid': os.getpid(), **extra}


def _dead_pid():
    done = subprocess.Popen([sys.executable, '-c', 'pass'])
    done.wait()
    return done.pid


def test_litetui_row_without_cwd_takes_the_cwd_of_its_one_live_pty_session(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    tui = w.litetui(10, retired=False, record_cwd=False)
    launched = tmp_path / 'launched'
    launched.mkdir()
    found = w.discover(pty_sessions=lambda: [_pty(tui.agent_id, launched), _pty(uid(99), tmp_path / 'other')])
    seat = found.candidates[0].seat
    assert seat is not None and seat.cwd == str(launched)
    assert found.cwd_sources == {tui.agent_id: 'pty-session'}


@pytest.mark.parametrize('sessions', [
    [],                                                       # no session for the seat
    None,                                                     # bridge unreachable
    [{'id': 'p', 'harnessAgentId': 'someone-else', 'cwd': 'C:/x'}],
    'two', 'relative', 'blank', 'dead',
])
def test_litetui_row_without_cwd_keeps_the_blocker_when_no_session_is_trustworthy(tmp_path, monkeypatch, sessions):
    w = World(tmp_path, monkeypatch)
    tui = w.litetui(10, retired=False, record_cwd=False)
    here = str(tmp_path)
    named = {'two': [_pty(tui.agent_id, here), _pty(tui.agent_id, here)],
             'relative': [_pty(tui.agent_id, 'work/dir')],
             'blank': [_pty(tui.agent_id, '  ')],
             'dead': [_pty(tui.agent_id, here, pid=_dead_pid())]}
    sessions = named[sessions] if isinstance(sessions, str) else sessions
    found = w.discover(pty_sessions=lambda: sessions)
    assert found.candidates[0].seat is None and found.cwd_sources == {}
    assert 'no recorded launch cwd' in found.candidates[0].blocker.remedy


def test_a_session_pid_the_row_names_is_trusted_even_if_the_os_cannot_find_it(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    tui = w.litetui(10, retired=False, record_cwd=False)
    row_path = w.harness / 'agents' / f'{tui.agent_id}.json'
    dead = _dead_pid()
    put(row_path, {**json.loads(row_path.read_text(encoding='utf-8')), 'session_pid': dead})
    found = w.discover(pty_sessions=lambda: [_pty(tui.agent_id, tmp_path, pid=dead)])
    assert found.candidates[0].seat is not None and found.cwd_sources == {tui.agent_id: 'pty-session'}


def test_the_rows_own_cwd_wins_and_the_session_list_is_not_even_read(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    tui = w.litetui(10, retired=False)

    def never():
        raise AssertionError('PTY inventory read although the row records its cwd')
    found = w.discover(pty_sessions=never)
    assert found.candidates[0].seat.cwd == str(tmp_path / 'tuiwork')
    assert found.cwd_sources == {tui.agent_id: 'registry'}


def test_the_cwd_source_never_reaches_the_seat_wire(tmp_path, monkeypatch):
    from liteharness.checkpoint_contract import wire
    w = World(tmp_path, monkeypatch)
    tui = w.litetui(10, retired=False)
    from_row = w.discover().candidates[0].seat
    row_path = w.harness / 'agents' / f'{tui.agent_id}.json'
    row = json.loads(row_path.read_text(encoding='utf-8'))
    row.pop('cwd')
    put(row_path, row)
    found = w.discover(pty_sessions=lambda: [_pty(tui.agent_id, from_row.cwd)])
    assert found.cwd_sources == {tui.agent_id: 'pty-session'}
    assert wire(found.candidates[0].seat) == wire(from_row)
    assert 'pty-session' not in json.dumps(wire(found.candidates[0].seat))
