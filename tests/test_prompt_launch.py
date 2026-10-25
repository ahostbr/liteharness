"""Would a LiteTUI home accept the default role a resume derives (T0558-F-E)?

The manifest shape and the role body rule replicate LiteTUI seat_prompts.py; these tests pin that
replica against the real `.prompt-launch.json` shape and a canonical-preamble-shaped role file.
"""
import hashlib
import json

import pytest

from liteharness import prompt_launch
from liteharness.prompt_launch import Undecidable, Verdict, read_manifest, role_body, role_file_arg, role_sha, verdict

AID = '11111111-1111-4111-8111-111111111111'
# Canonical preambles carry a generated-source comment before the role header.
PREAMBLE = '<!-- generated from preambles/leader-preamble.md -->\n# LEADER ROLE\nCURRENT LEADER BODY\n\ncanvas\n'


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def home(tmp_path, *, source='default', recorded='OLD LEADER BODY', live='OLD LEADER BODY', role_key=True):
    directory = tmp_path / 'Alpha'
    (directory / 'prompts').mkdir(parents=True)
    if live is not None:
        (directory / 'prompts' / 'role.md').write_text(live, encoding='utf-8')
    sources = {'method.md': sha('METHOD'), **({'role.md': sha(recorded)} if role_key else {})}
    # The exact shape LiteTUI writes (sort_keys), as read from a real owner home's .prompt-launch.json.
    (directory / '.prompt-launch.json').write_text(
        json.dumps({'version': 2, 'sources': sources, 'role_source': source}, sort_keys=True), encoding='utf-8')
    return directory


@pytest.fixture
def role(tmp_path):
    path = tmp_path / 'composed-leader-preamble.md'
    path.write_text(PREAMBLE, encoding='utf-8')
    return str(path)


def test_role_body_strips_the_scaffold_and_hashes_the_body():
    assert role_body(PREAMBLE, 'leader') == 'CURRENT LEADER BODY\n\ncanvas'
    assert role_sha(PREAMBLE, 'leader') == sha('CURRENT LEADER BODY\n\ncanvas')
    assert role_sha('plain body\n', 'worker') == sha('plain body')  # no header: the text itself, stripped


@pytest.mark.parametrize('text,role', [
    ('# WORKER ROLE\nbody', 'leader'),  # scaffold disagrees with the actual role
    ('# LEADER ROLE\na\n# LEADER ROLE\nb', 'leader'),  # duplicate scaffold
    ('stray\n# LEADER ROLE\nbody', 'leader'),  # text before the scaffold
    ('# LEADER ROLE\n# POLYMATHIC LINUS\nbody', 'leader'),  # a method inside the role
    ('\ufeff\ufeffbody', 'leader'),  # multiple BOMs
])
def test_role_body_refuses_what_litetui_refuses(text, role):
    with pytest.raises(Undecidable):
        role_body(text, role)


def test_manifest_shape_is_pinned(tmp_path):
    directory = home(tmp_path, source='custom')
    assert read_manifest(directory)['role_source'] == 'custom'
    assert read_manifest(tmp_path) is None  # a never-launched home has no manifest
    for broken in ('{', '[]', json.dumps({'version': 1, 'sources': {}, 'role_source': 'default'}),
                   json.dumps({'version': 2, 'sources': {}, 'role_source': 'weird'}),
                   json.dumps({'version': 2, 'sources': {'role.md': 'nothex'}, 'role_source': 'default'}),
                   json.dumps({'version': 2, 'sources': {}, 'role_source': 'default', 'extra': 1})):
        (directory / '.prompt-launch.json').write_text(broken, encoding='utf-8')
        with pytest.raises(Undecidable):
            read_manifest(directory)


def test_role_file_arg_reads_both_spellings():
    assert role_file_arg(['--model', 'x', '--role-prompt-file', '/r.md', '--prompt', 'p']) == '/r.md'
    assert role_file_arg(['--role-prompt-file=/r.md']) == '/r.md'
    assert role_file_arg(['--model', 'x']) is None
    assert role_file_arg(['--role-prompt-file']) is None


def check(directory, role_path):
    return verdict(directory, role_path, 'leader', agent_id=AID, cwd='C:/work', tier='leader')


def test_default_home_whose_recorded_role_matches_resumes_as_is(tmp_path, role):
    assert check(home(tmp_path, recorded='CURRENT LEADER BODY\n\ncanvas'), role) == Verdict('ok')


def test_stale_default_home_is_passed_its_own_role_file(tmp_path, role):
    directory = home(tmp_path)
    found = check(directory, role)
    assert found.action == 'override' and found.role_file == str(directory / 'prompts' / 'role.md')
    assert directory.name in found.reason and 'Launch prompt source changed' in found.reason


@pytest.mark.parametrize('live', [None, '# WORKER ROLE\nwrong tier', 'x' * 70000], ids=['missing', 'wrong-tier', 'oversized'])
def test_stale_default_home_without_a_usable_own_role_stops_with_the_manual_command(tmp_path, role, live):
    found = check(home(tmp_path, live=live), role)
    assert found.action == 'stop' and found.role_file == ''
    assert 'Alpha' in found.reason and f'spawn --resume {AID}' in found.reason
    assert f'--system-prompt-file "{role}"' in found.reason


@pytest.mark.parametrize('source', ['custom', 'seed', 'none'])
def test_only_default_homes_are_ever_touched(tmp_path, role, source):
    assert check(home(tmp_path, source=source, role_key=source != 'none'), role) == Verdict('ok')


def test_homes_that_cannot_be_judged_are_not_guessed(tmp_path, role):
    assert check(tmp_path / 'nowhere', role) == Verdict('ok')  # never launched: LiteTUI seeds it
    assert check(home(tmp_path), None) == Verdict('ok')  # the resolver sent no role file
    directory = home(tmp_path / 'b')
    (directory / '.prompt-launch.json').write_text('{', encoding='utf-8')
    found = check(directory, role)
    assert found.action == 'stop' and 'unreadable' in found.reason and 'Resume by hand' in found.reason
    assert check(home(tmp_path / 'c'), str(tmp_path / 'missing.md')).action == 'stop'


def test_the_check_writes_nothing(tmp_path, role):
    directory = home(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    check(directory, role)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert prompt_launch.manual_command(AID, 'C:/w', 'leader', None).endswith('"<the current leader preamble file>"')
