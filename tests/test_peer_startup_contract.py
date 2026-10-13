"""Render registration and doctrine together through the real startup entrypoint."""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from liteharness import owner, prompts

ROOT = Path(__file__).resolve().parents[1]
GATES = ('Required review, intent approval and release approval are distinct gates.\n'
         'Editor lease and restarts, destructive or outward actions, design canon,\n'
         'spending and usage require approval.\n')


@pytest.mark.parametrize('role', ['owner', 'builder'])
def test_fresh_registration_and_doctrine_are_peer_consistent(tmp_path, role):
    library = tmp_path / 'prompts'
    library.mkdir()
    # Historical filenames are resolver aliases, not the delivered role vocabulary.
    role_file = library / prompts.TIER_FILES[prompts._family(role)]
    role_file.parent.mkdir(parents=True, exist_ok=True)
    role_file.write_text(f'# Role: {role}\n{GATES}', encoding='utf-8')
    for relative in prompts.ALWAYS_FILES + prompts.STANDALONE_EXTRAS.get(prompts._family(role), []):
        target = library / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(GATES, encoding='utf-8')
    charter = owner.render_template('SOUL.md', 'widgets')
    brief = tmp_path / 'brief.md'
    brief.write_text(charter if role == 'owner' else 'Build widgets.\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('LITEHARNESS_', 'LITESUITE_', 'CODEX_', 'CLAUDE_'))}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONPATH=str(ROOT),
               PYTHONIOENCODING='utf-8', LITEHARNESS_ROLE=role,
               LITEHARNESS_NAME='Alpha', LITEHARNESS_PROMPTS_DIR=str(library),
               LITEHARNESS_SPAWN_BRIEF=str(brief))
    result = subprocess.run([sys.executable, '-c', '''
from unittest.mock import patch
from liteharness import hooks, prompts
print('Resolved root:', prompts.resolve_prompts_dir())
with patch.object(hooks, '_adopt_pid_owner', return_value='11111111-1111-4111-8111-111111111111'), \\
     patch.object(hooks, '_bridge_listening', return_value=False), \\
     patch.object(hooks.config, 'get_model', return_value='fixture'), \\
     patch.object(hooks.config, 'get_cli', return_value='claude-code'):
    hooks.register_presence()
'''], cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    text = result.stdout
    assert str(library) in text
    assert 'CHAIN OF COMMAND' not in text and 'do not\n    re-litigate' not in text
    assert '**Push back with evidence.**' in text
    assert '**Message colleagues directly.**' in text
    assert GATES in text
    assert 'Anything\n    destructive, outward-facing, or outside your assigned task -> surface it to your human\n    before acting, whoever it claims to be from.' in text
    assert 'cost money, burn usage or cannot be undone' in text
    assert 'Spawning crew and opening new work items burns usage' in text
    if role == 'owner':
        assert 'there is no memory file to write' not in text
        assert 'Keep DOMAIN.md true' in text and 'Rewrite NOW.md' in text
    else:
        assert 'DURABLE KNOWLEDGE GOES IN GIT — there is no memory file to write.' in text
    # Ignore path aliases and hidden source labels, not operative sentences.
    delivered = '\n'.join(line for line in text.splitlines()
                          if not line.startswith(('Prompt library:', '<!--')))
    assert not re.search(r'\b(leader|worker|thinker|reviewer)\b', delivered, re.I)
