"""PROVENANCE.json must describe the catalog that ships, on every copy of it.

A hand-vendored file (T916-G, 2e27e6e) changed the catalog without a sync, so the stamp's
hash described content that no longer existed. Then ed102a9 went RED on a Windows checkout,
because the hash read raw working-tree bytes and that checkout held CRLF where git holds LF.
"""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "liteharness" / "catalog"

_spec = importlib.util.spec_from_file_location("sync_catalog", ROOT / "scripts" / "sync_catalog.py")
sync_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_catalog)


def test_provenance_hash_and_count_match_the_catalog():
    stamp = json.loads((CATALOG / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert stamp["hash"] == sync_catalog.short_sha(CATALOG), "stale stamp: re-run scripts/sync_catalog.py"
    assert stamp["file_count"] == len(sync_catalog.hashed_files(CATALOG))
    # A partial stamp must name exactly the skills the script holds back from the source.
    assert stamp.get("kept_from_catalog") == sync_catalog.KEEP_FROM_CATALOG


def _tree(root: Path) -> Path:
    (root / "skills" / "a").mkdir(parents=True)
    (root / "skills" / "a" / "SKILL.md").write_bytes(b"line one\nline two\n")
    (root / "skills" / "a" / "ring.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\r\n")
    return root


def test_crlf_checkout_hashes_the_same(tmp_path):
    lf = _tree(tmp_path / "lf")
    crlf = _tree(tmp_path / "crlf")
    (crlf / "skills" / "a" / "SKILL.md").write_bytes(b"line one\r\nline two\r\n")
    assert sync_catalog.short_sha(crlf) == sync_catalog.short_sha(lf)


def test_eol_crlf_ps1_hashes_the_same_in_either_spelling(tmp_path):
    # .ps1/.bat are eol=crlf in .gitattributes, so git checks them out CRLF, while a
    # non-git copy may hold LF. Both spellings must stamp alike.
    a = _tree(tmp_path / "a")
    b = _tree(tmp_path / "b")
    (a / "skills" / "a" / "run.ps1").write_bytes(b"Write-Host hi\r\nexit 0\r\n")
    (b / "skills" / "a" / "run.ps1").write_bytes(b"Write-Host hi\nexit 0\n")
    assert sync_catalog.short_sha(a) == sync_catalog.short_sha(b)


def test_binary_bytes_are_not_normalised(tmp_path):
    # Text vs binary is a NUL-byte sniff, like git's text=auto. ring.png holds \r\n bytes
    # AND a NUL, so changing its \r\n must move the hash.
    a = _tree(tmp_path / "a")
    b = _tree(tmp_path / "b")
    (b / "skills" / "a" / "ring.png").write_bytes(b"\x89PNG\n\x1a\n\0\n")
    assert sync_catalog.short_sha(a) != sync_catalog.short_sha(b)


def test_rename_changes_the_hash(tmp_path):
    a = _tree(tmp_path / "a")
    b = _tree(tmp_path / "b")
    shutil.move(b / "skills" / "a", b / "skills" / "b")
    assert sync_catalog.short_sha(a) != sync_catalog.short_sha(b)


def test_one_byte_change_moves_the_hash(tmp_path):
    a = _tree(tmp_path / "a")
    before = sync_catalog.short_sha(a)
    (a / "skills" / "a" / "SKILL.md").write_bytes(b"line one\nline 2wo\n")
    assert sync_catalog.short_sha(a) != before


def test_empty_or_missing_path_refuses(tmp_path):
    for p in (tmp_path, tmp_path / "missing"):
        with pytest.raises(SystemExit):
            sync_catalog.short_sha(p)


def test_runtime_projection_preserves_release_resources(tmp_path):
    root = _tree(tmp_path / 'catalog')
    skill = root / 'skills/a'
    positive = {'config.json': b'{"fixture": true}\n', 'font.ttf': b'font\0data',
                'asset.lock': b'legitimate lock-named asset',
                'fixture.db-wal.example': b'not a runtime sidecar',
                'caf\u00e9.json': b'ordinary Unicode asset'}
    for name, data in positive.items():
        (skill / name).write_bytes(data)
    before = sync_catalog.short_sha(root)
    before_files = set(sync_catalog.hashed_files(root))
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
    assert set(sync_catalog.hashed_files(root)) == before_files
    assert sync_catalog.short_sha(root) == before
    for name in positive:
        path = skill / name
        previous = path.read_bytes()
        path.write_bytes(previous + b'changed')
        assert sync_catalog.short_sha(root) != before, f'{name} disappeared from projection'
        path.write_bytes(previous)


@pytest.mark.parametrize('name', ['index.loc\u212a', 'INDEX.LOC\u212a', '\u0130ndex.lock',
                                '\u0131ndex.lock', 'fixture.\u017fqlite'])
def test_unicode_near_miss_is_content_without_reserved_name_collision(tmp_path, name):
    # Separate filesystem matrices: Windows may alias these names with runtime
    # spellings. Do not overwrite a Unicode positive with a reserved-name write.
    root = _tree(tmp_path / 'catalog')
    target = root / 'skills/a' / name
    assert not target.exists()
    before = sync_catalog.short_sha(root)
    target.write_bytes(b'ordinary Unicode near miss')
    assert target.relative_to(root).as_posix() in sync_catalog.hashed_files(root)
    assert sync_catalog.short_sha(root) != before


@pytest.mark.parametrize('relative', ['.last_indexed', 'INDEX.LOCK', 'UPPER.DB',
                                     'deep/fixture.SQLITE3-WaL', '__PYCACHE__/ordinary.py'])
def test_runtime_only_catalog_refuses_empty_projection(tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'synthetic runtime state')
    with pytest.raises(SystemExit):
        sync_catalog.short_sha(tmp_path)


def test_compatibility_alias_is_classified_but_unknown_skill_still_refuses(tmp_path):
    for name in ('ls-theater', 'ls-mockup'):
        (tmp_path / 'skills' / name).mkdir(parents=True)
    sync_catalog.gate_skill_classification(tmp_path)
    (tmp_path / 'skills/unclassified-fixture').mkdir()
    with pytest.raises(SystemExit, match='unclassified'):
        sync_catalog.gate_skill_classification(tmp_path)
