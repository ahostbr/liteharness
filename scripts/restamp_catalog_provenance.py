"""Recompute the catalog PROVENANCE stamp the way tests/test_catalog_provenance.py checks it.

Edit catalog files, `git add` every changed or new one (the test reads the tracked set), then
run:  python scripts/restamp_catalog_provenance.py .
It rewrites only `hash`, `file_count` and the reconciliation payload fields of
liteharness/catalog/PROVENANCE.json; stage that file and commit it with the catalog change.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("sync_catalog", root / "scripts" / "sync_catalog.py")
sc = importlib.util.module_from_spec(spec)
sys.modules["sync_catalog"] = sc
spec.loader.exec_module(sc)

tmp = Path(tempfile.mkdtemp()) / "catalog"
names = subprocess.check_output(["git", "ls-files", "-z", "--", "liteharness/catalog"], cwd=root).decode().split("\0")
for name in filter(None, names):
    target = tmp / Path(name).relative_to("liteharness/catalog")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / name, target)

stamp_path = root / "liteharness" / "catalog" / "PROVENANCE.json"
stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
stamp["hash"] = sc.short_sha(tmp)
stamp["file_count"] = len(sc.hashed_files(tmp))

paths = sorted(p.relative_to(tmp).as_posix() for p in tmp.rglob("*")
               if p.is_file() and p.relative_to(tmp).as_posix() != "PROVENANCE.json"
               and not sc.is_runtime_catalog_path(p.relative_to(tmp)))
digest = hashlib.sha256()
for rel in paths:
    content = (tmp / rel).read_bytes()
    if b"\0" not in content:
        content = content.replace(b"\r\n", b"\n")
    digest.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(content).digest())
rec = stamp["reconciliation"]
rec["payload_paths"] = paths
rec["payload_file_count"] = len(paths)
rec["payload_sha256"] = digest.hexdigest()

raw = stamp_path.read_bytes()
newline = "\r\n" if b"\r\n" in raw else "\n"
text = json.dumps(stamp, indent=2, ensure_ascii=False) + "\n"
stamp_path.write_bytes(text.replace("\n", newline).encode("utf-8"))
print("hash", stamp["hash"], "files", stamp["file_count"], "payload", len(paths))
