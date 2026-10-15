from dataclasses import replace
import json
import stat
from types import SimpleNamespace

import pytest

from liteharness.checkpoint_census import census
from liteharness.checkpoint_contract import Exclusion
from checkpoint_fixtures import native, owned, put, uid


def run(selected, harness, roots, **kwargs):
    return census(selected, harness_root=harness, authorized_data_roots=roots,
                  authorized_cwd_roots=roots, exclusions=kwargs.pop('exclusions', []), **kwargs)


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_mixed_cli_multiple_roots_offline_selected_not_dormant_read_only(tmp_path):
    harness = tmp_path / 'harness'
    index = {}
    selected = [native(tmp_path / cli, harness, n, cli, index)
                for n, cli in enumerate(('claude', 'codex', 'copilot'), 1)]
    selected += [owned(tmp_path / 'owned-a', 4), owned(tmp_path / 'owned-b', 5)]
    dormant = owned(tmp_path / 'owned-a', 6)
    put(harness / 'names.json', index)
    before = tree_bytes(tmp_path)
    result = run(selected, harness, [s.data_root for s in selected],
                 exclusions=[Exclusion(dormant.name, dormant.agent_id, 'Not selected for recovery')])
    assert result.expected_count == 5
    assert not result.blockers
    assert result.rows[-1].seat.execution.model == 'home-model'  # home, not stale candidate
    assert result.rows[-1].expected.selection == 'explicit-offline'
    assert dormant.agent_id not in [r.expected.agent_id for r in result.rows]
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize('fault', ['stale-name', 'corrupt-home', 'missing-conversation',
                                   'unknown-cli', 'missing-cwd', 'unauthorized-root', 'traversal'])
def test_bad_selected_evidence_blocks_without_disappearing(tmp_path, fault):
    root = tmp_path / 'owned'
    s = owned(root)
    harness = tmp_path / 'harness'
    put(harness / 'names.json', {})
    roots = [root]
    if fault == 'stale-name':
        put(harness / 'names.json', {s.name: {'agent_id': uid(999), 'convo_id': s.conversation_id}})
    elif fault == 'corrupt-home':
        (root / '.agents' / s.name / 'settings.json').write_text('{broken')
    elif fault == 'missing-conversation':
        s = replace(s, conversation_id=uid(999))
    elif fault == 'unknown-cli':
        s = replace(s, cli='mystery')
    elif fault == 'missing-cwd':
        s = replace(s, cwd=str(root / 'absent'))
    elif fault == 'unauthorized-root':
        roots = [harness]
    else:
        s = replace(s, data_root=str(root / '..' / 'owned'))
    result = run([s], harness, roots)
    assert result.expected_count == 1
    assert result.rows[0].expected.agent_id == s.agent_id
    assert result.rows[0].seat is None
    assert result.blockers


def test_missing_current_registry_and_corrupt_index_keep_expected(tmp_path):
    s = replace(owned(tmp_path / 'owned'), selection='selected-current')
    harness = tmp_path / 'harness'
    put(harness / 'names.json', {})
    assert run([s], harness, [s.data_root]).blockers
    (harness / 'names.json').write_text('[]')
    result = run([s], harness, [s.data_root])
    assert result.expected_count == 1 and result.blockers[0].code == 'census-input'


def test_duplicate_conversation_blocks_both_selected_rows(tmp_path):
    a = owned(tmp_path / 'a', 1)
    b = replace(owned(tmp_path / 'b', 2), conversation_id=a.conversation_id)
    harness = tmp_path / 'harness'
    put(harness / 'names.json', {})
    result = run([a, b], harness, [a.data_root, b.data_root])
    assert len(result.rows) == 2
    assert all(any(b.code == 'duplicate-conversation' for b in r.blockers) for r in result.rows)


def test_duplicate_conversation_in_dormant_home_blocks_selected_owner(tmp_path):
    root = tmp_path / 'owned'
    a, b = owned(root, 1), owned(root, 2)
    other = root / '.agents' / b.name / 'conversations' / a.conversation_id
    other.mkdir()
    harness = tmp_path / 'harness'
    put(harness / 'names.json', {})
    result = run([a], harness, [root])
    assert result.expected_count == 1 and result.blockers


def test_duplicate_owned_identity_across_selected_roots(tmp_path):
    a = owned(tmp_path / 'a', 1)
    owned(tmp_path / 'b', 1)
    b = owned(tmp_path / 'b', 2)
    harness = tmp_path / 'harness'
    put(harness / 'names.json', {})
    result = run([a, b], harness, [a.data_root, b.data_root])
    assert result.rows[0].blockers
    assert not result.rows[1].blockers


@pytest.mark.parametrize('fault', ['original-cwd', 'missing-transcript', 'transcript-escape', 'wrong-cli'])
def test_native_cli_evidence_is_not_an_agent_id_resume_guess(tmp_path, fault):
    root, harness, index = tmp_path / 'claude', tmp_path / 'harness', {}
    s = native(root, harness, 1, 'claude', index)
    put(harness / 'names.json', index)
    registry_path = harness / 'agents' / f'{s.agent_id}.json'
    registry = json.loads(registry_path.read_text())
    if fault == 'original-cwd':
        s = replace(s, cwd=str(harness))
    elif fault == 'missing-transcript':
        registry.pop('transcript_path')
    elif fault == 'wrong-cli':
        registry['cli'] = 'codex'
    else:
        outside = tmp_path / 'outside.jsonl'
        outside.write_text('private')
        registry['transcript_path'] = str(outside)
    put(registry_path, registry)
    result = run([s], harness, [root, harness])
    assert result.blockers and result.expected_count == 1


def test_link_and_windows_reparse_refusal_without_creating_links(tmp_path, monkeypatch):
    # No test creates a symlink/junction: simulate the OS lstat boundary so it
    # exercises both refusal paths on Windows without requiring privilege.
    root, harness = tmp_path / 'owned', tmp_path / 'harness'
    s = owned(root)
    put(harness / 'names.json', {})
    target = root / '.agents' / s.name / 'settings.json'
    original = type(target).lstat
    for link_stat in (SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0),
                      SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)):
        monkeypatch.setattr(type(target), 'lstat', lambda p, *a, **kw:
                            link_stat if p == target else original(p, *a, **kw))
        result = run([s], harness, [root])
        assert result.expected_count == 1 and result.blockers
    monkeypatch.setattr(type(target), 'lstat', original)


def test_copied_identity_in_unselected_authorized_root_is_ambiguous(tmp_path):
    a = owned(tmp_path / 'a', 1)
    owned(tmp_path / 'b', 1)
    harness = tmp_path / 'harness'
    put(harness / 'names.json', {})
    result = run([a], harness, [a.data_root, tmp_path / 'b'])
    assert result.expected_count == 1 and result.blockers


def test_legacy_tier_owner_cannot_be_downgraded_by_candidate(tmp_path):
    s = owned(tmp_path / 'owned')
    harness = tmp_path / 'harness'
    put(harness / 'names.json', {})
    put(harness / 'agents' / f'{s.agent_id}.json', {'agent_id': s.agent_id, 'tier': 'leader'})
    assert run([s], harness, [s.data_root]).blockers


def test_native_index_model_conflict_blocks(tmp_path):
    harness, index = tmp_path / 'harness', {}
    s = native(tmp_path / 'claude', harness, 1, 'claude', index)
    index[s.name]['model'] = 'different-model'
    put(harness / 'names.json', index)
    assert run([s], harness, [s.data_root]).blockers
