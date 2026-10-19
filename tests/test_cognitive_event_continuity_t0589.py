"""Actual hook delivery/dedup, not a claim about hidden model/native history.
The owning process boundary is controlled; process birth inspection is real.
Canonical Gamma body + retained prior native NAME composition lives in Suite.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_concurrent_actual_hooks_for_same_verified_owner_emit_one_method(registered, tmp_path, monkeypatch):
    root, project, _, row = registered
    body = select_method(tmp_path, monkeypatch)
    marker = root / '.cog_printed' / AID
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('printed', encoding='utf-8')
    barrier = tmp_path / 'barrier'
    barrier.mkdir()
    # Synchronize AFTER both real hooks read the exact retained legacy marker,
    # not before startup. This deterministically reproduces the two-hook race.
    script = r'''
import os,json,time,sys
from pathlib import Path
from recovery_transport_guard_t0589 import network_fence
with network_fence(guard_bridge=False) as attempts:
 from liteharness import hooks,config
 p=json.loads(os.environ['T0589_CONCURRENT']); config.HARNESS_ROOT=Path(p['root']); config.CONFIG_PATH=config.HARNESS_ROOT/'config.json'
 hooks._resolve_session_pid=lambda existing=None:p['owner']
 hooks._apply_hook_context(dict(hook_event_name='SessionStart',source='startup',session_id=p['cid'],transcript_path=p['transcript'],cwd=p['project'],model='claude-opus'))
 original=Path.read_text; marker=Path(p['marker']); barrier=Path(p['barrier'])
 def read_text(path,*args,**kwargs):
  text=original(path,*args,**kwargs)
  if path==marker:
   (barrier/(sys.argv[1]+'.ready')).write_text('ready')
   deadline=time.monotonic()+15
   while len(list(barrier.glob('*.ready')))!=2:
    if time.monotonic()>deadline:raise RuntimeError('concurrent marker barrier timed out')
    time.sleep(.01)
  return text
 Path.read_text=read_text
 hooks.register_presence()
 assert not attempts,attempts
'''
    env = {**os.environ, 'LITETUI_NO_HARNESS': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'T0589_CONCURRENT': json.dumps(dict(root=str(root), project=str(project), owner=os.getpid(),
               cid=Path(row['transcript_path']).stem, transcript=row['transcript_path'], marker=str(marker), barrier=str(barrier)))}
    children = [subprocess.Popen([sys.executable, '-X', 'utf8', '-u', '-c', script, str(index)],
                cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8') for index in range(2)]
    outputs = []
    try:
        for child in children:
            stdout, stderr = child.communicate(timeout=25)
            assert child.returncode == 0, (child.returncode, stderr, stdout)
            outputs.append(stdout)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert sum(output.count(body) for output in outputs) == 1
    assert sum(output.count('## Cognitive Architecture — gamma') for output in outputs) == 1
    event = json.loads(marker.read_text(encoding='utf-8'))
    assert event['pid'] == os.getpid() and event['birth'] == psutil.Process().create_time()
    assert event['session'] == Path(row['transcript_path']).stem
    print('COGNITIVE_CONCURRENT_RECEIPT=' + json.dumps(dict(
        event=event, hook_processes=[child.pid for child in children], retained_marker='printed',
        body_counts=[output.count(body) for output in outputs], synthetic_method_body=True,
        unexpected_network_attempts=0)))
