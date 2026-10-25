"""Current-slot CLI destination REDs: never ordinary split after recovery lookup.

The host is the external authority boundary here. Its issuer/native/binder
pipeline has separate Suite coverage; this is the real owned resume entry.
"""
import io
import json
import os
import subprocess
import sys

import pytest

from liteharness import cli
from test_restore_runtime_receipt_t0589 import AID, SESSION, launch, world  # noqa: F401


@pytest.mark.parametrize('outcome', ['bound', 'unknown', 'refused'])
def test_owned_cli_reserved_slot_uses_host_recovery_and_never_an_ordinary_split(world, monkeypatch, capsys, outcome):
    _, _, _, state = world
    transport = cli._bridge_request
    calls = []

    def bridge(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if path.startswith('/harness/recovered-agent/locate?'):
            return {'ok': True, 'found': True, 'paneId': 'saved-pane', 'slotId': 'saved-slot',
                    'leafId': 'saved-agent-leaf', 'agentId': AID}
        if path == '/harness/recovered-agent/restore':
            return {'ok': outcome == 'bound', 'status': outcome, 'requestId': 'request-589',
                    'agentId': AID, 'sessionId': SESSION, 'paneId': 'saved-pane',
                    'slotId': 'saved-slot', 'leafId': 'saved-agent-leaf'}
        return transport(method, path, body, **kwargs)

    monkeypatch.setattr(cli, '_bridge_request', bridge)
    code = launch(world, 'owned-resume')
    assert not [item for item in state['calls'] if item[1] in ('/canvas/split', '/pty/create')], (
        'SEMANTIC RED: owned CLI resume created another leaf instead of consuming the reserved-slot destination')
    assert len([item for item in calls if item[1] == '/harness/recovered-agent/restore']) == 1
    assert code == (0 if outcome == 'bound' else 2)
    output = capsys.readouterr().out
    assert 'Launched owned seat' not in output
    if outcome == 'bound':
        assert 'saved-agent-leaf' in output


def _host(monkeypatch, response):
    transport = cli._bridge_request

    def bridge(method, path, body=None, **kwargs):
        if path.startswith('/harness/recovered-agent/locate?'):
            return {'ok': True, 'found': True, 'paneId': 'saved-pane', 'slotId': 'saved-slot',
                    'leafId': 'saved-agent-leaf', 'agentId': AID}
        if path == '/harness/recovered-agent/restore':
            return response
        return transport(method, path, body, **kwargs)
    monkeypatch.setattr(cli, '_bridge_request', bridge)


@pytest.mark.parametrize('status', ['refused', 'unknown'])
def test_host_refusal_reason_reaches_the_caller(world, monkeypatch, capsys, status):
    # T0558-F: the reason was dropped, so a refusal and a timeout read the same ("allocation outcome unknown").
    reason = 'Prior slot episode unresolved or its exact runtime not proved gone'
    _host(monkeypatch, {'ok': False, 'status': status, 'requestId': 'request-589', 'reason': reason})
    assert launch(world, 'owned-resume') == 2
    output = capsys.readouterr().out
    assert reason in output
    assert 'allocation outcome unknown' in output  # no session id was returned, and that is still said


def test_cwd_disagreement_is_reported_not_refused(world, monkeypatch, capsys):
    _host(monkeypatch, {'ok': True, 'status': 'bound', 'requestId': 'request-589', 'agentId': AID,
                        'sessionId': SESSION, 'paneId': 'saved-pane', 'slotId': 'saved-slot',
                        'leafId': 'saved-agent-leaf', 'placeholderCwd': 'G:\\SAS\\ShadowsAndShurikens',
                        'authorityCwd': 'G:\\SAS\\ShadowsAndShurikens\\.worktrees\\Seat'})
    assert launch(world, 'owned-resume') == 0
    output = capsys.readouterr().out
    assert 'saved-agent-leaf' in output
    assert 'placeholder cwd G:\\SAS\\ShadowsAndShurikens' in output
    assert 'used the seat\'s recorded cwd G:\\SAS\\ShadowsAndShurikens\\.worktrees\\Seat' in output


SEED = {'version': 1, 'path': os.path.abspath('library/tesla.md'), 'bodySha256': 'a' * 64}


def test_cognitive_seed_env_is_strict_and_optional(monkeypatch):
    from liteharness import recovered_destination as rd
    monkeypatch.delenv(rd.SEED_ENV, raising=False)
    assert rd.cognitive_seed() is None
    monkeypatch.setenv(rd.SEED_ENV, json.dumps(SEED))
    assert rd.cognitive_seed() == SEED
    for bad in ('not json', '[]', json.dumps({**SEED, 'version': 2}), json.dumps({**SEED, 'extra': 1}),
                json.dumps({**SEED, 'path': 'relative.md'}), json.dumps({**SEED, 'path': ''}),
                json.dumps({**SEED, 'bodySha256': 'A' * 64}), json.dumps({**SEED, 'bodySha256': 'a' * 63}),
                json.dumps({'version': 1, 'path': SEED['path']}), json.dumps({**SEED, 'path': 'x' * 5000})):
        monkeypatch.setenv(rd.SEED_ENV, bad)
        with pytest.raises(ValueError, match='Invalid cognitive seed'):
            rd.cognitive_seed()


@pytest.mark.parametrize('seeded', [True, False])
def test_cognitive_seed_is_forwarded_to_the_host_and_is_not_an_override(world, monkeypatch, seeded):
    from liteharness import recovered_destination as rd
    sent = []
    transport = cli._bridge_request

    def bridge(method, path, body=None, **kwargs):
        if path.startswith('/harness/recovered-agent/locate?'):
            return {'ok': True, 'found': True, 'paneId': 'saved-pane', 'slotId': 'saved-slot',
                    'leafId': 'saved-agent-leaf', 'agentId': AID}
        if path == '/harness/recovered-agent/restore':
            sent.append(body)
            return {'ok': True, 'status': 'bound', 'requestId': 'r', 'agentId': AID, 'sessionId': SESSION,
                    'leafId': 'saved-agent-leaf'}
        return transport(method, path, body, **kwargs)
    monkeypatch.setattr(cli, '_bridge_request', bridge)
    if seeded:
        monkeypatch.setenv(rd.SEED_ENV, json.dumps(SEED))
    else:
        monkeypatch.delenv(rd.SEED_ENV, raising=False)
    assert launch(world, 'owned-resume') == 0
    assert len(sent) == 1
    assert (sent[0].get('cognitiveSeed') == SEED) is seeded and ('cognitiveSeed' in sent[0]) is seeded


def _library(tmp_path, monkeypatch, text='# TESLA\nbody\n'):
    from liteharness import prompts
    path = tmp_path / 'tesla.md'
    path.write_text(text, encoding='utf-8')
    monkeypatch.setattr(prompts, 'resolve_cognitive_file', lambda name, tier=None, **kw: path if name == 'tesla' else None)
    import hashlib
    return {'version': 1, 'path': str(path), 'bodySha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def test_a_seed_is_used_only_while_the_library_still_resolves_that_exact_body(tmp_path, monkeypatch):
    from liteharness import recovered_destination as rd
    seed = _library(tmp_path, monkeypatch)
    assert rd.verified_seed(seed, 'builder') == 'tesla'
    (tmp_path / 'tesla.md').write_text('# TESLA\nedited since save\n', encoding='utf-8')
    assert rd.verified_seed(seed, 'builder') is None, 'a changed method is never silently substituted'
    assert rd.verified_seed({**seed, 'path': str(tmp_path / 'other.md')}, 'builder') is None  # not in the library
    assert rd.verified_seed(None, 'builder') is None and rd.verified_seed('x', 'builder') is None


def test_native_claude_prefers_the_verified_seed_over_the_registry_label(tmp_path, monkeypatch):
    from liteharness import recovered_destination as rd
    seed = _library(tmp_path, monkeypatch)
    assert rd.native_cognitive({'cognitive': 'euler'}, {'cognitiveSeed': seed}, 'builder') == 'tesla'
    assert rd.native_cognitive({}, {'cognitiveSeed': seed}, 'builder') == 'tesla', 'no registry label at all'
    (tmp_path / 'tesla.md').write_text('changed', encoding='utf-8')
    assert rd.native_cognitive({'cognitive': 'euler'}, {'cognitiveSeed': seed}, 'builder') == 'euler'
    assert rd.native_cognitive({}, {}, 'builder') is None


def _presence(root, name, **fields):
    (root / 'agents').mkdir(exist_ok=True)
    (root / 'agents' / f'{name}.json').write_text(json.dumps({'agent_id': name, **fields}), encoding='utf-8')


def test_session_holders_counts_only_live_or_unknown_rows(tmp_path, monkeypatch):
    from liteharness import config, recovered_destination
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    gone = subprocess.Popen([sys.executable, '-c', 'pass'])
    gone.wait()
    _presence(tmp_path, 'live', canvas_session_id='pty-9', session_pid=os.getpid())
    _presence(tmp_path, 'unknown-pid', canvas_session_id='pty-9')  # not exited, pid unreadable: held
    _presence(tmp_path, 'exited', canvas_session_id='pty-9', session_pid=os.getpid(), exited_at='2026-10-24T00:00:00+00:00')
    _presence(tmp_path, 'dead', canvas_session_id='pty-9', session_pid=gone.pid)
    _presence(tmp_path, 'other', canvas_session_id='pty-10', session_pid=os.getpid())
    assert recovered_destination.holders('pty-9') == {'agentIds': ['live', 'unknown-pid']}
    assert recovered_destination.holders('pty-404') == {'agentIds': []}


def test_session_holders_is_unknown_when_a_presence_row_is_unreadable(tmp_path, monkeypatch):
    from liteharness import config, recovered_destination
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'agents' / 'broken.json').write_text('{', encoding='utf-8')
    with pytest.raises(ValueError):
        recovered_destination.holders('pty-9')


def test_adapter_exposes_holders_to_the_host(tmp_path, monkeypatch, capsys):
    from liteharness import config, recovered_destination
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'action': 'holders', 'sessionId': 'pty-9'})))
    assert recovered_destination.main() == {'agentIds': []}
