"""T350 — a live agent must never be unregistered, refused, or have its mail deleted.

THE DEFECT. `deregister()` was wired to the **Stop** hook as well as SessionEnd. In
Claude Code, Stop fires at the end of every ASSISTANT TURN, so every seat unlinked its
own presence file once per turn and stayed unregistered for one Python startup.
Measured 2026-09-04 on this box: eight gaps in ten minutes across three seats,
1.1–4.1 s each. During a gap, sampled 11 times at 10 Hz with identical results:
`os.listdir` did not show the entry, `os.stat` raised a genuine ENOENT
(errno=2 winerror=2), and `cli._known_agent_ids()` REFUSED a live id — which is the
"no agent <id> is registered. NOTHING WAS SENT." that five real sends hit that day.

    STOP IS A TURN BOUNDARY. SessionEnd IS THE SESSION BOUNDARY.

And because SENDING A MESSAGE ENDS A TURN, a seat was deregistered by its own outgoing
traffic — which is why every refused agent was demonstrably alive and every retry worked.

Three things are pinned here, because fixing only the first heals nothing already
installed and leaves the silent half of the damage in place:
  1. the shipped configs wire `deregister` to SessionEnd and to NOTHING else;
  2. installing over a settings.json that still carries the old wiring REMOVES it,
     without disturbing a command that legitimately lives on two events;
  3. neither the reaper nor the mail sweep acts against a process that is alive.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import psutil

from liteharness import cli, config, hooks, inbox

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "liteharness"
DEREGISTER_CMD = "python -m liteharness.hooks deregister"


def _events_for(path: Path, needle: str) -> set[str]:
    """Every event whose config wires a command containing `needle`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for event, matchers in data.get("hooks", {}).items():
        for matcher in matchers or []:
            for entry in matcher.get("hooks", []) or []:
                if needle in (entry.get("command") or ""):
                    out.add(event)
    return out


class ShippedConfigWiringTests(unittest.TestCase):
    """The config files themselves — the surface that put the defect on every box."""

    def _config_files(self) -> list[Path]:
        files = sorted((PACKAGE_ROOT / "hooks_configs").glob("*.json"))
        catalog = PACKAGE_ROOT / "catalog" / "hooks" / "hooks.json"
        if catalog.exists():
            files.append(catalog)
        self.assertTrue(files, "no shipped hook configs found — this guard would be vacuous")
        return files

    def test_deregister_is_never_wired_to_stop(self) -> None:
        for path in self._config_files():
            with self.subTest(config=path.name):
                events = _events_for(path, "hooks deregister")
                self.assertNotIn(
                    "Stop", events,
                    f"{path.name} wires deregister to Stop, which fires every TURN — "
                    f"every seat would unregister itself once per turn",
                )

    def test_a_config_that_deregisters_at_all_does_it_on_session_end(self) -> None:
        # Not "SessionEnd is present": a config that dropped deregister entirely would
        # pass that, and would leak a presence row for every session that ever ends.
        for path in self._config_files():
            events = _events_for(path, "hooks deregister")
            if not events:
                continue
            with self.subTest(config=path.name):
                self.assertEqual(
                    {"SessionEnd"}, events,
                    f"{path.name} deregisters on {sorted(events)}; SessionEnd is the only "
                    f"event that means the session is over",
                )

    def test_at_least_one_shipped_config_actually_deregisters(self) -> None:
        # The control for the test above, which is satisfiable by silence.
        self.assertTrue(
            any(_events_for(p, "hooks deregister") for p in self._config_files()),
            "no shipped config deregisters at all — the two tests above are then vacuous",
        )


