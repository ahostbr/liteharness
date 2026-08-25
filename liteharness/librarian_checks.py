"""librarian_checks — deterministic claim checker for the Librarian's Vault Scout.

Daily notes are a CLAIM LIST, not a source. This module extracts claims from
note files, decomposes each into STRUCTURAL parts (a sha exists in a repo; a
path exists under a root) and a BEHAVIORAL part ("fixed", "works" — prose about
behavior), and runs every structural check itself. The scout that consumes this
JSON may only downgrade a result, never upgrade one — and behavioral prose is
NEVER promoted by a passing structural check: an existence proof must not
launder a behavioral claim.

Repo qualification is load-bearing: C:\\Projects is itself a git repo with
other repos NESTED inside it, so an unqualified `git cat-file` from the wrong
root REFUTES perfectly valid shas. Every claim resolves its repo root from
(1) an explicit path in the claim line, else (2) the nearest preceding path in
the same note, else (3) nothing — absent or ambiguous repo means the claim is
`unverifiable`, stated with the ambiguity, never guessed at.

Behavioral parts are `attested` only when a pattern with effective verified
state `human` (the WS3 attestation fold — imported from liteharness.cli, ONE
implementation) mentions the claim's sha. Everything else is
`awaiting-human`: Ryan's confirmation mints the attestation, which promotes it
the NEXT night.

Usage:
  python -m liteharness.librarian_checks --notes <glob> [--days 2] [--json-out PATH]
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cli import (
    _PATTERN_ATTESTATIONS_FILENAME,
    _effective_verified_map,
    _load_pattern_entries,
    _pattern_effective_id,
)

# A hex run long enough to be a sha and not a word. Bounded at 40 (full sha).
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
# Paths: absolute windows/posix, or backtick-wrapped relative with a slash.
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:[a-z]/)?)[\w.~\\/\-]+")
_TICK_PATH_RE = re.compile(r"`([^`\s]+[\\/][^`]*?)(?::(\d+))?`")
# Behavioral verbs — prose asserting that something WORKS, not that it exists.
_BEHAVIORAL_RE = re.compile(
    r"\b(fixed|fixes|works|working|worked|verified|proven|passes|passing|"
    r"resolved|solved|done|complete[d]?|stable|no longer (?:fails|crashes)|"
    r"landed the fix)\b",
    re.IGNORECASE,
)


def _git_root_of(path: Path) -> Path | None:
    """Walk up from `path` to the NEAREST enclosing git root (innermost wins —
    the nested-repo hazard is exactly why the walk stops at the first .git)."""
    p = path if path.is_dir() else path.parent
    for candidate in [p, *p.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _sha_exists(repo_root: Path, sha: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        timeout=15,
    )
    return proc.returncode == 0


def _human_attested_shas(repo_root: Path) -> dict[str, str]:
    """sha-mention -> attestation evidence, for patterns whose EFFECTIVE state
    is `human`. Uses the WS3 fold from liteharness.cli — one implementation."""
    patterns_path = repo_root / ".liteharness" / "patterns.jsonl"
    if not patterns_path.exists():
        return {}
    entries = _load_pattern_entries(patterns_path)
    attest_path = patterns_path.parent / _PATTERN_ATTESTATIONS_FILENAME
    effective = _effective_verified_map(entries, attest_path)
    out: dict[str, str] = {}
    for e in entries:
        pid = _pattern_effective_id(e)
        state = effective.get(pid) or e.get("verified") or "unverified"
        if state != "human":
            continue
        text = " ".join(
            str(e.get(k, "")) for k in ("description", "reason", "lesson")
        )
        for m in _SHA_RE.finditer(text.lower()):
            out[m.group(0)] = pid
    return out


def _note_files(notes_glob: str, days: int | None) -> list[Path]:
    """Expand the glob; keep files dated within `days` (filename date
    YYYY-MM-DD preferred — vault dailies; mtime as fallback)."""
    files = [Path(p) for p in globmod.glob(notes_glob, recursive=True)]
    if days is None:
        return sorted(files)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept: list[Path] = []
    for f in files:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", f.stem)
        if m:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            if dt >= cutoff - timedelta(days=1):
                kept.append(f)
            continue
        if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) >= cutoff:
            kept.append(f)
    return sorted(kept)


def _resolve_line_repo(line: str, context_root: Path | None) -> tuple[Path | None, str]:
    """Repo root for a claim line: explicit path in the line beats note
    context. Returns (root|None, how)."""
    for m in _ABS_PATH_RE.finditer(line):
        root = _git_root_of(Path(m.group(0)))
        if root is not None:
            return root, "explicit-path"
    if context_root is not None:
        return context_root, "note-context"
    return None, "unresolved"


def _repo_name_candidates(line: str, anchor: Path) -> list[Path]:
    """Git repos named BARE in the line (no path), resolved against the
    workspace: the outermost git ancestor of ``anchor``, plus its immediate
    child repos whose directory name appears as a word in the line. A name
    in prose can VERIFY a sha the named repo contains; it never refutes one.
    """
    ws = anchor
    parent_root = _git_root_of(ws.parent)
    while parent_root is not None and parent_root != ws:
        ws = parent_root
        parent_root = _git_root_of(ws.parent)
    tokens = {t.lower() for t in re.findall(r"[\w.-]+", line)}
    out: list[Path] = []
    try:
        children = [p for p in ws.iterdir() if p.is_dir()]
    except OSError:
        children = []
    for child in children:
        if child.name.lower() in tokens and (child / ".git").exists():
            out.append(child)
    return out


def check_notes(notes_glob: str, days: int | None = 2) -> dict:
    """Extract and check every claim in the matched notes. Pure function of
    the filesystem — no LLM, no guessing; ambiguity is a stated result."""
    claims: list[dict] = []
    files = _note_files(notes_glob, days)

    for note in files:
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            claims.append({
                "note": str(note), "line": 0, "text": f"<unreadable: {exc}>",
                "status": "unverifiable", "detail": "note not readable",
            })
            continue

        # Note context: the most recent line whose path resolved to a repo.
        context_root: Path | None = None

        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue

            line_root, how = _resolve_line_repo(stripped, context_root)
            if how == "explicit-path" and line_root is not None:
                context_root = line_root

            shas = [m.group(0) for m in _SHA_RE.finditer(stripped.lower())]
            tick_paths = _TICK_PATH_RE.findall(stripped)
            behavioral = _BEHAVIORAL_RE.search(stripped) is not None

            if not shas and not tick_paths:
                continue  # no checkable claim on this line

            parts: list[dict] = []
            name_candidates: list[Path] | None = None
            for sha in shas:
                if line_root is None:
                    parts.append({
                        "kind": "sha", "value": sha, "status": "unverifiable",
                        "detail": "no repo could be resolved for this claim "
                                  "(no path in the line or preceding context); "
                                  "an unqualified check from the wrong root "
                                  "would refute a valid sha",
                    })
                    continue
                if _sha_exists(line_root, sha):
                    parts.append({
                        "kind": "sha", "value": sha, "status": "verified",
                        "repo": str(line_root), "resolved_by": how,
                    })
                    continue
                # Miss in the resolved repo. A repo NAMED bare in the line can
                # still verify the sha (first live sweep, workspace 5a6320c:
                # vault lines name their repo in prose far more often than by
                # path). Names verify; they never refute.
                if name_candidates is None:
                    name_candidates = _repo_name_candidates(stripped, line_root)
                named = next(
                    (c for c in name_candidates if _sha_exists(c, sha)), None,
                )
                if named is not None:
                    parts.append({
                        "kind": "sha", "value": sha, "status": "verified",
                        "repo": str(named), "resolved_by": "repo-name",
                    })
                elif how == "explicit-path":
                    parts.append({
                        "kind": "sha", "value": sha, "status": "refuted",
                        "repo": str(line_root), "resolved_by": how,
                        "detail": "git cat-file -e failed in the resolved repo",
                    })
                else:
                    # note-context resolution is too weak to carry a refute:
                    # 108/117 refutes in the first live sweep were valid shas
                    # under a wrong context. Only an explicit path decides.
                    parts.append({
                        "kind": "sha", "value": sha, "status": "unverifiable",
                        "repo": str(line_root), "resolved_by": how,
                        "detail": f"git cat-file -e missed in {line_root}, but "
                                  "that root came from note context, not this "
                                  "line — refusing to refute; qualify the claim "
                                  "with an explicit path to decide it",
                    })

            for raw_path, _lineref in tick_paths:
                p = Path(raw_path)
                if p.is_absolute():
                    exists = p.exists()
                    parts.append({
                        "kind": "path", "value": raw_path,
                        "status": "verified" if exists else "refuted",
                    })
                elif line_root is not None:
                    exists = (line_root / raw_path).exists()
                    parts.append({
                        "kind": "path", "value": raw_path,
                        "status": "verified" if exists else "refuted",
                        "repo": str(line_root),
                    })
                else:
                    parts.append({
                        "kind": "path", "value": raw_path,
                        "status": "unverifiable",
                        "detail": "relative path with no resolved repo root",
                    })

            behavioral_out = None
            if behavioral:
                attested = None
                if line_root is not None and shas:
                    known = _human_attested_shas(line_root)
                    for sha in shas:
                        if sha in known:
                            attested = known[sha]
                            break
                behavioral_out = {
                    "present": True,
                    "status": "attested" if attested else "awaiting-human",
                    **({"attestation_pattern": attested} if attested else {}),
                    "detail": (
                        "behavioral prose promotes only via a human attestation; "
                        "a passing structural check never launders it"
                    ),
                }

            statuses = {p["status"] for p in parts}
            overall = (
                "refuted" if "refuted" in statuses
                else "unverifiable" if "unverifiable" in statuses
                else "verified"
            )
            claims.append({
                "note": str(note),
                "line": lineno,
                "text": stripped[:300],
                "repo": str(line_root) if line_root else None,
                "parts": parts,
                **({"behavioral": behavioral_out} if behavioral_out else {}),
                "status": overall,
            })

    summary = {
        "notes_matched": len(files),
        "claims": len(claims),
        "verified": sum(1 for c in claims if c["status"] == "verified"),
        "refuted": sum(1 for c in claims if c["status"] == "refuted"),
        "unverifiable": sum(1 for c in claims if c["status"] == "unverifiable"),
        "awaiting_human": sum(
            1 for c in claims
            if c.get("behavioral", {}).get("status") == "awaiting-human"
        ),
        "attested": sum(
            1 for c in claims
            if c.get("behavioral", {}).get("status") == "attested"
        ),
    }
    return {"summary": summary, "claims": claims}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="liteharness.librarian_checks")
    parser.add_argument("--notes", required=True, help="glob for note files")
    parser.add_argument("--days", type=int, default=2,
                        help="only notes dated within N days (0 = no filter)")
    parser.add_argument("--json-out", default=None,
                        help="also write the JSON to this path")
    args = parser.parse_args(argv)

    days = None if args.days == 0 else args.days
    result = check_notes(args.notes, days)
    payload = json.dumps(result, indent=2)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload, encoding="utf-8")
    # Exit 0 even with refuted claims: the checker REPORTS; the selftest and
    # the librarian decide. A non-zero here would make "found a false claim"
    # indistinguishable from "checker broke" in the schtasks log.
    return 0


if __name__ == "__main__":
    sys.exit(main())
