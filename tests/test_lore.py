"""T0583-C: the lore file is printed at SessionStart (boot and after a compaction).

Contract (Ryan: LiteHarness ships only the reader and an empty default):
  1. path: env LITEHARNESS_LORE_FILE > config `lore_file` > <harness root>/lore.md
  2. a missing or blank file prints nothing at all: no header, no warning
  3. cap 2,400 chars, cut on a line boundary, with one note naming the file and size
  4. at most 4 `## ` sections, the extras cut first, with a note
  5. `liteharness lore --check [--file P]` exits 1 over the cap or over 4 sections
  6. printed once per event, although both SessionStart hook entries run register
  7. a lore error never kills SessionStart
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from liteharness import config, hooks, lore

SECTIONS = "\n\n".join(f"## STORY{i}\nThe rule of story {i}, told short." for i in range(1, 7))


def run_cli(*args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "liteharness.cli", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={**os.environ, **env},
    )


class LoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "hroot"
        self.lore_file = self.root / "lore.md"
        self.original_root = config.HARNESS_ROOT
        self.original_config_path = config.CONFIG_PATH
        config.HARNESS_ROOT = self.root
        config.CONFIG_PATH = self.root / "config.json"
        self.root.mkdir()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        config.HARNESS_ROOT = self.original_root
        config.CONFIG_PATH = self.original_config_path
        self._tmp.cleanup()

    @property
    def where(self) -> str:
        return str(self.lore_file).replace("\\", "/")

    def write(self, text: str, path: Path | None = None) -> Path:
        path = path or self.lore_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def register(self, **env: str) -> str:
        buf = io.StringIO()
        full = {
            "LITEHARNESS_AGENT_ID": "agent-lore-1",
            "LITEHARNESS_CLI": "claude-code",
            "LITEHARNESS_MODEL": "claude-opus",
            **env,
        }
        with mock.patch.dict(hooks.os.environ, full, clear=False), \
                mock.patch.object(hooks.os, "getcwd", return_value=str(self.base)), \
                redirect_stdout(buf):
            hooks.register_presence()
        return buf.getvalue()


class PathTests(LoreCase):
    def test_default_is_lore_md_in_the_harness_root(self) -> None:
        with mock.patch.dict(os.environ, clear=False) as env:
            env.pop(lore.LORE_ENV, None)
            self.assertEqual(lore.lore_path(), self.lore_file)

    def test_config_lore_file_replaces_the_default(self) -> None:
        other = self.base / "elsewhere" / "mine.md"
        config.save({"lore_file": str(other)})
        with mock.patch.dict(os.environ, clear=False) as env:
            env.pop(lore.LORE_ENV, None)
            self.assertEqual(lore.lore_path(), other)

    def test_a_non_string_or_empty_config_value_falls_through_to_the_default(self) -> None:
        for bad in (5, ["x"], {"a": 1}, None, ""):
            config.save({"lore_file": bad})
            with mock.patch.dict(os.environ, clear=False) as env:
                env.pop(lore.LORE_ENV, None)
                self.assertEqual(lore.lore_path(), self.lore_file, bad)

    def test_a_config_that_is_not_an_object_falls_through_to_the_default(self) -> None:
        config.CONFIG_PATH.write_text("[1, 2]", encoding="utf-8")
        with mock.patch.dict(os.environ, clear=False) as env:
            env.pop(lore.LORE_ENV, None)
            self.assertEqual(lore.lore_path(), self.lore_file)

    def test_env_wins_over_config(self) -> None:
        config.save({"lore_file": str(self.base / "config.md")})
        with mock.patch.dict(os.environ, {lore.LORE_ENV: str(self.base / "env.md")}):
            self.assertEqual(lore.lore_path(), self.base / "env.md")


class BlockTests(LoreCase):
    def test_header_then_the_file(self) -> None:
        self.write("## THE ZERO\nProve your search can hit.\n")
        block = lore.lore_block(self.lore_file)
        self.assertEqual(block, f"{lore.HEADER}\n\n## THE ZERO\nProve your search can hit.\n")

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(lore.lore_block(self.lore_file), "")

    def test_blank_file_is_empty(self) -> None:
        self.write(" \r\n\t\n ")
        self.assertEqual(lore.lore_block(self.lore_file), "")

    def test_a_directory_in_the_way_is_empty_not_an_error(self) -> None:
        self.lore_file.mkdir()
        self.assertEqual(lore.lore_block(self.lore_file), "")

    def test_over_the_cap_is_cut_on_a_line_boundary_with_one_note(self) -> None:
        line = "x" * 99
        self.write("\n".join([line] * 40))  # 3,999 chars
        block = lore.lore_block(self.lore_file)
        body, note = block.rstrip("\n").rsplit("\n", 1)
        kept = body.split("\n", 2)[2]
        self.assertLessEqual(len(kept), lore.LORE_CAP)
        self.assertTrue(all(row == line for row in kept.split("\n")))  # no half line
        self.assertEqual(note, f"[... lore {self.where} truncated at 2400 of 3999 chars]")

    def test_a_cut_landing_exactly_on_a_line_end_keeps_that_line(self) -> None:
        self.write("\n".join(["y" * 99] * 24 + ["z" * 99]))  # line 24 ends at char 2400
        kept = lore.lore_block(self.lore_file).split("\n", 2)[2].split("\n[... lore")[0]
        self.assertEqual(kept.count("y"), 24 * 99)
        self.assertNotIn("z", kept)

    def test_extra_sections_are_cut_with_a_note_and_the_preamble_is_kept(self) -> None:
        self.write("Four stories.\n\n" + SECTIONS)
        block = lore.lore_block(self.lore_file)
        self.assertIn("Four stories.", block)
        self.assertIn("## STORY4", block)
        self.assertNotIn("STORY5", block)
        self.assertTrue(block.endswith(f"[... lore {self.where}: 6 sections, only the first 4 are injected]\n"))

    def test_both_limits_cut_sections_first_then_the_cap(self) -> None:
        self.write("\n\n".join(f"## S{i}\n" + "\n".join(["w" * 99] * 8) for i in range(1, 7)))
        block = lore.lore_block(self.lore_file)
        notes = [row for row in block.splitlines() if row.startswith("[... lore")]
        self.assertEqual(len(notes), 2)
        self.assertIn("6 sections", notes[0])
        self.assertIn("truncated at 2400 of", notes[1])
        self.assertNotIn("## S5", block)

    def test_no_line_boundary_in_the_cap_keeps_no_partial_line(self) -> None:
        self.write("a" * 3000)
        self.assertEqual(
            lore.lore_block(self.lore_file),
            f"{lore.HEADER}\n\n[... lore {self.where} truncated at 2400 of 3000 chars]\n",
        )

    def test_one_line_of_exactly_the_cap_is_whole(self) -> None:
        self.write("a" * 2400)
        self.assertEqual(lore.lore_block(self.lore_file), f"{lore.HEADER}\n\n{'a' * 2400}\n")

    def test_a_bom_is_dropped_and_does_not_hide_the_first_section(self) -> None:
        self.lore_file.write_bytes(b"\xef\xbb\xbf" + SECTIONS.split("\n\n## STORY6")[0].encode("utf-8"))  # 5 sections
        block = lore.lore_block(self.lore_file)
        self.assertNotIn("﻿", block)
        self.assertTrue(block.startswith(f"{lore.HEADER}\n\n## STORY1\n"))
        self.assertIn("5 sections, only the first 4", block)
        self.assertNotIn("STORY5", block)

    def test_crlf_cuts_on_the_same_line_as_lf(self) -> None:
        rows = [f"row {i:03d} " + "x" * 80 for i in range(40)]
        self.write("\n".join(rows))
        as_lf = lore.lore_block(self.lore_file)
        self.write("\r\n".join(rows))
        self.assertEqual(lore.lore_block(self.lore_file), as_lf)
        self.assertNotIn("\r", as_lf)

    def test_triple_hash_is_not_a_section(self) -> None:
        self.write("## ONE\n" + "\n".join(f"### sub {i}" for i in range(10)))
        self.assertNotIn("only the first", lore.lore_block(self.lore_file))


class SessionStartTests(LoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("## THE ZERO\nProve your search can hit.\n")
        patcher = mock.patch.dict(os.environ, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(lore.LORE_ENV, None)

    def session_start(self, source: str = "startup", event: str = "SessionStart", transcript: bool = True) -> str:
        """The stdout of `hooks check` for one hook event, run through the real dispatcher."""
        payload = {"hook_event_name": event, "session_id": "agent-lore-1", "source": source}
        if transcript:
            payload["transcript_path"] = str(self.base / "t.jsonl")
        buf = io.StringIO()
        with mock.patch.dict(hooks.os.environ, {"LITEHARNESS_AGENT_ID": "agent-lore-1"}, clear=False),                 mock.patch.object(sys, "argv", ["hooks", "check"]),                 mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),                 redirect_stdout(buf):
            hooks.main()
        return buf.getvalue()

    def test_boot_prints_it(self) -> None:
        out = self.session_start()
        self.assertEqual(out, f"{lore.HEADER}\n\n## THE ZERO\nProve your search can hit.\n\n")

    def test_compact_prints_it(self) -> None:
        self.assertIn(lore.HEADER, self.session_start("compact"))

    def test_resume_and_clear_print_it(self) -> None:
        for source in ("resume", "clear"):
            self.assertIn(lore.HEADER, self.session_start(source), source)

    def test_register_no_longer_prints_it_so_it_cannot_land_below_the_fold(self) -> None:
        for source in ("startup", "compact"):
            out = self.register(LITEHARNESS_HOOK_SOURCE=source)
            self.assertNotIn(lore.HEADER, out)

    def test_only_session_start_with_a_transcript_prints_it(self) -> None:
        self.assertNotIn(lore.HEADER, self.session_start(event="PostToolUse"))
        self.assertNotIn(lore.HEADER, self.session_start(transcript=False))  # a subagent

    def test_missing_file_prints_nothing_at_all(self) -> None:
        self.lore_file.unlink()
        for source in ("startup", "compact"):
            self.assertEqual(self.session_start(source), "")

    def test_blank_file_prints_nothing_at_all(self) -> None:
        self.write("\n  \n")
        self.assertEqual(self.session_start(), "")

    def test_the_two_concurrent_hooks_print_it_once(self) -> None:
        first, second = self.session_start(), self.session_start()
        self.assertEqual(first.count(lore.HEADER), 1)
        self.assertEqual(second.count(lore.HEADER), 0)

    def test_boot_and_compact_are_separate_events(self) -> None:
        self.assertEqual(self.session_start().count(lore.HEADER), 1)
        self.assertEqual(self.session_start("compact").count(lore.HEADER), 1)

    def test_a_later_event_of_the_same_kind_prints_again(self) -> None:
        self.assertEqual(self.session_start("compact").count(lore.HEADER), 1)
        marker = next((self.root / ".printed_once").iterdir())
        old = time.time() - 120
        os.utime(marker, (old, old))
        self.assertEqual(self.session_start("compact").count(lore.HEADER), 1)

    def test_exactly_one_of_many_racing_hooks_wins(self) -> None:
        wins: list[bool] = []
        gate = threading.Barrier(8)

        def race() -> None:
            gate.wait()
            wins.append(hooks._first_to_print("race.key"))

        threads = [threading.Thread(target=race) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(wins.count(True), 1)

    def stale(self, key: str) -> Path:
        marker = self.root / ".printed_once" / f"{key}.0"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"")
        old = time.time() - 300
        os.utime(marker, (old, old))
        return marker

    def test_exactly_one_of_many_threads_wins_a_stale_marker(self) -> None:
        for trial in range(200):
            key = f"stale.thread.{trial}"
            self.stale(key)
            wins: list[bool] = []
            gate = threading.Barrier(8)

            def race() -> None:
                gate.wait()
                wins.append(hooks._first_to_print(key))

            threads = [threading.Thread(target=race) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(wins.count(True), 1, f"trial {trial}: {wins}")

    def test_exactly_one_of_two_real_processes_wins_a_stale_marker(self) -> None:
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from liteharness import config, hooks\n"
            "config.HARNESS_ROOT = Path(sys.argv[1])\n"
            "while time.time() < float(sys.argv[3]):\n"
            "    pass\n"
            "print(hooks._first_to_print(sys.argv[2]))\n"
        )
        for trial in range(10):
            key = f"stale.proc.{trial}"
            self.stale(key)
            start = str(time.time() + 1.0)
            procs = [
                subprocess.Popen([sys.executable, "-c", script, str(self.root), key, start],
                                 stdout=subprocess.PIPE, text=True)
                for _ in range(2)
            ]
            wins = [p.communicate(timeout=60)[0].strip() for p in procs]
            self.assertEqual(sorted(wins), ["False", "True"], f"trial {trial}: {wins}")

    def test_a_retired_stale_marker_leaves_only_the_new_one_behind(self) -> None:
        self.stale("one.key")
        self.assertTrue(hooks._first_to_print("one.key"))
        self.assertEqual([p.name for p in (self.root / ".printed_once").iterdir()], ["one.key.1"])

    def test_a_lore_error_never_kills_session_start(self) -> None:
        with mock.patch.object(lore, "lore_block", side_effect=RuntimeError("boom")), \
                mock.patch.object(hooks, "check_inbox") as inbox:
            out = self.session_start()
        self.assertIn("lore unavailable", out)
        inbox.assert_called_once()  # the inbox check still ran after the lore failed


class CheckTests(LoreCase):
    def check(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return run_cli("lore", "--check", *args, **env)

    def test_clean_file_exits_zero(self) -> None:
        self.write(SECTIONS.split("\n\n## STORY5")[0])
        proc = self.check("--file", str(self.lore_file))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok:", proc.stdout)
        self.assertIn("4 sections", proc.stdout)

    def test_missing_or_blank_file_exits_zero(self) -> None:
        self.assertEqual(self.check("--file", str(self.lore_file)).returncode, 0)
        self.write("\n")
        self.assertEqual(self.check("--file", str(self.lore_file)).returncode, 0)

    def test_over_the_cap_exits_one_naming_file_and_size(self) -> None:
        self.write("a" * 2401)
        proc = self.check("--file", str(self.lore_file))
        self.assertEqual(proc.returncode, 1)
        self.assertIn(str(self.lore_file), proc.stdout)
        self.assertIn("2401 chars, over the 2400 cap", proc.stdout)

    def test_exactly_the_cap_passes(self) -> None:
        self.write("a" * 2400)
        self.assertEqual(self.check("--file", str(self.lore_file)).returncode, 0)

    def test_over_four_sections_exits_one(self) -> None:
        self.write(SECTIONS)
        proc = self.check("--file", str(self.lore_file))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("6 sections, over the 4 limit", proc.stdout)

    def test_without_file_it_checks_the_env_file(self) -> None:
        self.write("a" * 2401)
        proc = self.check(**{lore.LORE_ENV: str(self.lore_file)})
        self.assertEqual(proc.returncode, 1)

    def test_check_flag_is_required(self) -> None:
        self.assertEqual(run_cli("lore").returncode, 2)


if __name__ == "__main__":
    unittest.main()
