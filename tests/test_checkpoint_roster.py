"""Roster discovery against a synthetic harness root: mixed Claude/LiteTUI rows."""
import json

from liteharness.checkpoint_roster import discover_current, roster_census
from checkpoint_fixtures import owned, put, uid


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.harness = tmp_path / 'harness'
        self.projects = tmp_path / '.claude' / 'projects'
        self.owned_root = tmp_path / 'owned'
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / '.claude'))
        self.live = set()
        self.names = {}

    def claude(self, n, *, role='worker', parent=None, transcript=True, retired=False, alive=True,
               names_cwd='missing', cli='claude-code'):
        agent_id, cwd = uid(n), self.tmp / f'work{n}'
        cwd.mkdir(exist_ok=True)
        path = self.projects / 'enc' / f'{agent_id}.jsonl'
        if transcript:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'sessionId': agent_id, 'cwd': str(cwd)}) + '\n', encoding='utf-8')
        row = {'agent_id': agent_id, 'name': f'Claude-{n}', 'cli': cli, 'role': role, 'model': 'claude-x',
               'cognitive': 'tesla', 'transcript_path': str(path), 'registered_at': '2026-10-19T00:00:00+00:00',
               'workspace_id': 'ws', 'thread_id': 'th', 'pane_id': f'pane-{n}', 'leaf_id': f'leaf-{n}',
               **({'spawned_by': parent} if parent else {})}
        put(self.harness / ('retired-agents' if retired else 'agents') / f'{agent_id}.json', row)
        if names_cwd != 'missing':
            self.names[row['name']] = {'agent_id': agent_id, 'convo_id': agent_id, 'cwd': names_cwd,
                                       'backend': 'claude', 'model': 'claude-x'}
        if alive and not retired:
            self.live.add(agent_id)
        return agent_id

    def litetui(self, n, *, retired=True, role='worker'):
        seat = owned(self.owned_root, n)
        cwd = self.tmp / 'tuiwork'
        cwd.mkdir(exist_ok=True)
        put(self.harness / ('retired-agents' if retired else 'agents') / f'{seat.agent_id}.json', {
            'agent_id': seat.agent_id, 'name': seat.name, 'cli': 'litetui', 'role': role,
            'backend': 'codex', 'model': 'home-model', 'thinking_level': 'high', 'cwd': str(cwd)})
        if not retired:
            self.live.add(seat.agent_id)
        return seat

    def discover(self, **kwargs):
        put(self.harness / 'names.json', self.names)
        return discover_current(harness_root=self.harness, litetui_root=self.owned_root,
                                live=lambda row: row['agent_id'] in self.live, **kwargs)


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
