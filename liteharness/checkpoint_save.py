"""One-call save: freeze the roster, publish it, then collect receipts seat by seat.

The roster alone is the cure for the 2026-10-19 failure, so an `incomplete` manifest is
published BEFORE any seat is asked for anything. Receipts come through a small process
seam (`SaveEnv`): cooperative inbox handoff, a configured compact command for Claude
seats, LiteTUI's native checkpoint verbs. This module never commits, pushes, stashes,
kills, reboots or compacts the coordinator, and never reaches into private scripts.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Callable
from uuid import uuid4

from . import config
from .checkpoint_contract import (Blocker, Coordinator, ContractError, Decision, Decisions,
                                  ExpectedSeat, Manifest, RECOMMENDED_DECISIONS, Receipt, Result,
                                  Seat, wire)
from .checkpoint_ops import OperationLog, make_result, seat_result
from .checkpoint_roster import (Discovery, claude_projects_root, discover_current,
                                roster_census)
from .checkpoint_store import CheckpointStore, _atomic_json, _bytes, _mkdir, checked_path

REPLY = re.compile(r'CHECKPOINT-HANDOFF\s+(?P<path>.+?)\s+(?P<disposition>idle|waiting|held)\s*$',
                   re.IGNORECASE | re.MULTILINE)
REQUEST = ('CHECKPOINT {checkpoint_id}: write your handoff now (named paths, shas, owed work; do not '
           'commit, push, stash, rebuild or start new work). Then reply to {mailbox} by inbox with '
           'exactly one line: CHECKPOINT-HANDOFF <absolute path of your handoff file> <idle|waiting|held>. '
           'idle = nothing owed; waiting = waiting on others; held = mid-work or a mutating tool is in '
           'flight. Stay in your current state. Do not compact yourself.')
LITETUI_BOUNDARY_WAIT = 120.0


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _inbox_send(sender: str, recipient: str, body: str) -> str:
    from . import inbox
    return inbox.send(sender, recipient, body, msg_type='TASK', priority='high')


def _inbox_poll(mailbox: str) -> list[dict]:
    from . import inbox
    return inbox.read_and_claim(mailbox)


def _no_window() -> dict:
    """No console flash for a child of a worker that has none (Windows only)."""
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def _run(argv: list[str], timeout: float) -> int:
    return subprocess.run(argv, timeout=timeout, capture_output=True, **_no_window()).returncode


def _litetui(agent_id: str, command: dict) -> dict:
    from . import litetui_client
    return litetui_client.request(agent_id, command)


def _layout() -> tuple[str, set[str]] | None:
    """(revision, leaf ids) of the live canvas layout, or None when the bridge is unreachable."""
    from . import cli
    try:
        context = cli._bridge_request('GET', '/context')
    except Exception:
        return None
    panes = context.get('activePanes') if isinstance(context, dict) else None
    if not isinstance(panes, list):
        return None
    shape = sorted((str(p.get('id')), sorted(str(leaf.get('id')) for leaf in p.get('leaves', [])
                                              if isinstance(leaf, dict))) for p in panes if isinstance(p, dict))
    return (hashlib.sha256(json.dumps(shape).encode()).hexdigest()[:16],
            {leaf for _, leaves in shape for leaf in leaves})


@dataclass
class SaveEnv:
    """The process boundary. Tests replace it; nothing below it is mocked."""
    now: Callable[[], datetime] = _utc
    sleep: Callable[[float], None] = time.sleep
    send: Callable[[str, str, str], str] = _inbox_send
    poll: Callable[[str], list[dict]] = _inbox_poll
    run: Callable[[list[str], float], int] = _run
    litetui: Callable[[str, dict], dict] = _litetui
    layout: Callable[[], tuple[str, set[str]] | None] = _layout
    live: Callable[[dict], bool] | None = None
    handoff_timeout: float = 120.0
    compact_timeout: float = 900.0
    poll_interval: float = 2.0
    compact_command: list[str] | None = None

    @classmethod
    def from_config(cls, **overrides) -> 'SaveEnv':
        configured = (config.load().get('checkpoint') or {}).get('compact_command')
        if configured is not None and not (isinstance(configured, list) and configured
                                           and all(isinstance(a, str) for a in configured)):
            configured = None  # an argv list or nothing: never a shell string
        return cls(compact_command=configured, **overrides)


@dataclass
class SaveParams:
    harness_root: str
    coordinator_id: str
    coordinator_handoff: str
    checkpoint_id: str
    include_offline: list[str] = field(default_factory=list)
    exclude_ids: list[str] = field(default_factory=list)
    litetui_root: str | None = None
    cwd_roots: list[str] | None = None

    def wire(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_wire(cls, value: dict) -> 'SaveParams':
        return cls(**value)


@dataclass
class Collected:
    """What one seat produced; blockers are per-seat, disposition fails safe to held."""
    receipts: list[Receipt] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    disposition: str = 'held'
    source_revision: str | None = None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence(store: CheckpointStore, checkpoint_id: str, seat_id: str, kind: str, payload: dict) -> tuple[Path, str]:
    """Small JSON evidence file under the checkpoint; its sha is the receipt's sha."""
    path = checked_path(store.root / checkpoint_id / 'receipts' / seat_id / f'{kind}.json', beneath=store.root)
    _mkdir(path.parent)
    _atomic_json(path, payload)
    return path, sha256_file(path)


