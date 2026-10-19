"""Claude census authority: registry row + native transcript, not a drifting names row."""
from dataclasses import replace
import json

import pytest

from liteharness.checkpoint_census import census
from checkpoint_fixtures import put, seat, uid


def claude_seat(tmp_path, monkeypatch, *, names, transcript_cwd=None, evidence=True):
    config_dir = tmp_path / '.claude'
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(config_dir))
    projects = config_dir / 'projects'
    cwd = tmp_path / 'work'
    cwd.mkdir()
    s = replace(seat(projects, 1, 'claude', 'selected-current'), cwd=str(cwd), role='owner',
                owner_profile=None)
    s = replace(s, owner_profile=_profile(projects, s))
    transcript = projects / 'enc' / f'{s.conversation_id}.jsonl'
    transcript.parent.mkdir(parents=True)
    record = {'sessionId': s.conversation_id, 'cwd': transcript_cwd or str(cwd)} if evidence else {'type': 'meta'}
    transcript.write_text(json.dumps(record) + '\n', encoding='utf-8')
    harness = tmp_path / 'harness'
    # A real Claude row: cli 'claude-code', no thinking_level, no backend.
    put(harness / 'agents' / f'{s.agent_id}.json', {
        'agent_id': s.agent_id, 'name': s.name, 'cli': 'claude-code', 'role': 'owner',
        'model': s.execution.model, 'transcript_path': str(transcript)})
    put(harness / 'names.json', names(s))
    return s, harness


def _profile(projects, s):
    from liteharness.checkpoint_contract import OwnerProfile
    return OwnerProfile(s.name, str(projects))


def run(s, harness):
    return census([replace(s, execution=replace(s.execution, thinking_level='unrecorded'))],
                  harness_root=harness, authorized_data_roots=[s.data_root],
                  authorized_cwd_roots=[s.cwd, s.data_root], exclusions=[])


def test_names_row_absent_does_not_block(tmp_path, monkeypatch):
    s, harness = claude_seat(tmp_path, monkeypatch, names=lambda s: {})
    result = run(s, harness)
    assert not result.blockers and result.rows[0].seat.cwd == s.cwd


def test_names_row_with_null_cwd_does_not_block(tmp_path, monkeypatch):
    s, harness = claude_seat(tmp_path, monkeypatch, names=lambda s: {
        s.name: {'agent_id': s.agent_id, 'convo_id': s.conversation_id, 'cwd': None,
                 'backend': 'claude', 'model': s.execution.model}})
    assert not run(s, harness).blockers


def test_drifted_names_cwd_does_not_override_the_transcript(tmp_path, monkeypatch):
    s, harness = claude_seat(tmp_path, monkeypatch, names=lambda s: {
        s.name: {'agent_id': s.agent_id, 'convo_id': s.conversation_id, 'cwd': 'C:/elsewhere',
                 'backend': 'claude', 'model': s.execution.model}})
    assert not run(s, harness).blockers


def test_transcript_launch_cwd_contradiction_still_blocks(tmp_path, monkeypatch):
    other = tmp_path / 'other'
    other.mkdir()
    s, harness = claude_seat(tmp_path, monkeypatch, names=lambda s: {}, transcript_cwd=str(other))
    result = run(s, harness)
    assert result.expected_count == 1 and result.rows[0].seat is None
    assert 'disagrees with native transcript' in result.blockers[0].remedy


def test_no_transcript_evidence_and_no_names_row_blocks(tmp_path, monkeypatch):
    s, harness = claude_seat(tmp_path, monkeypatch, names=lambda s: {}, evidence=False)
    result = run(s, harness)
    assert result.rows[0].seat is None and 'not corroborated' in result.blockers[0].remedy


def test_names_row_for_another_conversation_still_blocks(tmp_path, monkeypatch):
    s, harness = claude_seat(tmp_path, monkeypatch, names=lambda s: {
        s.name: {'agent_id': s.agent_id, 'convo_id': uid(999), 'cwd': None}})
    assert run(s, harness).blockers


def test_unrecorded_thinking_only_matches_an_absent_registry_value(tmp_path, monkeypatch):
    s, harness = claude_seat(tmp_path, monkeypatch, names=lambda s: {})
    claimed = replace(s, execution=replace(s.execution, thinking_level='high'))
    result = census([claimed], harness_root=harness, authorized_data_roots=[s.data_root],
                    authorized_cwd_roots=[s.cwd, s.data_root], exclusions=[])
    assert result.blockers
