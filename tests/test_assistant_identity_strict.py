"""Synthetic process/registry controls, NOT fresh Stop host topology proof."""
import json
from types import SimpleNamespace

import pytest
from liteharness import assistant_identity as identity


class Missing(Exception):
    pass


class Process:
    def __init__(self, pid, parent, birth, executable):
        self.pid, self.parent, self.birth, self.executable = pid, parent, birth, executable
    def ppid(self): return self.parent
    def create_time(self): return self.birth
    def is_running(self): return True
    def status(self): return 'running'
    def exe(self): return self.executable


@pytest.fixture
def case(tmp_path):
    directory = tmp_path / 'agents'
    directory.mkdir()
    row = dict(agent_id='own', pane_id='pane-own', tier='orchestrator',
               session_pid=20, session_process_started_at=20000)
    path = directory / 'own.json'
    path.write_text(json.dumps(row))
    processes = {30: Process(30, 25, 30, '/python'),
                 25: Process(25, 20, 25, '/wrapper'),
                 20: Process(20, 10, 20, '/bin/claude'),
                 10: Process(10, 1, 10, '/shell')}
    def lookup(pid):
        if pid not in processes: raise Missing()
        return processes[pid]
    api = SimpleNamespace(Process=lookup, NoSuchProcess=Missing, STATUS_ZOMBIE='zombie')
    def resolve(payload=None):
        return identity.own_assistant_identity({'session_id': 'own'} if payload is None else payload,
                                               tmp_path, process_api=api, hook_pid=30)
    def write(change):
        row.update(change)
        path.write_text(json.dumps(row))
    return SimpleNamespace(root=tmp_path, path=path, directory=directory, row=row,
                           processes=processes, api=api, resolve=resolve, write=write)


def test_direct_singleton_positive_env_ignored(case, monkeypatch):
    for key in ('LITEHARNESS_AGENT_ID', 'LITEHARNESS_SESSION_PID', bytes.fromhex('4c49544553554954455f53454e54494e454c5f50414e455f4944').decode()):
        monkeypatch.setenv(key, 'other')
    assert case.resolve() == ('own', 'pane-own')


@pytest.mark.parametrize('value', [None, '', '../own', '/own', 1, True, {}, 'other', 'x'*129, 'own\n'])
def test_invalid_or_unmapped_payload(case, value):
    assert case.resolve({'session_id': value}) is None


@pytest.mark.parametrize('change', [
    {'agent_id': 'other'}, {'tier': 'worker'}, {'tier': True}, {'tier': []},
    {'pane_id': None}, {'pane_id': 1}, {'pane_id': ' '},
    {'session_pid': True}, {'session_pid': '20'}, {'session_pid': 0}, {'session_pid': 10},
    {'session_process_started_at': True}, {'session_process_started_at': None},
    {'session_process_started_at': float('inf')}, {'session_process_started_at': 0},
    {'session_process_started_at': 20002},
])
def test_own_strict_fields(case, change):
    case.write(change)
    assert case.resolve() is None


@pytest.mark.parametrize('mode', ['sibling', 'outer', 'shell', 'argv-mention', 'cycle', 'birth', 'denied', 'reused'])
def test_topology_refusals(case, mode):
    if mode == 'sibling': case.write({'session_pid': 40}); case.processes[40] = Process(40, 10, 20, '/claude')
    if mode == 'outer': case.processes[25].executable = '/claude'
    if mode in {'shell', 'argv-mention'}: case.processes[20].executable = '/bin/shell'
    if mode == 'cycle': case.processes[25].parent = 30
    if mode == 'birth': case.processes[25].birth = 31
    if mode == 'denied': case.processes[25].exe = lambda: (_ for _ in ()).throw(PermissionError())
    if mode == 'reused': case.processes[20].birth = 21
    assert case.resolve() is None


@pytest.mark.parametrize('value', ['not json', '[]', '{"agent_id":"other","tier":"unknown"}',
                                  '{"agent_id":"other","agent_id":"own","tier":"worker"}'])
def test_unknown_registry_entry_refuses(case, value):
    (case.directory / 'other.json').write_text(value)
    assert case.resolve() is None


def test_multiple_live_orchestrators_refuse_but_known_dead_excluded(case):
    other = dict(case.row, agent_id='other', session_pid=40)
    (case.directory / 'other.json').write_text(json.dumps(other))
    case.processes[40] = Process(40, 10, 20, '/claude')
    assert case.resolve() is None
    del case.processes[40]
    assert case.resolve() == ('own', 'pane-own')
    other['session_process_started_at'] = None
    (case.directory / 'other.json').write_text(json.dumps(other))
    assert case.resolve() is None


def test_inaccessible_other_candidate_not_excluded(case):
    (case.directory / 'other.json').write_text(json.dumps(dict(case.row, agent_id='other', session_pid=40)))
    original = case.api.Process
    def denied(pid):
        if pid == 40: raise PermissionError()
        return original(pid)
    case.api.Process = denied
    assert case.resolve() is None


def test_own_reread_race(case, monkeypatch):
    original = identity._read
    count = 0
    def racing(path):
        nonlocal count
        result = original(path)
        if path == case.path:
            count += 1
            if count == 2: case.write({'pane_id': 'replacement'})
        return result
    monkeypatch.setattr(identity, '_read', racing)
    assert case.resolve() is None


def test_depth_overflow(case):
    for pid in range(1, 90):
        case.processes[pid] = Process(pid, pid-1, pid, '/wrapper')
    assert identity.own_assistant_identity({'session_id': 'own'}, case.root,
        process_api=case.api, hook_pid=89) is None


def test_unknown_platform_and_inspection_failure(case, monkeypatch):
    monkeypatch.setattr(identity.sys, 'platform', 'unknown')
    assert case.resolve() is None


def test_missing_own_and_birth_race(case):
    case.path.unlink()
    assert case.resolve() is None


def test_pid_birth_recheck(case):
    original = case.api.Process
    count = 0
    def recycled(pid):
        nonlocal count
        if pid == 20:
            count += 1
            if count == 2: case.processes[20].birth = 21
        return original(pid)
    case.api.Process = recycled
    assert case.resolve() is None


@pytest.mark.parametrize('mode', ['tier', 'pane', 'deleted'])
def test_own_final_reread_refuses_changes(case, monkeypatch, mode):
    original = identity._read
    count = 0
    def racing(path):
        nonlocal count
        result = original(path)
        if path == case.path:
            count += 1
            if count == 2:
                if mode == 'deleted': case.path.unlink()
                elif mode == 'tier': case.write({'tier': 'worker'})
                else: case.write({'pane_id': 'replacement'})
        return result
    monkeypatch.setattr(identity, '_read', racing)
    assert case.resolve() is None


def test_process_dependency_absence(case, monkeypatch):
    monkeypatch.setitem(identity.sys.modules, 'psutil', None)
    assert identity.own_assistant_identity({'session_id': 'own'}, case.root) is None


def test_duplicate_registry_identity_refuses(case):
    (case.directory / 'duplicate.json').write_text(json.dumps(case.row))
    assert case.resolve() is None
