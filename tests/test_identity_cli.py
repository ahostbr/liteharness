"""Host identity resolution is explicit, backward compatible and side-effect free."""
from pathlib import Path

import pytest

from liteharness import prompts


@pytest.mark.parametrize('value, expected', [
    (None, 'claude-code'), ('claude', 'claude-code'), (' Claude-Code ', 'claude-code'),
    ('codex', 'codex-cli'), (' CODEX-CLI ', 'codex-cli'),
])
def test_supported_identity_hosts(value, expected):
    assert prompts.identity_cli(value) == expected


@pytest.mark.parametrize('value', ['', ' ', 'unknown', 1, [], {}])
def test_unsupported_identity_hosts_reject_before_missing_architecture(value, monkeypatch):
    def forbidden(*args):
        raise AssertionError('architecture lookup before host validation')
    monkeypatch.setattr(prompts, 'resolve_cognitive_file', forbidden)
    for function in (prompts.identity_cli, lambda cli: prompts.resolve_skill_target('Harbor Guide', cli),
                     lambda cli: prompts.verify_orchestrator_identity('Harbor Guide', cli)):
        with pytest.raises(ValueError):
            function(value)


def test_resolution_selects_real_user_skill_roots_without_creating_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    claude, _ = prompts.resolve_skill_target('Harbor Guide')
    codex, _ = prompts.resolve_skill_target('Harbor Guide', cli='codex')
    assert claude == tmp_path / '.claude/skills/harbor-guide/SKILL.md'
    assert codex == tmp_path / '.agents/skills/harbor-guide/SKILL.md'
    assert prompts.resolve_skill_target('Harbor Guide') == prompts.resolve_skill_target('Harbor Guide', 'claude-code')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('cli', ['claude-code', 'codex-cli'])
def test_real_identity_resolver_verifies_only_selected_host_and_link(tmp_path, monkeypatch, cli):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setenv('LITEHARNESS_USER_PROMPTS_DIR', str(tmp_path / 'user-prompts'))
    shipped = tmp_path / 'shipped-prompts'
    (shipped / 'cognitive-architectures/orchestrator').mkdir(parents=True)
    (shipped / 'cognitive-architectures/orchestrator/default.md').write_text('default architecture')
    monkeypatch.setenv('LITEHARNESS_PROMPTS_DIR', str(shipped))
    name = 'Harbor Guide'
    assert not prompts.verify_orchestrator_identity(name, cli)[0]
    arch, _ = prompts.resolve_orchestrator_target(name)
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text('personalized synthetic architecture', encoding='utf-8')
    assert prompts.resolve_cognitive_file(name, 'orchestrator') == arch
    assert 'NO SKILL' in prompts.verify_orchestrator_identity(name, cli)[1]
    skill, _ = prompts.resolve_skill_target(name, cli)
    skill.parent.mkdir(parents=True)
    skill.write_text('not linked', encoding='utf-8')
    assert 'does not reference' in prompts.verify_orchestrator_identity(name, cli)[1]
    skill.write_text(f'Load {arch.name}', encoding='utf-8')
    assert prompts.verify_orchestrator_identity(name, cli)[0]
    other = 'codex-cli' if cli == 'claude-code' else 'claude-code'
    assert not prompts.verify_orchestrator_identity(name, other)[0]
    arch.write_text('', encoding='utf-8')
    assert 'EMPTY' in prompts.verify_orchestrator_identity(name, cli)[1]
