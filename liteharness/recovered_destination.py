"""Governed original-slot transport. The host, not this caller, grants placement.

The stdio adapter is invoked by main with a request-bound opaque capability.
It retains existing owned/native authority and never falls back after recovery.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from contextlib import redirect_stdout
from urllib.parse import urlencode

_active = ContextVar('recovered_destination', default=None)


def active_scope():
    value = _active.get()
    return value[0] if value is not None else None


def recover_if_reserved(agent_id, caller, *, conversation_id=None, requested_cwd=None, requested_role=None, overrides=None):
    """Read-only authority checks precede this; NEW-CREATE liveness checks follow.

    A live runtime belonging to an independently authorized durable operation
    must be reconcilable, not rejected as an unrelated new launch.
    """
    if _active.get() is not None:
        return False
    from . import cli
    result = cli._bridge_request('GET', '/harness/recovered-agent/locate?' + urlencode({'agentId': agent_id}))
    if not isinstance(result, dict) or result.get('ok') is not True or not isinstance(result.get('found'), bool):
        raise ValueError('Recovery destination inspection unavailable; no ordinary split attempted')
    if not result['found']:
        return False
    unsupported = [key for key, value in (overrides or {}).items() if value is not None]
    if unsupported:
        raise ValueError('Reserved-slot recovery does not support explicit ' + ', '.join(unsupported) + '; nothing launched or applied')
    response = cli._bridge_request('POST', '/harness/recovered-agent/restore', {
        **{key: result[key] for key in ('paneId', 'slotId', 'agentId', 'requestId') if key in result},
        'kind': 'agent-slot-recovery', 'actorId': caller,
        **({'conversationId': conversation_id} if conversation_id else {}),
        **({'requestedCwd': requested_cwd} if requested_cwd else {}),
        **({'requestedRole': requested_role} if requested_role else {}),
    }, timeout=210)
    identity = f"request {result.get('requestId', '<host did not return request identity>')}; session {(response or {}).get('sessionId', '<allocation outcome unknown>')}"
    if not isinstance(response, dict) or response.get('status') != 'bound' or response.get('ok') is not True:
        raise ValueError(f'Reserved-slot recovery refused or UNKNOWN: {identity}; no split fallback; reconcile this same operation')
    if response.get('agentId') != agent_id or not response.get('sessionId') or not response.get('leafId'):
        raise ValueError(f'Reserved-slot receipt does not join target: {identity}; no split fallback')
    print(f"Restored {agent_id} into original leaf {response['leafId']}; session {response['sessionId']}")
    return True


def place(launch):
    context = _active.get()
    if context is None:
        return None
    from . import cli
    scope, capability = context
    result = cli._bridge_request('POST', '/harness/recovered-agent/create', {
        'requestId': scope['requestId'], 'paneId': scope['paneId'], 'slotId': scope['slotId'],
        'agentId': scope['agentId'], 'placeholderSessionId': scope['placeholderSessionId'],
        'capability': capability, 'launch': launch,
    })
    if not isinstance(result, dict) or result.get('ok') is not True or not result.get('sessionId'):
        raise ValueError('Governed native allocation uncertain; inspect request, never split')
    _last_session.set(result['sessionId'])
    return {'newSessionId': result['sessionId'], 'paneId': scope['paneId'], 'newLeafId': scope['leafId']}


def issuer(actor_id):
    from . import cli, config, roles
    from .agent_store import valid_id
    actor_id = valid_id(actor_id)
    path = config.get_root() / 'agents' / f'{actor_id}.json'
    row = json.loads(path.read_text(encoding='utf-8'))
    now = datetime.now(timezone.utc)
    seen = datetime.fromisoformat(row['last_seen'])
    registered = datetime.fromisoformat(row['registered_at'])
    if (row.get('agent_id') != actor_id or roles.row_role(row) != 'orchestrator'
            or row.get('exited_at') or not 0 <= (now - seen).total_seconds() <= 600
            or registered > now or not cli._owned_runtime_owner_live(actor_id, row)
            or cli._superseded_by_later_registration(actor_id, row)):
        raise ValueError('Actual online registry ORCHESTRATOR required')
    identity = [actor_id, row['session_pid'], row['session_process_started_at'], row['registered_at']]
    # Re-read identity after OS inspection: no recycled registration adoption.
    current = json.loads(path.read_text(encoding='utf-8'))
    if any(current.get(key) != row.get(key) for key in ('agent_id', 'session_pid', 'session_process_started_at', 'registered_at', 'role', 'tier')):
        raise ValueError('Issuer changed during inspection')
    return {'identity': actor_id, 'instance': json.dumps(identity, separators=(',', ':'))}


def prepare(agent_id, cwd, conversation_id=None):
    from . import owned_launch, roles
    from .agent_store import AgentStore, _unlinked, valid_id
    from .resume_seat import _registry
    agent_id = valid_id(agent_id)
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise ValueError('Recovered slot has no original absolute existing cwd')
    try:
        root = owned_launch.data_root()
    except owned_launch.RootUnknown:
        root = None
    row = _registry(agent_id)
    role = roles.normalize_role(row.get('role') or row.get('tier'))
    if role is None:
        raise ValueError('Recovered seat has no recorded role authority')
    agents = AgentStore(root).list_agents() if root is not None else []
    matches = [agent for agent in agents if agent.agent_id == agent_id]
    if len(matches) > 1:
        raise ValueError('Ambiguous owned agent')
    if matches:
        agent = matches[0]
        store = AgentStore(root)
        transcripts = [d for d in store.list_conversations(agent) if _unlinked(d / 'convo.jsonl').is_file()]
        if conversation_id:
            transcripts = [d for d in transcripts if d.name == conversation_id]
        if len(transcripts) != 1:
            raise ValueError('Recovery requires exactly one selected owned conversation')
        execution = agent.settings['execution']
        if not execution.get('model'):
            raise ValueError('Owned execution model is unchosen')
        descriptor = {'cli': 'litetui', 'agentId': agent_id, 'name': agent.name, 'root': str(root),
                      'home': str(agent.directory), 'conversationId': transcripts[0].name,
                      'cwd': cwd, 'role': role, **execution}
    else:
        if row.get('agent_id') != agent_id or row.get('cli') not in ('claude', 'claude-code') or str(row.get('backend') or 'claude').split('/')[0] != 'claude':
            raise ValueError('No owned LiteTUI or native Claude authority for target')
        from .claude_identity import transcript_identity
        native_id, original, transcript = transcript_identity(row, native_id=conversation_id, cwd=cwd)
        if Path(original).resolve() != Path(cwd).resolve() or not row.get('name') or not row.get('model'):
            raise ValueError('Claude original cwd/name/model authority unavailable')
        descriptor = {'cli': 'claude', 'agentId': agent_id, 'name': row['name'], 'conversationId': native_id,
                      'cwd': original, 'transcript': str(transcript), 'role': role,
                      'backend': 'claude', 'model': row['model'], 'thinking_level': row.get('thinking_level'),
                      'cognitive': row.get('cognitive')}
    descriptor['launchParent'] = row.get('spawned_by')
    descriptor['authorityFingerprint'] = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
    return descriptor


def verify(scope):
    from . import cli, fleet_policy
    plan = prepare(scope['agentId'], scope['cwd'], scope['conversationId'])
    if plan['authorityFingerprint'] != scope['ownedAuthorityFingerprint']:
        raise ValueError('Owned/native execution authority changed')
    error = cli._owned_runtime_receipt(scope['agentId'], scope['sessionId'], datetime.fromisoformat(scope['launchedAt']))
    if error:
        raise ValueError(error)
    governed = bool(fleet_policy.floor_for(fleet_policy.load()[0], plan['backend'], plan['model']))
    error = fleet_policy.verify_seat(scope['agentId'], cli.config.get_root(), wait=0, backend=plan['backend'],
        allow_silent=not governed, expect_model=plan['model'], expect_thinking=plan.get('thinking_level'))
    if error:
        raise ValueError(error)
    error = cli._owned_runtime_receipt(scope['agentId'], scope['sessionId'], datetime.fromisoformat(scope['launchedAt']))
    if error:
        raise ValueError(error)
    return {'verified': True}


def launch(scope, capability):
    from . import cli, fleet_policy, owned_resume, roles
    plan = prepare(scope['agentId'], scope['cwd'], scope['conversationId'])
    if plan['authorityFingerprint'] != scope['ownedAuthorityFingerprint']:
        raise ValueError('Recovery authority changed before launch')
    token = _active.set((scope, capability))
    try:
        if plan['cli'] == 'litetui':
            # Existing governed path repeats ownership/execution checks and its
            # exact runtime + floor success boundary. Only destination varies.
            with redirect_stdout(io.StringIO()):
                owned_resume.spawn(value=plan['agentId'], conversation_id=plan['conversationId'], pane=scope['paneId'],
                    direction=None, cwd=plan['cwd'], name=None, tier=plan['role'], model=None, backend=None,
                    thinking_level=None, spawned_by=scope['agentId'], kill_old=False, prompt=None, root=Path(plan['root']))
            return {'sessionId': _last_session.get()}
        # Native Claude keeps native transcript/registry identity; never AgentStore.
        from .resume_seat import _spawn_claude_resume
        entry = {'agent_id': plan['agentId'], 'name': plan['name'], 'convo_id': plan['conversationId'],
                 'cwd': plan['cwd'], 'backend': 'claude', 'model': plan['model']}
        with redirect_stdout(io.StringIO()):
            _spawn_claude_resume(entry, convo_id=plan['conversationId'], cwd=plan['cwd'], pane=scope['paneId'],
                direction=None, name=None, tier=plan['role'], model=None, backend=None, thinking_level=None,
                spawned_by=scope['agentId'], prompt=None)
        return {'sessionId': _last_session.get()}
    finally:
        _active.reset(token)


_last_session = ContextVar('recovery_session', default=None)

def launch_native_claude(entry, role):
    """Called only after resume_seat's actual native authority checks."""
    from . import cli, roles
    scope, _ = _active.get()
    from .resume_seat import _registry
    import psutil
    previous = _registry(entry['agent_id'])
    if previous.get('session_pid'):
        try:
            process = psutil.Process(int(previous['session_pid']))
            birth = previous.get('session_process_started_at')
            if birth is None or process.create_time() * 1000 == birth:
                raise ValueError('Native Claude owner is live or its process instance is unknown')
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            raise ValueError('Native Claude owner process is inaccessible') from exc
    result = cli._bridge_request('POST', '/harness/spawn/resolve', {
        'cli': 'claude', 'name': entry['name'], 'tier': roles.wire_tier(role), 'cwd': entry['cwd'], 'model': entry['model'],
        **({'cognitive': previous['cognitive']} if previous.get('cognitive') else {}),
    })
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise ValueError('Native Claude launch resolution refused')
    request = result['request']
    request['args'].extend(['--resume', entry['convo_id']])
    request['harnessAgentId'] = entry['agent_id']
    request['env']['LITEHARNESS_AGENT_ID'] = entry['agent_id']
    created = place(request)
    _last_session.set(created['newSessionId'])
    error = cli._wait_owned_runtime(entry['agent_id'], created['newSessionId'], datetime.fromisoformat(scope['launchedAt']))
    if error:
        raise ValueError(error)
    verify({**scope, 'sessionId': created['newSessionId']})


def main():
    request = json.load(sys.stdin)
    action = request['action']
    if action == 'issuer':
        return issuer(request['actorId'])
    if action == 'prepare':
        return prepare(request['agentId'], request['cwd'], request.get('conversationId'))
    if action == 'verify':
        return verify(request['scope'])
    if action == 'launch':
        return launch(request['scope'], request['capability'])
    raise ValueError('Unsupported recovery adapter operation')


if __name__ == '__main__':
    # -m executes as __main__; owned/native collaborators must share this exact
    # ContextVar instance, not import a second ungoverned module instance.
    sys.modules['liteharness.recovered_destination'] = sys.modules[__name__]
    try:
        result = main()
        print(json.dumps({'ok': True, **result}))
    except (Exception, SystemExit) as exc:
        # Preserve safe diagnosis without echoing argv, capability or payload.
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(json.dumps({'ok': False, 'error': 'Governed recovery adapter refused or runtime uncertain',
                          'errorType': type(exc).__name__, 'location': f'{Path(frame.filename).name}:{frame.name}:{frame.lineno}'}))
        sys.exit(1)
