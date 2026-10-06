"""Read-only handoff evidence. No save/copy/store creation or lease bypass."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import time

from .retirement import RetirementRefused

HANDOFF_MAX_AGE_SECONDS = 30 * 60
HANDOFF_MAX_BYTES = 1024 * 1024


def _unlinked(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RetirementRefused("handoff_link_or_reparse_path")
    return path


@dataclass(frozen=True)
class HandoffEvidence:
    path: str
    size: int
    sha256: str

    def body(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


def verify_handoff(path: Path, allowed_roots: tuple[Path, ...], *,
                   now: float | None = None) -> HandoffEvidence:
    """Bounded file read, measured fresh/stable regular file under verified roots."""
    now = time.time() if now is None else now
    try:
        path = _unlinked(path)
        roots = tuple(_unlinked(root) for root in allowed_roots)
        if not roots or not any(path.is_relative_to(root) for root in roots):
            raise RetirementRefused("handoff_outside_owned_roots")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= HANDOFF_MAX_BYTES:
            raise RetirementRefused("handoff_not_regular_nonempty_or_too_large")
        if before.st_mtime > now or now - before.st_mtime > HANDOFF_MAX_AGE_SECONDS:
            raise RetirementRefused("handoff_not_fresh_within_30_minutes")
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                raise RetirementRefused("handoff_changed_before_read")
            payload = handle.read(HANDOFF_MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = path.stat()
        fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if len(payload) != before.st_size or fields(after) != fields(before) or fields(current) != fields(before):
            raise RetirementRefused("handoff_changed_during_read")
        if not payload.decode("utf-8").strip():
            raise RetirementRefused("handoff_empty_text")
        return HandoffEvidence(str(path), len(payload), hashlib.sha256(payload).hexdigest())
    except (OSError, UnicodeError) as exc:
        raise RetirementRefused("handoff_unreadable") from exc


def owned_handoff_paths(agent_id: str, row: dict) -> tuple[tuple[Path, ...], Path | None]:
    """Use existing folder-authoritative resolver; never legacy conversation guesses."""
    cwd = row.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise RetirementRefused("seat_working_directory_unconfirmed")
    roots = [Path(cwd)]
    default = None
    if row.get("cli") == "litetui" or row.get("spawn_mode") == "litetui":
        from .agent_store import AgentStore, StoreError
        from .owned_launch import data_root
        try:
            store = AgentStore(data_root())
            agent = store.find_agent(agent_id=agent_id)
            roots.append(agent.directory)
            conversations = store.list_conversations(agent)
            if len(conversations) == 1:
                default = conversations[0] / "handoff.md"
        except (StoreError, OSError):
            # Explicit cwd-contained handoff remains supported. An unverified
            # home never enters allowed roots and never supplies a default.
            pass
    return tuple(roots), default


def verify_committed_handoff(evidence: HandoffEvidence, cwd: Path) -> None:
    """Claude handoff must match a committed document, not an untracked draft.

    Plain read-only Git with explicit cwd; never -C/--git-dir relocation or writes.
    """
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                              capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        relative = Path(evidence.path).relative_to(Path(root)).as_posix()
        # Freeze blob identity before its size check; moving HEAD must not select
        # a larger/different object between size and content reads.
        blob = subprocess.run(["git", "rev-parse", "HEAD:" + relative], cwd=cwd,
                              capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        import re
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", blob):
            raise RetirementRefused("committed_handoff_object_unconfirmed")
        result = subprocess.run(["git", "cat-file", "-s", blob], cwd=cwd,
                                capture_output=True, text=True, timeout=5, check=True)
        if not 0 < int(result.stdout.strip()) <= HANDOFF_MAX_BYTES:
            raise RetirementRefused("committed_handoff_too_large")
        content = subprocess.run(["git", "cat-file", "blob", blob], cwd=cwd,
                                 capture_output=True, timeout=5, check=True).stdout
        if verify_handoff(Path(evidence.path), (cwd,)) != evidence:
            raise RetirementRefused("handoff_changed_during_commit_check")
        with Path(evidence.path).open("rb") as handle:
            actual = handle.read(HANDOFF_MAX_BYTES + 1)
        if len(actual) != evidence.size or hashlib.sha256(actual).hexdigest() != evidence.sha256:
            raise RetirementRefused("handoff_changed_during_commit_check")
        # Git checkouts may use CRLF. Require same committed text, not raw newline bytes.
        if content.replace(b"\r\n", b"\n") != actual.replace(b"\r\n", b"\n"):
            raise RetirementRefused("handoff_differs_from_committed_document")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if isinstance(exc, RetirementRefused):
            raise
        raise RetirementRefused("committed_handoff_unconfirmed") from exc
