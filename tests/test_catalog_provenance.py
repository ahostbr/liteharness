"""PROVENANCE.json must describe the catalog that ships, on every copy of it.

A hand-vendored file (T916-G, 2e27e6e) changed the catalog without a sync, so the stamp's
hash described content that no longer existed. Then ed102a9 went RED on a Windows checkout,
because the hash read raw working-tree bytes and that checkout held CRLF where git holds LF.
"""
import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "liteharness" / "catalog"

def _read_projection_functions():
    """Test only pure readers; never import or execute the catalog sync script.

    No imports, module-level setup, sync/copy/delete routines or main entry point
    are compiled. Constants are literal data, except the explicitly checked
    frozenset constructor used for the public classification set.
    """
    tree = ast.parse((ROOT / 'scripts/sync_catalog.py').read_text(encoding='utf-8'))
    constants = {'UNHASHED', 'PUBLIC_SKILLS', 'PRIVATE_SKILLS', 'KEEP_FROM_CATALOG'}
    readers = {'is_runtime_catalog_path', 'hashed_files', 'short_sha',
               'gate_skill_classification'}
    namespace = {'Path': Path, 'hashlib': hashlib}
    functions = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in readers:
            functions.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if not isinstance(target, ast.Name) or target.id not in constants:
                continue
            value = node.value
            if target.id == 'PUBLIC_SKILLS':
                assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                assert value.func.id == 'frozenset' and len(value.args) == 1
                assert not value.keywords
                namespace[target.id] = frozenset(ast.literal_eval(value.args[0]))
            else:
                namespace[target.id] = ast.literal_eval(value)
    assert constants <= namespace.keys()
    assert {node.name for node in functions} == readers
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 '<catalog read-only projection>', 'exec'), namespace)
    return SimpleNamespace(**namespace)


projection = _read_projection_functions()


def _release_catalog(destination):
    """Checkouts validate tracked payload; extracted distributions validate all files.

    Tool-owned untracked locks are not release inputs. Do not delete them or
    broadly exclude *.lock: a tracked lock-named resource remains content.
    """
    if not (ROOT / '.git').exists():
        return CATALOG
    paths = subprocess.check_output(
        ['git', 'ls-files', '-z', '--', 'liteharness/catalog'], cwd=ROOT,
    ).decode('utf-8').split('\0')
    for name in filter(None, paths):
        relative = Path(name).relative_to('liteharness/catalog')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return destination


