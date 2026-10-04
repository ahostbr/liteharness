"""The per-project AGENT_INDEX.md: locate it, check its links, print its first screen.

One committed file at a repo root tells any agent what to read before touching
an area. This module owns the three things the harness does with it:

  * `find_index(start)`  - the index for the repo containing `start`
  * `dead_links(path)`   - markdown links in it whose target does not resolve
  * `brief_block(cwd)`   - SessionStart: path + first screen + managed game knowledge

The same convention (file name, walk-up-to-git-root rule, 12k cap) is
implemented independently by LiteTUI's store injection, which does not import
this package. Change one, change the other.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

INDEX_NAME = "AGENT_INDEX.md"
#: The index must fit in this many characters: `index --check` fails above it,
#: and LiteTUI's loader cuts (with a warning) at the same number.
INJECT_CAP = 12_000
#: SessionStart shows this much of the file. The hook output is previewed at
#: roughly 2 KB by some harnesses, so this is a first screen, not the file.
SCREEN_LINES = 40
SCREEN_CHARS = 2500

GAME_DIRECTOR_START = "<!-- game-director:start -->"
GAME_DIRECTOR_END = "<!-- game-director:end -->"
GAME_DIRECTOR_CHARS = 4000
GAME_DIRECTOR_POINTER = "[LITEHARNESS] Game Director: read .litesuite/game-director/addons.json for authoritative project knowledge."

_MD_LINK = re.compile(r'\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)')
_FENCE = re.compile(r"^\s*(```|~~~)")
_SKIP = ("http://", "https://", "mailto:")


def find_index(start: str | os.PathLike) -> Path | None:
    """The AGENT_INDEX.md of the repo containing `start`, or None.

    Walks up from `start` and stops at the first directory that holds a `.git`
    (a file in a worktree, a directory in a clone): a repo's index is never
    borrowed from the repo around it. With no git root above `start`, only
    `start` itself is consulted.
    """
    here = Path(start).resolve()
    if here.is_file():
        here = here.parent
    chain = (here, *here.parents)
    root = next((d for d in chain if (d / ".git").exists()), None)
    if root is None:
        candidate = here / INDEX_NAME
        return candidate if candidate.is_file() else None
    for d in chain[: chain.index(root) + 1]:
        candidate = d / INDEX_NAME
        if candidate.is_file():
            return candidate
    return None


def _link_path(target: str) -> str | None:
    """The path a markdown link target names, or None when it is not checked.

    Not checked: http://, https://, mailto: and a pure #anchor. Otherwise the
    target is URL-decoded and its #fragment dropped (anchors are not validated).
    """
    t = target.strip()
    if not t or t.startswith("#") or t.lower().startswith(_SKIP):
        return None
    t = unquote(t.split("#", 1)[0])
    return t or None


def dead_links(index_path: str | os.PathLike) -> list[tuple[int, str]]:
    """(line number, target as written) for each markdown link that does not resolve.

    Only `[text](target)` links count: backticked or bare paths are not checked.
    A target resolves relative to the directory holding the index. Links inside
    fenced code blocks are examples, not links, and are skipped.
    """
    index = Path(index_path)
    root = index.parent
    dead: list[tuple[int, str]] = []
    fenced = False
    for lineno, line in enumerate(index.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for m in _MD_LINK.finditer(line):
            path = _link_path(m.group(1))
            if path is not None and not (root / path).exists():
                dead.append((lineno, m.group(1)))
    return dead


def check(project_root: str | os.PathLike) -> tuple[bool, list[str]]:
    """(ok, report lines) for `liteharness index --check --project <root>`.

    Fails (ok False) when AGENT_INDEX.md is missing, is longer than INJECT_CAP
    characters, or has a dead link. The loaders still load an oversize file, with
    a warning, so the cap is enforced here at commit/tick time, not at spawn.
    """
    root = Path(project_root)
    index = root / INDEX_NAME
    if not index.is_file():
        return False, [f"{INDEX_NAME} not found in {root}"]
    # Raw characters: decode the bytes, no newline translation, so a CRLF file is
    # counted as it sits on disk (read_text would fold each CRLF to one char).
    size = len(index.read_bytes().decode("utf-8", errors="replace"))
    lines = [f"{INDEX_NAME}:{n}: dead link -> {target}" for n, target in dead_links(index)]
    if size > INJECT_CAP:
        lines.append(f"{INDEX_NAME} is {size} chars, over the {INJECT_CAP} cap")
    if not lines:
        lines.append(f"ok: {index} ({size} chars, no dead links)")
        return True, lines
    return False, lines


def first_screen(text: str) -> str:
    """The first screen of the index, cut on a line boundary, with a tail note."""
    lines = text.splitlines()
    kept: list[str] = []
    used = 0
    for line in lines[:SCREEN_LINES]:
        if used + len(line) + 1 > SCREEN_CHARS and kept:
            break
        kept.append(line)
        used += len(line) + 1
    rest = len(lines) - len(kept)
    body = "\n".join(kept)
    if rest > 0:
        body += f"\n[... {rest} more lines - read the file for the topic table]"
    return body


def _game_director_extra(text: str, screen: str, *, compacting: bool) -> tuple[str, str]:
    """Keep the first screen, plus bounded managed knowledge that may live at the tail."""
    if GAME_DIRECTOR_START not in text and GAME_DIRECTOR_END not in text:
        return screen, ""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == GAME_DIRECTOR_START]
    ends = [i for i, line in enumerate(lines) if line.strip() == GAME_DIRECTOR_END]
    if (len(starts) != 1 or len(ends) != 1 or
            text.count(GAME_DIRECTOR_START) != 1 or text.count(GAME_DIRECTOR_END) != 1 or
            ends[0] < starts[0]):
        # Mentions and malformed markers are user text, never permission to cut it.
        return screen, GAME_DIRECTOR_POINTER + "\n"
    if compacting:
        return screen, GAME_DIRECTOR_POINTER + "\n"
    start, end = starts[0], ends[0]
    block = "\n".join(lines[start:end + 1])
    if len(block) <= GAME_DIRECTOR_CHARS and block in screen:
        return screen, ""
    # first_screen's body is a prefix of file lines, optionally followed by its tail note.
    screen_lines = screen.splitlines()
    kept = 0
    for original, shown in zip(lines, screen_lines):
        if original != shown:
            break
        kept += 1
    if start < kept:
        kept = start  # remove only this well-formed block's partly visible content
    screen = "\n".join(lines[:kept])
    truncated = len(block) > GAME_DIRECTOR_CHARS
    shown_block_lines = end - start + 1
    if truncated:
        allowance = GAME_DIRECTOR_CHARS - len(GAME_DIRECTOR_POINTER) - 1
        shown = block[:allowance].rstrip()
        # A partially printed line is still unshown in the line-count tail note.
        shown_block_lines = shown.count("\n") + int(shown == "\n".join(lines[start:start + shown.count("\n") + 1]))
        block = shown + "\n" + GAME_DIRECTOR_POINTER
    rest = len(lines) - kept - shown_block_lines
    if rest > 0:
        screen += f"\n[... {rest} more lines - read the file for the topic table]"
    return screen, f"\n## Game Director - project knowledge\n{block}\n"


def brief_block(cwd: str | os.PathLike, *, compacting: bool = False) -> str:
    """SessionStart text for the repo around `cwd`; '' when it has no index.

    A compaction only re-teaches the pointer: the screen was already delivered
    and the tier card is meant to stay small.
    """
    index = find_index(cwd)
    if index is None:
        return ""
    where = str(index).replace("\\", "/")
    compact_pointer = f"[LITEHARNESS] Project index: {where} - re-read it after compaction before touching an area.\n"
    try:
        text = index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        if compacting:
            return compact_pointer
        return f"[LITEHARNESS] Project index {where} exists but could not be read - read it yourself.\n"
    screen, extra = _game_director_extra(text, "" if compacting else first_screen(text), compacting=compacting)
    if compacting:
        return compact_pointer + extra
    return (
        f"## Project index - {INDEX_NAME} (read before touching any area)\n"
        f"Path: {where}\n"
        f"First screen below; the topic table says what to read, in order, per area.\n\n"
        f"{screen}\n{extra}"
    )
