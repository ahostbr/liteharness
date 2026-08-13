"""
Typed memory consolidation tool — deterministic decision router.

This module is the safety-critical, LLM-proof core of the memory-consolidation
pipeline. It walks a directory of memory items, embeds candidate-vs-existing
items via the LiteHarness embed service (POST http://127.0.0.1:7439/embed), and
maps cosine-similarity bands onto typed, auditable actions.

The whole point is that the *destructive floor is deterministic Python* — an LLM
sitting above this tool can *suggest* a REPLACE, but it can never make one happen
below the cosine threshold, because the router refuses to emit REPLACE (and the
apply path re-checks the same floor). Every file that would be overwritten is
pre-imaged first, so any mutation is byte-restorable.

Decision router (EXACT thresholds):
  cosine < 0.7                       -> KEEP_SEPARATE
  0.7 <= cosine < 0.9                -> MERGE_UPDATE (non-destructive) / SKIP (pure dup)
  cosine >= 0.9 AND contradiction    -> REPLACE (destructive, but PROPOSED ONLY)
  cosine >= 0.9 AND not contradiction -> SKIP (pure restatement)

The router NEVER returns REPLACE below 0.9 — it is structurally impossible, not a
convention. A contradiction flagged at 0.88 downgrades to MERGE_UPDATE.

Subcommands:
  propose        walk a memory dir, embed items, emit typed proposals as JSON.
                 Non-destructive: writes proposals to the ledger, snapshots
                 pre-images of any REPLACE target, mutates nothing.
  apply-replace  the ONLY destructive path: re-verify cosine >= 0.9, snapshot the
                 live pre-image, overwrite the target, mark the ledger applied.
  undo           restore a target file from its pre-image (byte-exact).

Usage:
  python -m liteharness.memory_consolidate propose [--dir DIR] [--candidates FILE]
  python -m liteharness.memory_consolidate apply-replace <mutation-id>
  python -m liteharness.memory_consolidate undo <mutation-id>
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import pathlib
import socket
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

# ── Constants ─────────────────────────────────────────────────────────────────────

EMBED_URL = "http://127.0.0.1:7439/embed"
EMBED_TIMEOUT = 30  # seconds

# Decision-router thresholds. These are the destructive floor — do not soften.
MERGE_THRESHOLD = 0.7    # >= this and < REPLACE_THRESHOLD -> MERGE_UPDATE / SKIP
REPLACE_THRESHOLD = 0.9  # >= this AND contradiction-flagged -> REPLACE (proposed)

# A near-identical pair inside the merge band is a pure duplicate, not new detail.
DUPLICATE_RATIO = 0.97

# Typed actions.
KEEP_SEPARATE = "KEEP_SEPARATE"
MERGE_UPDATE = "MERGE_UPDATE"
SKIP = "SKIP"
REPLACE = "REPLACE"

# The only destructive action. There is deliberately no DELETE action anywhere in
# this module: consolidation overwrites (recoverably), it never deletes.
DESTRUCTIVE_ACTIONS = frozenset({REPLACE})

DEFAULT_MEMORY_DIR = pathlib.Path.home() / ".claude" / "projects" / "C--Projects" / "memory"
DEFAULT_STATE_DIR = pathlib.Path.home() / ".litesuite" / "memory-consolidation"

PROPOSAL_SCHEMA = "memory-consolidate/proposal/v1"


# ── Helpers ───────────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two vectors.

    Mirrors liteharness.rag.embeddings.cosine_similarity (same zero-norm guard)
    but stays pure-Python so this safety tool has no numpy dependency.
    """
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def embed_texts(
    texts: list[str],
    *,
    url: str = EMBED_URL,
    timeout: int = EMBED_TIMEOUT,
) -> list[list[float]] | None:
    """POST texts to the embed service; return vectors or None on any failure.

    Mirrors the {"texts": [...]} -> {"vectors": [[...]]} contract of
    liteharness.embed_service. Graceful degradation: any transport/parse error
    returns None so callers can abort without emitting destructive proposals.
    """
    if not texts:
        return []
    body = json.dumps({"texts": texts}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read()
    except Exception as exc:  # noqa: BLE001 — any failure is a degradation signal
        print(f"[memory-consolidate] embed service unreachable: {exc}", file=sys.stderr)
        return None
    try:
        data = json.loads(raw)
        vectors = data.get("vectors")
    except (json.JSONDecodeError, AttributeError) as exc:
        print(f"[memory-consolidate] bad embed response: {exc}", file=sys.stderr)
        return None
    if not isinstance(vectors, list):
        return None
    return vectors


# ── Decision router (the deterministic destructive floor) ──────────────────────────


def route(cosine_score: float, *, contradiction: bool = False, duplicate: bool = False) -> str:
    """Map a cosine band + flags onto a typed action.

    This is the LLM-proof floor. REPLACE is returned ONLY inside the
    ``cosine_score >= REPLACE_THRESHOLD`` branch, so a REPLACE below 0.9 is
    structurally impossible — a contradiction flagged at, say, 0.88 falls through
    to the merge band and downgrades to MERGE_UPDATE. Callers cannot override this.
    """
    if cosine_score >= REPLACE_THRESHOLD:  # >= 0.9
        if contradiction:
            return REPLACE  # destructive — but only ever a *proposal* here
        return SKIP  # pure restatement / duplicate above the floor
    if cosine_score >= MERGE_THRESHOLD:  # 0.7 <= score < 0.9
        # Below the destructive floor: contradiction can NEVER escalate to REPLACE.
        if duplicate:
            return SKIP  # pure dup in the merge band — nothing new to fold in
        return MERGE_UPDATE  # non-destructive enrichment
    return KEEP_SEPARATE  # < 0.7 — distinct memories, leave both alone


def is_destructive(action: str) -> bool:
    return action in DESTRUCTIVE_ACTIONS


# ── Ledger + pre-image state ───────────────────────────────────────────────────────


def _ledger_path(state_dir: pathlib.Path) -> pathlib.Path:
    return state_dir / "ledger.jsonl"


def _pre_image_dir(state_dir: pathlib.Path) -> pathlib.Path:
    return state_dir / "pre-images"


def append_ledger(entry: dict, state_dir: pathlib.Path) -> None:
    """Append one JSON row to the append-only ledger."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _ledger_path(state_dir)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def read_ledger(state_dir: pathlib.Path) -> list[dict]:
    path = _ledger_path(state_dir)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def find_proposal(mutation_id: str, state_dir: pathlib.Path) -> dict | None:
    """Return the newest 'propose' row for a mutation id, or None."""
    match = None
    for row in read_ledger(state_dir):
        if row.get("event") == "propose" and row.get("mutation_id") == mutation_id:
            match = row
    return match


def is_applied(mutation_id: str, state_dir: pathlib.Path) -> bool:
    return any(
        row.get("event") == "apply" and row.get("mutation_id") == mutation_id
        for row in read_ledger(state_dir)
    )


def _snapshot_pre_image(
    mutation_id: str, target: pathlib.Path, state_dir: pathlib.Path
) -> pathlib.Path:
    """Copy the current bytes of ``target`` into the pre-image store, byte-exact."""
    dest_dir = _pre_image_dir(state_dir) / mutation_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target.name
    dest.write_bytes(target.read_bytes())
    return dest


# ── Single-writer lock ──────────────────────────────────────────────────────────────
#
# The lock serializes memory_consolidate TOOL runs against one another so two
# runs can never interleave ledger appends or race a pre-image + overwrite. It is
# an ADDITION to — never a replacement for — the deterministic destructive floor
# (REPLACE only at cosine >= 0.9, downgrade below, proposal-only apply, pre-image
# every mutation). The lock WRAPS that floor.
#
# It deliberately does NOT try to gate native Claude Code auto-dream: that path
# cannot be made to take this lock. Coexistence with native auto-dream rests on
# the tool's pre-imaging — every file this tool mutates is byte-restorable via
# `undo` — not on mutual exclusion with native.

LOCK_FILENAME = ".write.lock"
# A lock whose file mtime is older than this is presumed abandoned by a crashed
# run and is broken (with a warning) so consolidation can never wedge forever.
#
# NOTE: this is a HARD CEILING on any single locked operation.  It is safe only
# because propose/apply runs are seconds-long embed batches — no heartbeat is
# implemented.  If operations could ever exceed STALE_LOCK_SECONDS, a heartbeat
# (periodic mtime touch from the holder) would be needed to prevent a live
# holder from being stolen as stale.
STALE_LOCK_SECONDS = 15 * 60


class LockHeldError(RuntimeError):
    """The single-writer lock is held by another live, non-stale run.

    Subclasses RuntimeError so the CLI's existing RuntimeError handlers surface
    it as a clean non-zero exit ("another consolidation is in progress") rather
    than a traceback — and so it can never clobber the holder's work.
    """


def _lock_path(state_dir: pathlib.Path) -> pathlib.Path:
    return state_dir / LOCK_FILENAME


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "?"


def _pid_alive(pid: int) -> bool:
    """Best-effort, cross-platform liveness check for ``pid``.

    Prefers ``psutil.pid_exists`` (a hard dependency of this package). NEVER uses
    ``os.kill(pid, 0)`` on Windows — CPython maps signal 0 there onto
    TerminateProcess, which would kill the very process being probed. The POSIX
    fallback (signal 0) is safe and only used off-Windows.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil  # declared dependency; imported lazily to keep import light

        return bool(psutil.pid_exists(pid))
    except Exception:  # noqa: BLE001 — fall back if psutil is somehow unavailable
        pass
    if os.name == "nt":
        # Conservative: without psutil we cannot safely probe on Windows, so
        # assume the pid is alive (never break a lock we cannot verify as dead).
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False
    return True


def _read_lock(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _lock_is_stale(path: pathlib.Path, *, stale_seconds: float = STALE_LOCK_SECONDS) -> bool:
    """True if the lock is abandoned: file too old, OR its recorded pid is dead.

    A missing/corrupt lock body has no recorded pid, so only the age test can
    condemn it — this avoids racing a holder that has just created the file but
    not yet written its payload (fresh mtime -> not stale).
    """
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True  # vanished under us — effectively free
    if age > stale_seconds:
        return True
    info = _read_lock(path)
    pid = (info or {}).get("pid")
    if isinstance(pid, int) and not _pid_alive(pid):
        return True
    return False


class FileLock:
    """Single-writer advisory lock via atomic ``O_EXCL`` file creation.

    Acquire semantics:
      * free            -> create the lock, record pid + timestamp + token.
      * held (live)     -> raise ``LockHeldError`` (abort, never clobber).
      * held (stale)    -> break it with a warning (old mtime OR dead pid), then
                           acquire; a crashed run cannot wedge consolidation.

    Release only removes the lock if it is still OURS (token match), so if a later
    run legitimately broke ours as stale and took over, ``release`` won't delete
    theirs. Use as a context manager so release runs in ``__exit__`` (finally).
    """

    def __init__(self, state_dir, *, stale_seconds: float = STALE_LOCK_SECONDS) -> None:
        self.state_dir = pathlib.Path(state_dir)
        self.path = _lock_path(self.state_dir)
        self.stale_seconds = stale_seconds
        self._token: str | None = None
        self._held = False

    def _write_new(self) -> None:
        """Atomically create the lock file and stamp it. Raises FileExistsError."""
        token = uuid.uuid4().hex
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        fd = os.open(str(self.path), flags)
        try:
            payload = json.dumps(
                {"pid": os.getpid(), "ts": _now(), "token": token, "host": _hostname()}
            )
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        self._token = token

    def acquire(self) -> "FileLock":
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._write_new()
        except FileExistsError:
            if _lock_is_stale(self.path, stale_seconds=self.stale_seconds):
                info = _read_lock(self.path) or {}
                stale_token = info.get("token")
                print(
                    f"[memory-consolidate] WARNING: breaking stale lock at {self.path} "
                    f"(pid={info.get('pid')}, ts={info.get('ts')})",
                    file=sys.stderr,
                )
                # ATOMIC STEAL — rename the stale lock aside to a unique
                # per-acquirer path.  os.rename is atomic on both POSIX
                # and Windows: only ONE racer can move a given self.path.
                # The loser's rename raises FileNotFoundError (source
                # already moved by the winner).  The destination is unique
                # per uuid, so it never pre-exists (Windows os.rename
                # raises FileExistsError if dest exists — cannot happen).
                #
                # GUARD: only attempt the steal if we could read the lock's
                # token.  If stale_token is None (lock vanished between our
                # FileExistsError and our _read_lock, or file is corrupt),
                # skip the steal — a blind rename could move a FRESH lock
                # that a racing winner just created at self.path.
                if stale_token is not None:
                    steal_id = uuid.uuid4().hex
                    steal_target = self.path.parent / f"{self.path.name}.stale.{steal_id}"
                    try:
                        os.rename(str(self.path), str(steal_target))
                    except (FileNotFoundError, OSError):
                        # Another racer already stole (or someone released)
                        # the lock.  Fall through to a fresh O_EXCL attempt.
                        pass
                    else:
                        # We won the atomic rename.  Verify we moved the
                        # STALE lock we checked, not a fresh one a racing
                        # winner created between our _read_lock and here.
                        stolen_info = _read_lock(steal_target) or {}
                        if stolen_info.get("token") != stale_token:
                            # Moved a DIFFERENT (possibly live) lock — put
                            # it back and abort.  Never create a 2nd holder.
                            try:
                                os.rename(str(steal_target), str(self.path))
                            except OSError:
                                pass  # self.path recreated; orphan harmless
                            raise LockHeldError(
                                "another consolidation is in progress "
                                f"(stale-break raced: stole a live lock at {self.path})"
                            ) from None
                        # Correct file stolen.  Clean up the aside copy.
                        try:
                            steal_target.unlink()
                        except FileNotFoundError:
                            pass
                # Create our own lock via O_EXCL.
                try:
                    self._write_new()
                except FileExistsError:
                    raise LockHeldError(
                        "another consolidation is in progress "
                        f"(raced to re-acquire {self.path})"
                    ) from None
            else:
                info = _read_lock(self.path) or {}
                raise LockHeldError(
                    "another consolidation is in progress — lock held by pid "
                    f"{info.get('pid', '?')} since {info.get('ts', '?')} at {self.path}"
                ) from None
        self._held = True
        return self

    def release(self) -> None:
        if not self._held:
            return
        info = _read_lock(self.path)
        if info is not None and info.get("token") == self._token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self._held = False

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc) -> bool:
        self.release()
        return False


# ── Proposal construction ──────────────────────────────────────────────────────────


def _new_mutation_id(action: str) -> str:
    prefix = {REPLACE: "repl", MERGE_UPDATE: "merg", SKIP: "skip", KEEP_SEPARATE: "keep"}.get(
        action, "prop"
    )
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def build_proposal(
    *,
    action: str,
    cosine_score: float,
    candidate: str,
    existing: str,
    contradiction: bool = False,
    target_file: str | None = None,
    new_content: str | None = None,
    reason: str = "",
) -> dict:
    """Construct a typed proposal row (event='propose', applied=False)."""
    destructive = is_destructive(action)
    proposal = {
        "ts": _now(),
        "event": "propose",
        "mutation_id": _new_mutation_id(action),
        "action": action,
        "cosine": round(float(cosine_score), 6),
        "candidate": candidate,
        "existing": existing,
        "contradiction": bool(contradiction),
        "destructive": destructive,
        "target_file": target_file,
        "applied": False,
        "reason": reason,
    }
    if destructive and new_content is not None:
        proposal["new_content"] = new_content
        proposal["new_content_sha256"] = _sha256(new_content)
    return proposal


# ── propose ────────────────────────────────────────────────────────────────────────


def _load_items(memory_dir: pathlib.Path) -> list[tuple[str, str]]:
    """Return [(name, text)] for every memory item file, sorted by name.

    MEMORY.md is the human-facing index, not an item — it is skipped. Dotfiles
    and non-markdown files are ignored.
    """
    items: list[tuple[str, str]] = []
    for path in sorted(memory_dir.glob("*.md")):
        if path.name == "MEMORY.md" or path.name.startswith("."):
            continue
        try:
            items.append((path.name, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return items


def _text_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def propose(
    memory_dir: pathlib.Path,
    *,
    state_dir: pathlib.Path = DEFAULT_STATE_DIR,
    candidates: list[dict] | None = None,
    embed_url: str = EMBED_URL,
    embed_fn=embed_texts,
    stale_seconds: float = STALE_LOCK_SECONDS,
) -> dict:
    """Walk a memory dir, embed items, and emit typed proposals.

    Non-destructive. Returns the strict-JSON result dict (also printed by the
    CLI). If the embed service is unreachable, returns an aborted result with
    ZERO destructive proposals and writes nothing destructive to the ledger.

    ``candidates`` (optional): a list of {id, text, contradiction?} dicts — the
    only route by which a REPLACE can be *proposed*, because contradiction cannot
    be derived deterministically from embeddings alone. Even then it is proposed,
    never applied.
    """
    items = _load_items(memory_dir)
    result: dict = {
        "schema": PROPOSAL_SCHEMA,
        "generated_at": _now(),
        "dir": str(memory_dir),
        "thresholds": {"merge": MERGE_THRESHOLD, "replace": REPLACE_THRESHOLD},
        "embed_ok": True,
        "aborted": False,
        "proposals": [],
        "destructive_count": 0,
    }

    # Build the corpus to embed: existing items + any candidate texts.
    existing_texts = [text for _, text in items]
    candidate_texts = [c.get("text", "") for c in candidates] if candidates else []
    all_texts = existing_texts + candidate_texts

    if not all_texts:
        return result  # nothing to do

    vectors = embed_fn(all_texts, url=embed_url)
    if vectors is None or len(vectors) != len(all_texts):
        # Graceful degradation: embed service down or malformed. Emit ZERO
        # destructive proposals and abort. Nothing is written to the ledger.
        result["embed_ok"] = False
        result["aborted"] = True
        result["proposals"] = []
        result["destructive_count"] = 0
        return result

    existing_vecs = vectors[: len(existing_texts)]
    candidate_vecs = vectors[len(existing_texts):]

    proposals: list[dict] = []

    if candidates:
        # Candidate-vs-existing: each candidate is matched to its most similar
        # existing item; the candidate may carry a contradiction flag.
        for cand, cvec in zip(candidates, candidate_vecs):
            best_idx, best_cos = -1, -1.0
            for idx, evec in enumerate(existing_vecs):
                score = cosine(cvec, evec)
                if score > best_cos:
                    best_idx, best_cos = idx, score
            if best_idx < 0:
                continue
            ename, etext = items[best_idx]
            contradiction = bool(cand.get("contradiction", False))
            duplicate = _text_ratio(cand.get("text", ""), etext) >= DUPLICATE_RATIO
            action = route(best_cos, contradiction=contradiction, duplicate=duplicate)
            proposal = build_proposal(
                action=action,
                cosine_score=best_cos,
                candidate=str(cand.get("id", "<candidate>")),
                existing=ename,
                contradiction=contradiction,
                target_file=str(memory_dir / ename) if action == REPLACE else None,
                new_content=cand.get("text") if action == REPLACE else None,
                reason=_reason_for(action, best_cos, contradiction),
            )
            proposals.append(proposal)
    else:
        # Pairwise dedup walk over existing items. This path can NEVER emit
        # REPLACE (contradiction is not deterministically derivable), so it is
        # destruction-free by construction — it only surfaces MERGE/SKIP pairs.
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                score = cosine(existing_vecs[i], existing_vecs[j])
                if score < MERGE_THRESHOLD:
                    continue  # implicit KEEP_SEPARATE — not worth recording
                duplicate = _text_ratio(items[i][1], items[j][1]) >= DUPLICATE_RATIO
                action = route(score, contradiction=False, duplicate=duplicate)
                proposals.append(
                    build_proposal(
                        action=action,
                        cosine_score=score,
                        candidate=items[i][0],
                        existing=items[j][0],
                        contradiction=False,
                        reason=_reason_for(action, score, False),
                    )
                )

    # Persist proposals + snapshot pre-images for any destructive (REPLACE) target.
    # The single-writer lock wraps EVERY state mutation here (pre-image snapshots
    # and ledger appends) and is released in the context manager's __exit__
    # (finally). A concurrent live run aborts with LockHeldError rather than
    # interleaving writes; a stale/crashed run's lock is broken and reclaimed.
    if proposals:
        with FileLock(state_dir, stale_seconds=stale_seconds):
            for proposal in proposals:
                if proposal["destructive"] and proposal.get("target_file"):
                    target = pathlib.Path(proposal["target_file"])
                    if target.exists():
                        snap = _snapshot_pre_image(
                            proposal["mutation_id"], target, state_dir
                        )
                        proposal["pre_image"] = str(snap)
                append_ledger(proposal, state_dir)

    result["proposals"] = proposals
    result["destructive_count"] = sum(1 for p in proposals if p["destructive"])
    return result


def _reason_for(action: str, score: float, contradiction: bool) -> str:
    if action == REPLACE:
        return f"contradiction at cosine {score:.3f} >= {REPLACE_THRESHOLD} — REPLACE proposed for review"
    if action == MERGE_UPDATE:
        return f"cosine {score:.3f} in merge band [{MERGE_THRESHOLD}, {REPLACE_THRESHOLD}) — fold in new detail"
    if action == SKIP:
        return f"cosine {score:.3f} — pure duplicate/restatement, nothing to change"
    return f"cosine {score:.3f} < {MERGE_THRESHOLD} — distinct memories, keep separate"


# ── apply-replace (the only destructive path) ──────────────────────────────────────


def apply_replace(
    mutation_id: str,
    *,
    state_dir: pathlib.Path = DEFAULT_STATE_DIR,
    embed_url: str = EMBED_URL,
    reembed: bool = False,
    stale_seconds: float = STALE_LOCK_SECONDS,
) -> dict:
    """Apply a proposed REPLACE: re-verify the floor, pre-image, overwrite, log.

    Raises RuntimeError if the proposal is missing, not a REPLACE, already
    applied, or fails the deterministic cosine >= 0.9 floor. On any refusal the
    target file is left untouched.

    The whole critical section (validation + pre-image + overwrite + ledger
    append) runs under the single-writer lock, so a concurrent run aborts with
    LockHeldError before touching the file and the ``is_applied`` re-check is
    serialized against a racing apply. The lock is released in ``finally`` (the
    ``with`` block) on every path, success or exception.
    """
    with FileLock(state_dir, stale_seconds=stale_seconds):
        proposal = find_proposal(mutation_id, state_dir)
        if proposal is None:
            raise RuntimeError(f"no proposal found for mutation-id {mutation_id!r}")
        if proposal.get("action") != REPLACE:
            raise RuntimeError(
                f"mutation {mutation_id!r} is {proposal.get('action')!r}, not REPLACE — "
                "apply-replace is the only destructive path and only handles REPLACE"
            )
        if is_applied(mutation_id, state_dir):
            raise RuntimeError(f"mutation {mutation_id!r} already applied")

        # Deterministic destructive floor — re-checked at apply time, LLM-proof.
        stored_cos = float(proposal.get("cosine", 0.0))
        effective_cos = stored_cos
        if reembed:
            recomputed = _reembed_cosine(proposal, embed_url)
            if recomputed is not None:
                effective_cos = min(stored_cos, recomputed)
        if effective_cos < REPLACE_THRESHOLD:
            raise RuntimeError(
                f"REFUSED: cosine {effective_cos} < {REPLACE_THRESHOLD} destructive floor — "
                "REPLACE is only permitted at or above the floor"
            )

        target_file = proposal.get("target_file")
        new_content = proposal.get("new_content")
        if not target_file or new_content is None:
            raise RuntimeError(
                f"mutation {mutation_id!r} lacks target_file/new_content — cannot apply"
            )
        target = pathlib.Path(target_file)
        if not target.exists():
            raise RuntimeError(f"target file {target_file!r} does not exist")

        # Snapshot the LIVE bytes right before overwriting — this is the
        # authoritative pre-image for undo (the propose-time snapshot may be stale).
        pre_image = _snapshot_pre_image(mutation_id, target, state_dir)
        original_bytes = target.read_bytes()

        # Byte-exact write: encode once and write raw bytes so the on-disk bytes are
        # EXACTLY what new_content_sha256 is computed over. write_text() would apply
        # platform newline translation (\n -> \r\n on Windows), which breaks the
        # ledger sha256 integrity for multi-line content.
        new_bytes = new_content.encode("utf-8")
        target.write_bytes(new_bytes)

        append_ledger(
            {
                "ts": _now(),
                "event": "apply",
                "mutation_id": mutation_id,
                "action": REPLACE,
                "target_file": str(target),
                "pre_image": str(pre_image),
                "cosine": effective_cos,
                "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
                "new_content_sha256": hashlib.sha256(new_bytes).hexdigest(),
                "applied": True,
            },
            state_dir,
        )
        return {
            "mutation_id": mutation_id,
            "applied": True,
            "target_file": str(target),
            "pre_image": str(pre_image),
            "cosine": effective_cos,
        }


def _reembed_cosine(proposal: dict, embed_url: str) -> float | None:
    """Best-effort re-embed of candidate vs existing; None if unavailable."""
    cand = proposal.get("new_content")
    target_file = proposal.get("target_file")
    if cand is None or not target_file:
        return None
    target = pathlib.Path(target_file)
    if not target.exists():
        return None
    existing = target.read_text(encoding="utf-8")
    vectors = embed_texts([cand, existing], url=embed_url)
    if not vectors or len(vectors) != 2:
        return None
    return cosine(vectors[0], vectors[1])


# ── undo ───────────────────────────────────────────────────────────────────────────


def undo(mutation_id: str, *, state_dir: pathlib.Path = DEFAULT_STATE_DIR) -> dict:
    """Restore a target file from its pre-image, byte-exact."""
    apply_row = None
    for row in read_ledger(state_dir):
        if row.get("event") == "apply" and row.get("mutation_id") == mutation_id:
            apply_row = row
    if apply_row is None:
        raise RuntimeError(f"no applied mutation {mutation_id!r} to undo")

    pre_image = pathlib.Path(apply_row["pre_image"])
    target = pathlib.Path(apply_row["target_file"])
    if not pre_image.exists():
        raise RuntimeError(f"pre-image {pre_image} missing — cannot undo")

    target.write_bytes(pre_image.read_bytes())
    append_ledger(
        {
            "ts": _now(),
            "event": "undo",
            "mutation_id": mutation_id,
            "target_file": str(target),
            "restored_from": str(pre_image),
        },
        state_dir,
    )
    return {"mutation_id": mutation_id, "restored": str(target), "from": str(pre_image)}


# ── CLI ─────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory_consolidate",
        description="Typed, deterministic memory-consolidation tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_propose = sub.add_parser("propose", help="emit typed consolidation proposals (non-destructive)")
    p_propose.add_argument("--dir", default=str(DEFAULT_MEMORY_DIR), help="memory directory to walk")
    p_propose.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="ledger/pre-image state dir")
    p_propose.add_argument("--candidates", help="path to a candidates JSON file (enables REPLACE proposals)")
    p_propose.add_argument("--embed-url", default=EMBED_URL, help="embed service /embed URL")

    p_apply = sub.add_parser("apply-replace", help="apply a proposed REPLACE (ONLY destructive path)")
    p_apply.add_argument("mutation_id")
    p_apply.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p_apply.add_argument("--embed-url", default=EMBED_URL)
    p_apply.add_argument("--reembed", action="store_true", help="re-embed and re-verify the floor before applying")

    p_undo = sub.add_parser("undo", help="restore a target file from its pre-image")
    p_undo.add_argument("mutation_id")
    p_undo.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))

    args = parser.parse_args(argv)

    if args.command == "propose":
        candidates = None
        if args.candidates:
            candidates = json.loads(pathlib.Path(args.candidates).read_text(encoding="utf-8"))
        try:
            result = propose(
                pathlib.Path(args.dir),
                state_dir=pathlib.Path(args.state_dir),
                candidates=candidates,
                embed_url=args.embed_url,
            )
        except LockHeldError as exc:
            print(json.dumps({"error": str(exc), "aborted": True}))
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "apply-replace":
        try:
            result = apply_replace(
                args.mutation_id,
                state_dir=pathlib.Path(args.state_dir),
                embed_url=args.embed_url,
                reembed=args.reembed,
            )
        except RuntimeError as exc:
            print(json.dumps({"error": str(exc), "applied": False}))
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "undo":
        try:
            result = undo(args.mutation_id, state_dir=pathlib.Path(args.state_dir))
        except RuntimeError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        print(json.dumps(result, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
