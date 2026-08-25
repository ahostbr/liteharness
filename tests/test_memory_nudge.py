"""Tests for the memory-nudge hook.

The nudge is a tiny INDEX pointer, NOT content-injection: it names the agent's
durable MEMORY.md path on an every-other-turn cadence (configurable) and MUST
NEVER open or read MEMORY.md. These tests pin that contract.
"""

import inspect
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from liteharness import config, hooks


class MemoryNudgeTests(unittest.TestCase):
    AGENT_ID = "agent-mem-1"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_root = config.HARNESS_ROOT
        self.original_config_path = config.CONFIG_PATH
        config.HARNESS_ROOT = self.root
        config.CONFIG_PATH = self.root / "config.json"

    def tearDown(self) -> None:
        config.HARNESS_ROOT = self.original_root
        config.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    # ---- helpers -------------------------------------------------------

    def _write_config(self, **memory_nudge) -> None:
        config.save({"memory_nudge": memory_nudge})

    def _env(self, hook_event: str = "UserPromptSubmit", **extra) -> dict:
        env = {
            "LITEHARNESS_AGENT_ID": self.AGENT_ID,
            "LITEHARNESS_HOOK_EVENT": hook_event,
        }
        env.update(extra)
        return env

    def _run_nudge(self, env: dict) -> str:
        buf = io.StringIO()
        with mock.patch.dict(hooks.os.environ, env, clear=False):
            with redirect_stdout(buf):
                hooks.memory_nudge()
        return buf.getvalue()

    def _turn_file(self) -> Path:
        return self.root / f".memory_nudge_turns_{self.AGENT_ID}"

    # ---- cadence gating ------------------------------------------------

    def test_default_cadence_two_fires_on_even_turns(self) -> None:
        self._write_config(enabled=True, cadence=2)
        env = self._env()
        outputs = [self._run_nudge(env) for _ in range(4)]
        # turns 1 and 3 are silent; turns 2 and 4 emit the pointer
        self.assertEqual(outputs[0], "")
        self.assertNotEqual(outputs[1].strip(), "")
        self.assertEqual(outputs[2], "")
        self.assertNotEqual(outputs[3].strip(), "")
        # counter advanced once per (UserPromptSubmit) turn
        self.assertEqual(self._turn_file().read_text(encoding="utf-8").strip(), "4")

    def test_configurable_cadence_three(self) -> None:
        self._write_config(enabled=True, cadence=3)
        env = self._env()
        outputs = [self._run_nudge(env) for _ in range(6)]
        fired = [i + 1 for i, o in enumerate(outputs) if o.strip()]
        self.assertEqual(fired, [3, 6])

    # ---- enabled gate --------------------------------------------------

    def test_disabled_is_silent_and_does_not_increment(self) -> None:
        self._write_config(enabled=False, cadence=2)
        env = self._env()
        for _ in range(4):
            self.assertEqual(self._run_nudge(env), "")
        # early-return before increment: no counter file created
        self.assertFalse(self._turn_file().exists())

    def test_missing_config_defaults_on(self) -> None:
        # No config written -> get_memory_nudge() defaults enabled=True, cadence=2
        # (fleet-wide default-on). Turn 1 is silent (1 % 2 != 0); turn 2 emits the
        # pointer; the counter file is created (increment happens once enabled).
        env = self._env()
        self.assertEqual(self._run_nudge(env), "")             # turn 1 silent
        self.assertNotEqual(self._run_nudge(env).strip(), "")  # turn 2 fires
        self.assertEqual(self._turn_file().read_text(encoding="utf-8").strip(), "2")

    # ---- non-UserPromptSubmit gate ------------------------------------

    def test_non_userpromptsubmit_does_not_increment_or_emit(self) -> None:
        self._write_config(enabled=True, cadence=1)  # would fire every turn if allowed
        env = self._env(hook_event="PostToolUse")
        for _ in range(3):
            self.assertEqual(self._run_nudge(env), "")
        self.assertFalse(self._turn_file().exists())

    def test_non_userpromptsubmit_stdin_via_main_is_silent(self) -> None:
        """Drive the full main() dispatch with real stdin JSON to prove the
        hook_event_name -> env bridge gates a PostToolUse turn out."""
        self._write_config(enabled=True, cadence=1)
        payload = json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "session_id": self.AGENT_ID,
                "transcript_path": str(self.root / "projects" / "p" / "s.jsonl"),
            }
        )
        buf = io.StringIO()
        original_argv = sys.argv
        original_stdin = sys.stdin
        env = {
            "LITEHARNESS_AGENT_ID": "",
            "LITEHARNESS_HOOK_EVENT": "",
            "LITEHARNESS_TRANSCRIPT_PATH": "",
        }
        try:
            sys.argv = ["hooks.py", "memory-nudge"]
            sys.stdin = io.StringIO(payload)
            with mock.patch.dict(hooks.os.environ, env, clear=False):
                with redirect_stdout(buf):
                    hooks.main()
        finally:
            sys.argv = original_argv
            sys.stdin = original_stdin
        self.assertEqual(buf.getvalue(), "")
        # counter for the session id must not exist (no increment on PostToolUse)
        self.assertFalse((self.root / f".memory_nudge_turns_{self.AGENT_ID}").exists())

    def test_legacy_codex_command_emits_valid_user_prompt_json(self) -> None:
        """An already-loaded generic plugin hook must self-heal in Codex."""
        self._write_config(enabled=True, cadence=1)
        payload = json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": self.AGENT_ID,
                "prompt": "test prompt",
            }
        )
        buf = io.StringIO()
        original_argv = sys.argv
        original_stdin = sys.stdin
        env = {
            "CODEX_THREAD_ID": "codex-thread-1",
            "LITEHARNESS_AGENT_ID": self.AGENT_ID,
            "LITEHARNESS_HOOK_EVENT": "",
            "LITEHARNESS_TRANSCRIPT_PATH": "",
        }
        try:
            sys.argv = ["hooks.py", "memory-nudge"]
            sys.stdin = io.StringIO(payload)
            with mock.patch.dict(hooks.os.environ, env, clear=False):
                with redirect_stdout(buf):
                    hooks.main()
        finally:
            sys.argv = original_argv
            sys.stdin = original_stdin

        result = json.loads(buf.getvalue())
        specific = result["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "UserPromptSubmit")
        ctx = specific["additionalContext"]
        # The nudge no longer names a memory file (git-as-memory v2 WS1). Assert
        # BOTH halves: the old target is gone AND the new doctrine arrived. Dropping
        # the assertion instead would leave this test passing on an empty string.
        self.assertNotIn("MEMORY.md", ctx)
        self.assertIn("lst run pattern", ctx)

    # ---- payload contract: tiny pointer, MEMORY.md never opened --------

    def test_payload_is_tiny_pointer_and_never_opens_memory(self) -> None:
        self._write_config(enabled=True, cadence=1)
        transcript = str(self.root / "projects" / "C--Projects" / "abc.jsonl")
        expected_path = str(self.root / "projects" / "C--Projects" / "memory" / "MEMORY.md")
        env = self._env(LITEHARNESS_TRANSCRIPT_PATH=transcript)

        # pathlib's read_text/write_text route through io.open (NOT builtins.open)
        # on CPython, so a builtins.open-only patch observes nothing — the tracker
        # stays empty and the MEMORY.md assertion passes trivially even if the code
        # DID Path(...).read_text() the index. Patch the primitives pathlib actually
        # uses so every opened path is recorded.
        opened: list[str] = []
        real_read_text = Path.read_text
        real_write_text = Path.write_text
        real_path_open = Path.open
        real_io_open = io.open
        real_builtin_open = open

        def track_read_text(self_path, *args, **kwargs):
            opened.append(str(self_path))
            return real_read_text(self_path, *args, **kwargs)

        def track_write_text(self_path, *args, **kwargs):
            opened.append(str(self_path))
            return real_write_text(self_path, *args, **kwargs)

        def track_path_open(self_path, *args, **kwargs):
            opened.append(str(self_path))
            return real_path_open(self_path, *args, **kwargs)

        def track_io_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_io_open(file, *args, **kwargs)

        def track_builtin_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_builtin_open(file, *args, **kwargs)

        buf = io.StringIO()
        with mock.patch.dict(hooks.os.environ, env, clear=False):
            with mock.patch.object(Path, "read_text", track_read_text), \
                    mock.patch.object(Path, "write_text", track_write_text), \
                    mock.patch.object(Path, "open", track_path_open), \
                    mock.patch.object(io, "open", track_io_open), \
                    mock.patch("builtins.open", new=track_builtin_open):
                with redirect_stdout(buf):
                    hooks.memory_nudge()

        payload = buf.getvalue().strip()
        # fired
        self.assertNotEqual(payload, "")
        # A pointer, not injected content. Pinned to the SINGLE source of truth
        # rather than to a magic number: the payload must be exactly what
        # _memory_checkin_text() renders and nothing appended. A literal bound
        # cannot express this — 6736b4e grew the text from ~390 to 841 chars on
        # purpose (it added the 200-line / 25KB cap language, which is Claude
        # Code's actual loader behaviour, not advice) and broke this assertion in
        # the same commit. The red then shipped through 0.3.7 AND 0.3.8, because a
        # threshold nobody can justify is a threshold nobody updates.
        # Still pinned to the SINGLE source of truth rather than a literal, and now
        # mirrors the call site exactly: memory_nudge() renders
        # _memory_checkin_text(_resolve_tier()), so the test must resolve the tier the
        # same way instead of hardcoding one. Hardcoding "worker" here would go green
        # while an orchestrator got the wrong text.
        self.assertEqual(
            payload, hooks._memory_checkin_text(hooks._resolve_tier()).strip()
        )
        # Backstop for the property the number was reaching for: MEMORY.md is
        # ~384KB here, so any real content-injection blows past this by orders of
        # magnitude while the deliberate template stays far under it.
        self.assertLess(len(payload), 4000)
        # INVERTED by git-as-memory v2 WS1: the nudge must no longer name the memory
        # file OR its path. This is the load-bearing assertion of the retarget — it is
        # what stops the old instruction coming back in a later edit.
        self.assertNotIn("MEMORY.md", payload)
        self.assertNotIn(expected_path, payload)
        # And the positive half, so "absent" cannot be satisfied by an empty payload:
        self.assertIn("lst run pattern", payload)
        self.assertIn("HANDOFF", payload)

        # POSITIVE CONTROL: the tracker must have actually observed file I/O,
        # otherwise the MEMORY.md check below is vacuous (an empty list always
        # passes it — the exact trap the old builtins.open-only version fell in).
        # memory_nudge writes the per-agent turn-counter file, so its path MUST
        # appear in the captured opens.
        turn_file = str(self._turn_file())
        self.assertIn(
            turn_file,
            opened,
            f"positive control failed: turn-counter file was never observed by "
            f"the open tracker; saw {opened}",
        )

        # HARD CONSTRAINT: MEMORY.md was never opened/read anywhere in the call.
        memory_opens = [p for p in opened if "MEMORY.md" in p]
        self.assertEqual(memory_opens, [], f"MEMORY.md must never be opened; saw {memory_opens}")

    def test_corrupt_turn_counter_self_heals_not_suppressed(self) -> None:
        # A garbled counter file must reset to 0 (self-heal) rather than raise
        # ValueError -> caught -> permanently suppress the nudge. cadence=1 so a
        # healed counter fires the same turn.
        self._write_config(enabled=True, cadence=1)
        self._turn_file().write_text("not-a-number", encoding="utf-8")
        out = self._run_nudge(self._env())
        self.assertNotEqual(out.strip(), "")  # fired despite the corrupt file
        # healed to 1 (0 reset + 1 increment)
        self.assertEqual(self._turn_file().read_text(encoding="utf-8").strip(), "1")

    def test_resolver_and_nudge_only_build_strings_never_read(self) -> None:
        # Static proof: neither the path resolver NOR memory_nudge itself opens
        # or reads a file — the MEMORY.md path is only ever built as a string.
        # (All turn-counter file I/O lives in _bump_turn_counter, deliberately
        # kept out of memory_nudge so this guard stays meaningful.)
        for fn in (hooks._resolve_memory_index_path, hooks.memory_nudge):
            src = inspect.getsource(fn)
            self.assertNotIn("read_text", src)
            self.assertNotIn("open(", src)
            self.assertNotIn(".read(", src)


if __name__ == "__main__":
    unittest.main()
