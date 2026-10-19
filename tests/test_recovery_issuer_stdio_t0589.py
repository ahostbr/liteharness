"""Actual -m adapter/stdin/JSON issuer boundary in isolated homes.
Only AccessDenied and an inspection-time registration swap substitute the OS
inspection boundary; the other liveness/birth/supersession cases use real rows
and the real pytest owning PID. No bridge/native/model launch is requested.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

AID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


def invoke(tmp_path, case):
    home = tmp_path / 'home'
    agents = home / '.liteharness' / 'agents'
    agents.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    row = dict(agent_id=AID, role='orchestrator', tier='orchestrator', cli='claude-code',
               session_pid=os.getpid(), session_process_started_at=psutil.Process().create_time() * 1000,
               registered_at=now.isoformat(), last_seen=now.isoformat())
    if case == 'stale-heartbeat': row['last_seen'] = (now - timedelta(seconds=601)).isoformat()
    if case == 'future-heartbeat': row['last_seen'] = (now + timedelta(seconds=60)).isoformat()
    if case == 'missing-heartbeat': del row['last_seen']
    if case == 'missing-birth': del row['session_process_started_at']
    if case == 'reused-pid': row['session_process_started_at'] -= 1000
    if case == 'dead-pid':
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        row['session_pid'] = child.pid
        child.wait(timeout=10)
    if case == 'exited': row['exited_at'] = now.isoformat()
    if case == 'wrong-tier': row.update(role='builder', tier='worker')
    if case == 'untrusted-actor': row['agent_id'] = OTHER
    if case == 'future-registration': row['registered_at'] = (now + timedelta(seconds=60)).isoformat()
    path = agents / f'{AID}.json'
    path.write_text(json.dumps(row), encoding='utf-8')
    if case == 'superseded':
        (agents / f'{OTHER}.json').write_text(json.dumps({**row, 'agent_id': OTHER,
            'registered_at': (now + timedelta(milliseconds=1)).isoformat()}), encoding='utf-8')
    if case == 'missing-registration': path.unlink()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('LITEHARNESS', 'LITESUITE', 'LITETUI', 'CLAUDE', 'CODEX'))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONIOENCODING='utf-8')
    argv = [sys.executable, '-m', 'liteharness.recovered_destination']
    if case in ('inaccessible-pid', 'registration-swapped-during-inspection'):
        # Substitute only this nondeterministic OS boundary in the CHILD, then
        # execute the actual production __main__/stdin/issuer implementation.
        wrapper = '''
import os,json,runpy,psutil
from pathlib import Path
real=psutil.Process
path=Path(os.environ['TEST_ISSUER_ROW'])
class Inspected:
 def __init__(self,pid): self.process=real(pid)
 def __getattr__(self,key): return getattr(self.process,key)
 def create_time(self):
  row=json.loads(path.read_text()); row['role']='builder'; row['tier']='worker'; path.write_text(json.dumps(row))
  return self.process.create_time()
def inspect(pid=None):
 if os.environ['TEST_ISSUER_CASE']=='inaccessible-pid': raise psutil.AccessDenied(pid)
 return Inspected(pid)
psutil.Process=inspect
runpy.run_module('liteharness.recovered_destination',run_name='__main__')
'''
        argv = [sys.executable, '-c', wrapper]
        env.update(TEST_ISSUER_ROW=str(path), TEST_ISSUER_CASE=case)
    completed = subprocess.run(argv, input=json.dumps({'action': 'issuer', 'actorId': AID}),
                               text=True, encoding='utf-8', capture_output=True, env=env, timeout=15)
    return completed, json.loads(completed.stdout)


def test_CONTROL_actual_stdio_adapter_accepts_exact_alive_orchestrator_with_birth_and_fresh_heartbeat(tmp_path):
    completed, result = invoke(tmp_path, 'valid')
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result['ok'] is True and result['identity'] == AID
    identity = json.loads(result['instance'])
    assert identity[0] == AID and identity[1] == os.getpid()
    assert identity[2] == psutil.Process().create_time() * 1000


@pytest.mark.parametrize('case', [
    'stale-heartbeat', 'future-heartbeat', 'missing-heartbeat', 'missing-birth',
    'dead-pid', 'reused-pid', 'inaccessible-pid', 'exited', 'wrong-tier',
    'untrusted-actor', 'future-registration', 'superseded', 'missing-registration',
    'registration-swapped-during-inspection',
])
def test_actual_stdio_adapter_refuses_untrusted_or_stale_issuer_without_launch(tmp_path, case):
    completed, result = invoke(tmp_path, case)
    assert completed.returncode != 0
    assert result['ok'] is False
    assert 'identity' not in result and 'instance' not in result
    assert result['location'].split(':')[0] in ('recovered_destination.py', 'pathlib.py')
