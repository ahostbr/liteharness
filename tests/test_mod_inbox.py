"""Native inbox receipts/lease tests use only temporary maildir/presence files."""
import json
import os
import time
from unittest.mock import patch

import psutil
import pytest

from liteharness import config, hooks, inbox, mod_inbox


@pytest.fixture
def seat(tmp_path, monkeypatch):
    root = tmp_path / '.liteharness'
    monkeypatch.setattr(config, 'HARNESS_ROOT', root)
    for name in ('ROOT', 'NEW', 'CUR', 'DONE', 'TMP'):
        monkeypatch.setattr(inbox, 'INBOX_' + name, root / 'inbox' if name == 'ROOT' else root / 'inbox' / name.lower())
    inbox.ensure_dirs()
    (root / 'agents').mkdir()
    row = {'agent_id': 'seat', 'cli': 'claude-code', 'session_pid': os.getpid(),
           'session_process_started_at': psutil.Process().create_time() * 1000}
    (root / 'agents' / 'seat.json').write_text(json.dumps(row), encoding='utf-8')
    # In production adapter must be a descendant of the recorded process. Unit
    # tests deliberately substitute ONLY that external process-tree boundary.
    monkeypatch.setattr(mod_inbox, '_prove_caller', lambda pid: None)
    return root


def send(body='hello', to='seat', **extra):
    msg = {'id': 'm1', 'from': 'leader', 'to': to, 'body': body, **extra}
    path = inbox.INBOX_NEW / 'm1.json'
    path.write_text(json.dumps(msg), encoding='utf-8')
    return path


def op(action='poll', owner='owner', receipts=None):
    return mod_inbox.operate(action, 'seat', 'session', owner, receipts)


def test_atomic_claim_is_replayed_until_ack_and_ack_archives(seat):
    source = send()
    batch = op()
    assert not source.exists()
    assert (inbox.INBOX_CUR / source.name).exists()
    assert op()['messages'] == batch['messages']
    assert not list(inbox.INBOX_DONE.iterdir())
    op('ack', receipts=['m1.json'])
    assert (inbox.INBOX_DONE / 'm1.json').exists()
    assert not op()['messages']


def test_failed_delivery_and_reload_recover_cur_without_drop(seat):
    send()
    op()
    with pytest.raises(ValueError, match='live owner'):
        op(owner='replacement')
    with patch.object(mod_inbox.time, 'time', return_value=time.time() + mod_inbox.LEASE_SECONDS + 1):
        assert not mod_inbox.native_owner_active('seat')
        recovered = op(owner='replacement')
    assert [m['id'] for m in recovered['messages']] == ['m1']
    assert not list(inbox.INBOX_DONE.iterdir())


def test_release_retains_mail_and_legacy_check_replays(seat, capsys, monkeypatch):
    send()
    op()
    monkeypatch.setattr(config, 'get_agent_id', lambda: 'seat')
    # Fresh legacy watcher still scans only new, so stale native cur receipts
    # must override watcher deferral in the turn hook.
    monkeypatch.setattr(hooks, '_a_live_watcher_is_attached', lambda _: True)
    monkeypatch.setattr(hooks, '_should_check', lambda: True)
    monkeypatch.setattr(hooks, '_mark_checked', lambda: None)
    monkeypatch.setattr(hooks, '_refresh_presence_model', lambda: None)
    monkeypatch.setattr(hooks, '_maybe_cleanup', lambda: None)
    hooks.check_inbox()
    assert capsys.readouterr().out == ''
    assert (inbox.INBOX_CUR / 'm1.json').exists()
    op('release')
    assert not mod_inbox.native_owner_active('seat')
    hooks.check_inbox()
    assert 'hello' in capsys.readouterr().out
    assert (inbox.INBOX_DONE / 'm1.json').exists()


