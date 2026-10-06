"""Release identity contracts: configured names or structural metadata, never defaults."""
import importlib.util
import inspect
import json
import sys
import types
import uuid
from pathlib import Path

import pytest

from liteharness import config, nudge_bot, prompts


# Load the real dependency-free scanner and base without rag.__init__'s optional
# vector storage imports. No scanner behavior is replaced.
@pytest.fixture
def scanner_class(monkeypatch):
    root = Path(prompts.__file__).parent / 'rag' / 'scanners'
    package = types.ModuleType('_identity_scanners')
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in ('base', 'pattern_scanner'):
        qualified = package.__name__ + '.' + name
        spec = importlib.util.spec_from_file_location(qualified, root / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
    return module.PatternScanner
from liteharness.tts import smart_tts


def test_diagnosis_requires_explicit_chosen_name():
    assert inspect.signature(prompts.diagnose).parameters['name'].default is inspect.Parameter.empty


def test_summary_without_name_has_neutral_address_instruction():
    text = smart_tts._build_summary_prompt('Fixed indexing', 'stop')
    assert 'Do not address the listener by name' in text
    assert 'ALWAYS start with' not in text


def test_summary_honors_configured_name():
    text = smart_tts._build_summary_prompt('Fixed indexing', 'stop', user_name='Avery')
    assert '"Avery, "' in text


@pytest.mark.parametrize('name', ['', 'Avery'])
def test_tts_fallback_uses_existing_setting_without_inventing_name(monkeypatch, name):
    monkeypatch.setattr(smart_tts.sys, 'argv', ['smart-tts', 'Task complete'])
    monkeypatch.setattr(smart_tts, 'get_settings', lambda: {'tts': {'userName': name}})
    monkeypatch.setattr(smart_tts, 'read_stdin_context', dict)
    heard = []
    monkeypatch.setattr(smart_tts, 'speak_via_litesuite', lambda text, **kw: heard.append(text) or True)
    smart_tts.main()
    assert heard == [('Avery, ' if name else '') + 'Task complete']


def test_pattern_tier_does_not_use_person_name_or_prose(tmp_path, scanner_class):
    source = tmp_path / 'patterns.jsonl'
    source.write_text(json.dumps({'name': 'Fleet Designer', 'description': 'review orchestration'}) + '\n' +
                      json.dumps({'name': 'Harbor', 'tier': 'orchestrator'}) + '\n', encoding='utf-8')
    rows = scanner_class(str(tmp_path)).parse_file(str(source))
    assert [row['content'].splitlines()[-1] for row in rows] == ['Tier: worker', 'Tier: orchestrator']


def test_nudge_uses_configured_sender_uuid_not_display_name(tmp_path, monkeypatch):
    sender = str(uuid.uuid4())
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(nudge_bot.NudgeBot, '_setup_logging', lambda self: None)
    agents = tmp_path / 'agents'
    agents.mkdir()
    (agents / f'{sender}.json').write_text(json.dumps({'agent_id': sender, 'name': 'Harbor', 'tier': 'orchestrator'}))
    # Exercise sending with real presence without requiring optional YAML parsing.
    bot = nudge_bot.NudgeBot.__new__(nudge_bot.NudgeBot)
    bot.sender_id = sender
    sent = []
    monkeypatch.setattr(nudge_bot.inbox, 'send', lambda **kw: sent.append(kw))
    bot._send_reply('worker-id', 'Continue.')
    assert sent == [{'from_agent': sender, 'to_agent': 'worker-id', 'body': 'Continue.'}]
    # Changed display name does not change routing; missing presence fails closed.
    (agents / f'{sender}.json').unlink()
    with pytest.raises(ValueError, match='presence'):
        bot._send_reply('worker-id', 'Continue.')
    assert len(sent) == 1


@pytest.mark.parametrize('raw', [{}, {'old_sender_id': 'unused'}, {'sender_id': 'unused', 'old_sender_id': 'unused'}])
def test_nudge_rejects_missing_or_obsolete_sender_config_without_mutating(tmp_path, monkeypatch, raw):
    # JSON is a YAML subset; replace only the unavailable optional parser boundary.
    monkeypatch.setitem(sys.modules, 'yaml', types.SimpleNamespace(safe_load=json.loads))
    cfg = tmp_path / 'nudge.json'
    cfg.write_text(json.dumps(raw), encoding='utf-8')
    bot = nudge_bot.NudgeBot.__new__(nudge_bot.NudgeBot)
    bot.config_path = cfg
    bot.sender_id = 'prior-sender'
    with pytest.raises(ValueError, match='sender_id'):
        bot._load_config()
    assert bot.sender_id == 'prior-sender'


@pytest.mark.parametrize('presence', [[], {'agent_id': 'different', 'tier': 'orchestrator'}, {'tier': 'worker'}, 'malformed'])
def test_nudge_rejects_invalid_presence(tmp_path, monkeypatch, presence):
    sender = str(uuid.uuid4())
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    agents = tmp_path / 'agents'
    agents.mkdir()
    (agents / f'{sender}.json').write_text('{' if presence == 'malformed' else json.dumps(presence))
    bot = nudge_bot.NudgeBot.__new__(nudge_bot.NudgeBot)
    with pytest.raises(ValueError, match='presence'):
        bot._validate_sender(sender)


def test_sync_requires_explicit_source_without_executing_sync():
    # Release preparation forbids even an argument-error invocation of sync.
    # Inspect syntax only; no import, execution, copying or store access.
    import ast
    path = Path(prompts.__file__).parents[1] / 'scripts/sync_catalog.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument'
             and any(isinstance(arg, ast.Constant) and arg.value == '--source' for arg in node.args)]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert isinstance(keywords.get('required'), ast.Constant) and keywords['required'].value is True
    assert 'default' not in keywords
    assert not any(isinstance(node, ast.Name) and node.id == 'DEFAULT_SOURCE'
                   for node in ast.walk(tree))
