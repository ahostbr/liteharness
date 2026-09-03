"""T244 — a resume must not out-register the name a takeover claimed.

MEASURED FIRST, NOT ASSUMED, AND THE FIRST MEASUREMENT WAS WRONG. The card's
stated mechanism — the hook writing the per-resume payload `session_id` into the
OVERRIDE slot — was already fixed on 2026-08-29 (`b6e39a3`), so I first keyed the
guard to that path: fire only when the id came from the payload DEFAULT. Sentinel
refuted it from his own seat, measured: at 16:2x on 2026-09-03 his hook registered
`99dd8b40` on pid 23100 while his shell's `CLAUDE_CODE_SESSION_ID` read — and
still reads — `bfc5e812`. Either the var was absent from the hook subprocess, or
Claude Code had already rewritten it for the resumed session. Both produce the
same collision, so the guard is keyed to the COLLISION and both are armed here.

    A GUARD KEYED TO THE CAUSE YOU GUESSED MISSES THE CAUSE YOU DID NOT.

`_superseded_by_later_registration` (cli.py:805) compares nothing but
`registered_at`, which is why the newcomer won:

    SUPERSESSION BY TIMESTAMP ALONE CANNOT TELL A SUCCESSOR FROM AN IMPOSTOR.

The end-to-end acceptance Sentinel carded — resume a real seat, take over its
name, three prompts, `discover` still lists it — was NOT driven. Doing so means
resuming a live fleet seat, and these arms encode the same property at the unit
level against a tmp root.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liteharness import cli, config, hooks, naming

TAKEOVER_ID = "bfc5e812-ec7b-4588-b74f-769e7bbe2eb1"
RESUME_PAYLOAD_ID = "99dd8b40-1111-2222-3333-444444444444"


class ResumeEvictionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_root = config.HARNESS_ROOT
        self.original_config_path = config.CONFIG_PATH
        config.HARNESS_ROOT = self.root
        config.CONFIG_PATH = self.root / "config.json"
        hooks._LAST_PRESENCE = {}
        self.live_pid = hooks.os.getpid()

    def tearDown(self) -> None:
        config.HARNESS_ROOT = self.original_root
        config.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    def _path(self, agent_id: str) -> Path:
        return self.root / "agents" / f"{agent_id}.json"

    def _register(self, env: dict, *, pid: int | None = None) -> None:
        base = {
            "LITEHARNESS_CLI": "claude-code",
            "LITEHARNESS_MODEL": "claude-opus",
            "LITEHARNESS_SESSION_PID": str(pid or self.live_pid),
        }
        base.update(env)
        with mock.patch.dict(hooks.os.environ, base, clear=False):
            hooks.register_presence()

    def _seed_takeover(self) -> None:
        """The record `register --takeover` leaves: authoritative id, named."""
        self._register({"LITEHARNESS_AGENT_ID": TAKEOVER_ID})
        naming.set_override(TAKEOVER_ID, "Sentinel")

    def _resume_hook(self, env_session_id: str | None) -> None:
        """A SessionStart after `--resume`, with the payload carrying a NEW id.

        `env_session_id` is hypothesis (a) when None — the var never reached the
        hook — and hypothesis (b) when it carries the NEW id, i.e. the var was
        present but Claude Code had already rewritten it for the resumed session.
        """
        env = {k: v for k, v in hooks.os.environ.items() if k != "LITEHARNESS_AGENT_ID"}
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.pop("LITEHARNESS_PAYLOAD_SESSION_ID", None)
        if env_session_id:
            env["CLAUDE_CODE_SESSION_ID"] = env_session_id
        with mock.patch.dict(hooks.os.environ, env, clear=True):
            hooks._apply_hook_context({"session_id": RESUME_PAYLOAD_ID, "transcript_path": "x"})
            self._register({})

    def _assert_takeover_intact(self) -> None:
        self.assertTrue(self._path(TAKEOVER_ID).exists(), "the takeover record was removed")
        self.assertFalse(
            self._path(RESUME_PAYLOAD_ID).exists(),
            "a second record was registered for one seat; the name will lose on timestamp",
        )
        data = json.loads(self._path(TAKEOVER_ID).read_text(encoding="utf-8"))
        self.assertFalse(
            cli._superseded_by_later_registration(TAKEOVER_ID, data),
            "a resume's generated identity retired the record a takeover had claimed",
        )
        self.assertEqual(naming.get_name(TAKEOVER_ID), "Sentinel")

    def test_hypothesis_a_env_id_absent_from_the_hook(self) -> None:
        self._seed_takeover()
        self._resume_hook(env_session_id=None)
        self._assert_takeover_intact()

    def test_hypothesis_b_env_id_present_but_already_rewritten(self) -> None:
        """Sentinel's case. The deferral HONOURS the var and still collides."""
        self._seed_takeover()
        self._resume_hook(env_session_id=RESUME_PAYLOAD_ID)
        self._assert_takeover_intact()

    def test_the_adopted_record_is_refreshed_not_merely_left_alone(self) -> None:
        """Adoption must keep the seat REACHABLE, not just keep the name.

        A guard that skipped registering would preserve the name and leave the
        record stale; one that dropped `session_pid` would make it read as an
        orphaned watcher. Both are worse than the phantom, so this pins that the
        surviving record is the one that got written.
        """
        self._seed_takeover()
        before = json.loads(self._path(TAKEOVER_ID).read_text(encoding="utf-8"))
        self._resume_hook(env_session_id=None)
        after = json.loads(self._path(TAKEOVER_ID).read_text(encoding="utf-8"))

        # ⚠️ `>=` HERE WOULD BE VACUOUS: with adoption disabled the record is
        # never touched, so `after == before` satisfies any non-strict compare
        # and the arm passes having measured nothing. Caught by mutation —
        # disabling the fix killed three arms and left this one green.
        self.assertEqual(after.get("session_pid"), self.live_pid)
        self.assertNotEqual(after, before, "the adopted record was not rewritten by the resume")

    def test_a_seat_with_no_competing_record_keeps_its_payload_id(self) -> None:
        """Codex and Copilot: the payload IS their only id source.

        Adoption needs a DIFFERENT authoritative record on the same live pid. A
        lone seat has none, so nothing is adopted and the payload id stands —
        armed rather than argued, because breaking these two would be a silent
        regression in CLIs nobody here runs.
        """
        self._resume_hook(env_session_id=None)
        self.assertTrue(self._path(RESUME_PAYLOAD_ID).exists())
        self.assertFalse(self._path(TAKEOVER_ID).exists())

    def test_a_record_on_a_different_pid_is_never_adopted(self) -> None:
        """The key is the pid, so a foreign seat's name must not be stealable.

        `_resolve_session_pid` walks to the nearest owning CLI ancestor — the
        seat's OWN process — so two live sessions never share one. This pins the
        consequence rather than the reasoning.
        """
        self._register({"LITEHARNESS_AGENT_ID": TAKEOVER_ID}, pid=self.live_pid)
        # A different, live pid: this process's parent is alive and is not us.
        other_pid = hooks.os.getppid()
        if other_pid == self.live_pid or not hooks._pid_alive(other_pid):
            self.skipTest("no distinct live pid available to represent a second seat")
        self._resume_hook_on_pid(other_pid)
        self.assertTrue(self._path(RESUME_PAYLOAD_ID).exists(), "should have registered as itself")
        self.assertTrue(self._path(TAKEOVER_ID).exists())

    def _resume_hook_on_pid(self, pid: int) -> None:
        env = {k: v for k, v in hooks.os.environ.items() if k != "LITEHARNESS_AGENT_ID"}
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.pop("LITEHARNESS_PAYLOAD_SESSION_ID", None)
        with mock.patch.dict(hooks.os.environ, env, clear=True):
            hooks._apply_hook_context({"session_id": RESUME_PAYLOAD_ID, "transcript_path": "x"})
            self._register({}, pid=pid)

    def test_both_ids_are_logged_from_the_one_process_that_decided(self) -> None:
        """The instrumentation Sentinel asked for, and why it exists.

        The 09-03 phantom was diagnosed from a shell's env and a hook's payload —
        two processes, two moments — leaving hypotheses (a) and (b) impossible to
        separate. One line written where the decision is made ends that.
        """
        self._seed_takeover()
        self._resume_hook(env_session_id=RESUME_PAYLOAD_ID)

        lines = (self.root / "identity-log.jsonl").read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[-1])
        self.assertEqual(entry["resolved"], TAKEOVER_ID)
        self.assertEqual(entry["adopted_from"], RESUME_PAYLOAD_ID)
        self.assertEqual(entry["env_claude_code_session_id"], RESUME_PAYLOAD_ID)
        self.assertEqual(entry["payload_session_id"], RESUME_PAYLOAD_ID)


if __name__ == "__main__":
    unittest.main()