class MergeHealsAnInstalledBoxTests(unittest.TestCase):
    """Fixing the shipped file does not fix a machine that already ran the old one."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Path(self.temp_dir.name) / "settings.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _matcher(command: str) -> dict:
        return {"hooks": [{"type": "command", "command": command, "timeout": 5000}]}

    def _shipped(self) -> dict:
        return json.loads(
            (PACKAGE_ROOT / "hooks_configs" / "claude_hooks.json").read_text(encoding="utf-8")
        )

    def _events(self, needle: str) -> set[str]:
        return _events_for(self.settings, needle)

    def test_installing_over_the_old_wiring_removes_stop_deregister(self) -> None:
        """The measured regression: BEFORE SessionEnd -> AFTER SessionEnd, Stop."""
        self.settings.write_text(json.dumps({"hooks": {
            "Stop": [self._matcher(DEREGISTER_CMD)],
        }}), encoding="utf-8")

        cli._merge_claude_hooks(self.settings, self._shipped())

        self.assertNotIn("Stop", self._events("hooks deregister"),
                         "an install left deregister on Stop — the defect survives the fix")
        self.assertIn("SessionEnd", self._events("hooks deregister"))

    def test_the_merge_does_not_collapse_a_command_that_belongs_to_two_events(self) -> None:
        """🔴 THE CONTROL THAT FORBIDS THE OBVIOUS FIX.

        A blanket "one command, one event" dedupe would pass every other test in this
        file and silently drop a hook: `liteharness.hooks check` is wired to BOTH
        PostToolUse and SessionStart on purpose. The rule has to be narrower — our
        command is removed only from an event the SHIPPED config does not list.
        """
        shipped = self._shipped()
        check_events = set()
        for event, matchers in shipped.get("hooks", {}).items():
            for matcher in matchers:
                for entry in matcher.get("hooks", []):
                    if "hooks check" in entry.get("command", ""):
                        check_events.add(event)
        self.assertGreater(len(check_events), 1,
                           "this control needs a command shipped on two events to be real")

        self.settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
        cli._merge_claude_hooks(self.settings, shipped)
        self.assertEqual(check_events, self._events("hooks check"))

    def test_our_OWN_commands_on_events_the_config_does_not_ship_are_left_alone(self) -> None:
        """🔴 THE ARM THAT CAUGHT THE FIRST DRAFT, ON A REAL FILE.

        The first version of the heal used the general rule "the shipped config is
        authoritative about which events our commands belong to". Run once against this
        box's actual ~/.claude/settings.json it removed THREE deliberate hooks the
        shipped config does not list — `register` on PreCompact and PostCompact (the
        PostCompact one is what re-registers a seat after a compaction) and `check` on
        UserPromptSubmit — and the settings.json had to be restored from git.
            AN INSTALLER CANNOT TELL A DEFECT FROM A DELIBERATE ADDITION.
        So the heal knows one command by name, and everything else the user has wired
        stays wired.
        """
        deliberate = "python -m liteharness.hooks register"
        self.settings.write_text(json.dumps({"hooks": {
            "PostCompact": [self._matcher(deliberate)],
            "UserPromptSubmit": [self._matcher("python -m liteharness.hooks check")],
            "Stop": [self._matcher(DEREGISTER_CMD)],
        }}), encoding="utf-8")

        cli._merge_claude_hooks(self.settings, self._shipped())

        self.assertIn("PostCompact", self._events(deliberate),
                      "the heal deleted a deliberate register hook the config does not ship")
        self.assertIn("UserPromptSubmit", self._events("hooks check"),
                      "the heal deleted a deliberate check hook the config does not ship")
        self.assertNotIn("Stop", self._events("hooks deregister"),
                         "...while still removing the one hook it exists to remove")

    def test_a_foreign_tools_hook_on_stop_is_left_alone(self) -> None:
        foreign = "python -m someone_elses_tool on-stop"
        self.settings.write_text(json.dumps({"hooks": {
            "Stop": [self._matcher(foreign), self._matcher(DEREGISTER_CMD)],
        }}), encoding="utf-8")

        cli._merge_claude_hooks(self.settings, self._shipped())

        self.assertIn("Stop", self._events(foreign),
                      "the heal removed another tool's hook — it must only touch ours")

    def test_the_written_file_ends_with_a_newline(self) -> None:
        """A no-op install must leave a tracked file BYTE-identical.

        `~/.claude/settings.json` is git-tracked on at least one box, and `json.dumps`
        does not end with a newline — so an install that changed nothing still produced
        "\\ No newline at end of file" and dirtied the repo. Measured 2026-09-04 while
        proving T350 on the real file; it was the ONLY diff that run.
        """
        self.settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
        cli._merge_claude_hooks(self.settings, self._shipped())
        self.assertTrue(self.settings.read_text(encoding="utf-8").endswith("\n"))

    def test_installing_twice_is_a_no_op(self) -> None:
        self.settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
        cli._merge_claude_hooks(self.settings, self._shipped())
        first = self.settings.read_text(encoding="utf-8")
        cli._merge_claude_hooks(self.settings, self._shipped())
        self.assertEqual(first, self.settings.read_text(encoding="utf-8"))


def _a_pid_that_is_not_running() -> int:
    for candidate in range(999_000, 1_000_000, 7):
        if not psutil.pid_exists(candidate):
            return candidate
    raise unittest.SkipTest("could not find a free pid to represent a dead process")


class ReaperSparesLiveAgentsTests(unittest.TestCase):
    """`last_seen` is written by the WATCHER, not by the agent.

    So a seat whose watcher was stopped, compacted or killed ages out by the clock while
    it is still working. The idle branches must not be allowed to unregister it.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_root = config.HARNESS_ROOT
        config.HARNESS_ROOT = self.root
        (self.root / "agents").mkdir(parents=True)

    def tearDown(self) -> None:
        config.HARNESS_ROOT = self.original_root
        self.temp_dir.cleanup()

    def _write(self, agent_id: str, *, pid: int, idle_s: float, recap: bool) -> Path:
        from datetime import datetime, timedelta, timezone

        seen = datetime.now(timezone.utc) - timedelta(seconds=idle_s)
        payload = {
            "agent_id": agent_id,
            "session_pid": pid,
            "last_seen": seen.isoformat(),
        }
        if recap:
            payload["recap_at"] = seen.isoformat()
        path = self.root / "agents" / f"{agent_id}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _purge(self) -> int:
        # The reaper skips its OWN row; pin that to something unrelated so the rows
        # under test are genuinely eligible and the assertions are not vacuous.
        with mock.patch.object(config, "get_agent_id", return_value="not-under-test"):
            return hooks._purge_stale_agents()

    def test_a_recapped_idle_agent_with_a_LIVE_pid_is_kept(self) -> None:
        path = self._write("live-recapped", pid=os.getpid(),
                           idle_s=hooks.RECAP_STALE_SECONDS * 10, recap=True)
        self._purge()
        self.assertTrue(path.exists(),
                        "a running process was unregistered for being quiet")

    def test_an_agent_idle_past_the_hour_with_a_LIVE_pid_is_kept(self) -> None:
        path = self._write("live-idle", pid=os.getpid(),
                           idle_s=hooks.STALE_AGENT_SECONDS * 2, recap=False)
        self._purge()
        self.assertTrue(path.exists())

    def test_CONTROL_the_same_row_with_a_DEAD_pid_is_removed(self) -> None:
        """Without this the tests above pass against a reaper that deletes nothing."""
        path = self._write("dead-idle", pid=_a_pid_that_is_not_running(),
                           idle_s=hooks.STALE_AGENT_SECONDS * 2, recap=False)
        self._purge()
        self.assertFalse(path.exists(),
                         "the reaper kept a row whose process is gone — it now reaps nothing")


