"""Cross-surface contract: status percentages must reach model-visible hooks.

The status line receives usage; documented PostToolUse input does not. This
regression intentionally exposes the missing transport rather than inventing
usage fields in hook stdin or guessing a window from a model name.
"""
import io
import json

import pytest

from liteharness import hooks, statusline

SID = '11111111-1111-4111-8111-111111111111'


@pytest.fixture
def tick(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hooks.config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(hooks.config, 'get_agent_id', lambda: SID)
    monkeypatch.setattr(hooks, 'check_inbox', lambda: None)
    monkeypatch.setattr(hooks, 'update_heartbeat', lambda: None)
    monkeypatch.setattr(statusline, 'git_branch', lambda _: '')
    monkeypatch.setattr(statusline, 'agent_badge', lambda _: 'Alpha')
    from liteharness import paste_history
    monkeypatch.setattr(paste_history, 'handle', lambda *a: None)
    monkeypatch.setenv('LITEHARNESS_ROLE', 'owner')
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('', encoding='utf-8')

    def run(used, session_id=SID, publish=True, before_check=None, hook_extra=None):
        # Only the status-line schema contains a context-window percentage.
        context = {} if used is None else {'used_percentage': used, 'context_window_size': 200000}
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({
            'session_id': SID, 'context_window': context,
        })))
        if publish:
            statusline.main()
        capsys.readouterr()  # Display text is NOT model context.
        if before_check:
            before_check()
        monkeypatch.setattr('sys.argv', ['hooks', 'check'])
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({
            'session_id': session_id, 'transcript_path': str(transcript),
            'cwd': str(tmp_path), 'hook_event_name': 'PostToolUse',
            'tool_name': 'Read', 'tool_input': {'file_path': str(transcript)},
            'tool_response': {}, 'tool_use_id': 'fixture', **(hook_extra or {}),
        })))
        hooks.main()
        captured = capsys.readouterr()
        assert not captured.err
        return captured.out
    return run


def test_owner_crossing_seventy_gets_one_model_visible_handoff_reminder(tick):
    assert 'owner-handoff' not in tick(69)
    reminder = tick(70)
    assert 'owner-handoff' in reminder and 'NOW.md' in reminder
    context = json.loads(reminder)['hookSpecificOutput']
    assert context['hookEventName'] == 'PostToolUse'
    assert 'owner-handoff' in context['additionalContext']
    assert 'owner-handoff' not in tick(71)
    assert 'owner-handoff' not in tick(90)
    assert 'owner-handoff' not in tick(69)
    assert 'owner-handoff' in tick(70)


def test_other_roles_get_no_owner_reminder(tick, monkeypatch):
    monkeypatch.setenv('LITEHARNESS_ROLE', 'builder')
    assert 'owner-handoff' not in tick(69)
    assert 'owner-handoff' not in tick(70)


@pytest.mark.parametrize('usage', [None, 'unreadable', True, float('nan'), float('inf'), -1, 101])
def test_unknown_usage_is_silent(tick, usage, tmp_path):
    assert 'owner-handoff' not in tick(usage)
    assert not list((tmp_path / 'context-usage').glob('*.json'))


def test_missing_state_is_silent(tick):
    assert 'owner-handoff' not in tick(70, publish=False)


@pytest.mark.parametrize('session_id', ['22222222-2222-4222-8222-222222222222', None])
def test_missing_or_other_session_cannot_consume_usage(tick, session_id):
    assert 'owner-handoff' not in tick(70, session_id=session_id)


@pytest.mark.parametrize('damage', ['wrong-session', 'stale', 'unreadable', 'future'])
def test_unusable_state_is_silent(tick, tmp_path, damage):
    def corrupt():
        files = list((tmp_path / 'context-usage').glob('*.json'))
        assert len(files) == 1
        path = files[0]
        state = json.loads(path.read_text(encoding='utf-8'))
        if damage == 'unreadable':
            path.write_text('not json', encoding='utf-8')
            return
        if damage == 'wrong-session':
            state['session_id'] = 'other'
        elif damage == 'stale':
            state['updated_at'] -= 121
        else:
            state['updated_at'] += 121
        path.write_text(json.dumps(state), encoding='utf-8')
    assert 'owner-handoff' not in tick(70, before_check=corrupt)


def test_reminder_preserves_inbox_and_display_context(tick, monkeypatch):
    from liteharness import paste_history
    monkeypatch.setattr(hooks, 'check_inbox', lambda: print('peer message'))
    monkeypatch.setattr(paste_history, 'handle', lambda *a: print('display pointer'))
    payload = json.loads(tick(70))
    text = payload['hookSpecificOutput']['additionalContext']
    assert 'peer message' in text and 'display pointer' in text and 'NOW.md' in text


def test_parallel_hooks_claim_only_one_reminder(tick, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from liteharness import owner_context
    # Record a high value without letting an owner consume it first.
    monkeypatch.setenv('LITEHARNESS_ROLE', 'builder')
    tick(70)
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: owner_context.reminder(SID, 'owner'), range(16)))
    assert sum('owner-handoff' in reply for reply in replies) == 1


def test_state_write_failure_never_emits_unlatched_reminder(tick, monkeypatch):
    def fail(*args):
        raise OSError('unavailable state')
    def before_check():
        monkeypatch.setattr(hooks.config, 'atomic_write_json', fail)
    assert 'owner-handoff' not in tick(70, before_check=before_check)


def test_state_write_failure_does_not_break_statusline_or_hook(tick, monkeypatch):
    def fail(*args):
        raise OSError('unavailable state')
    monkeypatch.setattr(hooks.config, 'atomic_write_json', fail)
    assert 'owner-handoff' not in tick(70)


def test_invalid_sample_does_not_overwrite_previous_state(tick, tmp_path):
    assert 'owner-handoff' in tick(70)
    path = next((tmp_path / 'context-usage').glob('*.json'))
    before = path.read_bytes()
    assert 'owner-handoff' not in tick(None)
    assert path.read_bytes() == before


def test_no_percentage_is_inferred_from_token_counts(tmp_path, monkeypatch):
    from liteharness import owner_context
    monkeypatch.setattr(hooks.config, 'get_root', lambda: tmp_path)
    owner_context.record({'session_id': SID, 'context_window': {
        'total_input_tokens': 150000, 'context_window_size': 200000,
    }})
    assert not (tmp_path / 'context-usage').exists()


def test_subagent_hook_cannot_consume_parent_owner_reminder(tick):
    assert 'owner-handoff' not in tick(70, hook_extra={'agent_id': 'child'})
    assert 'owner-handoff' in tick(70)
