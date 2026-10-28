"""Exercise sync only against disposable fixtures, never the shipped catalog."""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sync_module(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('isolated_sync', ROOT / 'scripts/sync_catalog.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'THIS_PKG_ROOT', tmp_path)
    monkeypatch.setattr(module, 'DEST_ROOT', tmp_path / 'current/catalog')
    monkeypatch.setattr(module, 'PUBLIC_SKILLS', frozenset({'held', 'ordinary'}))
    monkeypatch.setattr(module, 'KEEP_FROM_CATALOG', {'held': 'newer OSS fixture'})
    return module


def put(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def trees(sync_module, tmp_path):
    source = tmp_path / 'source'
    current = sync_module.DEST_ROOT
    for root in (source, current):
        put(root, 'skills/held/SKILL.md', b'old source\n')
        put(root, 'skills/ordinary/SKILL.md', b'unchanged\n')
    put(current, 'skills/held/SKILL.md', b'newer OSS\n')
    put(current, 'skills/removed/SKILL.md', b'only current\n')
    put(source, 'commands/new.md', b'new command\n')
    put(source, 'hooks/binary.bin', b'\x00\xff')
    put(current, '__init__.py', sync_module.CATALOG_INIT_TEMPLATE.read_bytes())
    return source, current


@pytest.mark.parametrize('keep', [True, False])
def test_dry_run_isolated_and_reports_full_changes(sync_module, trees, monkeypatch, keep):
    source, current = trees
    if not keep:
        monkeypatch.setattr(sync_module, 'KEEP_FROM_CATALOG', {})
    before = hashes(source), hashes(current)
    changes = sync_module.dry_run(source, current, clean=True)
    assert (hashes(source), hashes(current)) == before
    rows = {row['path']: row for row in changes}
    assert ('skills/held/SKILL.md' in rows) is not keep
    if not keep:
        assert rows['skills/held/SKILL.md']['before'] == 'newer OSS\n'
        assert rows['skills/held/SKILL.md']['after'] == 'old source\n'
    assert rows['commands/new.md'] == {
        'path': 'commands/new.md', 'before': None, 'after': 'new command\n', 'verdict': 'added'}
    assert rows['skills/removed/SKILL.md']['after'] is None
    assert rows['skills/removed/SKILL.md']['verdict'] == 'removed'
    assert rows['hooks/binary.bin']['after'] == (
        '[binary: 2 bytes; sha256=' + hashlib.sha256(b'\x00\xff').hexdigest() + ']')
    assert 'skills/ordinary/SKILL.md' not in rows
    assert not list(sync_module.THIS_PKG_ROOT.glob('.catalog-dry-run-*'))


def test_dry_run_failure_leaves_both_trees_untouched(sync_module, trees, monkeypatch):
    source, current = trees
    before = hashes(source), hashes(current)

    def fail(*args, **kwargs):
        raise SystemExit('fixture import failed')

    monkeypatch.setattr(sync_module, 'assert_catalog_imports', fail)
    with pytest.raises(SystemExit, match='fixture import failed'):
        sync_module.dry_run(source, current, clean=True)
    assert (hashes(source), hashes(current)) == before
    assert not list(sync_module.THIS_PKG_ROOT.glob('.catalog-dry-run-*'))


@pytest.mark.parametrize('platform, expected', [('win32', 0x08000000), ('linux', 0)])
def test_import_child_creationflags(sync_module, monkeypatch, tmp_path, platform, expected):
    calls = []
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='fixture import OK', stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    sync_module.assert_catalog_imports(tmp_path)
    assert calls[0][1]['creationflags'] == expected
    assert '-B' in calls[0][0][0]


def test_import_gate_checks_selected_catalog_not_checkout(sync_module, tmp_path):
    broken = tmp_path / 'broken'
    put(broken, '__init__.py', b'"""Missing locator functions."""\n')
    with pytest.raises(SystemExit, match='does not import'):
        sync_module.assert_catalog_imports(ROOT, broken)
    assert not (broken / '__pycache__').exists()


def test_dry_run_cli_writes_json(sync_module, trees, monkeypatch, tmp_path):
    source, current = trees
    output = tmp_path / 'diff.json'
    before = hashes(source), hashes(current)
    monkeypatch.setattr(sys, 'argv', ['sync_catalog.py', '--source', str(source),
                                    '--dry-run', '--diff-output', str(output)])
    sync_module.main()
    assert any(row['path'] == 'commands/new.md' for row in json.loads(output.read_text(encoding='utf-8')))
    assert (hashes(source), hashes(current)) == before


@pytest.mark.parametrize('target', ['source', 'catalog', 'dest'])
def test_cli_refuses_diff_output_in_input_trees(sync_module, trees, monkeypatch, tmp_path, target):
    source, current = trees
    alternate = tmp_path / 'alternate'
    output = {'source': source, 'catalog': current, 'dest': alternate}[target] / 'diff.json'
    before = hashes(source), hashes(current)
    monkeypatch.setattr(sys, 'argv', ['sync_catalog.py', '--source', str(source),
                                    '--dest', str(alternate), '--dry-run', '--diff-output', str(output)])
    with pytest.raises(SystemExit):
        sync_module.main()
    assert not output.exists()
    assert (hashes(source), hashes(current)) == before


def test_cli_refuses_diff_output_without_dry_run(sync_module, trees, monkeypatch, tmp_path):
    source, current = trees
    before = hashes(source), hashes(current)
    monkeypatch.setattr(sys, 'argv', ['sync_catalog.py', '--source', str(source),
                                    '--diff-output', str(tmp_path / 'diff.json')])
    with pytest.raises(SystemExit):
        sync_module.main()
    assert (hashes(source), hashes(current)) == before


@pytest.mark.parametrize('target', ['source', 'catalog'])
def test_dry_run_refuses_temporary_workspace_inside_inputs(sync_module, trees, monkeypatch, target):
    source, current = trees
    before = hashes(source), hashes(current)
    monkeypatch.setattr(sync_module, 'THIS_PKG_ROOT', source if target == 'source' else current)
    with pytest.raises(SystemExit, match='outside both input trees'):
        sync_module.dry_run(source, current, clean=True)
    assert (hashes(source), hashes(current)) == before


@pytest.mark.parametrize('clean', [True, False])
def test_dry_run_honors_clean_option(sync_module, trees, clean):
    source, current = trees
    put(current, 'local-resource.txt', b'current catalog root resource')
    before = hashes(source), hashes(current)
    rows = {row['path']: row for row in sync_module.dry_run(source, current, clean=clean)}
    assert ('local-resource.txt' in rows) is clean
    assert (hashes(source), hashes(current)) == before
