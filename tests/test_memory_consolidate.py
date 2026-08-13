"""Tests for the typed memory-consolidation tool.

Covers the deterministic decision router (boundaries + the destructive floor),
the pre-image/undo recovery path, and graceful degradation when the embed
service is unreachable. The embed endpoint is never contacted — tests either
drive ``route`` directly with exact cosine values, or inject a fake
``embed_fn`` that returns engineered vectors.
"""

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from liteharness import memory_consolidate as mc


def _dead_pid() -> int:
    """Return a pid that is guaranteed dead: spawn a no-op child and reap it."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


def _vec_for_cosine(c: float) -> list[float]:
    """Return a 2-D unit vector whose cosine with [1, 0] is exactly ``c``."""
    c = max(-1.0, min(1.0, c))
    return [c, math.sqrt(1.0 - c * c)]


class RouterBoundaryTests(unittest.TestCase):
    """route() is the LLM-proof floor — verify every band boundary."""

    def test_below_merge_band_keep_separate(self) -> None:
        self.assertEqual(mc.route(0.69), mc.KEEP_SEPARATE)
        self.assertEqual(mc.route(0.0), mc.KEEP_SEPARATE)
        self.assertEqual(mc.route(0.6999), mc.KEEP_SEPARATE)

    def test_merge_band_lower_boundary_inclusive(self) -> None:
        # Exactly 0.70 is inside the merge band.
        self.assertEqual(mc.route(0.70), mc.MERGE_UPDATE)

    def test_merge_band_midpoint(self) -> None:
        self.assertEqual(mc.route(0.85), mc.MERGE_UPDATE)

    def test_merge_band_duplicate_skips(self) -> None:
        # Pure duplicate inside the merge band has nothing to fold in -> SKIP.
        self.assertEqual(mc.route(0.80, duplicate=True), mc.SKIP)

    def test_high_similarity_restatement_skips(self) -> None:
        # 0.95 with no contradiction is a pure restatement -> SKIP, not REPLACE.
        self.assertEqual(mc.route(0.95), mc.SKIP)
        self.assertEqual(mc.route(0.90), mc.SKIP)

    def test_contradiction_at_floor_replaces(self) -> None:
        # 0.90+ AND contradiction-flagged -> REPLACE (proposed only).
        self.assertEqual(mc.route(0.90, contradiction=True), mc.REPLACE)
        self.assertEqual(mc.route(0.95, contradiction=True), mc.REPLACE)


class DestructiveFloorTests(unittest.TestCase):
    """The hard constraint: REPLACE is structurally impossible below 0.9."""

    def test_contradiction_below_floor_never_replaces(self) -> None:
        # A pair flagged 'contradict' at 0.88 sits below the destructive floor.
        # It MUST downgrade — never REPLACE, never a delete.
        action = mc.route(0.88, contradiction=True)
        self.assertIn(action, {mc.MERGE_UPDATE, mc.KEEP_SEPARATE})
        self.assertNotEqual(action, mc.REPLACE)
        self.assertFalse(mc.is_destructive(action))

    def test_contradiction_across_the_whole_sub_floor_range(self) -> None:
        # Sweep the entire below-floor range with contradiction=True: not one
        # value may produce REPLACE. This is the deterministic guarantee.
        c = 0.0
        while c < 0.9:
            self.assertNotEqual(
                mc.route(c, contradiction=True), mc.REPLACE,
                msg=f"REPLACE leaked at cosine={c:.4f}",
            )
            c += 0.01

    def test_no_delete_action_exists(self) -> None:
        # There is no DELETE action anywhere — consolidation overwrites
        # (recoverably), it never deletes.
        self.assertEqual(mc.DESTRUCTIVE_ACTIONS, frozenset({mc.REPLACE}))


class ProposeContradictionProposedNotAppliedTests(unittest.TestCase):
    """0.90+ contradiction -> REPLACE proposed, but zero files mutated."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.mem = self.tmp / "memory"
        self.state = self.tmp / "state"
        self.mem.mkdir()
        self.existing = self.mem / "item.md"
        self.existing.write_text("original memory content", encoding="utf-8")
        self.original_bytes = self.existing.read_bytes()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fake_embed(self, cos: float):
        # existing item -> [1,0]; candidate -> vector engineered for `cos`.
        def _embed(texts, *, url=mc.EMBED_URL, timeout=mc.EMBED_TIMEOUT):
            return [[1.0, 0.0], _vec_for_cosine(cos)]
        return _embed

    def test_replace_is_proposed_but_not_applied(self) -> None:
        candidates = [{"id": "cand-1", "text": "the corrected, contradicting fact", "contradiction": True}]
        result = mc.propose(
            self.mem,
            state_dir=self.state,
            candidates=candidates,
            embed_fn=self._fake_embed(0.95),
        )
        replaces = [p for p in result["proposals"] if p["action"] == mc.REPLACE]
        self.assertEqual(len(replaces), 1)
        proposal = replaces[0]
        self.assertTrue(proposal["destructive"])
        self.assertFalse(proposal["applied"])
        self.assertEqual(result["destructive_count"], 1)

        # The file is untouched — propose mutates nothing.
        self.assertEqual(self.existing.read_bytes(), self.original_bytes)

        # A pre-image safety snapshot was captured for the REPLACE target.
        self.assertIn("pre_image", proposal)
        self.assertEqual(Path(proposal["pre_image"]).read_bytes(), self.original_bytes)

        # Ledger has the proposal row, and it is NOT marked applied.
        rows = mc.read_ledger(self.state)
        propose_rows = [r for r in rows if r.get("event") == "propose"]
        self.assertTrue(any(r["action"] == mc.REPLACE and not r["applied"] for r in propose_rows))
        self.assertFalse(any(r.get("event") == "apply" for r in rows))

    def test_contradiction_below_floor_downgrades_in_propose(self) -> None:
        # Same contradiction flag, but cosine 0.88 -> propose must downgrade to a
        # non-destructive action and record ZERO destructive rows.
        candidates = [{"id": "cand-1", "text": "the corrected, contradicting fact", "contradiction": True}]
        result = mc.propose(
            self.mem,
            state_dir=self.state,
            candidates=candidates,
            embed_fn=self._fake_embed(0.88),
        )
        self.assertEqual(result["destructive_count"], 0)
        self.assertFalse(any(p["action"] == mc.REPLACE for p in result["proposals"]))
        self.assertEqual(self.existing.read_bytes(), self.original_bytes)
        rows = mc.read_ledger(self.state)
        self.assertFalse(any(r.get("destructive") for r in rows))


