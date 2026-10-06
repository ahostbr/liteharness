"""Named-agent resume uses folder identity/execution, never archive or index truth."""
from pathlib import Path
import os

from .agent_store import AgentStore, StoreError, _unlinked, valid_id
from . import owned_launch


def spawn(*, value: str | None, conversation_id: str | None, pane: str | None,
          direction: str | None, cwd: str | None, name: str | None, tier: str | None,
          model: str | None, backend: str | None, thinking_level: str | None,
          spawned_by: str | None, kill_old: bool, prompt: str | None) -> None:
    from . import cli, fleet_policy
    if kill_old:
        raise StoreError('Owned named resume never closes a seat; wait until offline')
    root = owned_launch.data_root()
    store = AgentStore(root)
    if value:
        try:
            identity = valid_id(value)
        except StoreError:
            agent = store.find_agent(name=value)
        else:
            agent = store.find_agent(agent_id=identity)
    elif conversation_id:
        agent = store.find_agent(name=store.locate_conversation(conversation_id).parent.parent.name)
    else:
        raise StoreError('Select an owned agent first')
    saved = agent.settings['execution']
    if saved['model'] is None:
        raise StoreError('Agent model is unchosen; choose a model in its owned standalone seat before executable resume')
    for supplied, expected in ((name, agent.name), (backend, saved['backend']),
                               (model, saved['model']), (thinking_level, saved['thinking_level'])):
        if supplied is not None and supplied != expected:
            raise StoreError('Resume override disagrees with agent folder authority')
    directories = store.list_conversations(agent)
    transcripts = [d for d in directories if _unlinked(d / 'convo.jsonl').is_file()]
    if conversation_id is None:
        if len(transcripts) > 1:
            raise StoreError('Multiple owned conversations; select --convo explicitly')
        conversation_id = transcripts[0].name if transcripts else None
    else:
        target = store.conversation_directory(agent, conversation_id)
        if not _unlinked(target / 'convo.jsonl').is_file():
            raise StoreError('Conversation absent from selected agent; archive is read-only')
    if not cwd or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise StoreError('Owned resume requires explicit absolute existing --cwd')
    if tier not in cli.VALID_TIERS:
        raise StoreError('Owned resume requires explicit valid --tier')
    caller = spawned_by or os.environ.get('LITEHARNESS_AGENT_ID')
    if not caller:
        raise StoreError('Owned resume requires explicit parent identity')
    refusal = fleet_policy.gate(saved['backend'], saved['model'], saved['thinking_level'])
    if refusal:
        raise StoreError(refusal)
    # Readonly preflight. This lock is released before creation; the launched
    # child independently reacquires it before registration. No transfer/gap claim.
    from .agent_ownership import AgentSession
    with AgentSession.acquire_existing(store, agent_id=agent.agent_id) as session:
        session.authority
    rows = cli._bridge_request('GET', '/pty/list')
    if (not isinstance(rows, dict) or rows.get('error') or rows.get('ok') is False
            or not isinstance(rows.get('sessions'), list)
            or any(not isinstance(row, dict) for row in rows['sessions'])):
        raise StoreError('Cannot inspect current PTYs; owned resume deferred')
    if any(row.get('harnessAgentId') == agent.agent_id for row in rows['sessions']):
        raise StoreError('Live owned seat exists; message it instead')
    resolution = cli._bridge_request('POST', '/harness/spawn/resolve', {
        'cli': 'litetui', 'name': agent.name, 'tier': tier, 'model': saved['model'],
        'backend': saved['backend'], 'thinkingLevel': saved['thinking_level'],
        'cwd': cwd, 'spawnedBy': caller, **({'prompt': prompt} if prompt is not None else {}),
    })
    if not isinstance(resolution, dict) or resolution.get('ok') is not True:
        raise StoreError('Owned spawn resolution refused')
    launch = owned_launch.request(resolution, root=root, name=agent.name, agent_id=agent.agent_id,
                                  backend=saved['backend'], model=saved['model'],
                                  thinking_level=saved['thinking_level'], conversation_id=conversation_id)
    # Revalidate folder after the external boundary; child repeats ownership check.
    current = store.find_agent(agent_id=agent.agent_id)
    if current != agent:
        raise StoreError('Agent authority changed during launch resolution')
    result = cli._place_split(pane, caller, direction, cwd=cwd, launch=launch)
    if not isinstance(result, dict) or not result.get('newSessionId') or result.get('error') or result.get('ok') is False:
        raise StoreError('Owned seat creation refused or uncertain; inspect sessions')
    # Preserve the existing fleet verification/cleanup contract on owned resumes.
    # Only the exact newly returned session is eligible for cleanup.
    session_id = result['newSessionId']
    try:
        governed = bool(fleet_policy.floor_for(fleet_policy.load()[0], saved['backend'], saved['model']))
        why = fleet_policy.verify_seat(agent.agent_id, cli.config.get_root(), wait=90,
                                      backend=saved['backend'], allow_silent=not governed,
                                      expect_model=saved['model'], expect_thinking=saved['thinking_level'])
    except Exception as exc:
        why = f'Owned fleet verification failed: {exc}'
    if why:
        try:
            cleanup = cli._bridge_request('DELETE', f'/pty/{session_id}')
        except Exception as exc:
            raise StoreError(f'{why}; exact new session cleanup failed: {exc}') from exc
        if (not isinstance(cleanup, dict) or cleanup.get('success') is not True
                or cleanup.get('ok') is False or cleanup.get('error')):
            raise StoreError(f'{why}; exact new session cleanup failed: {cleanup}')
        raise StoreError(f'{why}; exact new session kill requested, reap pending')
    # Existing named thread identity is preserved; no registry/name-index repair.
    print(f"Launched owned seat {agent.name} ({agent.agent_id}); conversation {conversation_id or 'new'}; "
          f"session {result['newSessionId']}; home {agent.directory}")
