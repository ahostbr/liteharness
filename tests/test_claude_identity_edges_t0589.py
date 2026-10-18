"""Claude transcript/index refusal and retired UUID coverage through real entries."""
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from liteharness import agent_names, cli, hooks, seat_lifecycle
from test_claude_registration_resume_t0589 import AID, CID, NAME, registered  # noqa: F401


def resume(target, monkeypatch):
    entrypoint = cli.cmd_spawn
    launches = []
    monkeypatch.setattr(cli, 'cmd_spawn', lambda **kwargs: launches.append(kwargs))
    try:
        entrypoint(split_mode=True, resume_agent_id=target, tier='worker', spawned_by='parent-589')
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    return code, launches


@pytest.mark.parametrize('fault', ['missing', 'foreign-session', 'wrong-cwd', 'ambiguous', 'live-owner'])
def test_uuid_never_launches_a_missing_foreign_ambiguous_or_live_native_session(registered, monkeypatch, capsys, fault):
    _, project, row_path, row = registered
    transcript = Path(row['transcript_path'])
    if fault != 'live-owner':
        row.update(exited_at=datetime.now(timezone.utc).isoformat(), status='offline')
        row_path.write_text(json.dumps(row), encoding='utf-8')
    if fault == 'missing':
        transcript.unlink()
    elif fault == 'foreign-session':
        transcript.write_text(json.dumps({'sessionId': AID, 'cwd': str(project)}) + '\n', encoding='utf-8')
    elif fault == 'wrong-cwd':
        transcript.write_text(json.dumps({'sessionId': CID, 'cwd': str(project.parent)}) + '\n', encoding='utf-8')
    elif fault == 'ambiguous':
        duplicate = transcript.parent.parent / 'duplicate-project' / transcript.name
        duplicate.parent.mkdir()
        duplicate.write_bytes(transcript.read_bytes())
    code, launches = resume(AID, monkeypatch)
    assert code == 2 and launches == []
    assert 'SPAWN REFUSED' in capsys.readouterr().out


def test_raw_uuid_uses_real_retired_identity_without_a_names_index(registered, monkeypatch):
    root, project, row_path, row = registered
    assert seat_lifecycle.preserve_before_delete(row_path, row, 'fixture-owner-exit')
    retired = json.loads((root / 'retired-agents' / row_path.name).read_text(encoding='utf-8'))
    assert retired['transcript_path'] == row['transcript_path']
    # Simulate the transient active roster being absent after retirement; never
    # invent/repair a saved-name row to make the dispatch work.
    row_path.unlink()
    agent_names.index_path().unlink()
    import psutil
    original_process = psutil.Process

    def process(pid):
        if pid == row['session_pid']:
            raise psutil.NoSuchProcess(pid)
        return original_process(pid)

    monkeypatch.setattr(psutil, 'Process', process)  # Observed owner exit, not an alive archived owner.
    code, launches = resume(AID, monkeypatch)
    assert code == 0 and len(launches) == 1
    assert launches[0]['spawn_cli'] == 'claude'
    assert launches[0]['cwd'] == str(project)
    assert launches[0]['additional_args'] == f'--resume {CID}'


@pytest.mark.parametrize('fault', ['collision', 'corrupt-index'])
def test_real_registration_reports_index_failure_without_eviction_or_repair(registered, capsys, fault):
    _, project, _, _ = registered
    index = agent_names.index_path()
    if fault == 'collision':
        data = json.loads(index.read_text(encoding='utf-8'))
        data[NAME]['agent_id'] = CID
        index.write_text(json.dumps(data), encoding='utf-8')
    else:
        index.write_text('{torn', encoding='utf-8')
    before = index.read_bytes()
    hooks.register_presence()
    assert index.read_bytes() == before
    assert 'Claude resume index unavailable' in capsys.readouterr().err
