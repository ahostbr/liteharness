"""Roster discovery against a synthetic harness root: mixed Claude/LiteTUI rows."""
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
