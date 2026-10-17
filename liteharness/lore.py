"""The lore file: a few short stories every seat is told at boot and after a compaction.

LiteHarness ships only the reader. The file is the operator's, kept outside the
repo, and a missing or blank one prints nothing at all (no header, no warning).

  * `lore_path()`        - env LITEHARNESS_LORE_FILE > config `lore_file` > ~/.liteharness/lore.md
  * `lore_block(path)`   - SessionStart text: one header, the file, a note per cut
  * `check(path)`        - `liteharness lore --check`: fails over the cap or 4 sections

The same contract (path rule, cap, section limit, cut notes, header) is implemented
independently by LiteTUI's store injection, which does not import this package.
Change one, change the other.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import config

LORE_ENV = "LITEHARNESS_LORE_FILE"
#: The lore must fit in this many characters (stripped): `lore --check` fails above it,
#: and the loader cuts (with a note) at the same number.
LORE_CAP = 2_400
#: Sections are lines starting `## `; text before the first one is kept.
MAX_SECTIONS = 4
HEADER = "## Lore, loaded at the start of this conversation"

_SECTION = re.compile(r"^## ", re.MULTILINE)


def lore_path() -> Path:
    cfg = config.load()
    configured = cfg.get("lore_file") if isinstance(cfg, dict) else None
    raw = os.environ.get(LORE_ENV) or (configured if isinstance(configured, str) else "")
    return Path(raw).expanduser() if raw else config.get_root() / "lore.md"


def _read(path: Path) -> str:
    """The stripped file text; '' when it is missing, unreadable or blank.

    A BOM is dropped and CRLF is read as LF, so the cap and the section count see
    the same characters whatever editor wrote the file.
    """
    try:
        return path.read_bytes().decode("utf-8-sig", errors="replace").replace("\r\n", "\n").strip()
    except OSError:
        return ""


def lore_block(path: str | os.PathLike | None = None) -> str:
    """SessionStart text for the lore file; '' when there is nothing to print."""
    path = Path(path) if path else lore_path()
    text = _read(path)
    if not text:
        return ""
    where = str(path).replace("\\", "/")
    notes: list[str] = []
    heads = [m.start() for m in _SECTION.finditer(text)]
    if len(heads) > MAX_SECTIONS:
        text = text[: heads[MAX_SECTIONS]].rstrip()
        notes.append(f"[... lore {where}: {len(heads)} sections, only the first {MAX_SECTIONS} are injected]")
    if len(text) > LORE_CAP:
        size = len(text)
        kept = text[:LORE_CAP]
        if text[LORE_CAP] != "\n":  # the cut lands inside a line: keep whole lines only
            kept = kept[: kept.rindex("\n")] if "\n" in kept else ""
        text = kept.rstrip()
        notes.append(f"[... lore {where} truncated at {LORE_CAP} of {size} chars]")
    return "\n".join([HEADER, "", *([text] if text else []), *notes]) + "\n"


def check(path: str | os.PathLike | None = None) -> tuple[bool, list[str]]:
    """(ok, report lines) for `liteharness lore --check [--file <path>]`.

    Fails when the stripped text is longer than LORE_CAP characters or holds more
    than MAX_SECTIONS sections. A missing or blank file is fine: nothing is printed.
    """
    path = Path(path) if path else lore_path()
    text = _read(path)
    if not text:
        return True, [f"ok: {path} is missing or empty (nothing is injected)"]
    size, sections = len(text), len(_SECTION.findall(text))
    lines = []
    if size > LORE_CAP:
        lines.append(f"{path} is {size} chars, over the {LORE_CAP} cap")
    if sections > MAX_SECTIONS:
        lines.append(f"{path} has {sections} sections, over the {MAX_SECTIONS} limit")
    if not lines:
        return True, [f"ok: {path} ({size} chars, {sections} sections)"]
    return False, lines