class PreImageUndoTests(unittest.TestCase):
    """apply-replace pre-images the live file; undo restores it byte-exact."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.mem = self.tmp / "memory"
        self.state = self.tmp / "state"
        self.mem.mkdir()
        self.target = self.mem / "item.md"
        # Deliberately include bytes that a naive text round-trip could mangle.
        self.original = "old memory line 1\r\nline 2 — unicode ✓\n\ttrailing tab\t"
        self.target.write_text(self.original, encoding="utf-8", newline="")
        self.original_bytes = self.target.read_bytes()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_replace_proposal(self, cosine: float, new_content: str) -> str:
        proposal = mc.build_proposal(
            action=mc.REPLACE,
            cosine_score=cosine,
            candidate="cand-1",
            existing=self.target.name,
            contradiction=True,
            target_file=str(self.target),
            new_content=new_content,
        )
        mc.append_ledger(proposal, self.state)
        return proposal["mutation_id"]

    def test_apply_replace_snapshots_and_overwrites_then_undo_restores(self) -> None:
        new_content = "the replacement memory"
        mid = self._seed_replace_proposal(0.95, new_content)

        res = mc.apply_replace(mid, state_dir=self.state)

        # Overwrite happened.
        self.assertEqual(self.target.read_text(encoding="utf-8"), new_content)

        # Pre-image holds the ORIGINAL bytes, exactly.
        pre_image = Path(res["pre_image"])
        self.assertTrue(pre_image.exists())
        self.assertEqual(pre_image.read_bytes(), self.original_bytes)

        # Ledger marked applied.
        self.assertTrue(mc.is_applied(mid, self.state))

        # Undo restores the file byte-for-byte.
        mc.undo(mid, state_dir=self.state)
        self.assertEqual(self.target.read_bytes(), self.original_bytes)

    def test_multiline_replace_on_disk_bytes_match_ledger_sha256(self) -> None:
        # Multi-line replacement content with several '\n' separators. A naive
        # write_text() would translate every '\n' to '\r\n' on Windows, so the
        # on-disk bytes would NOT match the new_content_sha256 recorded in the
        # ledger. This is the byte-integrity regression test for that path.
        new_content = "line one\nline two\nline three\nfinal line\n"
        mid = self._seed_replace_proposal(0.97, new_content)

        mc.apply_replace(mid, state_dir=self.state)

        # The ledger records the sha256 the apply committed to.
        apply_row = next(
            r for r in mc.read_ledger(self.state)
            if r.get("event") == "apply" and r.get("mutation_id") == mid
        )
        ledger_sha = apply_row["new_content_sha256"]

        # The on-disk bytes must hash to EXACTLY that sha256 — no newline
        # translation may have altered a single byte.
        on_disk = self.target.read_bytes()
        self.assertEqual(hashlib.sha256(on_disk).hexdigest(), ledger_sha)
        # And they must equal the raw UTF-8 encoding of new_content, with no
        # stray '\r' bytes injected around the newlines.
        self.assertEqual(on_disk, new_content.encode("utf-8"))
        self.assertNotIn(b"\r\n", on_disk)

        # Undo still restores the byte-exact pre-image.
        mc.undo(mid, state_dir=self.state)
        self.assertEqual(self.target.read_bytes(), self.original_bytes)

    def test_apply_replace_refuses_below_floor_and_leaves_file_untouched(self) -> None:
        # A proposal that somehow carries cosine 0.88 must be refused at apply
        # time by the deterministic floor — the file is never touched.
        mid = self._seed_replace_proposal(0.88, "malicious replacement")
        with self.assertRaises(RuntimeError):
            mc.apply_replace(mid, state_dir=self.state)
        self.assertEqual(self.target.read_bytes(), self.original_bytes)
        self.assertFalse(mc.is_applied(mid, self.state))

    def test_apply_replace_refuses_non_replace_action(self) -> None:
        proposal = mc.build_proposal(
            action=mc.MERGE_UPDATE,
            cosine_score=0.85,
            candidate="cand-1",
            existing=self.target.name,
        )
        mc.append_ledger(proposal, self.state)
        with self.assertRaises(RuntimeError):
            mc.apply_replace(proposal["mutation_id"], state_dir=self.state)
        self.assertEqual(self.target.read_bytes(), self.original_bytes)

    def test_apply_replace_is_not_double_applied(self) -> None:
        mid = self._seed_replace_proposal(0.95, "the replacement memory")
        mc.apply_replace(mid, state_dir=self.state)
        with self.assertRaises(RuntimeError):
            mc.apply_replace(mid, state_dir=self.state)


class GracefulDegradationTests(unittest.TestCase):
    """Embed service unreachable -> abort with zero destructive proposals."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.mem = self.tmp / "memory"
        self.state = self.tmp / "state"
        self.mem.mkdir()
        (self.mem / "a.md").write_text("memory A about widgets", encoding="utf-8")
        (self.mem / "b.md").write_text("memory B about widgets too", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_embed_down_aborts_zero_destructive(self) -> None:
        # Simulate the /embed endpoint being closed: embed_fn returns None.
        def _down(texts, *, url=mc.EMBED_URL, timeout=mc.EMBED_TIMEOUT):
            return None

        candidates = [{"id": "c", "text": "memory A about widgets", "contradiction": True}]
        result = mc.propose(
            self.mem,
            state_dir=self.state,
            candidates=candidates,
            embed_fn=_down,
        )
        self.assertFalse(result["embed_ok"])
        self.assertTrue(result["aborted"])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["destructive_count"], 0)

        # Nothing destructive was written to the ledger (in fact nothing at all).
        rows = mc.read_ledger(self.state)
        self.assertFalse(any(r.get("destructive") for r in rows))
        self.assertFalse(any(r.get("action") == mc.REPLACE for r in rows))

    def test_embed_real_closed_port_returns_none(self) -> None:
        # Exercise the real client shape against a definitely-closed port so the
        # graceful-degradation path (return None) is covered end-to-end.
        vectors = mc.embed_texts(["hello"], url="http://127.0.0.1:1/embed", timeout=1)
        self.assertIsNone(vectors)