def reference_roots(store: CheckpointStore, params: SaveParams, seats: list[Seat]) -> list[Path]:
    """Where a receipt reference may live: never outside what the host/seat legitimately owns."""
    roots = {checked_path(params.harness_root), claude_projects_root().parent,
             Path(params.coordinator_handoff).parent}
    for seat in seats:
        roots.update((Path(seat.cwd), Path(seat.data_root)))
        if seat.owner_profile:
            roots.add(Path(seat.owner_profile.memory_directory))
    return sorted(root for root in roots if root.is_dir())


def observe_compaction(transcript: Path, since: datetime) -> dict | None:
    """Newest Claude `compact_boundary` record stamped at or after `since`, else None."""
    found = None
    try:
        with transcript.open(encoding='utf-8') as stream:
            for line in stream:
                if '"compact_boundary"' not in line:
                    continue
                try:
                    record = json.loads(line)
                    stamp = datetime.fromisoformat(str(record['timestamp']).replace('Z', '+00:00'))
                except (ValueError, KeyError):
                    continue
                if record.get('subtype') == 'compact_boundary' and stamp >= since:
                    found = {'uuid': record.get('uuid'), 'timestamp': record['timestamp']}
    except OSError:
        return None
    return found


def _receipt(checkpoint_id: str, seat: Seat, kind: str, revision: str, path: Path, digest: str, *,
             durable: bool, assurance: str, at: datetime) -> Receipt:
    receipt = Receipt(str(uuid4()), checkpoint_id, seat.agent_id, kind, revision, str(path), digest,
                      durable, assurance, at.isoformat())
    wire(receipt)
    return receipt


