"""T0589 bounded fourth RED: real automatic registration, no saved-name repair.

The real hook context/registration and real CLI resume dispatcher execute. Only
its final native Claude launcher delegation is captured in the UUID test; this
is dispatch evidence, not proof of a live restarted Claude process.
"""
import json
import os
from datetime import datetime, timezone

import pytest

from liteharness import agent_names, cli, config, hooks

AID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
CID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
NAME = 'Claude-Registration-589'


@pytest.fixture
def registered(tmp_path, monkeypatch, capsys):
    # Isolate EVERY hook path (naming, lifecycle, prompts and transcript lookup),
    # not only config.get_root. No bridge token, watcher, or native CLI is used.
    for key in tuple(os.environ):
        if key.startswith(('LITEHARNESS', 'LITESUITE', 'LITETUI', 'CLAUDE', 'CODEX', 'COPILOT', 'LITECODE')):
            monkeypatch.delenv(key)
    home, root, project, data = (tmp_path / part for part in ('home', 'registry', 'project', 'data'))
    for path in (home, root, project, data):
        path.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('LITETUI_DATA_ROOT', str(data))
    monkeypatch.setenv('LITESUITE_FLEET_POLICY', str(tmp_path / 'absent-policy.json'))
    monkeypatch.setenv('LITEHARNESS_AGENT_ID', AID)
    monkeypatch.setenv('LITEHARNESS_REQUESTED_NAME', NAME)
    monkeypatch.setenv('LITEHARNESS_MODEL', 'claude-opus')
    monkeypatch.setenv('LITEHARNESS_CLI', 'claude-code')
    monkeypatch.setenv('LITEHARNESS_TIER', 'worker')
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', CID)
    monkeypatch.setattr(config, 'HARNESS_ROOT', root)
    monkeypatch.setattr(config, 'CONFIG_PATH', root / 'config.json')
    monkeypatch.setattr(hooks, '_LAST_PRESENCE', {})
    monkeypatch.setattr(hooks, '_resolve_session_pid', lambda existing=None: os.getpid())
    monkeypatch.chdir(project)
    encoded = ''.join(char if char.isalnum() else '-' for char in str(project))
    transcript = home / '.claude' / 'projects' / encoded / f'{CID}.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({
        'type': 'user', 'sessionId': CID, 'cwd': str(project),
        'uuid': 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        'message': {'role': 'user', 'content': 'fixture persisted first turn'},
    }) + '\n', encoding='utf-8')
    hooks._apply_hook_context({'hook_event_name': 'SessionStart', 'source': 'startup',
        'session_id': CID, 'transcript_path': str(transcript), 'cwd': str(project),
        'model': 'claude-opus'})
    hooks.register_presence()
    row_path = root / 'agents' / f'{AID}.json'
    row = json.loads(row_path.read_text(encoding='utf-8'))
    assert row['agent_id'] == AID and row['name'] == NAME
    assert row['cli'] == 'claude-code'
    assert row['cwd'] == str(project) and row['transcript_path'] == str(transcript)
    assert AID != CID and transcript.is_file()
    capsys.readouterr()  # Discard the unrelated doctrine banner, not failure evidence.
    return root, project, row_path, row


def test_sessionstart_persists_actual_resolved_name_and_native_conversation(registered):
    _, project, _, _ = registered
    entry = agent_names.resolve_name(NAME)
    assert entry is not None, 'SEMANTIC RED: automatic Claude registration did not persist names.json'
    assert entry['agent_id'] == AID and entry['convo_id'] == CID
    assert entry['cwd'] == str(project)
    assert str(entry['backend']).split('/', 1)[0] == 'claude'


@pytest.mark.parametrize('target', [NAME, AID])
def test_registered_claude_resumes_by_name_and_uuid_without_manual_index_repair(registered, monkeypatch, capsys, target):
    _, project, row_path, row = registered
    # Simulated owner exit at the registry boundary, NOT name-index repair.
    row.update(exited_at=datetime.now(timezone.utc).isoformat(), status='offline')
    row_path.write_text(json.dumps(row), encoding='utf-8')
    entrypoint = cli.cmd_spawn
    native_launches = []
    monkeypatch.setattr(cli, 'cmd_spawn', lambda **kwargs: native_launches.append(kwargs))
    try:
        entrypoint(split_mode=True, resume_agent_id=target, tier='worker', spawned_by='parent-589')
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    output = capsys.readouterr().out
    assert code == 0, (f'SEMANTIC RED: actual registered Claude target {target!r} cannot resume '
                       f'without names.json repair / UUID goes to owned LiteTUI; stdout={output!r}')
    assert len(native_launches) == 1
    launch = native_launches[0]
    assert launch['spawn_cli'] == 'claude'
    assert launch['name'] == NAME and launch['cwd'] == str(project)
    assert launch['additional_args'] == f'--resume {CID}'


def test_CONTROL_real_hook_registered_exact_harness_id_name_cwd_and_native_transcript(registered):
    _, _, _, row = registered
    assert row['name'] == NAME
    assert row['agent_id'] == AID
    assert CID in row['transcript_path'] and AID not in row['transcript_path']
