"""Install the deny-floor gate for Claude Code (the orchestrator runs this; seats do not).

    python scripts/install_deny_gate.py [target-folder] --ref <sha> [--only deny_gate.py]...
    python scripts/install_deny_gate.py [target-folder] --record-only
                                        [--ref-gate <sha>] [--ref-floor <sha>]

Installs liteharness/deny_gate.py and liteharness/deny_floor.py, byte for byte,
from the commit named by --ref (read with `git show <sha>:liteharness/<file>`,
never from the working tree) into ~/.claude/hooks/deny-floor/ (or the folder
given), verifies each copy, and prints the settings.json entry to add and the
one to remove it again. There is no implicit HEAD: an install names its commit.

--only (repeatable) installs just that file; the other file and its record
entry are left alone. The default is both.

INSTALLED.json in the target records, per file, the full commit sha, the git
blob id and the time of the install. An --only install updates only its own
entry. tests/test_deny_floor.py compares each installed file with the commit
its entry names, and reports whether that commit is an ancestor of the
checkout under test.

--record-only installs nothing. For each file given a commit (--ref-gate,
--ref-floor) it checks the already-installed bytes equal that commit's bytes
and writes the entry; any mismatch refuses and writes nothing. It is how a pair
installed before the record existed gets one.

Refused, with nothing written: no --ref, a ref that is not a commit, a corrupt
INSTALLED.json, and uncommitted edits to a file being installed. Reading from a
commit already makes the working tree irrelevant to the bytes; the dirty check
catches the "I meant my edit" mistake (the 2026-09-26 incident shipped a
mid-edit worktree). It covers only the files being installed, so a work in
progress in the other file does not block a per-file install.

Why a folder outside this repo: this checkout is a working tree people switch
branches in, and a missing gate file makes python exit 2, which Claude Code
treats as BLOCK for every matched call on every seat. The installed pair
cannot vanish on a branch switch; tests/test_deny_floor.py fails when it
drifts from its recorded commit.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FILES = ("deny_gate.py", "deny_floor.py")
RECORD = "INSTALLED.json"
DEFAULT_TARGET = Path.home() / ".claude" / "hooks" / "deny-floor"
MATCHER = "Bash|PowerShell|mcp__.*|Write|Edit|MultiEdit|NotebookEdit"


def _git(repo, *args: str) -> subprocess.CompletedProcess:
    # Bytes in, bytes out: no text decoding, no newline translation. GIT_* from a
    # parent hook would point git at the wrong repo.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)


def _resolve(repo, ref: str) -> str:
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if done.returncode != 0:
        raise SystemExit(f"refusing: {ref!r} is not a commit in {repo}. Nothing was written.")
    return done.stdout.decode().strip()


def _commit_bytes(repo, sha: str, name: str) -> bytes:
    done = _git(repo, "show", f"{sha}:liteharness/{name}")
    if done.returncode != 0:
        raise SystemExit(f"refusing: {sha} has no liteharness/{name}. Nothing was written.")
    return done.stdout


def _blob_id(data: bytes) -> str:
    """The git blob id of these bytes (what `git hash-object` prints), without git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _load_record(target: Path) -> dict:
    path = target / RECORD
    if not path.exists():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("not a JSON object")
    except ValueError as err:
        raise SystemExit(f"refusing: {path} is not a valid record ({err}). "
                         "Fix or delete it. Nothing was written.")
    return record


