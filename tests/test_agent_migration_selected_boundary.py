"""Selected plan permits sibling identity-settings guard, never payload access."""
import json
from pathlib import Path

import pytest
from liteharness import agent_migration_policy as policy
from test_agent_migration import AID, DID
from test_agent_migration_policy import conversation, NAMES


def deny_payload(monkeypatch, sibling):
    observed = []
    for operation in ('stat', 'lstat', 'open', 'iterdir'):
        original = getattr(Path, operation)
        def guarded(self, *args, _op=operation, _original=original, **kwargs):
            if sibling in self.parents and self != sibling / 'settings.json':
                observed.append((_op, str(self)))
                raise AssertionError('Unselected payload access: ' + _op + ' ' + str(self))
            if self == sibling and _op == 'iterdir':
                observed.append((_op, str(self)))
                raise AssertionError('Unselected inventory enumeration')
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(Path, operation, guarded)
    return observed


def world(root):
    conversation(root)
    sibling = conversation(root, DID)
    settings_path = sibling / 'settings.json'
    settings = json.loads(settings_path.read_text())
    settings.update(seat_name='KindGrid', seat_id=DID)
    settings_path.write_text(json.dumps(settings))
    (sibling / 'convo.jsonl').write_text(json.dumps({'type':'meta','id':DID,'agent_id':DID,'created':1})+'\n')
    return sibling, {**NAMES, 'KindGrid': {'agent_id':DID,'convo_id':DID}}


def test_selected_plan_never_reads_or_hashes_other_indexed_payload(tmp_path, monkeypatch):
    sibling, names = world(tmp_path)
    observed = deny_payload(monkeypatch, sibling)
    manifest = policy.named_plan(tmp_path, names=names, selected_agent='QuietHelm')
    assert observed == []
    assert [row['name'] for row in manifest['agents']] == ['QuietHelm']
    assert [row['source'] for row in manifest['folders']] == [NAMES['QuietHelm']['convo_id']]


def test_full_mode_exact_baseline_bytes_and_selected_row_fields(tmp_path):
    import subprocess
    sibling, names = world(tmp_path)
    repository = Path(policy.__file__).resolve().parents[1]
    source = subprocess.check_output(['git', '-C', str(repository), 'show',
                                     '1245f57:liteharness/agent_migration_policy.py'])
    namespace = {'__name__': 'liteharness._baseline_policy', '__package__': 'liteharness'}
    exec(compile(source, '<baseline-policy>', 'exec'), namespace)
    baseline = namespace['named_plan'](tmp_path, names=names)
    current = policy.named_plan(tmp_path, names=names)
    assert json.dumps(current, sort_keys=True).encode() == json.dumps(baseline, sort_keys=True).encode()
    selected = policy.named_plan(tmp_path, names=names, selected_agent='QuietHelm')
    assert selected['agents'] == [a for a in current['agents'] if a['name'] == 'QuietHelm']
    assert selected['folders'] == [f for f in current['folders'] if f.get('name') == 'QuietHelm']


def test_sibling_nonidentity_content_never_enters_selected_digest(tmp_path):
    sibling, names = world(tmp_path)
    before = policy.named_plan(tmp_path, names=names, selected_agent='QuietHelm')
    (sibling / 'unknown.bin').write_bytes(b'changed sibling payload')
    saved = json.loads((sibling / 'settings.json').read_text())
    saved.update(model='OTHER SECRET MODEL', secret='PRIVATE SIBLING CONTENT', tool_iterations=999)
    (sibling / 'settings.json').write_text(json.dumps(saved))
    after = policy.named_plan(tmp_path, names=names, selected_agent='QuietHelm')
    assert before == after
    assert 'PRIVATE SIBLING CONTENT' not in json.dumps(after)


@pytest.mark.parametrize('indexed', [False, True])
def test_selected_historical_collision_still_blocks_indexed_or_unindexed_sibling(tmp_path, monkeypatch, indexed):
    sibling, names = world(tmp_path)
    saved = json.loads((sibling / 'settings.json').read_text())
    saved['seat_name'] = 'QuietHelm'
    (sibling / 'settings.json').write_text(json.dumps(saved))
    if not indexed:
        names = NAMES
    observed = deny_payload(monkeypatch, sibling)
    selected = policy.named_plan(tmp_path, names=names, selected_agent='QuietHelm')
    assert observed == []
    assert selected['agents'][0]['disposition'] == 'archive-only'
    assert 'Historical named lineage collides across agent identities' in selected['agents'][0]['reasons']


