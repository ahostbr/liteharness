#!/usr/bin/env python
"""Pre-commit PII / machine-path guard for the public liteharness-plugin.

Blocks a commit (or `--all` scan) that introduces the author's machine paths,
personal emails, Discord IDs, or private project codenames into tracked files.
This is the gate that makes the 2026-06 privacy scrub stick: even if the
/library catalog is regenerated, a username path can never be committed.

Does NOT block brand/identity that is intentionally public: the author's name
(Ryan / Ryan Devlin attribution), the Marlee Rose / Carly dedication, or public
product names (LiteSuite, LiteEditor, LiteSpeak, ...). Those are the brand.

Usage:
  python scripts/check_pii.py          # scan STAGED files (pre-commit)
  python scripts/check_pii.py --all    # scan all tracked text files (CI/manual)
"""
import sys, subprocess, re
from pathlib import Path

# A real user home — capture the user segment so we can allow documented
# placeholders (TestUser, <username>, ...) while blocking actual people's homes.
USERNAME_RE = re.compile(r"(?:C:[\\/]Users[\\/]|/c/Users/)([^\\/\s\"'<>]+)", re.I)
PLACEHOLDER_USERS = {
    "testuser", "user", "username", "youruser", "you", "youruser",
    "example", "name", "me", "yourname", "public", "default",
}

# (label, regex) — machine/personal data that must never enter the public repo.
PATTERNS = [
    ("private drive",    re.compile(r"E:[\\/]SAS", re.I)),
    ("personal email",   re.compile(r"\b(?:ryanjdevlin87|ckudola01)@", re.I)),
    ("discord id",       re.compile(r"\b(?:399714565845417995|433014003950682112)\b")),
    ("private codename", re.compile(r"\bKuroryuu\b", re.I)),
    ("private codename", re.compile(r"Nexus Prismatica", re.I)),
]

TEXT_EXT = {".md", ".py", ".ps1", ".sh", ".js", ".mjs", ".ts", ".tsx", ".json",
            ".yaml", ".yml", ".txt", ".bat", ".cjs", ".cmd"}
# Third-party license files legitimately carry their own authors' emails.
ALLOW_SUBSTR = ("/canvas-fonts/",)

class GitUnavailable(Exception):
    """git did not answer — the guard could not look, which is NOT 'clean'."""

def _git(args):
    """Run git and REFUSE to treat a failure as an empty result.

    🔴 THIS SWALLOWED THE FAILURE AND THAT WAS THE BUG. It returned
    .stdout.splitlines() unconditionally, so a non-zero git — wrong directory,
    git missing, not a repo, an unreadable index — produced an EMPTY LIST,
    which flowed straight through to "clean (0 text files scanned)" and
    exit 0. Run this script from anywhere outside a repo and it certified the
    commit. A guard whose every pass condition is an ABSENCE reports success
    most confidently when it cannot see anything at all.
    """
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitUnavailable(
            "git %s failed (exit %d): %s"
            % (" ".join(args), proc.returncode,
               (proc.stderr or "").strip()[:200] or "no stderr"))
    return proc.stdout.splitlines()

def files_to_scan():
    """(candidates, mode). Raises GitUnavailable rather than returning []."""
    if "--all" in sys.argv:
        return [l for l in _git(["ls-files"]) if l.strip()], "all"
    return [l for l in _git(
        ["diff", "--cached", "--name-only", "--diff-filter=ACM"]) if l.strip()], "staged"


def selftest():
    """Prove the guard BLOCKS. Run: python scripts/check_pii.py --selftest

    Every pass condition in this script is an absence, so green is its
    failure mode and no ordinary run can distinguish "found nothing" from
    "cannot find anything". This plants one line per denylist entry and
    requires a hit, then plants a documented placeholder and requires NO hit
    — only the pair is a test, or "blocked" could be a constant.
    """
    import tempfile

    must_block = [
        ("username path",    r"C:\Users\SomeRealPerson\Documents\x.md"),
        ("username path",    r"/c/Users/SomeRealPerson/x.md"),
        ("private drive",    r"E:\SAS\REPO_CLONES\thing"),
        ("personal email",   "contact ryanjdevlin87@example.com"),
        ("discord id",       "user 399714565845417995 pinged"),
        ("private codename", "the Kuroryuu build"),
        ("private codename", "Nexus Prismatica notes"),
    ]
    must_pass = [
        r"C:\Users\TestUser\Documents\x.md",
        r"C:\Users\<username>\x.md",
        "LiteSuite and LiteSpeak are public brand",
        "Ryan Devlin, for Marlee Rose",
    ]

    def scan_line(line):
        """Exactly the matching main() does, on one line."""
        found = []
        for m in USERNAME_RE.finditer(line):
            if m.group(1).lower() not in PLACEHOLDER_USERS:
                found.append("username path")
                break
        for label, pat in PATTERNS:
            if pat.search(line):
                found.append(label)
        return found

    ok = True
    for expect, line in must_block:
        got = scan_line(line)
        if expect not in got:
            ok = False
            print("SELFTEST FAIL: %r should trip [%s], tripped %s" % (line, expect, got or "nothing"))
    for line in must_pass:
        got = scan_line(line)
        if got:
            ok = False
            print("SELFTEST FAIL: %r is allowed brand/placeholder but tripped %s" % (line, got))

    me = str(Path(__file__).resolve())

    # And the defect this whole exercise came from: an unreachable git must
    # be an ERROR, never an empty scan that renders as clean.
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run([sys.executable, me, "--all"],
                              capture_output=True, text=True, cwd=tmp)
        if proc.returncode == 0:
            ok = False
            print("SELFTEST FAIL: run outside a repo exited 0 — the vacuous "
                  "green is back (%s)" % (proc.stdout.strip()[:120]))

    # The same shape one level out: an unknown flag must not exit 0 by
    # silently becoming staged mode. A pipeline reads only this number.
    for bad in ("--alll", "--scan-everything", "extra-positional"):
        proc = subprocess.run([sys.executable, me, bad],
                              capture_output=True, text=True,
                              cwd=str(Path(me).parent))
        if proc.returncode == 0:
            ok = False
            print("SELFTEST FAIL: %r exited 0 — an unknown flag still passes "
                  "green having scanned nothing" % bad)

    # ...and the recognised flags must still work, or the check above could
    # be satisfied by a script that rejects everything.
    proc = subprocess.run([sys.executable, me, "--all"],
                          capture_output=True, text=True,
                          cwd=str(Path(me).parent))
    if proc.returncode != 0:
        ok = False
        print("SELFTEST FAIL: --all now exits %d — the argv guard rejects a "
              "VALID flag, so its refusals prove nothing" % proc.returncode)

    print("SELFTEST %s: %d block cases, %d allow cases, the empty-set case, "
          "3 unknown-flag cases and the valid-flag control"
          % ("OK" if ok else "FAILED", len(must_block), len(must_pass)))
    return 0 if ok else 1

