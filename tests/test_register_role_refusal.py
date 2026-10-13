"""T0470: registration refuses unknown input without breaking shipped callers."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from liteharness import cli, config, roles

ROOT = Path(__file__).resolve().parents[1]
ALIASES = {"leader": "owner", "worker": "builder", "thinker": "scout", "reviewer": "critic"}

@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "get_root", lambda: tmp_path)
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("LITEHARNESS_NO_ANNOUNCE", "1")
    (tmp_path / "agents").mkdir()
    return tmp_path



def _presence(home, agent_id):
    return json.loads((home / "agents" / f"{agent_id}.json").read_text(encoding="utf-8"))



@pytest.mark.parametrize("old,new", sorted(ALIASES.items()))
def test_register_with_an_old_word_stores_the_new_word_and_says_so(home, capsys, old, new):
    cli.cmd_register("seat-1", cli="claude-code", tier=old, name="Alpha")
    row = _presence(home, "seat-1")
    assert row["role"] == new
    assert row["tier"] == old, "the old key keeps the old word for readers that have not moved"
    out = capsys.readouterr()
    assert "deprecated" in out.err.lower() and old in out.err and new in out.err
    assert "deprecated" not in out.out.lower(), "stdout stays clean: spawn/register output is parsed"



@pytest.mark.parametrize("word", roles.ROLES)
def test_register_with_a_new_word_prints_no_deprecation(home, capsys, word):
    cli.cmd_register("seat-1", cli="claude-code", tier=word, name="Alpha")
    assert _presence(home, "seat-1")["role"] == word
    captured = capsys.readouterr()
    assert "deprecated" not in (captured.out + captured.err).lower()



@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("word", ["janitor", "", "bad\nrole"])
def test_register_unknown_role_refuses_before_any_side_effect(home, monkeypatch, capsys, existing, word):
    from liteharness import announce
    if existing:
        (home / "agents" / "seat-1.json").write_text(json.dumps({
            "agent_id": "seat-1", "name": "Original", "role": "owner", "tier": "leader"}),
            encoding="utf-8")
        (home / "names").mkdir()
        (home / "names" / "seat-1").write_text("Original", encoding="utf-8")
    else:
        (home / "agents").rmdir()  # even an empty registry directory must not be created

    def snapshot():
        return {str(p.relative_to(home)): p.read_bytes() if p.is_file() else None
                for p in home.rglob("*")}

    before = snapshot()
    announcements = []
    monkeypatch.setattr(announce, "announce_registration", lambda *a, **kw: announcements.append(kw))
    monkeypatch.setattr(cli.sys, "argv", ["liteharness", "register", "--agent-id", "seat-1",
                                         "--tier", word, "--name", "Changed"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code != 0
    assert snapshot() == before
    assert announcements == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert repr(word) in captured.err
    assert all(accepted in captured.err for accepted in (*roles.ROLES, *ALIASES))



def test_register_default_is_builder(home):
    cli.cmd_register("seat-1", cli="claude-code", name="Alpha")
    assert _presence(home, "seat-1")["role"] == "builder"



@pytest.mark.parametrize("stored", [{"tier": "worker"}, {"role": "owner", "tier": "leader"}])
def test_a_stored_old_word_survives_a_reregister_without_a_role(home, stored):
    (home / "agents" / "seat-1.json").write_text(json.dumps({
        "agent_id": "seat-1", **stored,
        "last_seen": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    cli.cmd_register("seat-1", cli="claude-code", name="Alpha")
    assert all(_presence(home, "seat-1")[key] == value for key, value in stored.items())



def test_shipped_registration_callers_send_only_accepted_role_words(home):
    """Walk command-bearing sources, then exercise their role input at registration."""
    from liteharness import codex_hooks

    expected = {
        'liteharness/cli.py': {None},
        'liteharness/hooks.py': {None},
        'liteharness/catalog/skills/ls-liteharness/SKILL.md': {None},
        'liteharness/catalog/skills/ls-init-liteharness/skill-template.md': {'orchestrator'},
        **{f'liteharness/catalog/skills/{skill}/SKILL.md': {None}
           for skill in ('ls-leader', 'ls-worker', 'ls-thinker', 'ls-reviewer', 'ls-sentinel')},
    }
    hook_callers = {
        'liteharness/catalog/skills/ls-liteharness/SKILL.md',
        'liteharness/catalog/hooks/hooks.json',
        'liteharness/hooks_configs/claude_hooks.json',
        'liteharness/hooks_configs/copilot_hooks.json',
        'liteharness/hooks_configs/opencode_plugin.ts',
        'scripts/t418_watch_auto_e2e.py',
        'scripts/t563_watch_auto_reresolve_e2e.py',
    }
    found, found_hooks = {}, set()
    for base in ('liteharness', 'scripts'):
        for path in (ROOT / base).rglob('*'):
            if path.suffix not in {'.py', '.md', '.json', '.ts', '.mjs'}:
                continue
            text = path.read_text(encoding='utf-8').replace('\\' + '\n', ' ')
            rel = path.relative_to(ROOT).as_posix()
            commands = re.findall(r'python -m liteharness\.cli register\b[^\n]*', text)
            if commands:
                words = set()
                for command in commands:
                    flag = re.search(r'--(?:tier|role)\s+(\S+)', command)
                    words.add(flag.group(1) if flag else None)
                found[rel] = words
            if (re.search(r'python -m liteharness\.hooks register\b', text)
                    or '"liteharness.hooks", "register"' in text):
                found_hooks.add(rel)
    assert found == expected
    assert found_hooks == hook_callers
    assert codex_hooks.COMMANDS['session-start-register'] == ('SessionStart', 'register_presence')
    codex_config = (ROOT / 'liteharness/hooks_configs/codex_hooks.json').read_text(encoding='utf-8')
    assert 'python -m liteharness.codex_hooks session-start-register' in codex_config

    # CLI snippets send None or orchestrator. Hook callers supply no role argument:
    # register_presence reads env/stored role, whose canonical/legacy input is pinned below.
    for index, (caller, words) in enumerate(found.items()):
        for word in words:
            assert word is None or roles.normalize_role(word), caller
            identity = f'caller-{index}'
            cli.cmd_register(identity, tier=word)
            assert roles.row_role(_presence(home, identity)) == (word or roles.DEFAULT_ROLE)
    for word in (*roles.ROLES, *ALIASES):
        assert roles.env_role({'LITEHARNESS_TIER': word}) == roles.normalize_role(word)