@pytest.mark.parametrize('collision', ['agent-id', 'convo-id', 'casefold-name'])
def test_selected_all_catalog_collision_guards_retained(tmp_path, collision):
    world(tmp_path)
    alias = {'agent_id':DID,'convo_id':DID}
    name = 'Alias'
    if collision == 'agent-id':
        alias['agent_id'] = AID
    elif collision == 'convo-id':
        alias['convo_id'] = NAMES['QuietHelm']['convo_id']
    else:
        name = 'quiethelm'
    result = policy.named_plan(tmp_path, names={**NAMES,name:alias}, selected_agent='QuietHelm')
    assert 'Name index identity/name/conversation collision' in result['agents'][0]['reasons']


@pytest.mark.parametrize('manifest_kind', ['selected', 'original-full'])
def test_actual_copy_verify_activate_never_accesses_sibling_payload(tmp_path, monkeypatch, manifest_kind):
    from liteharness import agent_migration_copy as copy_tool, agent_activation as activation, cli
    from liteharness.agent_migration import digest
    from liteharness.agent_store import AgentStore
    from test_agent_activation import approval
    sibling, names = world(tmp_path)
    registry = tmp_path / 'registry'
    registry.mkdir()
    (registry / 'agents').mkdir()
    (registry / 'names.json').write_text(json.dumps(names))
    kwargs = {'selected_agent':'QuietHelm'} if manifest_kind == 'selected' else {}
    manifest = policy.named_plan(tmp_path, names=names, **kwargs)
    receipt = {'plan_digest':manifest['plan_digest'],'authority':'fixture leader',
               'merged_commit':'a'*40,'review_evidence':'fixture-only'}
    monkeypatch.setattr(cli, '_bridge_request', lambda *args: {'sessions':[]})
    observed = deny_payload(monkeypatch, sibling)
    copied = copy_tool.copy_named_agent(manifest, agent_id=AID, registry_root=registry, merge_receipt=receipt)
    home = tmp_path / '.agents' / 'QuietHelm'
    assert copied['status'] == 'copied' and (home / '.agent.initializing').is_file()
    verified = copy_tool.copy_named_agent(manifest, agent_id=AID, registry_root=registry, merge_receipt=receipt, existing_only=True)
    assert verified == copied
    result = activation.activate(manifest, agent_id=AID, registry_root=registry,
                                 copy_approval=receipt, activation_approval=approval(manifest, receipt))
    assert result['status'] == 'activated' and observed == []
    assert not (home / '.agent.initializing').exists()
    assert AgentStore(tmp_path).find_agent(agent_id=AID).directory == home


def test_selected_digest_tamper_refuses_before_any_copy(tmp_path, monkeypatch):
    from liteharness import agent_migration_copy as copy_tool
    from liteharness.agent_store import StoreError
    world(tmp_path)
    manifest = policy.named_plan(tmp_path, names=NAMES, selected_agent='QuietHelm')
    receipt = {'plan_digest':manifest['plan_digest'],'authority':'fixture leader',
               'merged_commit':'a'*40,'review_evidence':'fixture-only'}
    manifest['selection']['agent_name'] = 'Other'
    monkeypatch.setattr(copy_tool, '_offline', lambda *a, **k: pytest.fail('must refuse before offline checks'))
    with pytest.raises(StoreError, match='digest mismatch'):
        copy_tool.copy_named_agent(manifest, agent_id=AID, registry_root=tmp_path, merge_receipt=receipt)
    assert not (tmp_path / '.agents').exists()


def test_actual_cli_dry_run_selected_json_never_accesses_sibling_payload(tmp_path, monkeypatch, capsys):
    from liteharness.agent_migration_cli import main
    sibling, names = world(tmp_path)
    registry = tmp_path / 'registry'
    registry.mkdir()
    (registry / 'names.json').write_text(json.dumps(names))
    observed = deny_payload(monkeypatch, sibling)
    main(['--copy-agent', 'QuietHelm', '--root', str(tmp_path), '--registry-root', str(registry)])
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'dry-run' and result['apply'] is False
    manifest = result['manifest']
    assert manifest['selection'] == {'agent_name': 'QuietHelm'}
    assert [a['name'] for a in manifest['agents']] == ['QuietHelm']
    assert [f['source'] for f in manifest['folders']] == [NAMES['QuietHelm']['convo_id']]
    assert observed == [] and not (tmp_path / '.agents').exists()