KNOWN_FLAGS = {"--all", "--selftest"}

def check_argv():
    """Reject anything we do not recognise. Returns an error string or None.

    🔴 FOUND BY SENTINEL IN THE FIX FOR THE VACUOUS GREEN, which is the same
    defect one level out. Flags were tested with `"--all" in sys.argv` and
    nothing validated the rest, so an unrecognised token — a typo, a renamed
    flag, a CI step that drifted — FELL THROUGH TO STAGED MODE. With nothing
    staged that scans zero files and exits 0.

        `check_pii.py --alll` PASSED GREEN HAVING SCANNED NOTHING.

    The NOTHING SCANNED message saves a human reading output. It does not save
    a PIPELINE, which reads only the exit code — and a pipeline is the only
    thing that runs this in CI. "You passed an unknown flag" and "your staged
    set is clean" have to stop being the same answer.
    """
    unknown = [a for a in sys.argv[1:] if a not in KNOWN_FLAGS]
    if unknown:
        return ("unrecognised argument(s): %s\nusage: check_pii.py [--all] "
                "[--selftest]\n(refusing rather than defaulting to staged mode: "
                "an unknown flag scanning nothing must not exit 0)"
                % ", ".join(unknown))
    return None


def main():
    argv_error = check_argv()
    if argv_error:
        print("ERROR: %s" % argv_error)
        return 2

    if "--selftest" in sys.argv:
        return selftest()

    hits = []
    scanned = 0
    try:
        candidates, mode = files_to_scan()
    except GitUnavailable as exc:
        # NOT clean. The guard could not look, and saying "clean" here is the
        # exact failure this script exists to prevent, one level up.
        print("ERROR: cannot determine what to scan — %s" % exc)
        print("The guard did NOT run. This is not a pass; re-run inside the repo.")
        return 2

    # A repo always has tracked files, so zero in --all mode means the listing
    # is lying, not that the project is empty.
    if mode == "all" and not candidates:
        print("ERROR: `git ls-files` returned nothing. A tracked repo is never "
              "empty, so this is a broken listing, not a clean scan.")
        return 2

    for rel in candidates:
        if Path(rel).suffix.lower() not in TEXT_EXT:
            continue
        if any(s in ("/" + rel) for s in ALLOW_SUBSTR):
            continue
        if Path(rel).name == "check_pii.py":  # the guard holds the denylist literals
            continue
        p = Path(rel)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        scanned += 1
        for i, line in enumerate(text.splitlines(), 1):
            for m in USERNAME_RE.finditer(line):
                if m.group(1).lower() not in PLACEHOLDER_USERS:
                    hits.append((rel, i, "username path", line.strip()[:120]))
                    break
            for label, pat in PATTERNS:
                if pat.search(line):
                    hits.append((rel, i, label, line.strip()[:120]))
    if hits:
        print("BLOCKED: personal/machine data must not enter the public plugin:")
        for rel, i, label, snip in hits:
            print(f"  {rel}:{i}  [{label}]  {snip}")
        print("\nFix: use ~ or ${CLAUDE_SKILL_DIR} for paths; remove the data; then re-commit.")
        print("(Brand is allowed — author name, Marlee/Carly dedication, and product names are NOT blocked.)")
        return 1
    # ⚠️ REPORT WHAT WAS OFFERED, NOT ONLY WHAT WAS FOUND. "clean" over zero
    # files is the vacuous green; a reader must be able to tell "nothing
    # matched" from "nothing was looked at". Zero staged text files is
    # LEGITIMATE (a binary-only or rename-only commit), so it is not an
    # error -- but it is not the word "clean" either.
    if scanned == 0:
        print("check_pii: NOTHING SCANNED — %d path(s) offered, none were text "
              "files this guard covers. No PII claim is made about this commit."
              % len(candidates))
        return 0
    print(f"check_pii: clean ({scanned} of {len(candidates)} offered path(s) "
          f"scanned, {mode} mode)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
