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

def _git(args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.splitlines()

def files_to_scan():
    if "--all" in sys.argv:
        return [l for l in _git(["ls-files"]) if l.strip()]
    return [l for l in _git(["diff", "--cached", "--name-only", "--diff-filter=ACM"]) if l.strip()]

def main():
    hits = []
    scanned = 0
    for rel in files_to_scan():
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
    print(f"check_pii: clean ({scanned} text files scanned)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
