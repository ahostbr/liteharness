"""R1/R2/R3 detector regressions; fixtures are temporary and synthetic."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

GUARD = Path(__file__).parents[1] / 'scripts/check_pii.py'
spec = importlib.util.spec_from_file_location('review_guard', GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
ACTOR = bytes.fromhex('73656e74696e656c').decode()
OWNER = bytes.fromhex('61686f73746272').decode()
AUTHOR = bytes.fromhex('5279616e204465766c696e').decode()
EMAIL = bytes.fromhex('7279616e406c69746573756974652e646576').decode()
# A token that still blocks; a name alone is only a printed note.
CODENAME = bytes.fromhex('4b75726f72797575').decode()


def legal_line(field='authors'):
    return field + ' = [{name = "' + AUTHOR + '", email = "' + EMAIL + '"}]'


@pytest.mark.parametrize('suffix', ['/'+ACTOR, '?actor='+ACTOR, '#'+ACTOR,
                                   '/' + 'C:' + '/Projects/private', '?path=' + 'C:' + '/Projects/private'])
def test_repository_suffix_is_scanned(suffix):
    assert guard.scan_text('https://github.com/' + OWNER + '/liteharness' + suffix)


def test_exact_repo_positive_and_longer_components_negative():
    assert not guard.scan_text('https://github.com/' + OWNER + '/liteharness')
    assert not guard.scan_text('https://github.com/' + OWNER + '/liteharness.git')
    for repo in ('liteharness-extra', 'liteharness.git-extra', 'liteharness_foo', 'liteharness.foo'):
        assert guard.scan_text('https://github.com/' + OWNER + '/' + repo)


ENDPOINT = 'http://127.0.0.1:7423/v1/sentinel/assistant-message'


def test_fixed_endpoint_exact_and_standalone_positive():
    assert not guard.scan_text(ENDPOINT)
    assert not guard.scan_text('/v1/sentinel/assistant-message')


@pytest.mark.parametrize('url', [
    ENDPOINT.replace('127.0.0.1', 'localhost'), ENDPOINT.replace('7423', '7424'),
    ENDPOINT.replace('/v1/', '/v2/'), ENDPOINT + '-extra', ENDPOINT + '.extra',
    ENDPOINT.replace('http:', 'https:'), 'x' + ENDPOINT,
])
def test_endpoint_near_matches_not_exempted(url):
    assert guard.scan_text(url)


@pytest.mark.parametrize('suffix', ['/'+ACTOR, '?actor='+ACTOR, '#'+ACTOR, ' '+ACTOR,
                                   '/' + 'C:' + '/Projects/private'])
def test_endpoint_suffix_survives_mask(suffix):
    assert guard.scan_text(ENDPOINT + suffix)


@pytest.mark.parametrize('field', ['authors', 'maintainers'])
def test_valid_project_legal_owned_fields(field):
    assert guard.digest(AUTHOR) == guard.LEGAL_AUTHOR
    assert guard.digest(EMAIL) == '3a1a74ad4cda051b58a5eff11deedeb54af70ac724d89ad9ac37543f57c515cc'
    assert not guard.scan_file('pyproject.toml', ('[project]\n' + legal_line(field) + '\n').encode())


@pytest.mark.parametrize('text', [
    '[tool.fixture]\n' + legal_line(), legal_line(),
    '[project.lookalike]\n' + legal_line(),
    '[tool.fixture]\nproject.authors = [{name = "'+AUTHOR+'", email = "'+EMAIL+'"}]',
    '[project]\n' + legal_line() + '\n' + legal_line(),
    '[project]\n' + legal_line() + '\nmalformed =',
    '[tool.fixture]\ntext = """\n[project]\n' + legal_line() + '\n"""',
    '[project]\nauthors = [\n{name = "'+AUTHOR+'", email = "'+EMAIL+'"}\n]',
    '[project]\n"authors.extra" = [{name = "'+AUTHOR+'", email = "'+EMAIL+'"}]',
    '[project]\nvalue = "'+AUTHOR+'"',
])
def test_toml_ambiguity_other_contexts_raw_scan(text):
    assert guard.scan_file('pyproject.toml', text.encode())


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Fresh inert repos do not inherit machine/global hooks or identity.
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    # Command-line config can otherwise override the isolated global config.
    monkeypatch.delenv('GIT_CONFIG_COUNT', raising=False)
    monkeypatch.delenv('GIT_CONFIG_PARAMETERS', raising=False)
    for key in tuple(os.environ):
        if key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')):
            monkeypatch.delenv(key, raising=False)
    git(tmp_path, 'init', '-q')
    configured = subprocess.run(['git', 'config', '--get', 'core.hooksPath'],
                                cwd=tmp_path, capture_output=True)
    assert configured.returncode == 1 and not configured.stdout
    git(tmp_path, 'config', 'user.name', 'Public Fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    return tmp_path


def git(repo, *args, input=None):
    result = subprocess.run(['git', *args], cwd=repo, input=input, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result.stdout


def run(repo):
    return subprocess.run([sys.executable, str(GUARD)], cwd=repo, capture_output=True, text=True)


def seed(repo, name, data):
    (repo/name).write_bytes(data)
    git(repo, 'add', '--', name)
    git(repo, 'commit', '-qm', 'fixture baseline')


def test_mixed_benign_and_dirty_rename_destination_index(repo):
    seed(repo, 'old.txt', ('prefix\n'*30 + CODENAME).encode())
    git(repo, 'mv', 'old.txt', 'destination.txt')
    (repo/'clean.txt').write_text('clean')
    git(repo, 'add', 'clean.txt')
    (repo/'destination.txt').write_text('clean worktree substitution')
    result = run(repo)
    assert result.returncode == 1
    assert 'destination.txt:' in result.stdout and CODENAME not in result.stdout


def test_mixed_benign_and_dirty_typechange_index(repo):
    seed(repo, 'link', b'clean target')
    blob = git(repo, 'hash-object', '-w', '--stdin', input=CODENAME.encode()).decode().strip()
    git(repo, 'update-index', '--cacheinfo', '120000', blob, 'link')
    (repo/'clean.txt').write_text('clean')
    git(repo, 'add', 'clean.txt')
    result = run(repo)
    assert result.returncode == 1
    assert 'link:' in result.stdout and CODENAME not in result.stdout


def test_unmerged_index_refuses_even_with_clean_file(repo):
    seed(repo, 'base.txt', b'clean')
    blob = git(repo, 'hash-object', '-w', '--stdin', input=b'clean').decode().strip()
    git(repo, 'update-index', '--index-info', input=('100644 '+blob+' 1\tconflict.txt\n100644 '+blob+' 2\tconflict.txt\n').encode())
    (repo/'clean.txt').write_text('clean')
    git(repo, 'add', 'clean.txt')
    assert run(repo).returncode == 2
