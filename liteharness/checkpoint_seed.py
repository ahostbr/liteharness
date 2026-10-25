"""Exact cognitive-method seed: a bounded reference, never prompt text and never launch authority.

SAVE records which method file a seat's process was actually launched with (the v2 `.cog_printed`
marker the SessionStart hook writes), joined to the seat's own process. RESTORE hands the reference
to the governed recovery path, which re-verifies it against the library before any use. A seed
never overrides an existing managed LiteTUI home, and legacy or unjoinable markers stay unsupported.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .checkpoint_contract import ContractError, Receipt, Seat, SeedEvidence, decode, wire
from .checkpoint_store import CheckpointStore, checked_path, _unique_object, _invalid_constant

MARKER_DIR = '.cog_printed'
BIRTH_TOLERANCE_MS = 2000  # the same tolerance restore liveness applies to a recorded process birth


def _json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def _registry(harness_root: Path, agent_id: str) -> dict | None:
    for directory in ('agents', 'retired-agents'):
        try:
            row = _json(harness_root / directory / f'{agent_id}.json')
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            return row
    return None


def observe(seat: Seat, checkpoint_id: str, harness_root: Path | str) -> SeedEvidence | str | None:
    """A SeedEvidence, a one-line reason it is unavailable, or None when no method was ever printed."""
    root = Path(harness_root)
    marker = root / MARKER_DIR / seat.agent_id
    if not marker.is_file():
        return None  # not a polymath seat, or one that predates the marker: nothing to say
    try:
        event = _json(marker)
    except (OSError, ValueError):
        return 'marker unreadable or legacy (no process identity)'
    if not isinstance(event, dict) or event.get('version') != 2:
        return 'marker is legacy or unsupported (no process identity)'
    pid, birth, session = event.get('pid'), event.get('birth'), event.get('session')
    method, body = event.get('method'), event.get('body')
    if (type(pid) is not int or pid <= 0 or type(birth) not in (int, float) or birth <= 0
            or not (session is None or isinstance(session, str)) or not isinstance(method, str)
            or not isinstance(body, str)):
        return 'marker fields are malformed'
    row = _registry(root, seat.agent_id)
    recorded = row.get('session_process_started_at') if row else None
    if (not row or row.get('session_pid') != pid or type(recorded) not in (int, float)
            or abs(float(recorded) - float(birth) * 1000) >= BIRTH_TOLERANCE_MS):
        return 'marker does not belong to the seat\'s recorded process'
    path = Path(method)
    try:
        text = path.read_text(encoding='utf-8') if path.is_absolute() else None
    except OSError:
        text = None
    if text is None:
        return 'method file is missing or not absolute'
    if hashlib.sha256(text.encode('utf-8')).hexdigest() != body:
        return 'method file changed since it was printed'
    seed = SeedEvidence(1, 'cognitive-seed-v1', checkpoint_id, seat.agent_id, str(path), body, pid,
                        float(birth), session or None)
    try:
        wire(seed)
    except ContractError as exc:
        return f'seed does not satisfy the contract: {exc}'
    return seed


def retain(store: CheckpointStore, seat: Seat, observation: SeedEvidence, at: str) -> Receipt:
    from .checkpoint_save import evidence
    path, digest = evidence(store, observation.checkpoint_id, seat.agent_id, 'seed', wire(observation))
    receipt = Receipt(str(uuid4()), observation.checkpoint_id, seat.agent_id, 'seed', seat.source_revision,
                      str(path), digest, True, 'native', at)
    wire(receipt)
    return receipt


def read(receipt: Receipt, checkpoint_id: str, seat_id: str) -> SeedEvidence:
    wire(receipt)
    if (receipt.kind != 'seed' or receipt.checkpoint_id != checkpoint_id or receipt.seat_id != seat_id
            or not receipt.durable):
        raise ContractError('Seed receipt identity or durability mismatch')
    try:
        raw = checked_path(receipt.reference).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt.sha256:
            raise ContractError('Seed receipt hash mismatch')
        value = decode(SeedEvidence, json.loads(raw, object_pairs_hook=_unique_object,
                                                parse_constant=_invalid_constant))
    except (OSError, ValueError) as exc:
        raise ContractError(f'Invalid seed receipt: {exc}') from exc
    if (value.checkpoint_id, value.seat_id) != (checkpoint_id, seat_id):
        raise ContractError('Seed evidence checkpoint/seat mismatch')
    return value


def reference(receipts: list[Receipt], seat: Seat, checkpoint_id: str) -> tuple[dict | None, str | None]:
    """(bounded reference for the recovery path, None) | (None, reason) | (None, None) when never saved.

    The reference is only what SAVE recorded. Whether the library still resolves that method is the
    recovery path's check, made at launch; here only the receipt itself is validated.
    """
    matches = [r for r in receipts if r.kind == 'seed' and r.seat_id == seat.agent_id]
    if not matches:
        return None, None
    if len(matches) != 1:
        return None, 'ambiguous seed receipts'
    try:
        seed = read(matches[0], checkpoint_id, seat.agent_id)
    except ContractError as exc:
        return None, f'seed-evidence-invalid: {exc}'
    return {'version': 1, 'path': seed.method_path, 'bodySha256': seed.body_sha256}, None
