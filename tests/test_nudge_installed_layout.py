"""Load packaged defaults from a staged installed layout; no actual installation."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tomllib
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_default_config_loads_from_installed_layout_and_refuses_unconfigured_sender(tmp_path, monkeypatch):
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    resources = metadata['tool']['setuptools']['package-data']['liteharness']
    assert 'nudge_bot_default.yaml' in resources
    assert 'hooks_configs/CLAUDE_INSTALL_CONTRACT.md' in resources
    installed = tmp_path / 'site-packages/liteharness'
    installed.mkdir(parents=True)
    for name in ('nudge_bot.py', 'nudge_bot_default.yaml'):
        shutil.copyfile(ROOT / 'liteharness' / name, installed / name)
    spec = importlib.util.spec_from_file_location('liteharness._installed_nudge_fixture', installed / 'nudge_bot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    default = Path(module.__file__).with_name('nudge_bot_default.yaml')
    bot = module.NudgeBot.__new__(module.NudgeBot)
    bot.config_path = default
    presence_root = tmp_path / 'harness'
    monkeypatch.setattr(module.config, 'get_root', lambda: presence_root)
    monkeypatch.setattr(module.inbox, 'send', lambda **kwargs: pytest.fail('unconfigured sender sent a reply'))
    # Exercise the real default-resource read while keeping optional YAML dependency
    # external to the packaging proof (stdlib-only narrow fixture parser).
    reads = []
    def safe_load(text):
        reads.append(text)
        line = next(line for line in text.splitlines() if line.startswith('sender_id:'))
        return {'sender_id': json.loads(line.split(':', 1)[1].strip())}
    monkeypatch.setitem(sys.modules, 'yaml', SimpleNamespace(safe_load=safe_load))
    with pytest.raises(ValueError, match='sender_id requires an existing orchestrator UUID presence'):
        bot._load_config()
    assert reads and 'sender_id: "00000000-0000-0000-0000-000000000000"' in reads[0]
    assert not presence_root.exists()  # no logging, registry creation or implicit default identity
