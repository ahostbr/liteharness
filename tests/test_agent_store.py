"""Shared T0308 resolver contract: fixture-only, no real store creation."""
import ast
import json
import os
from pathlib import Path

import pytest

from liteharness import agent_store as store

AID = '11111111-1111-4111-8111-111111111111'
BID = '22222222-2222-4222-8222-222222222222'
CID = '33333333-3333-4333-8333-333333333333'


def agent(root, name='QuietHelm', identity=AID):
    directory = root / '.agents' / name
    directory.mkdir(parents=True)
    settings = {'schema_version': 1, 'name': name, 'agent_id': identity,
                'execution': {'backend': 'codex', 'model': 'fixture', 'thinking_level': 'high'}}
    (directory / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    return directory


def test_lookup_never_materializes_empty_root(tmp_path):
    root = tmp_path / 'unborn'
    resolver = store.AgentStore(root)
    assert resolver.list_agents() == []
    assert resolver.agent_directory('QuietHelm') == root / '.agents' / 'QuietHelm'
    assert resolver.legacy_conversation(CID) == root / '.convos' / CID
    with pytest.raises(store.StoreError):
        resolver.find_agent(name='QuietHelm')
    assert not root.exists()


def test_agent_truth_and_owned_conversation_paths(tmp_path):
    directory = agent(tmp_path)
    resolver = store.AgentStore(tmp_path)
    found = resolver.find_agent(name='quiethelm')
    assert found.agent_id == AID and found.memory_root == directory
    assert resolver.find_agent(agent_id=AID) == found
    convo = resolver.conversation_directory(found, CID)
    assert convo == directory / 'conversations' / CID
    assert not convo.exists()
    convo.mkdir(parents=True)
    assert resolver.list_conversations(found) == [convo]
    assert resolver.locate_conversation(CID) == convo
    assert found.settings['execution']['backend'] == 'codex'
    for kwargs in ({}, {'name': 'QuietHelm', 'agent_id': AID}, {'agent_id': BID}):
        with pytest.raises(store.StoreError):
            resolver.find_agent(**kwargs)


@pytest.mark.parametrize('name', ['', ' ', '.', '..', '../escape', 'C:drive', 'a/b', 'a\\b',
                                  'CON', 'nul.txt', 'COM1', 'lpt9.x', 'name.', ' name',
                                  'name ', 'x\x00y', 'x\ny', 'e\u0301', '\ud800', 'x'*256])
def test_windows_names_and_ambiguous_spellings_fail_closed(tmp_path, name):
    with pytest.raises(store.StoreError):
        store.AgentStore(tmp_path).agent_directory(name)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('identity', ['not-id', '../x', '', None, CID.upper(), '{'+CID+'}'])
def test_canonical_uuid_components_only(identity):
    # Numeric fixture UUID has no letters, so exercise a genuinely mixed-case ID.
    if identity == CID.upper():
        identity = 'ABCDEFAB-1234-4234-8234-ABCDEFABCDEF'
    with pytest.raises(store.StoreError):
        store.valid_id(identity)


def test_duplicate_identity_and_conversation_ownership_rejected(tmp_path):
    first = agent(tmp_path)
    second = agent(tmp_path, 'KindGrid', BID)
    for directory in (first, second):
        (directory / 'conversations' / CID).mkdir(parents=True)
    resolver = store.AgentStore(tmp_path)
    with pytest.raises(store.StoreError, match='ambiguous ownership'):
        resolver.locate_conversation(CID)
    raw = json.loads((second / 'settings.json').read_text())
    raw['agent_id'] = AID
    (second / 'settings.json').write_text(json.dumps(raw))
    with pytest.raises(store.StoreError, match='ambiguous'):
        resolver.list_agents()


@pytest.mark.parametrize('change', [{'schema_version': 2}, {'schema_version': True},
                                    {'name': 'Wrong'}, {'agent_id': 'bad'},
                                    {'execution': {}}, {'execution': 'bad'}])
def test_authoritative_settings_corruption_is_not_registry_fallback(tmp_path, change):
    directory = agent(tmp_path)
    path = directory / 'settings.json'
    raw = json.loads(path.read_text())
    raw.update(change)
    path.write_text(json.dumps(raw))
    with pytest.raises(store.StoreError):
        store.AgentStore(tmp_path).find_agent(name='QuietHelm')
    assert json.loads(path.read_text()) == raw


def test_file_and_ancestor_links_rejected(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    directory = agent(real)
    linked = tmp_path / 'linked'
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip('host cannot create symlinks')
    with pytest.raises(store.StoreError, match='Linked'):
        store.AgentStore(linked)
    settings = directory / 'settings.json'
    other = real / 'original.json'
    settings.rename(other)
    settings.symlink_to(other)
    with pytest.raises(store.StoreError, match='Linked'):
        store.AgentStore(real).list_agents()


def test_unicode_casefold_name_collision_is_not_an_alias(tmp_path):
    agent(tmp_path, 'Straße')
    agent(tmp_path, 'STRASSE', BID)
    with pytest.raises(store.StoreError, match='ambiguous'):
        store.AgentStore(tmp_path).list_agents()


def test_reparse_ancestor_and_unknown_settings_fail_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    directory = agent(tmp_path)
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == directory:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(store.StoreError, match='Linked'):
        store.AgentStore(tmp_path).list_agents()


def test_stdlib_contract_and_canonical_mirror_parity():
    source = Path(store.__file__).read_bytes()
    tree = ast.parse(source)
    modules = {node.module.split('.')[0] for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module}
    modules.update(alias.name.split('.')[0] for node in ast.walk(tree)
                   if isinstance(node, ast.Import) for alias in node.names)
    assert modules <= {'__future__', 'dataclasses', 'json', 'pathlib', 're', 'stat', 'unicodedata', 'uuid'}
    counterpart = os.environ.get('T0308_MIRROR_AGENT_STORE')
    if not counterpart:
        pytest.skip('paired canonical checkout not provided; package contract tested above')
    assert Path(counterpart).read_bytes() == source
