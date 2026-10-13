"""Standing owners: the identity files a seat boots from and carries across compactions.

An owner's memory folder holds three files:

  SOUL.md    the charter (who the owner is and how it works with peers)
  DOMAIN.md  what the owner knows about its area
  NOW.md     where the owner is right now (rewritten at every handoff)

`liteharness spawn --owner <Title> --memory <dir>` boots a seat from them; the seat is named
by its title, and `spawn --resume <Title>` returns to the same conversation.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

OWNER_FILES = ("SOUL.md", "DOMAIN.md", "NOW.md")
MAX_TITLE = 64


class OwnerError(ValueError):
    """An owner operation failed; optional index records make recovery explicit."""

    def __init__(self, message: str, *, recovery_path: Path | None = None):
        self.recovery_path = recovery_path
        if recovery_path is not None:
            message += f". Prior index records retained at {recovery_path}"
        super().__init__(message)


def check_title(title) -> str:
    if (not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE
            or title != title.strip() or any(c in title for c in '"\'`$\\') or not title.isprintable()):
        raise OwnerError(
            f"owner title {title!r} is not usable: 1-{MAX_TITLE} printable characters, "
            "no quotes, backticks, '$' or backslash, no leading or trailing space")
    return title


def memory_files(memory_dir) -> dict[str, Path]:
    """SOUL.md / DOMAIN.md / NOW.md of `memory_dir`, all required."""
    root = Path(str(memory_dir)).expanduser()
    if not root.is_dir():
        raise OwnerError(f"--memory {str(memory_dir)!r} is not a directory")
    root = root.resolve()
    missing = [name for name in OWNER_FILES if not (root / name).is_file()]
    if missing:
        raise OwnerError(f"--memory {str(root)!r} is missing {', '.join(missing)}; "
                         f"an owner folder holds {', '.join(OWNER_FILES)}")
    return {name: root / name for name in OWNER_FILES}


def render_template(name: str, project: str, date: str | None = None) -> str:
    """A shipped skeleton (SOUL.md charter, DOMAIN.md, NOW.md) with {{project}} / {{date}} filled in."""
    if name not in OWNER_FILES:
        raise OwnerError(f"no owner template {name!r}; the templates are {', '.join(OWNER_FILES)}")
    text = (Path(__file__).parent / "owner_templates" / name).read_text(encoding="utf-8")
    stamp = date or datetime.now().strftime("%Y-%m-%d %H:%M")
    return text.replace("{{project}}", project).replace("{{date}}", stamp)


def owner_prompt(title, memory_dir) -> str:
    """The spawn prompt: the charter, then the order to read NOW.md and DOMAIN.md first."""
    check_title(title)
    files = memory_files(memory_dir)
    charter = files["SOUL.md"].read_text(encoding="utf-8").strip()
    if not charter:
        raise OwnerError(f"{files['SOUL.md']} is empty: an owner needs its charter")
    return (
        f"{charter}\n\n"
        f"Your name is {title}. Before anything else, read {files['NOW.md']} and then "
        f"{files['DOMAIN.md']}."
    )


# ── rolling self-handoff ─────────────────────────────────────────────────────────

MAX_NOW_LINES = 60
#: NOW.md must carry these headings, each once, each with something under it.
REQUIRED_HEADINGS = ("Updated", "Doing now", "Next 3 steps", "Waiting on", "Crew alive", "Do NOT redo")

_HEADING = re.compile(r"^#{1,6}\s*(.*?)\s*$")
#: The plain spelling of the template: a line that starts with the heading name and a colon
#: (`Doing now: text`). Not indented, so list items and body lines never match. A name the file
#: also heads with a markdown `## Name` takes that heading, and a plain line of the same name
#: is body text; in a file with no markdown heading for the name, every such line is a heading.
_PLAIN_HEADINGS = [(name, re.compile(re.escape(name) + r"\s*:\s*(.*?)\s*$", re.IGNORECASE))
                   for name in REQUIRED_HEADINGS]


def _plain_heading(line: str) -> tuple[str, str] | None:
    """(required heading, text after its colon) when `line` is a plain `Heading: text` line."""
    for name, pattern in _PLAIN_HEADINGS:
        match = pattern.match(line)
        if match:
            return name, match.group(1)
    return None


def _markdown_heading(line: str) -> tuple[str, str] | None:
    """(required heading, text after its name) when `line` is a markdown `## Heading` line."""
    match = _HEADING.match(line)
    if not match:
        return None
    title = match.group(1).lower()
    for name in REQUIRED_HEADINGS:
        key = name.lower()
        if title == key or re.match(re.escape(key) + r"\b", title):
            return name, title[len(key):].lstrip(" :-\u2014")
    return None


def validate_now(text: str) -> list[str]:
    """The reasons NOW.md fails the template; an empty list means it passes."""
    problems: list[str] = []
    lines = text.splitlines()
    if len(lines) > MAX_NOW_LINES:
        problems.append(f"NOW.md has {len(lines)} lines; the limit is {MAX_NOW_LINES} (cut it, "
                        "move lasting knowledge into DOMAIN.md)")
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    seen: dict[str, int] = {}
    # Decided per name, not per line: a name the file heads with `## Name` is never also
    # headed by a plain `Name: text` line (that line is body text under whatever is above it).
    markdown_names = {found[0] for found in map(_markdown_heading, lines) if found}
    for line in lines:
        if _HEADING.match(line):
            current = None
            found = _markdown_heading(line)
            if found:
                name, rest = found
                seen[name] = seen.get(name, 0) + 1
                current = sections.setdefault(name, [])
                if rest:
                    current.append(rest)
            continue
        plain = _plain_heading(line)
        if plain and plain[0] not in markdown_names:
            name, rest = plain
            seen[name] = seen.get(name, 0) + 1
            current = sections.setdefault(name, [])
            if rest:
                current.append(rest)
        elif current is not None and line.strip():
            current.append(line.strip())
    for name in REQUIRED_HEADINGS:
        if name not in seen:
            problems.append(f"NOW.md has no '{name}' heading")
        elif seen[name] > 1:
            problems.append(f"NOW.md has the '{name}' heading {seen[name]} times")
        elif not sections[name]:
            problems.append(f"NOW.md section '{name}' is empty")
    return problems


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def _save_index_recovery(base: Path, entries: str, prefix: str) -> Path:
    """Save root-relative index-info input before mutation, including absent paths."""
    git_dir = _git(base, "rev-parse", "--absolute-git-dir")
    if git_dir.returncode != 0:
        raise OwnerError("could not locate index recovery storage: " + git_dir.stderr.strip())
    removals = "".join(f"0 {'0' * 40}\t{prefix}{name}\0" for name in ("NOW.md", "DOMAIN.md"))
    with tempfile.NamedTemporaryFile("w", prefix="handoff-index-", suffix=".recovery",
                                     dir=git_dir.stdout.rstrip("\r\n"), delete=False,
                                     encoding="utf-8", newline="") as handle:
        handle.write(removals + entries)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _restore_index(base: Path, recovery_path: Path) -> None:
    """Apply only the saved paths; never replace the entire index."""
    result = subprocess.run(["git", "update-index", "-z", "--index-info"], cwd=base,
                            input=recovery_path.read_text(encoding="utf-8"), encoding="utf-8",
                            capture_output=True, timeout=120)
    if result.returncode != 0:
        raise OwnerError("could not restore the handoff index: " +
                         (result.stderr.strip() or result.stdout.strip()))


def handoff(memory_dir, *, agent_id: str, agent_name: str) -> list[str]:
    """Validate NOW.md, commit NOW.md and DOMAIN.md, return the pointer lines to print."""
    files = memory_files(memory_dir)
    base = files["NOW.md"].parent
    # utf-8-sig: PowerShell 5 and Notepad save a BOM, which would sit in front of the first heading.
    problems = validate_now(files["NOW.md"].read_text(encoding="utf-8-sig"))
    if problems:
        raise OwnerError("NOW.md does not pass the template: " + "; ".join(problems))
    if _git(base, "rev-parse", "--show-toplevel").returncode != 0:
        raise OwnerError(f"{base} is not inside a git repository; the handoff is a commit")
    tracked = _git(base, "ls-files", "--error-unmatch", "--", "NOW.md").returncode == 0
    changed = _git(base, "status", "--porcelain", "--", "NOW.md").stdout.strip()
    if tracked and not changed:
        raise OwnerError("NOW.md has not changed since the last commit; rewrite it for this "
                         "handoff first, a stale NOW.md would reload the wrong state")
    prefix = _git(base, "rev-parse", "--show-prefix")
    if prefix.returncode != 0:
        raise OwnerError("could not resolve the handoff index paths: " + prefix.stderr.strip())
    snapshot = _git(base, "ls-files", "--full-name", "--stage", "-z", "--", "NOW.md", "DOMAIN.md")
    if snapshot.returncode != 0:
        raise OwnerError("could not snapshot the handoff index: " + snapshot.stderr.strip())
    message = (
        "chore(handoff): rolling handoff of NOW.md and DOMAIN.md\n\n"
        f"Agent-Role: owner\nAgent-Name: {agent_name}\nAgent-ID: {agent_id}\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(message)
        message_file = handle.name
    try:
        recovery_path = _save_index_recovery(base, snapshot.stdout, prefix.stdout.rstrip("\r\n"))
        try:
            added = _git(base, "add", "--", "NOW.md", "DOMAIN.md")
            if added.returncode != 0:
                raise OwnerError("git add failed: " + (added.stderr.strip() or added.stdout.strip()))
            committed = _git(base, "commit", "-q", "-F", message_file, "--", "NOW.md", "DOMAIN.md")
            if committed.returncode != 0:
                raise OwnerError("git commit failed: " +
                                 (committed.stderr.strip() or committed.stdout.strip()))
        except (OwnerError, OSError, subprocess.SubprocessError) as exc:
            try:
                _restore_index(base, recovery_path)
            except (OwnerError, OSError, subprocess.SubprocessError) as restore_error:
                raise OwnerError(f"{exc}; index restoration failed: {restore_error}; "
                                 "inspect the index before retrying",
                                 recovery_path=recovery_path) from exc
            recovery_path.unlink()
            raise OwnerError(f"{exc} (the index for NOW.md and DOMAIN.md was restored; "
                             "your edits are untouched)") from exc
        recovery_path.unlink()
    finally:
        os.unlink(message_file)
    here = base.as_posix()
    return ["/compact", f"Reload: read {here}/NOW.md then {here}/DOMAIN.md"]
