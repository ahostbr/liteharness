"""Named-agent resume uses folder identity/execution, never archive or index truth."""
from pathlib import Path
import os
from datetime import datetime, timezone

from .agent_store import AgentStore, StoreError, _unlinked, valid_id
from . import owned_launch, roles


def spawn(*, value: str | None, conversation_id: str | None, pane: str | None,
          direction: str | None, cwd: str | None, name: str | None, tier: str | None,
          model: str | None, backend: str | None, thinking_level: str | None,
          spawned_by: str | None, kill_old: bool, prompt: str | None,
          cognitive: str | None = None, owner_title: str | None = None,
          memory_dir: str | None = None, root: Path | None = None,
          system_prompt_file: str | None = None) -> None:
    from . import cli, fleet_policy
    if system_prompt_file is not None:
        cli._validate_system_prompt_file(system_prompt_file)
    if kill_old:
        raise StoreError('Owned named resume never closes a seat; wait until offline')
    root = owned_launch.data_root() if root is None else root
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
    from .resume_seat import _registry
    # Presence supplies only a downgrade guard, never folder/execution authority.
    if owner_title or memory_dir:
        if not (owner_title and memory_dir):
            raise StoreError('Owner resume requires --owner <Title> --memory <dir>')
        if owner_title != agent.name:
            raise StoreError('--owner must match the resumed agent name')
        tier = 'owner'
    elif roles.row_role(_registry(agent.agent_id)) == 'owner' or roles.normalize_role(tier) == 'owner':
        # An owner spawned with a plain --tier owner saved its conversation in its agent home,
        # and LiteTUI refuses to reopen that under --owner/--memory ("Store mismatch").
        # Keep the owner role and let the folder decide the store; never resume it lower.
        if tier is not None and roles.normalize_role(tier) != 'owner':
            raise StoreError('Owner resume requires --owner <Title> --memory <dir>')
        tier = 'owner'
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
    tier = roles.normalize_role(tier)
    if tier not in roles.ROLES:
        raise StoreError('Owned resume requires explicit valid --tier')
    from . import recovered_destination
    recovery_scope = recovered_destination.active_scope()
    caller = recovery_scope.get('launchParent') if recovery_scope else (spawned_by or os.environ.get('LITEHARNESS_AGENT_ID'))
    if not caller and recovery_scope is None:
        raise StoreError('Owned resume requires explicit parent identity')
    refusal = fleet_policy.gate(saved['backend'], saved['model'], saved['thinking_level'])
    if refusal:
        raise StoreError(refusal)
    if recovery_scope is None:
        try:
            if recovered_destination.recover_if_reserved(agent.agent_id, caller, conversation_id=conversation_id,
                    requested_cwd=cwd, requested_role=tier, overrides={
                        '--system-prompt-file': system_prompt_file, '--prompt': prompt, '--cognitive': cognitive,
                        '--owner': owner_title, '--memory': memory_dir}):
                return
        except ValueError as exc:
            raise StoreError(str(exc)) from exc
    # Readonly preflight. This lock is released before creation; the launched
    # child independently reacquires it before registration. No transfer/gap claim.
    from .agent_ownership import AgentSession, OwnershipError
    try:
        with AgentSession.acquire_existing(store, agent_id=agent.agent_id) as session:
            session.authority
    except OwnershipError as exc:
        if system_prompt_file is not None:
            raise StoreError('--system-prompt-file was not applied: seat is owned or inaccessible; '
                             'live prompt publication actor capability is unavailable') from exc
        raise
    rows = cli._bridge_request('GET', '/pty/list')
    if (not isinstance(rows, dict) or rows.get('error') or rows.get('ok') is False
            or not isinstance(rows.get('sessions'), list)
            or any(not isinstance(row, dict) for row in rows['sessions'])):
        raise StoreError('Cannot inspect current PTYs; owned resume deferred')
    if any(row.get('harnessAgentId') == agent.agent_id for row in rows['sessions']):
        if system_prompt_file is not None:
            raise StoreError('Live owned seat exists; --system-prompt-file was not applied: '
                             'live prompt publication actor capability is unavailable; no seat was closed or duplicated')
        raise StoreError('Live owned seat exists; message it instead')
    resolution = cli._bridge_request('POST', '/harness/spawn/resolve', {
        'cli': 'litetui', 'name': agent.name, 'tier': roles.wire_tier(tier), 'model': saved['model'],
        'backend': saved['backend'], 'thinkingLevel': saved['thinking_level'],
        'cwd': cwd, 'spawnedBy': caller, **({'prompt': prompt} if prompt is not None else {}),
        **({'cognitive': cognitive} if cognitive else {}),
        **({'systemPromptFile': system_prompt_file} if system_prompt_file is not None else {}),
    })
    if not isinstance(resolution, dict) or resolution.get('ok') is not True:
        raise StoreError('Owned spawn resolution refused')
    launch = owned_launch.request(resolution, root=root, name=agent.name, agent_id=agent.agent_id,
                                  backend=saved['backend'], model=saved['model'],
                                  thinking_level=saved['thinking_level'], conversation_id=conversation_id,
                                  owner_title=owner_title, memory_dir=memory_dir,
                                  system_prompt_file=system_prompt_file)
    # Revalidate folder after the external boundary; child repeats ownership check.
    current = store.find_agent(agent_id=agent.agent_id)
    if current != agent:
        raise StoreError('Agent authority changed during launch resolution')
    launched_at = datetime.now(timezone.utc)
    result = (recovered_destination.place(launch) if recovery_scope is not None
              else cli._place_split(pane, caller, direction, cwd=cwd, launch=launch))
    if not isinstance(result, dict) or not result.get('newSessionId') or result.get('error') or result.get('ok') is False:
        raise StoreError('Owned seat creation refused or uncertain; inspect sessions')
    # Preserve the existing fleet verification/cleanup contract on owned resumes.
    # Only the exact newly returned session is eligible for cleanup.
    session_id = result['newSessionId']
    receipt_error = cli._wait_owned_runtime(agent.agent_id, session_id, launched_at)
    if receipt_error:
        raise StoreError(receipt_error)
    try:
        governed = bool(fleet_policy.floor_for(fleet_policy.load()[0], saved['backend'], saved['model']))
        why = fleet_policy.verify_seat(agent.agent_id, cli.config.get_root(), wait=90,
                                      backend=saved['backend'], allow_silent=not governed,
                                      expect_model=saved['model'], expect_thinking=saved['thinking_level'])
    except Exception as exc:
        why = f'Owned fleet verification failed: {exc}'
    if why:
        if recovery_scope is not None:
            raise StoreError(f'Exact recovery session {session_id}: {why}; inspect, no automatic kill/relaunch')
        try:
            cleanup = cli._bridge_request('DELETE', f'/pty/{session_id}')
        except Exception as exc:
            raise StoreError(f'{why}; exact new session cleanup failed: {exc}') from exc
        if (not isinstance(cleanup, dict) or cleanup.get('success') is not True
                or cleanup.get('ok') is False or cleanup.get('error')):
            raise StoreError(f'{why}; exact new session cleanup failed: {cleanup}')
        raise StoreError(f'{why}; exact new session kill requested, reap pending')
    try:
        final_policy_error = fleet_policy.verify_seat(
            agent.agent_id, cli.config.get_root(), wait=0, backend=saved['backend'],
            allow_silent=not governed, expect_model=saved['model'], expect_thinking=saved['thinking_level'])
    except Exception as exc:
        final_policy_error = f'Final policy evidence unavailable: {exc}'
    if final_policy_error:
        raise StoreError(f'Exact session {session_id}: {final_policy_error}; inspect, do not relaunch')
    receipt_error = cli._owned_runtime_receipt(agent.agent_id, session_id, launched_at)
    if receipt_error:
        raise StoreError(f'Exact session {session_id}: {receipt_error}; inspect, do not relaunch')
    # Existing named thread identity is preserved; no registry/name-index repair.
    print(f"Launched owned seat {agent.name} ({agent.agent_id}); conversation {conversation_id or 'new'}; "
          f"session {result['newSessionId']}; home {agent.directory}")