def test_provenance_hash_and_count_match_the_catalog(tmp_path):
    catalog = _release_catalog(tmp_path / 'tracked-catalog')
    stamp = json.loads((catalog / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert stamp["hash"] == projection.short_sha(catalog), "stale release stamp"
    assert stamp["file_count"] == len(projection.hashed_files(catalog))
    # A partial stamp must name exactly the skills the script holds back from the source.
    assert stamp.get("kept_from_catalog") == projection.KEEP_FROM_CATALOG


def _payload_digest(catalog):
    """Independent v1 payload oracle, including catalog module/resources."""
    paths = sorted(p.relative_to(catalog).as_posix() for p in catalog.rglob('*')
                   if p.is_file() and p.relative_to(catalog).as_posix() != 'PROVENANCE.json'
                   and not projection.is_runtime_catalog_path(p.relative_to(catalog)))
    digest = hashlib.sha256()
    for relative in paths:
        content = (catalog / relative).read_bytes()
        if b'\0' not in content:
            content = content.replace(b'\r\n', b'\n')
        digest.update(relative.encode('utf-8') + b'\0' + hashlib.sha256(content).digest())
    return paths, digest.hexdigest()


def test_reconciliation_payload_inventory_and_digest(tmp_path):
    catalog = _release_catalog(tmp_path / 'tracked-catalog')
    stamp = json.loads((catalog / 'PROVENANCE.json').read_text(encoding='utf-8'))
    current = stamp['reconciliation']
    paths, digest = _payload_digest(catalog)
    assert current['payload_algorithm'] == 'catalog-payload-sha256-v1'
    assert current['payload_paths'] == paths
    assert current['payload_file_count'] == len(paths)
    assert current['payload_sha256'] == digest
    assert '__init__.py' in paths and 'PROVENANCE.json' not in paths
    assert 'historical' in stamp['upstream_provenance_status']
    assert current['published_restoration_baseline'] == 'fba17c7c40a83584283418c6499467a1a8805196'
    assert current['initial_target_commit'] == '4e57b5f3e851446965f5798431d23f77005bd778'
    assert current['plugin_public_baseline'] == '02f880cfe474e170e511210fb2cd6b8b1dd79f0e'
    assert current['package_version'] == '0.4.6'
    assert current['tracked_runtime_exclusions'] == ['skills/ls-conversation-lookup/.last_indexed']
    for name, expected in current['policy_sha256_lf'].items():
        content = (ROOT / 'liteharness' / name).read_bytes().replace(b'\r\n', b'\n')
        assert hashlib.sha256(content).hexdigest() == expected


def test_payload_digest_covers_init_but_avoids_stamp_cycle(tmp_path):
    root = _tree(tmp_path / 'catalog')
    (root / '__init__.py').write_bytes(b'fixture module\n')
    paths, before = _payload_digest(root)
    (root / 'PROVENANCE.json').write_text('{"fixture": 1}', encoding='utf-8')
    assert _payload_digest(root) == (paths, before)
    (root / '__init__.py').write_bytes(b'changed module\n')
    assert _payload_digest(root)[1] != before


def _tree(root: Path) -> Path:
    (root / "skills" / "a").mkdir(parents=True)
    (root / "skills" / "a" / "SKILL.md").write_bytes(b"line one\nline two\n")
    (root / "skills" / "a" / "ring.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\r\n")
    return root


def test_crlf_checkout_hashes_the_same(tmp_path):
    lf = _tree(tmp_path / "lf")
    crlf = _tree(tmp_path / "crlf")
    (crlf / "skills" / "a" / "SKILL.md").write_bytes(b"line one\r\nline two\r\n")
    assert projection.short_sha(crlf) == projection.short_sha(lf)


def test_eol_crlf_ps1_hashes_the_same_in_either_spelling(tmp_path):
    # .ps1/.bat are eol=crlf in .gitattributes, so git checks them out CRLF, while a
    # non-git copy may hold LF. Both spellings must stamp alike.
    a = _tree(tmp_path / "a")
    b = _tree(tmp_path / "b")
    (a / "skills" / "a" / "run.ps1").write_bytes(b"Write-Host hi\r\nexit 0\r\n")
    (b / "skills" / "a" / "run.ps1").write_bytes(b"Write-Host hi\nexit 0\n")
    assert projection.short_sha(a) == projection.short_sha(b)


def test_binary_bytes_are_not_normalised(tmp_path):
    # Text vs binary is a NUL-byte sniff, like git's text=auto. ring.png holds \r\n bytes
    # AND a NUL, so changing its \r\n must move the hash.
    a = _tree(tmp_path / "a")
    b = _tree(tmp_path / "b")
    (b / "skills" / "a" / "ring.png").write_bytes(b"\x89PNG\n\x1a\n\0\n")
    assert projection.short_sha(a) != projection.short_sha(b)


def test_rename_changes_the_hash(tmp_path):
    a = _tree(tmp_path / "a")
    b = _tree(tmp_path / "b")
    shutil.move(b / "skills" / "a", b / "skills" / "b")
    assert projection.short_sha(a) != projection.short_sha(b)


def test_one_byte_change_moves_the_hash(tmp_path):
    a = _tree(tmp_path / "a")
    before = projection.short_sha(a)
    (a / "skills" / "a" / "SKILL.md").write_bytes(b"line one\nline 2wo\n")
    assert projection.short_sha(a) != before


def test_empty_or_missing_path_refuses(tmp_path):
    for p in (tmp_path, tmp_path / "missing"):
        with pytest.raises(SystemExit):
            projection.short_sha(p)


def test_runtime_projection_preserves_release_resources(tmp_path):
    root = _tree(tmp_path / 'catalog')
    skill = root / 'skills/a'
    positive = {'config.json': b'{"fixture": true}\n', 'font.ttf': b'font\0data',
                'asset.lock': b'legitimate lock-named asset',
                'fixture.db-wal.example': b'not a runtime sidecar',
                'caf\u00e9.json': b'ordinary Unicode asset'}
    for name, data in positive.items():
        (skill / name).write_bytes(data)
    before = projection.short_sha(root)
    before_files = set(projection.hashed_files(root))
    databases = ('convo_index.db', 'fixture.sqlite', 'fixture.sqlite3')
    runtime_names = ('.last_indexed', 'index.lock', 'loose.pyc', *databases,
                     *(database + suffix for database in databases
                       for suffix in ('-wal', '-shm', '-journal')))
    # Successor contract: UPPER.DB is runtime, not the old positive whose
    # Windows artifact RED is preserved in the release evidence receipts.
    runtime_names = (*runtime_names, 'UPPER.DB', *(name.upper() for name in runtime_names))
    for directory in (root, skill):
        for name in runtime_names:
            (directory / name).write_bytes(b'synthetic runtime state')
        for cache_name in ('__pycache__', '__PYCACHE__'):
            cache = directory / cache_name
            cache.mkdir(exist_ok=True)
            (cache / 'fixture.pyc').write_bytes(b'synthetic cache')
    assert set(projection.hashed_files(root)) == before_files
    assert projection.short_sha(root) == before
    for name in positive:
        path = skill / name
        previous = path.read_bytes()
        path.write_bytes(previous + b'changed')
        assert projection.short_sha(root) != before, f'{name} disappeared from projection'
        path.write_bytes(previous)


@pytest.mark.parametrize('name', ['index.loc\u212a', 'INDEX.LOC\u212a', '\u0130ndex.lock',
                                '\u0131ndex.lock', 'fixture.\u017fqlite'])
def test_unicode_near_miss_is_content_without_reserved_name_collision(tmp_path, name):
    # Separate filesystem matrices: Windows may alias these names with runtime
    # spellings. Do not overwrite a Unicode positive with a reserved-name write.
    root = _tree(tmp_path / 'catalog')
    target = root / 'skills/a' / name
    assert not target.exists()
    before = projection.short_sha(root)
    target.write_bytes(b'ordinary Unicode near miss')
    assert target.relative_to(root).as_posix() in projection.hashed_files(root)
    assert projection.short_sha(root) != before


@pytest.mark.parametrize('relative', ['.last_indexed', 'INDEX.LOCK', 'UPPER.DB',
                                     'deep/fixture.SQLITE3-WaL', '__PYCACHE__/ordinary.py'])
def test_runtime_only_catalog_refuses_empty_projection(tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'synthetic runtime state')
    with pytest.raises(SystemExit):
        projection.short_sha(tmp_path)


def test_compatibility_alias_is_classified_but_unknown_skill_still_refuses(tmp_path):
    for name in ('ls-theater', 'ls-mockup'):
        (tmp_path / 'skills' / name).mkdir(parents=True)
    projection.gate_skill_classification(tmp_path)
    (tmp_path / 'skills/unclassified-fixture').mkdir()
    with pytest.raises(SystemExit, match='unclassified'):
        projection.gate_skill_classification(tmp_path)