class FileLockTests(unittest.TestCase):
    """The single-writer lock primitive: acquire / release / stale-break / finally."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_acquire_creates_stamped_lock_release_removes_it(self) -> None:
        lock = mc.FileLock(self.state)
        with lock:
            # Acquired: the lock file exists and records THIS pid + a timestamp.
            self.assertTrue(lock.path.exists())
            self.assertEqual(lock.path, self.state / mc.LOCK_FILENAME)
            info = json.loads(lock.path.read_text(encoding="utf-8"))
            self.assertEqual(info["pid"], os.getpid())
            self.assertIn("ts", info)
            self.assertIn("token", info)
        # Released in __exit__ (finally): the lock file is gone.
        self.assertFalse(lock.path.exists())

    def test_second_concurrent_acquire_aborts_while_held(self) -> None:
        first = mc.FileLock(self.state).acquire()
        try:
            with self.assertRaises(mc.LockHeldError):
                mc.FileLock(self.state).acquire()
            # The holder's lock is untouched by the aborted second acquire.
            self.assertTrue(first.path.exists())
        finally:
            first.release()
        self.assertFalse(first.path.exists())

    def test_lock_released_on_exception_finally(self) -> None:
        lock = mc.FileLock(self.state)
        with self.assertRaises(ValueError):
            with lock:
                self.assertTrue(lock.path.exists())
                raise ValueError("boom inside the critical section")
        # Even though the body raised, __exit__ ran and released the lock.
        self.assertFalse(lock.path.exists())

    def test_stale_lock_dead_pid_is_broken(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / mc.LOCK_FILENAME
        # A crashed run's lock: a dead pid, but a FRESH mtime.
        path.write_text(
            json.dumps({"pid": _dead_pid(), "ts": mc._now(), "token": "old"}),
            encoding="utf-8",
        )
        self.assertTrue(mc._lock_is_stale(path))
        # Acquire must break it and succeed (dead pid => abandoned).
        lock = mc.FileLock(self.state).acquire()
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(info["pid"], os.getpid())  # now OURS
        finally:
            lock.release()

    def test_stale_lock_old_mtime_is_broken(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / mc.LOCK_FILENAME
        # A live pid, but the file mtime is well past the stale threshold.
        path.write_text(
            json.dumps({"pid": os.getpid(), "ts": mc._now(), "token": "old"}),
            encoding="utf-8",
        )
        old = time.time() - (mc.STALE_LOCK_SECONDS + 60)
        os.utime(path, (old, old))
        self.assertTrue(mc._lock_is_stale(path))
        lock = mc.FileLock(self.state).acquire()
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(info["token"], lock._token)  # reclaimed
        finally:
            lock.release()

    def test_live_fresh_lock_is_not_stale(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / mc.LOCK_FILENAME
        path.write_text(
            json.dumps({"pid": os.getpid(), "ts": mc._now(), "token": "t"}),
            encoding="utf-8",
        )
        self.assertFalse(mc._lock_is_stale(path))

    def test_release_does_not_delete_a_lock_it_no_longer_owns(self) -> None:
        # If our lock was broken as stale and another run took over, releasing
        # ours must NOT delete the new holder's lock (token mismatch guard).
        lock = mc.FileLock(self.state).acquire()
        # Simulate a takeover: overwrite the on-disk lock with a different token.
        lock.path.write_text(
            json.dumps({"pid": os.getpid(), "ts": mc._now(), "token": "someone-else"}),
            encoding="utf-8",
        )
        lock.release()
        # The other holder's lock survives our release.
        self.assertTrue(lock.path.exists())
        info = json.loads(lock.path.read_text(encoding="utf-8"))
        self.assertEqual(info["token"], "someone-else")


class ApplyReplaceLockTests(unittest.TestCase):
    """The lock wraps apply-replace: serialize, abort-on-held, break-stale, finally."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.mem = self.tmp / "memory"
        self.state = self.tmp / "state"
        self.mem.mkdir()
        self.target = self.mem / "item.md"
        self.original = "original memory content"
        self.target.write_text(self.original, encoding="utf-8", newline="")
        self.original_bytes = self.target.read_bytes()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_replace_proposal(self, cosine: float, new_content: str) -> str:
        proposal = mc.build_proposal(
            action=mc.REPLACE,
            cosine_score=cosine,
            candidate="cand-1",
            existing=self.target.name,
            contradiction=True,
            target_file=str(self.target),
            new_content=new_content,
        )
        mc.append_ledger(proposal, self.state)  # creates the state dir
        return proposal["mutation_id"]

    def _lock_path(self) -> Path:
        return self.state / mc.LOCK_FILENAME

    def test_apply_replace_acquires_then_releases_the_lock(self) -> None:
        mid = self._seed_replace_proposal(0.95, "the replacement memory")
        # No lock before.
        self.assertFalse(self._lock_path().exists())
        res = mc.apply_replace(mid, state_dir=self.state)
        self.assertTrue(res["applied"])
        # Lock released after a successful apply (finally / __exit__).
        self.assertFalse(self._lock_path().exists())

    def test_concurrent_apply_replace_aborts_and_leaves_file_untouched(self) -> None:
        mid = self._seed_replace_proposal(0.95, "the replacement memory")
        # Simulate another live run holding the lock: our pid, fresh mtime.
        self._lock_path().write_text(
            json.dumps({"pid": os.getpid(), "ts": mc._now(), "token": "held"}),
            encoding="utf-8",
        )
        with self.assertRaises(mc.LockHeldError):
            mc.apply_replace(mid, state_dir=self.state)
        # The overwrite never happened — file byte-identical, not applied.
        self.assertEqual(self.target.read_bytes(), self.original_bytes)
        self.assertFalse(mc.is_applied(mid, self.state))
        # The holder's lock is left intact (we never owned it).
        self.assertTrue(self._lock_path().exists())
        info = json.loads(self._lock_path().read_text(encoding="utf-8"))
        self.assertEqual(info["token"], "held")

    def test_stale_dead_pid_lock_is_broken_and_apply_proceeds(self) -> None:
        mid = self._seed_replace_proposal(0.95, "the replacement memory")
        # A crashed prior run's lock: dead pid, fresh mtime.
        self._lock_path().write_text(
            json.dumps({"pid": _dead_pid(), "ts": mc._now(), "token": "crashed"}),
            encoding="utf-8",
        )
        res = mc.apply_replace(mid, state_dir=self.state)
        self.assertTrue(res["applied"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "the replacement memory")
        # The stale lock was broken and then released by our run.
        self.assertFalse(self._lock_path().exists())

    def test_lock_released_when_apply_refuses_below_floor(self) -> None:
        # A below-floor proposal raises inside the locked critical section; the
        # lock must still be released (finally / __exit__) and the file untouched.
        mid = self._seed_replace_proposal(0.88, "malicious replacement")
        with self.assertRaises(RuntimeError):
            mc.apply_replace(mid, state_dir=self.state)
        self.assertEqual(self.target.read_bytes(), self.original_bytes)
        self.assertFalse(self._lock_path().exists())


class ConcurrentStaleBreakTests(unittest.TestCase):
    """Concurrent stale-break: the atomic steal prevents dual holders.

    The old code used ``unlink()`` + ``O_EXCL create`` in the stale branch,
    which allowed two racers to both proceed (B unlinks A's *fresh* lock,
    creates its own -> both hold).  The fix uses ``os.rename`` (atomic) so
    only one racer can move a given lock file — the loser gets
    ``FileNotFoundError`` and must retry.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sequential_stale_break_second_acquirer_blocked(self) -> None:
        """After one acquirer breaks a stale lock, a second sees a live lock."""
        self.state.mkdir(parents=True, exist_ok=True)
        lock_path = self.state / mc.LOCK_FILENAME

        # Plant a stale lock: dead pid, fresh mtime.
        dead = _dead_pid()
        lock_path.write_text(
            json.dumps({"pid": dead, "ts": mc._now(), "token": "stale-original"}),
            encoding="utf-8",
        )

        # First acquirer breaks the stale lock and holds.
        lock_a = mc.FileLock(self.state)
        lock_a.acquire()
        self.assertTrue(lock_a._held)

        # Second acquirer sees A's live lock and aborts.
        with self.assertRaises(mc.LockHeldError):
            mc.FileLock(self.state).acquire()

        # A still holds — on-disk token matches A's.
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(info["token"], lock_a._token)
        self.assertEqual(info["pid"], os.getpid())

        lock_a.release()
        self.assertFalse(lock_path.exists())

    def test_concurrent_stale_break_never_both_win(self) -> None:
        """Two threads race to break the same stale lock: at most one holds.

        The CRITICAL invariant is that both threads MUST NEVER BOTH WIN (dual
        holders = the TOCTOU bug).  Both losing is safe (conservative); the
        liveness check verifies at least one round produces a winner.
        """
        NUM_ROUNDS = 20

        both_won = 0
        one_won = 0
        both_lost = 0

        for _round in range(NUM_ROUNDS):
            self.state.mkdir(parents=True, exist_ok=True)
            lock_path = self.state / mc.LOCK_FILENAME

            dead = _dead_pid()
            lock_path.write_text(
                json.dumps({"pid": dead, "ts": mc._now(), "token": "stale-original"}),
                encoding="utf-8",
            )

            barrier = threading.Barrier(2, timeout=5)
            results: list = [None, None]

            def try_acquire(idx: int) -> None:
                barrier.wait()
                lock = mc.FileLock(self.state)
                try:
                    lock.acquire()
                    results[idx] = ("won", lock)
                except mc.LockHeldError:
                    results[idx] = ("lost", None)
                except Exception as exc:
                    results[idx] = ("error", str(exc))

            t0 = threading.Thread(target=try_acquire, args=(0,))
            t1 = threading.Thread(target=try_acquire, args=(1,))
            t0.start()
            t1.start()
            t0.join(timeout=10)
            t1.join(timeout=10)

            winners = [r for r in results if r is not None and r[0] == "won"]

            # CRITICAL: both MUST NEVER win simultaneously.
            self.assertLessEqual(
                len(winners), 1,
                f"Round {_round}: BOTH threads acquired the lock — "
                f"TOCTOU double-holder bug! {results}",
            )

            if len(winners) == 1:
                one_won += 1
                winner_lock = winners[0][1]
                info = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(info["token"], winner_lock._token)
                winner_lock.release()
            elif len(winners) == 0:
                both_lost += 1
            else:
                both_won += 1

            # Clean up for next round.
            if lock_path.exists():
                lock_path.unlink()
            for f in self.state.glob(f"{mc.LOCK_FILENAME}.stale.*"):
                f.unlink()

        # Safety invariant: never both win.
        self.assertEqual(both_won, 0, "TOCTOU BUG: both threads held the lock")
        # Liveness: at least one round should produce a single winner.
        self.assertGreater(
            one_won, 0,
            f"Liveness: 0/{NUM_ROUNDS} rounds produced a winner "
            f"(both_lost={both_lost}). The lock can be acquired sequentially "
            f"(see test_sequential_stale_break_second_acquirer_blocked).",
        )


class CosineTests(unittest.TestCase):
    def test_cosine_matches_engineered_values(self) -> None:
        self.assertAlmostEqual(mc.cosine([1.0, 0.0], _vec_for_cosine(0.5)), 0.5, places=6)
        self.assertAlmostEqual(mc.cosine([1.0, 0.0], [1.0, 0.0]), 1.0, places=6)

    def test_cosine_zero_norm_guard(self) -> None:
        self.assertEqual(mc.cosine([0.0, 0.0], [1.0, 0.0]), 0.0)


if __name__ == "__main__":
    unittest.main()