@pytest.mark.parametrize('change', ['expired', 'dead', 'pid-reuse', 'tui', 'corrupt', 'future'])
def test_uncertain_or_nonclaude_lease_never_suppresses_legacy(seat, change):
    op()
    lease = seat / 'mods' / 'inbox' / 'seat.json'
    row = json.loads(lease.read_text())
    presence = seat / 'agents' / 'seat.json'
    owner = json.loads(presence.read_text())
    if change == 'expired': row['expiresAt'] = 0
    if change == 'future': row['renewedAt'] = time.time() + 10
    if change == 'dead': owner['session_pid'] = -1
    if change == 'pid-reuse': owner['session_process_started_at'] += 10000
    if change == 'tui': owner['cli'] = 'litetui'
    lease.write_text('broken' if change == 'corrupt' else json.dumps(row))
    presence.write_text(json.dumps(owner))
    assert not mod_inbox.native_owner_active('seat')


def test_wrong_owner_ack_and_path_escape_refused(seat):
    send()
    op()
    with pytest.raises(ValueError): op('ack', owner='other', receipts=['m1.json'])
    with pytest.raises(ValueError): op('ack', receipts=['../m1.json'])
    assert (inbox.INBOX_CUR / 'm1.json').exists()
    assert not list(inbox.INBOX_DONE.iterdir())


def test_recipient_and_both_producer_shapes(seat):
    send(to='other')
    assert op()['messages'] == []
    send(to='broadcast', body=None, payload={'text': 'desktop mail'})
    assert op()['messages'][0]['body'] == 'desktop mail'


def test_oversize_invalid_and_self_mail_are_not_silently_archived(seat):
    send('x' * (mod_inbox.MAX_BODY + 1))
    assert op()['blocked'] == 1
    assert not mod_inbox.native_owner_active('seat')
    assert (inbox.INBOX_NEW / 'm1.json').exists()
    assert not list(inbox.INBOX_DONE.iterdir())
    send(**{'from': 'seat'})
    assert op()['messages'] == []


def test_utf16_bounds_match_javascript_and_release_unsupported_mail(seat):
    send('\U0001f600' * 13000)
    assert op()['blocked'] == 1
    assert not mod_inbox.native_owner_active('seat')
    assert (inbox.INBOX_NEW / 'm1.json').exists()
    send('\U0001f600' * 12000)
    assert op()['messages'][0]['id'] == 'm1'


def test_atomic_claim_lost_race_does_not_report_delivery(seat, monkeypatch):
    send()
    monkeypatch.setattr(inbox, 'claim', lambda _: False)
    assert op()['messages'] == []
    assert (inbox.INBOX_NEW / 'm1.json').exists()


def test_caller_must_descend_from_owner():
    with pytest.raises(ValueError, match='not a child'):
        mod_inbox._prove_caller(os.getpid())


def test_watcher_defers_only_while_native_lease_is_fresh(seat, monkeypatch, capsys):
    source = send()
    op()
    # Add another new message; native's claimed one remains in cur.
    source.write_text(json.dumps({'id': 'm2', 'from': 'leader', 'to': 'seat', 'body': 'watch mail'}))
    calls = 0

    class Watcher:
        def wait(self, timeout):
            nonlocal calls
            calls += 1
            if calls == 2:
                op('release')
            if calls == 3:
                raise KeyboardInterrupt()
            return True

    monkeypatch.setattr(hooks, '_create_watcher', lambda _: Watcher())
    monkeypatch.setattr(hooks, 'update_heartbeat', lambda **kwargs: None)
    with pytest.raises(KeyboardInterrupt):
        hooks.watch_inbox(override_agent_id='seat')
    assert calls == 3
    assert 'watch mail' in capsys.readouterr().out
    assert (inbox.INBOX_DONE / 'm1.json').exists()


def test_claim_publication_crash_replays_receipt(seat, monkeypatch):
    send()
    original = inbox.claim

    def crash_after_rename(message):
        assert original(message)
        raise OSError('adapter terminated after claim')

    with monkeypatch.context() as scope:
        scope.setattr(inbox, 'claim', crash_after_rename)
        with pytest.raises(OSError):
            op()
    assert op()['messages'][0]['id'] == 'm1'
    assert not list(inbox.INBOX_DONE.iterdir())