class Saver:
    def __init__(self, store: CheckpointStore, params: SaveParams, env: SaveEnv, operation_id: str):
        self.store, self.params, self.env, self.operation_id = store, params, env, operation_id
        self.log = OperationLog(store, operation_id)
        self.mailbox = f'checkpoint-{operation_id}'
        self._last = env.now()
        self.layout = None  # (revision, leaf ids) as frozen; None until freeze() or when unobserved
        self.prepared: list[str] = []  # LiteTUI seats THIS save put on hold: the only holds it may release

    def tick(self) -> datetime:
        """Receipt clock: never earlier than the previous receipt of this save."""
        self._last = max(self._last, self.env.now())
        return self._last

    # -- freeze --------------------------------------------------------------------------
    def freeze(self) -> Manifest:
        params, env = self.params, self.env
        self.layout = env.layout()
        discovery = discover_current(harness_root=params.harness_root, include_offline=params.include_offline,
                                     exclude_ids=params.exclude_ids, litetui_root=params.litetui_root,
                                     layout_revision=self.layout[0] if self.layout else 'unobserved',
                                     live=env.live)
        census = roster_census(discovery, harness_root=params.harness_root, cwd_roots=params.cwd_roots)
        if params.coordinator_id not in {r.expected.agent_id for r in census.rows}:
            raise ContractError('Coordinator is not a live seat in the registry; nothing was saved')
        handoff = checked_path(params.coordinator_handoff)
        if not handoff.is_file():
            raise ContractError('Coordinator handoff file is absent')
        import psutil
        from . import __version__
        decisions = Decisions(**{k: Decision(v, 'unconfirmed-recommendation')
                                 for k, v in RECOMMENDED_DECISIONS.items()})
        manifest = Manifest(
            1, params.checkpoint_id, env.now().isoformat(), socket.gethostname(),
            str(int(psutil.boot_time())), {'liteharness': __version__}, decisions,
            Coordinator(params.coordinator_id, 'external-manual-return', str(handoff), sha256_file(handoff)),
            [r.expected for r in census.rows], [r.seat for r in census.rows if r.seat is not None],
            list(census.exclusions), [], [], list(census.blockers), 'incomplete')
        self.publish(manifest)
        return manifest

    def publish(self, manifest: Manifest) -> None:
        self.store.publish(manifest, authorized_reference_roots=reference_roots(
            self.store, self.params, manifest.seats))

    # -- per seat ------------------------------------------------------------------------
    def request_handoffs(self, seats: list[Seat]) -> None:
        for seat in seats:
            self.env.send(self.mailbox, seat.agent_id,
                          REQUEST.format(checkpoint_id=self.params.checkpoint_id, mailbox=self.mailbox))

    def await_handoffs(self, seats: list[Seat]) -> dict[str, tuple[Path, str]]:
        """Collect replies; a seat that never answers stays absent (blocker), never invented."""
        waiting, replies = {s.agent_id for s in seats}, {}
        deadline = self.env.now().timestamp() + self.env.handoff_timeout
        while waiting:
            for message in self.env.poll(self.mailbox):
                sender, match = message.get('from'), REPLY.search(str(message.get('body', '')))
                if sender in waiting and match:
                    replies[sender] = (Path(match['path'].strip().strip('"\'`')), match['disposition'].lower())
                    waiting.discard(sender)
            if not waiting or self.env.now().timestamp() >= deadline:
                break
            self.env.sleep(self.env.poll_interval)
        return replies

    def collect_claude(self, manifest: Manifest, seat: Seat, reply: tuple[Path, str] | None,
                       requested_at: datetime, roots: list[Path]) -> Collected:
        out, cid = Collected(), manifest.checkpoint_id
        if reply is None:
            out.blockers.append(Blocker('handoff-missing', seat.agent_id,
                                        'Seat did not report a handoff in time; ask it, then retry this seat'))
            return out
        path, out.disposition = reply
        if not self.under_roots(path, roots):
            out.blockers.append(Blocker('handoff-invalid', seat.agent_id,
                                        'Reported handoff is not an existing file under an authorized root'))
            out.disposition = 'held'
            return out
        revision = seat.source_revision
        out.receipts.append(_receipt(cid, seat, 'handoff', revision, path.resolve(), sha256_file(path), durable=True,
                                     assurance='cooperative', at=self.tick()))
        if out.disposition == 'held':
            return self.not_compacted(out, cid, seat, revision)
        compaction = self.compact_claude(manifest, seat, requested_at)
        if compaction is None:
            return self.unsupported(out, cid, seat, revision)
        out.receipts.extend(compaction.receipts)
        out.blockers.extend(compaction.blockers)
        return out

    @staticmethod
    def under_roots(path: Path, roots: list[Path]) -> bool:
        """Containment on RESOLVED paths: case, short names and `.` segments cannot sneak out or in."""
        if not (path.is_absolute() and '..' not in path.parts and path.is_file()):
            return False
        real = path.resolve()
        return any(real.is_relative_to(root.resolve()) for root in roots)

    def not_compacted(self, out: Collected, cid: str, seat: Seat, revision: str) -> Collected:
        """A mid-work seat keeps its handoff but is never compacted: that would end its turn."""
        self.unsupported(out, cid, seat, revision, blocker=False)
        out.blockers.append(Blocker('held-not-compacted', seat.agent_id,
                                    'Mid-work: let it finish or reach a safe stop, then retry this seat'))
        return out

    def unsupported(self, out: Collected, cid: str, seat: Seat, revision: str, *,
                    blocker: bool = True) -> Collected:
        for kind in ('compact', 'boundary'):
            path, digest = evidence(self.store, cid, seat.agent_id, kind,
                                    {'kind': kind, 'result': 'unsupported', 'reason': 'no compact_command configured'})
            out.receipts.append(_receipt(cid, seat, kind, revision, path, digest, durable=False,
                                         assurance='unsupported', at=self.tick()))
        if blocker:
            out.blockers.append(Blocker('compact-unsupported', seat.agent_id,
                                        'Configure checkpoint.compact_command, then retry this seat'))
        return out

    def compact_claude(self, manifest: Manifest, seat: Seat, requested_at: datetime) -> Collected | None:
        """None = no compact command configured (distinct from a failed/unobserved compaction)."""
        out, cid, argv = Collected(), manifest.checkpoint_id, self.env.compact_command
        if not argv:
            return None
        resolved = [a.replace('{agent_id}', seat.agent_id).replace('{name}', seat.name) for a in argv]
        try:
            returncode = self.env.run(resolved, self.env.compact_timeout)
        except (OSError, subprocess.SubprocessError):
            returncode = None
        transcript = self.transcript_of(seat)
        marker = observe_compaction(transcript, requested_at) if transcript else None
        revision = seat.source_revision
        path, digest = evidence(self.store, cid, seat.agent_id, 'compact',
                                {'kind': 'compact', 'returncode': returncode, 'observed': marker})
        ok = returncode == 0 and marker is not None
        out.receipts.append(_receipt(cid, seat, 'compact', revision, path, digest, durable=ok,
                                     assurance='cooperative' if ok else 'unsupported', at=self.tick()))
        if not ok:
            out.blockers.append(Blocker('compact-failed' if returncode != 0 else 'compact-unobserved', seat.agent_id,
                                        'Compact command did not produce an observed compaction boundary'))
        stat = transcript.stat() if transcript and transcript.exists() else None
        boundary = {'kind': 'boundary', 'transcript': str(transcript) if transcript else None,
                    'size': stat.st_size if stat else None, 'mtime_ns': stat.st_mtime_ns if stat else None,
                    'compact_record': marker}
        path, digest = evidence(self.store, cid, seat.agent_id, 'boundary', boundary)
        out.receipts.append(_receipt(cid, seat, 'boundary', revision, path, digest, durable=ok,
                                     assurance='cooperative' if ok else 'unsupported', at=self.tick()))
        return out

    @staticmethod
    def transcript_of(seat: Seat) -> Path | None:
        found = list(Path(seat.data_root).glob(f'*/{seat.conversation_id}.jsonl'))
        return found[0] if len(found) == 1 else None

    def collect_litetui(self, manifest: Manifest, seat: Seat, reply: tuple[Path, str] | None,
                        roots: list[Path]) -> Collected:
        out, cid = Collected(), manifest.checkpoint_id
        if reply is None:
            out.blockers.append(Blocker('handoff-missing', seat.agent_id,
                                        'Seat did not report a handoff in time; ask it, then retry this seat'))
            return out
        path, out.disposition = reply
        home_handoff = Path(seat.data_root) / '.agents' / seat.name / 'handoff.md'
        if not self.under_roots(path, roots):
            out.blockers.append(Blocker('handoff-invalid', seat.agent_id,
                                        'Reported handoff is not an existing file under an authorized root'))
            out.disposition = 'held'
            return out
        handoff_at = self.tick()
        try:
            status = self.env.litetui(seat.agent_id, {'type': 'gui.checkpoint.prepare', 'checkpoint_id': cid,
                                                      'disposition': out.disposition})
            self.prepared.append(seat.agent_id)
            if out.disposition == 'held':
                out.receipts.append(_receipt(cid, seat, 'handoff', seat.source_revision, path.resolve(),
                                             sha256_file(path), durable=True, assurance='cooperative', at=handoff_at))
                return self.not_compacted(out, cid, seat, seat.source_revision)
            digest = sha256_file(path)
            self.env.litetui(seat.agent_id, {'type': 'gui.conversations.compact', 'checkpoint_id': cid,
                                             'handoff_sha256': digest})
            compact_at = self.tick()  # before flush: the native boundary is stamped after it
            deadline = self.env.now().timestamp() + LITETUI_BOUNDARY_WAIT
            while True:
                status = self.env.litetui(seat.agent_id, {'type': 'gui.checkpoint.flush', 'checkpoint_id': cid})
                if status.get('boundary_ready') or self.env.now().timestamp() >= deadline:
                    break
                self.env.sleep(self.env.poll_interval)
        except ValueError as exc:
            out.blockers.append(Blocker('litetui-checkpoint-refused', seat.agent_id,
                                        f'{str(exc)[:160]}; inspect the seat, then retry'))
            return out
        native = status.get('receipt') if status.get('boundary_ready') else None
        if not native:
            out.blockers.append(Blocker('litetui-boundary-missing', seat.agent_id,
                                        f"Seat blockers: {', '.join(status.get('blockers') or ['none reported'])}"))
            return out
        boundary = Receipt(**native)
        revision = boundary.source_revision
        out.source_revision = revision
        out.disposition = status.get('disposition') or out.disposition
        out.receipts.append(_receipt(cid, seat, 'handoff', revision, path, sha256_file(path), durable=True,
                                     assurance='native' if path == home_handoff else 'cooperative', at=handoff_at))
        epath, edigest = evidence(self.store, cid, seat.agent_id, 'compact',
                                  {'kind': 'compact', 'verb': 'gui.conversations.compact', 'accepted': True})
        out.receipts.append(_receipt(cid, seat, 'compact', revision, epath, edigest, durable=True,
                                     assurance='native', at=compact_at))
        out.receipts.append(boundary)
        return out

    def layout_receipt(self, manifest: Manifest, seat: Seat, revision: str | None = None) -> Receipt | None:
        if seat.slot is None:
            return None
        # Evidenced = the seat's leaf is in the live /context layout at the frozen revision.
        # LiteSuite exposes no flush-receipt endpoint, so this is observation, not a flush proof.
        live = self.env.layout() if self.layout is None else self.layout
        observed = bool(live) and live[0] == seat.slot.layout_revision and seat.slot.leaf in live[1]
        path, digest = evidence(self.store, manifest.checkpoint_id, seat.agent_id, 'layout',
                                {'kind': 'layout', 'slot': {k: getattr(seat.slot, k) for k in
                                 ('workspace', 'thread', 'pane', 'leaf', 'tab', 'layout_revision')},
                                 'source': 'registry-row+context-activePanes', 'observed': observed})
        return _receipt(manifest.checkpoint_id, seat, 'layout', seat.slot.layout_revision, path, digest,
                        durable=observed, assurance='native' if observed else 'unsupported', at=self.tick())

    # -- whole save ----------------------------------------------------------------------
    def run(self, only: list[str] | None = None) -> Result:
        try:
            manifest = self.store.read_manifest(self.params.checkpoint_id)
            if self.store.read_manifest(manifest.checkpoint_id).state == 'complete' and not only:
                return self.report(manifest, 'complete')
        except ContractError:
            manifest = self.freeze()
        self.log.event('roster-frozen', None, self.report(manifest, 'running'))
        roots = reference_roots(self.store, self.params, manifest.seats)
        todo = [s for s in manifest.seats if only is None or s.agent_id in only]
        peers = [s for s in todo if s.agent_id != manifest.coordinator.agent_id]
        requested_at = self.env.now()
        self.request_handoffs(peers)
        replies = self.await_handoffs(peers)
        for seat in todo:
            if self.cancelled():
                self.release_holds()
                result = self.report(manifest, 'canceled')
                self.log.event('final', None, result)
                return result
            manifest = self.collect_one(manifest, seat, replies.get(seat.agent_id), requested_at, roots)
            self.log.event('seat-collected', seat.agent_id, self.report(manifest, 'running'))
        manifest = self.finalize(manifest)
        result = self.report(manifest, manifest.state)
        self.log.event('final', None, result)
        return result

    def cancelled(self) -> bool:
        return any(e.stage == 'cancel-requested' for e in self.log.operation().events)

    def release_holds(self) -> None:
        """Cancel releases only the holds this save made; no seat is stopped, compacted or replayed."""
        for agent_id in self.prepared:
            try:
                self.env.litetui(agent_id, {'type': 'gui.checkpoint.release', 'checkpoint_id': self.params.checkpoint_id,
                                            'reconciled': True})
            except ValueError:
                pass  # a refused release stays held: visible, never forced

    def collect_one(self, manifest: Manifest, seat: Seat, reply, requested_at, roots) -> Manifest:
        if seat.agent_id == manifest.coordinator.agent_id:
            got = self.collect_coordinator(manifest, seat, roots)
        elif seat.cli == 'litetui':
            got = self.collect_litetui(manifest, seat, reply, roots)
        else:
            got = self.collect_claude(manifest, seat, reply, requested_at, roots)
        layout = self.layout_receipt(manifest, replace(seat, source_revision=got.source_revision or seat.source_revision))
        if layout:
            got.receipts.append(layout)
        updated = replace(seat, disposition=got.disposition, source_revision=got.source_revision or seat.source_revision)
        seats = [updated if s.agent_id == seat.agent_id else s for s in manifest.seats]
        receipts = [r for r in manifest.receipts if r.seat_id != seat.agent_id] + got.receipts
        keep = [b for b in manifest.blockers if b.seat_id != seat.agent_id or b.code == 'seat-unresolved']
        merged = replace(manifest, seats=seats, receipts=receipts, blockers=keep + got.blockers)
        self.publish(merged)
        return merged

    def collect_coordinator(self, manifest: Manifest, seat: Seat, roots: list[Path]) -> Collected:
        """The coordinator's handoff is supplied; its compaction is observed, never requested."""
        out, cid = Collected(disposition='idle'), manifest.checkpoint_id
        handoff = Path(manifest.coordinator.read_first)
        revision = seat.source_revision
        out.receipts.append(_receipt(cid, seat, 'handoff', revision, handoff, sha256_file(handoff), durable=True,
                                     assurance='cooperative', at=self.tick()))
        marker = None
        transcript = self.transcript_of(seat) if seat.cli == 'claude' else None
        if transcript:
            since = datetime.fromtimestamp(handoff.stat().st_mtime, timezone.utc)
            marker = observe_compaction(transcript, since)
        if marker is None:
            out.blockers.append(Blocker('coordinator-compaction-pending', seat.agent_id,
                                        'Compact the coordinator after its handoff, then retry its seat'))
            return self.unsupported(out, cid, seat, revision, blocker=False)
        for kind in ('compact', 'boundary'):
            path, digest = evidence(self.store, cid, seat.agent_id, kind,
                                    {'kind': kind, 'observed': marker, 'transcript': str(transcript)})
            out.receipts.append(_receipt(cid, seat, kind, revision, path, digest, durable=True,
                                         assurance='cooperative', at=self.tick()))
        return out

    def finalize(self, manifest: Manifest) -> Manifest:
        """Complete only when the contract accepts it; otherwise stay incomplete and say why."""
        blockers = list(manifest.blockers)
        for seat in manifest.seats:
            boundary = next((r for r in manifest.receipts if r.seat_id == seat.agent_id and r.kind == 'boundary'
                             and r.durable), None)
            transcript = self.transcript_of(seat) if seat.cli == 'claude' else None
            if boundary is not None and transcript is not None:
                try:
                    seen = json.loads(Path(boundary.reference).read_text(encoding='utf-8')).get('size')
                except (OSError, ValueError):
                    seen = None
                if isinstance(seen, int) and transcript.stat().st_size > seen \
                        and seat.agent_id != manifest.coordinator.agent_id:
                    blockers.append(Blocker('activity-after-boundary', seat.agent_id,
                                            'Seat wrote after its boundary; retry this seat'))
        candidate = replace(manifest, state='complete', blockers=blockers)
        try:
            wire(candidate)
        except ContractError:
            candidate = replace(manifest, blockers=blockers)
        self.publish(candidate)
        return candidate

    def report(self, manifest: Manifest, state: str) -> Result:
        return manifest_report(manifest, self.operation_id, state)


