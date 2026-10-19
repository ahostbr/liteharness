"""Synthetic harness world shared by the roster, save and restore tests."""
import json

from liteharness.checkpoint_roster import discover_current
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