def _atomic_write(path: Path, data: bytes) -> None:
    """Temp file in the same folder, then replace: a reader never sees half a file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_record(target: Path, record: dict) -> None:
    _atomic_write(target / RECORD, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())


def _entry(sha: str, data: bytes) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return {"sha": sha, "blob": _blob_id(data), "installed_at": now.isoformat()}


def _chosen(only) -> tuple:
    names = tuple(n for n in FILES if not only or n in only)
    unknown = set(only or ()) - set(FILES)
    if unknown:
        raise SystemExit(f"refusing: --only takes {' or '.join(FILES)}, not {sorted(unknown)}")
    return names


def install(target: Path, ref: str = None, only=None, repo: Path = REPO) -> dict:
    target, repo = Path(target), Path(repo)
    if not ref:
        raise SystemExit("refusing: --ref <sha> is required; an install names its commit. "
                         "Nothing was written.")
    names = _chosen(only)
    sha = _resolve(repo, ref)
    dirty = _git(repo, "status", "--porcelain", "--",
                 *(f"liteharness/{n}" for n in names)).stdout.decode().strip()
    if dirty:
        raise SystemExit("refusing: uncommitted edits to a file being installed "
                         f"(commit or revert them, or install a different ref):\n{dirty}\n"
                         "Nothing was written.")
    wanted = {name: _commit_bytes(repo, sha, name) for name in names}
    record = _load_record(target)
    target.mkdir(parents=True, exist_ok=True)
    for name, data in wanted.items():
        _atomic_write(target / name, data)
        if (target / name).read_bytes() != data:
            raise SystemExit(f"copy of {name} does not match {sha}:liteharness/{name}")
        record[name] = _entry(sha, data)
        _write_record(target, record)  # per file, so the record never trails the bytes
    command = f"{Path(sys.executable).as_posix()} {(target / 'deny_gate.py').as_posix()}"
    return {"matcher": MATCHER,
            "hooks": [{"type": "command", "command": command, "timeout": 10}]}


def record_only(target: Path, refs: dict, repo: Path = REPO) -> None:
    """Write the entry for each {file: ref} after proving the installed bytes are that commit's."""
    target, repo = Path(target), Path(repo)
    if not refs:
        raise SystemExit("refusing: --record-only needs --ref-gate and/or --ref-floor.")
    record = _load_record(target)
    entries, mismatched = {}, []
    for name, ref in refs.items():
        sha = _resolve(repo, ref)
        installed = target / name
        if not installed.exists():
            mismatched.append(f"{name}: not installed in {target}")
            continue
        data = installed.read_bytes()
        if data != _commit_bytes(repo, sha, name):
            mismatched.append(f"{name}: installed bytes (blob {_blob_id(data)}) "
                              f"are not {sha}:liteharness/{name}")
            continue
        entries[name] = _entry(sha, data)
    if mismatched:
        raise SystemExit("refusing to record, nothing was written:\n  " + "\n  ".join(mismatched))
    record.update(entries)
    _write_record(target, record)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install the deny-floor gate from a commit.")
    parser.add_argument("target", nargs="?", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--ref", help="commit to install from (required unless --record-only)")
    parser.add_argument("--only", action="append", choices=FILES,
                        help="install just this file; repeatable; default both")
    parser.add_argument("--record-only", action="store_true",
                        help="install nothing; record the commit each installed file equals")
    parser.add_argument("--ref-gate", help="with --record-only: commit deny_gate.py came from")
    parser.add_argument("--ref-floor", help="with --record-only: commit deny_floor.py came from")
    parser.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)  # test seam
    args = parser.parse_args(argv)

    if args.record_only:
        if args.ref or args.only:
            parser.error("--record-only takes --ref-gate/--ref-floor, not --ref or --only")
        refs = {n: r for n, r in (("deny_gate.py", args.ref_gate),
                                  ("deny_floor.py", args.ref_floor)) if r}
        record_only(args.target, refs, repo=args.repo)
        print(f"recorded {', '.join(refs)} in {args.target / RECORD}")
        return 0
    if args.ref_gate or args.ref_floor:
        parser.error("--ref-gate/--ref-floor only go with --record-only")
    entry = install(args.target, ref=args.ref, only=args.only, repo=args.repo)
    print(f"installed {', '.join(_chosen(args.only))} from {_resolve(args.repo, args.ref)} "
          f"-> {args.target}")
    print("ADD to hooks.PreToolUse in ~/.claude/settings.json (synchronous):")
    print(json.dumps(entry, indent=2))
    print("REMOVE: delete that one PreToolUse entry (matcher "
          f"{MATCHER!r}); the files in {args.target} are then inert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