def manifest_report(manifest: Manifest, operation_id: str | None, state: str) -> Result:
    by_seat: dict[str, list[Blocker]] = {}
    for blocker in manifest.blockers:
        by_seat.setdefault(blocker.seat_id or '', []).append(blocker)
    resolved = {s.agent_id: s for s in manifest.seats}
    rows = []
    for expected in manifest.expected:
        errors = by_seat.get(expected.agent_id, [])
        receipts = [r for r in manifest.receipts if r.seat_id == expected.agent_id]
        done = {r.kind for r in receipts if r.durable and r.assurance != 'unsupported'}
        need = {'handoff', 'compact', 'boundary'}
        if expected.agent_id in resolved and resolved[expected.agent_id].slot:
            need.add('layout')
        if errors:
            rows.append(seat_result(expected.agent_id, 'blocked', evidence=receipts, errors=errors))
        elif need <= done or (expected.agent_id in resolved and need - {'layout'} <= done and state != 'running'):
            rows.append(seat_result(expected.agent_id, 'saved', evidence=receipts))
        else:
            rows.append(seat_result(expected.agent_id, 'preparing', evidence=receipts))
    if state == 'complete' and any(r.state != 'saved' for r in rows):
        state = 'incomplete'
    return make_result(operation_id, manifest.checkpoint_id, state, rows,
                       [b for b in manifest.blockers if b.seat_id is None])


def run_save(store: CheckpointStore, operation_id: str, params: SaveParams, env: SaveEnv,
             *, only: list[str] | None = None) -> Result:
    return Saver(store, params, env, operation_id).run(only)
