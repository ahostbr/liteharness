"""Actual hook delivery/dedup, not a claim about hidden model/native history.
The owning process boundary is controlled; process birth inspection is real.
Canonical Gamma body + retained prior native NAME composition lives in Suite.
"""
import json
import os
import subprocess
import sys

import psutil

from liteharness import hooks
from test_claude_registration_resume_t0589 import AID, registered  # noqa: F401


def select_method(tmp_path, monkeypatch):
    method = tmp_path / 'gamma.md'
    body = '# POLYMATHIC GAMMA\nFeel the pain first, then refactor to patterns.\nUnicode: λ\n'
    method.write_text(body, encoding='utf-8')
    monkeypatch.setenv('LITEHARNESS_COGNITIVE_FILE', str(method))
    return body


def test_legacy_printed_marker_is_retained_then_upgraded_after_real_hook_delivery(registered, tmp_path, monkeypatch, capsys):
    root, _, _, _ = registered
    body = select_method(tmp_path, monkeypatch)
    marker = root / '.cog_printed' / AID
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('printed', encoding='utf-8')  # supported legacy format, not deleted to make green
    hooks.register_presence()
    first = capsys.readouterr().out
    assert body in first and first.count('## Cognitive Architecture — gamma') == 1
    stamped = marker.read_bytes()
    event = json.loads(stamped)
    assert event['version'] == 2 and event['pid'] == os.getpid()
    assert event['birth'] == psutil.Process().create_time()
    hooks.register_presence()
    duplicate = capsys.readouterr().out
    assert body not in duplicate and '## Cognitive Architecture' not in duplicate
    assert marker.read_bytes() == stamped


def test_same_stable_identity_on_a_different_verified_native_owner_receives_method_once(registered, tmp_path, monkeypatch, capsys):
    root, _, _, _ = registered
    body = select_method(tmp_path, monkeypatch)
    hooks.register_presence()
    assert body in capsys.readouterr().out
    marker = root / '.cog_printed' / AID
    prior = marker.read_bytes()
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
    try:
        monkeypatch.setattr(hooks, '_resolve_session_pid', lambda existing=None: child.pid)
        hooks.register_presence()
        first = capsys.readouterr().out
        assert body in first and first.count('## Cognitive Architecture — gamma') == 1
        current = marker.read_bytes()
        event = json.loads(current)
        assert current != prior and event['pid'] == child.pid
        assert event['birth'] == psutil.Process(child.pid).create_time()
        hooks.register_presence()
        assert body not in capsys.readouterr().out
        assert marker.read_bytes() == current
    finally:
        child.stdin.close()
        child.wait(timeout=10)


def test_unknown_native_birth_never_uses_a_prior_marker_as_delivery_proof(registered, tmp_path, monkeypatch, capsys):
    root, _, _, _ = registered
    body = select_method(tmp_path, monkeypatch)
    hooks.register_presence()
    capsys.readouterr()
    marker = root / '.cog_printed' / AID
    prior = marker.read_bytes()
    real_process = psutil.Process

    def inaccessible(pid=None):
        if pid == os.getpid():
            raise psutil.AccessDenied(pid)
        return real_process(pid)

    monkeypatch.setattr(psutil, 'Process', inaccessible)
    hooks.register_presence()
    assert body in capsys.readouterr().out
    assert marker.read_bytes() == prior  # uncertain identity cannot stamp a false delivery event
