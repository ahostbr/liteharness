"""Frozen release artifact proof, separate from privacy/source eligibility.

Raw catalog equality includes files outside historical provenance hash domain.
This is not a universal future-input or cross-platform guarantee.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('release_sync', ROOT / 'scripts/sync_catalog.py')
sync_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_catalog)


def _relative(name):
    path = PurePosixPath(name)
    assert name and not path.is_absolute() and '..' not in path.parts and '\\' not in name
    assert path.as_posix() == name and ':' not in name, 'noncanonical archive path'
    return path


def _files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def _assert_eligible_catalog(root):
    # Unsupported root additions FAIL, never intersect them out of hash/raw proof.
    subtrees = {'skills', 'agents', 'commands', 'hooks'}
    unsupported = []
    for entry in root.iterdir():
        if entry.is_symlink():
            unsupported.append(entry.name + ' (symlink)')
        elif sync_catalog.is_runtime_catalog_path(entry.name):
            continue
        elif entry.name in subtrees and entry.is_dir():
            continue
        elif entry.name in {'__init__.py', 'PROVENANCE.json'} and entry.is_file():
            continue
        else:
            unsupported.append(entry.name + (' (directory)' if entry.is_dir() else ' (file)'))
    assert not unsupported, 'unsupported nonruntime catalog roots: ' + repr(sorted(unsupported))


def test_unsupported_root_positive_refuses_eligibility(tmp_path):
    target = tmp_path / 'ordinary-root.json'
    target.write_bytes(b'ordinary content, unsupported root')
    assert target.name in sync_catalog.hashed_files(tmp_path)
    with pytest.raises(AssertionError, match='ordinary-root.json'):
        _assert_eligible_catalog(tmp_path)


@pytest.mark.parametrize('name,is_directory', [('unexpected-empty', True), ('skills', False),
                                               ('PROVENANCE.json', True)])
def test_unsupported_root_entry_type_refuses_eligibility(tmp_path, name, is_directory):
    target = tmp_path / name
    if is_directory:
        target.mkdir()
    else:
        target.write_bytes(b'not a directory subtree')
    with pytest.raises(AssertionError, match=name):
        _assert_eligible_catalog(tmp_path)


def test_filesystem_alias_probe_before_reserved_writes(tmp_path):
    # Exact filesystem lookup/samefile/bytes, not normcase inference. Never write
    # the reserved counterpart if lookup already resolves the Unicode positive.
    receipts = []
    for index, (positive, reserved) in enumerate((('index.loc\u212a', 'index.lock'),
                                                ('INDEX.LOC\u212a', 'INDEX.LOCK'))):
        directory = tmp_path / str(index)
        directory.mkdir()
        unicode_path = directory / positive
        reserved_path = directory / reserved
        unicode_path.write_bytes(b'Unicode positive harbor')
        exists = reserved_path.exists()
        receipt = {'positive': positive, 'reserved': reserved, 'reserved_lookup_exists': exists,
                   'entries': sorted(p.name for p in directory.iterdir()),
                   'positive_sha256': hashlib.sha256(unicode_path.read_bytes()).hexdigest()}
        if exists:
            receipt['samefile'] = os.path.samefile(unicode_path, reserved_path)
            receipt['reserved_lookup_sha256'] = hashlib.sha256(reserved_path.read_bytes()).hexdigest()
        receipts.append(receipt)
        assert unicode_path.read_bytes() == b'Unicode positive harbor'
    print('FILESYSTEM_ALIAS_RECEIPTS=' + json.dumps(receipts, sort_keys=True))


def _build(source, output, kind):
    result = subprocess.run(
        [sys.executable, '-m', 'build', '--no-isolation', '--' + kind, '--outdir', str(output)],
        cwd=source, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    candidates = list(output.glob('*.whl' if kind == 'wheel' else '*.tar.gz'))
    assert len(candidates) == 1
    return candidates[0]


def _extract_catalog(artifact, destination):
    raw = {}  # Archive spellings/bytes are authoritative BEFORE host extraction.
    collisions = []
    if artifact.suffix == '.whl':
        with zipfile.ZipFile(artifact) as archive:
            assert len(archive.namelist()) == len(set(archive.namelist())), 'duplicate wheel members'
            for member in archive.infolist():
                if member.is_dir():
                    continue
                _relative(member.filename)
                if not member.filename.startswith('liteharness/catalog/'):
                    continue
                mode = member.external_attr >> 16
                assert not stat.S_ISLNK(mode), 'symlink wheel catalog member'
                assert not stat.S_IFMT(mode) or stat.S_ISREG(mode), 'nonregular wheel member'
                relative = member.filename[len('liteharness/catalog/'):]
                _relative(relative)
                raw[relative] = archive.read(member)
    else:
        prefix = artifact.name.removesuffix('.tar.gz') + '/liteharness/catalog/'
        with tarfile.open(artifact, 'r:gz') as archive:
            members = archive.getmembers()
            assert len(members) == len({member.name for member in members}), 'duplicate sdist members'
            for member in members:
                _relative(member.name)
                assert member.isdir() or member.isfile(), 'nonregular sdist member'
                if member.isdir() or not member.name.startswith(prefix):
                    continue
                relative = member.name[len(prefix):]
                _relative(relative)
                raw[relative] = archive.extractfile(member).read()
    for relative, data in raw.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            collisions.append(relative)
            continue  # Never overwrite an archive member resolved by another spelling.
        target.write_bytes(data)
    return raw, _files(destination), collisions


def _unpack_source(artifact, destination):
    with tarfile.open(artifact, 'r:gz') as archive:
        members = archive.getmembers()
        assert len(members) == len({member.name for member in members})
        for member in members:
            _relative(member.name)
            assert member.isdir() or member.isfile(), 'unsafe sdist source member'
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                assert not target.exists(), 'sdist unpack prewrite namespace collision: ' + member.name
                target.write_bytes(archive.extractfile(member).read())
    return destination / artifact.name.removesuffix('.tar.gz')


@pytest.mark.parametrize('matrix', ['runtime', 'unicode-positive'])
def test_built_distributions_exclude_catalog_runtime_state(tmp_path, matrix):
    assert importlib.util.find_spec('build') is not None, 'release validation requires python-build'
    original_package = _files(ROOT / 'liteharness')
    original_metadata = {name: (ROOT / name).read_bytes() for name in ('pyproject.toml', 'README.md', 'LICENSE')}
    source = tmp_path / 'source'
    source.mkdir()
    for name in original_metadata:
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(ROOT / 'liteharness', source / 'liteharness')
    catalog = source / 'liteharness/catalog'
    skill = catalog / 'skills/ls-conversation-lookup'
    assert (skill / '.last_indexed').is_file(), 'retain real tracked marker in build input'
    databases = ('convo_index.db', 'fixture.sqlite', 'fixture.sqlite3')
    names = ('.last_indexed', 'index.lock', 'loose.pyc', *databases,
             *(database + suffix for database in databases for suffix in ('-wal', '-shm', '-journal')))
    runtime_names = (*names, 'UPPER.DB', 'Fixture.SQLITE3-WaL', *(name.upper() for name in names))
    positive = {'release-fixture.json': b'{"fixture": true}\n',
                'release-fixture.png': b'PNG\0fixture', 'release-fixture.ttf': b'font\0fixture',
                'asset.lock': b'legitimate asset', 'fixture.db-wal.example': b'ordinary asset',
                'index.loc\u212a': b'Unicode near miss', 'INDEX.LOC\u212a': b'Unicode near miss',
                '\u0130ndex.lock': b'Unicode near miss', '\u0131ndex.lock': b'Unicode near miss',
                'fixture.\u017fqlite': b'Unicode near miss', 'caf\u00e9.json': b'ordinary Unicode asset'}
    if matrix == 'runtime':
        for directory in (catalog, skill, skill / 'deep'):
            directory.mkdir(exist_ok=True)
            for name in runtime_names:
                target = directory / name
                # Preserve tracked marker/case-equivalent existing runtime paths.
                if not target.exists():
                    target.write_bytes(b'synthetic runtime state\n')
            for cache_name in ('__pycache__', '__PYCACHE__'):
                cache = directory / cache_name
                cache.mkdir(exist_ok=True)
                (cache / 'fixture.pyc').write_bytes(b'synthetic cache\n')
                (cache / 'ordinary.py').write_bytes(b'# runtime component, ordinary Python module\n')
    # Ordinary positives live within explicitly supported subtrees, never root.
    # Unicode matrix uses a separate fresh skill, avoiding reserved runtime writes.
    positive_skill = skill if matrix == 'runtime' else catalog / 'skills/unicode-positive-fixture'
    fixture_spellings = []
    for directory in (positive_skill, positive_skill / 'deep'):
        directory.mkdir(parents=True, exist_ok=True)
        fake_cache = directory / 'x__pycache__'
        fake_cache.mkdir(exist_ok=True)
        (fake_cache / 'ordinary.py').write_bytes(b'# legitimate near-miss component\n')
        for name, data in positive.items():
            if matrix == 'runtime' and any(ord(c) > 127 for c in name):
                continue  # Separate matrix verifies ALL Unicode positives, not excluded from expected.
            target = directory / name
            # Pre-write lookup prevents silent overwrite if a source namespace aliases.
            if target.exists():
                assert target.read_bytes() == data, 'fixture name aliases existing different bytes: ' + name
            else:
                target.write_bytes(data)
            realized = sorted(entry.name for entry in directory.iterdir()
                              if entry.is_file() and os.path.samefile(entry, target))
            fixture_spellings.append({'directory': directory.relative_to(catalog).as_posix(),
                                      'requested': name, 'realized': realized,
                                      'exact_spelling_present': name in realized})
    print('FIXTURE_SPELLINGS=' + json.dumps(fixture_spellings, sort_keys=True))
    _assert_eligible_catalog(catalog)
    before_catalog = _files(catalog)
    expected_raw = {relative: data for relative, data in before_catalog.items()
                    if not sync_catalog.is_runtime_catalog_path(relative)}
    direct_wheel = _build(source, tmp_path / 'direct-wheel', 'wheel')
    sdist = _build(source, tmp_path / 'sdist', 'sdist')
    rebuilt_source = _unpack_source(sdist, tmp_path / 'rebuilt-source')
    rebuilt_wheel = _build(rebuilt_source, tmp_path / 'rebuilt-wheel', 'wheel')
    receipts = []
    failures = []
    for number, artifact in enumerate((direct_wheel, sdist, rebuilt_wheel)):
        root = tmp_path / ('extracted-' + str(number))
        actual, extracted, extraction_collisions = _extract_catalog(artifact, root)
        forbidden = sorted(relative for relative in actual if sync_catalog.is_runtime_catalog_path(relative))
        missing = sorted(expected_raw.keys() - actual.keys())
        extra = sorted(actual.keys() - expected_raw.keys())
        changed = sorted(relative for relative in expected_raw.keys() & actual.keys()
                         if expected_raw[relative] != actual[relative])
        projected = sync_catalog.hashed_files(root)
        projected_source = sync_catalog.hashed_files(catalog)
        receipt = {'artifact': str(artifact), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                   'raw_count': len(actual), 'expected_raw_count': len(expected_raw),
                   'forbidden': forbidden, 'missing': missing, 'extra': extra, 'changed': changed,
                   'projected_sets_equal': set(projected) == set(projected_source),
                   'projected_count': len(projected), 'expected_projected_count': len(projected_source),
                   'hash': sync_catalog.short_sha(root), 'expected_hash': sync_catalog.short_sha(catalog)}
        receipt['raw_archive_equals_extracted'] = actual == extracted
        receipt['extraction_collisions'] = extraction_collisions
        receipt['raw_projected_sets_equal'] = {
            name for name in actual if PurePosixPath(name).name not in sync_catalog.UNHASHED
            and not sync_catalog.is_runtime_catalog_path(name)} == set(projected_source)
        receipt['unhashed_equal'] = {
            name: name in actual and name in expected_raw and actual[name] == expected_raw[name]
            for name in ('__init__.py', 'PROVENANCE.json')}
        receipts.append(receipt)
        if (forbidden or missing or extra or changed or not all(receipt['unhashed_equal'].values())
                or not receipt['raw_archive_equals_extracted'] or extraction_collisions
                or not receipt['raw_projected_sets_equal']
                or not receipt['projected_sets_equal']
                or receipt['projected_count'] != receipt['expected_projected_count']
                or receipt['hash'] != receipt['expected_hash']):
            failures.append(receipt)
    immutability = {
        'copied_catalog': _files(catalog) == before_catalog,
        'worktree_package': _files(ROOT / 'liteharness') == original_package,
        'worktree_metadata': {name: (ROOT / name).read_bytes() for name in original_metadata} == original_metadata,
    }
    print('CATALOG_ARTIFACT_RECEIPTS=' + json.dumps(
        {'matrix': matrix, 'artifacts': receipts, 'immutability': immutability}, sort_keys=True))
    assert all(immutability.values()), 'source mutated during builds: ' + repr(immutability)
    assert not failures, 'full raw catalog equality/runtime absence failed: ' + json.dumps(failures, sort_keys=True)
