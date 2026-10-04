"""T0308 official same-ID/name registration, isolated registry fixtures only."""
import json
import os

import pytest

from liteharness import cli, config, naming, strict_registration
from liteharness.agent_store import StoreError

AID = '11111111-1111-4111-8111-111111111111'
BID = '22222222-2222-4222-8222-222222222222'


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(strict_registration, 'pid_alive',
                        lambda pid: _known_pid(pid))
    from liteharness import seat_lifecycle, announce
    monkeypatch.setattr(seat_lifecycle, 'log_path', lambda: tmp_path / 'lifecycle.jsonl')
    monkeypatch.setattr(announce, 'announce_registration', lambda *a, **k: [])
    return tmp_path


def _known_pid(pid):
    if type(pid) is not int or pid <= 0:
        raise StoreError('fixture unknown PID')
    return pid != 999999999


def row(root, identity=AID, **updates):
    directory = root / 'agents'
    directory.mkdir(exist_ok=True)
    raw = {'agent_id': identity, 'name': 'QuietHelm', 'session_pid': os.getpid(),
           'backend': 'claude', 'model': 'poison', 'thinking_level': 'low',
           'last_seen': '2000-01-01T00:00:00+00:00'}
    raw.update(updates)
    path = directory / (identity + '.json')
    path.write_text(json.dumps(raw), encoding='utf-8')
    return path


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def register(**overrides):
    values = dict(agent_id=AID, name='QuietHelm', session_pid=os.getpid(),
                  backend='codex', model='folder-model', thinking_level='high',
                  cli='litetui', strict_identity=True)
    values.update(overrides)
    cli.cmd_register(**values)


def test_missing_registry_official_registration_same_folder_identity(registry, capsys):
    register()
    raw = json.loads((registry / 'agents' / (AID + '.json')).read_text())
    assert (raw['agent_id'], raw['name']) == (AID, 'QuietHelm')
    assert (raw['backend'], raw['model'], raw['thinking_level']) == ('codex', 'folder-model', 'high')
    assert raw['session_pid'] == os.getpid()
    assert 'name=QuietHelm' in capsys.readouterr().out
    assert not (registry / 'names').exists()  # no competing naming override


def test_same_owner_heartbeat_refreshes_presence_not_naming_override(registry):
    row(registry)
    naming.set_override(AID, 'PoisonIndex')
    name_path = registry / 'names' / AID
    before = name_path.read_bytes()
    register()
    register()
    raw = json.loads((registry / 'agents' / (AID + '.json')).read_text())
    assert raw['agent_id'] == AID and raw['name'] == 'QuietHelm'
    assert raw['backend'] == 'codex' and raw['model'] == 'folder-model'
    assert name_path.read_bytes() == before


@pytest.mark.parametrize('kind', ['same_live', 'name_live', 'same_unknown', 'name_unknown',
                                  'malformed', 'unreadable'])
def test_conflicting_or_inaccessible_registry_refused_before_any_write(registry, monkeypatch, kind):
    identity = BID if kind.startswith('name') else AID
    path = row(registry, identity, session_pid=None if 'unknown' in kind else os.getpid() + 1)
    if kind == 'malformed':
        path.write_text('{invalid', encoding='utf-8')
    before = snapshot(registry)
    if kind == 'unreadable':
        original = type(path).read_text
        def denied(p, *args, **kwargs):
            if p == path:
                raise PermissionError('fixture unreadable')
            return original(p, *args, **kwargs)
        monkeypatch.setattr(type(path), 'read_text', denied)
    with pytest.raises(StoreError):
        register()
    assert snapshot(registry) == before
    assert not (registry / 'names').exists()


@pytest.mark.parametrize('change', [dict(agent_id='legacy-nonuuid'), dict(name='CON'),
                                     dict(takeover=True), dict(session_pid=None),
                                     dict(session_pid=999999999), dict(backend=None),
                                     dict(model=''), dict(thinking_level=None)])
def test_invalid_strict_request_does_not_materialize_registry(registry, change):
    with pytest.raises(StoreError):
        register(**change)
    assert list(registry.iterdir()) == []


def test_known_dead_same_id_resumes_without_eviction_or_execution_fallback(registry):
    path = row(registry, session_pid=999999999)
    register()
    raw = json.loads(path.read_text())
    assert raw['agent_id'] == AID and raw['name'] == 'QuietHelm'
    assert (raw['backend'], raw['model'], raw['thinking_level']) == ('codex', 'folder-model', 'high')
    assert not list(registry.glob('backup*'))


def test_legacy_registration_keeps_nonuuid_and_override_behavior(registry):
    cli.cmd_register('legacy-seat', name='LegacyCustom', session_pid=os.getpid())
    raw = json.loads((registry / 'agents' / 'legacy-seat.json').read_text())
    assert raw['name'] == 'LegacyCustom'
    assert naming.get_override('legacy-seat') == 'LegacyCustom'


def test_pid_inspection_failure_is_not_dead_permission(monkeypatch):
    import psutil
    def inaccessible(_):
        raise PermissionError('fixture process inspection')
    monkeypatch.setattr(psutil, 'pid_exists', inaccessible)
    with pytest.raises(StoreError, match='cannot be inspected'):
        strict_registration.pid_alive(os.getpid())


def test_official_cli_flag_is_parsed():
    import inspect
    source = inspect.getsource(cli.main)
    assert 'sys.argv[i] == "--strict-identity"' in source
    assert 'strict_identity=reg_strict_identity' in source


@pytest.mark.parametrize('prior_pid', [1.0, True, '1', 0, -1])
def test_same_id_pid_equality_never_bypasses_exact_integer_validation(registry, prior_pid, monkeypatch):
    row(registry, session_pid=prior_pid)
    before = snapshot(registry)
    def validate_pid(pid):
        if type(pid) is not int or pid <= 0:
            raise StoreError('fixture unknown PID')
        return True
    monkeypatch.setattr(strict_registration, 'pid_alive', validate_pid)
    with pytest.raises(StoreError):
        register(session_pid=1)
    assert snapshot(registry) == before


@pytest.mark.parametrize('name', [None, '', 42, 'CON'])
def test_unrelated_incomplete_name_cannot_be_silently_ruled_out_as_collision(registry, name):
    row(registry, 'legacy-arbitrary-id', name=name)
    before = snapshot(registry)
    with pytest.raises(StoreError):
        register()
    assert snapshot(registry) == before


def test_unrelated_complete_legacy_identity_does_not_require_canonical_uuid(registry):
    path = row(registry, 'legacy-arbitrary-id', name='OtherSeat', session_pid=None)
    before = path.read_bytes()
    register()
    assert path.read_bytes() == before
    assert json.loads((registry / 'agents' / (AID + '.json')).read_text())['name'] == 'QuietHelm'