class MailSweepSparesLiveRecipientsTests(unittest.TestCase):
    """The silent twin: a refusal can be retried, a deleted message cannot be noticed."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_root = config.HARNESS_ROOT
        config.HARNESS_ROOT = self.root
        (self.root / "agents").mkdir(parents=True)
        self.new = self.root / "inbox" / "new"
        self.new.mkdir(parents=True)
        self._patch = mock.patch.object(inbox, "INBOX_NEW", self.new)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        config.HARNESS_ROOT = self.original_root
        self.temp_dir.cleanup()

    def _message(self, name: str, to: str) -> Path:
        path = self.new / f"{name}.json"
        path.write_text(json.dumps({"to": to, "from": "someone", "body": "hi"}),
                        encoding="utf-8")
        return path

    def test_mail_for_an_agent_whose_record_exists_survives(self) -> None:
        agent_id = "11111111-2222-3333-4444-555555555555"
        (self.root / "agents" / f"{agent_id}.json").write_text("{}", encoding="utf-8")
        msg = self._message("keep", agent_id)
        hooks._purge_orphaned_messages()
        self.assertTrue(msg.exists(), "mail for a registered agent was destroyed")

    def test_mail_survives_when_the_set_was_photographed_before_the_agent_returned(self) -> None:
        """The exact 1–4 s window: the listing is taken, then the seat re-registers.

        `known_ids` is built once and the deletions happen afterwards, so the fix has to
        re-ask at the moment of deletion rather than trust the older photograph.
        """
        agent_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        msg = self._message("late", agent_id)
        real_glob = Path.glob

        def glob_then_register(self_path, pattern):
            results = list(real_glob(self_path, pattern))
            # the seat's next hook lands between the listing and the unlink
            (Path(config.HARNESS_ROOT) / "agents" / f"{agent_id}.json").write_text(
                "{}", encoding="utf-8")
            return iter(results)

        with mock.patch.object(Path, "glob", glob_then_register):
            hooks._purge_orphaned_messages()
        self.assertTrue(msg.exists(),
                        "mail was deleted on the strength of a listing taken before the "
                        "agent came back")

    def test_CONTROL_mail_for_a_genuinely_unknown_agent_is_still_purged(self) -> None:
        """Without this the two tests above pass against a sweep that never deletes.

        ⚠️ THE FIXTURE IS NOW AGED, AND THE ASSERTION IS UNCHANGED (T364). This test used
        to write a message with the current time and expect it gone on the next sweep —
        which is the very behaviour that destroyed `806ac83b`, a deliberate `send --force`
        to an id that had no presence file yet. The sweep now holds an orphan for its own
        TTL before collecting it, so "genuinely unknown" is no longer a question the sweep
        can answer on arrival; it takes time to become true.

        What is being kept here is this test's INTENT — the sweep must still collect real
        orphans, or the two tests above are satisfiable by a sweep that deletes nothing.
        Only the fixture moved: the message is now older than its TTL, which is what
        "genuinely unknown" has to mean once a recipient is allowed to arrive late.
        """
        msg = self._message("orphan", "99999999-0000-0000-0000-000000000000")
        stale = (datetime.now(timezone.utc) - timedelta(minutes=inbox.DEFAULT_TTL_MINUTES + 5))
        msg.write_text(
            json.dumps({"to": "99999999-0000-0000-0000-000000000000", "from": "someone",
                        "body": "hi", "timestamp": stale.isoformat()}),
            encoding="utf-8",
        )
        os.utime(msg, (stale.timestamp(), stale.timestamp()))
        removed = hooks._purge_orphaned_messages()
        self.assertFalse(msg.exists(), "the sweep no longer purges anything")
        self.assertEqual(1, removed)

    def test_fresh_mail_to_an_unknown_agent_is_HELD_not_purged(self) -> None:
        """The other half of the change, pinned next to the control it modified (T364).

        Without this line sitting here, a future reader sees only that the control's
        fixture grew a timestamp and has no way to tell whether that was a real behaviour
        change or a test being bent to fit.
        """
        msg = self._message("just-sent", "99999999-0000-0000-0000-000000000000")
        removed = hooks._purge_orphaned_messages()
        self.assertTrue(msg.exists(), "a --force send was deleted before its recipient could register")
        self.assertEqual(0, removed)


if __name__ == "__main__":
    unittest.main()
