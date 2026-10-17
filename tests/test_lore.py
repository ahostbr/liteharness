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

    def test_boot_prints_it_after_the_project_index_and_before_the_banner(self) -> None:
        repo = self.base / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "AGENT_INDEX.md").write_text("# Agent index\n", encoding="utf-8")
        out = self.register_in(repo)
        self.assertLess(out.index("Agent index"), out.index(lore.HEADER))
        self.assertLess(out.index(lore.HEADER), out.index("Inter-agent messaging active"))

    def register_in(self, cwd: Path, **env: str) -> str:
        buf = io.StringIO()
        full = {"LITEHARNESS_AGENT_ID": "agent-lore-1", "LITEHARNESS_CLI": "claude-code",
                "LITEHARNESS_MODEL": "claude-opus", **env}
        with mock.patch.dict(hooks.os.environ, full, clear=False), \
                mock.patch.object(hooks.os, "getcwd", return_value=str(cwd)), redirect_stdout(buf):
            hooks.register_presence()
        return buf.getvalue()

    def test_boot_prints_it(self) -> None:
        out = self.register()
        self.assertIn(f"{lore.HEADER}\n\n## THE ZERO\nProve your search can hit.", out)

    def test_compact_prints_it_after_the_tier_card(self) -> None:
        out = self.register(LITEHARNESS_HOOK_SOURCE="compact")
        self.assertIn("context was compacted", out)
        self.assertIn(lore.HEADER, out)
        self.assertLess(out.index("context was compacted"), out.index(lore.HEADER))

    def test_missing_file_prints_nothing_at_all(self) -> None:
        self.lore_file.unlink()
        for source in ("startup", "compact"):
            out = self.register(LITEHARNESS_HOOK_SOURCE=source)
            self.assertNotIn(lore.HEADER, out)
            self.assertNotIn("[... lore", out)
            self.assertNotIn("lore unavailable", out)

    def test_blank_file_prints_nothing_at_all(self) -> None:
        self.write("\n  \n")
        self.assertNotIn(lore.HEADER, self.register())

    def test_the_two_concurrent_hooks_print_it_once(self) -> None:
        first, second = self.register(), self.register()
        self.assertEqual(first.count(lore.HEADER), 1)
        self.assertEqual(second.count(lore.HEADER), 0)

    def test_boot_and_compact_are_separate_events(self) -> None:
        self.assertEqual(self.register().count(lore.HEADER), 1)
        self.assertEqual(self.register(LITEHARNESS_HOOK_SOURCE="compact").count(lore.HEADER), 1)

    def test_a_later_event_of_the_same_kind_prints_again(self) -> None:
        self.assertEqual(self.register(LITEHARNESS_HOOK_SOURCE="compact").count(lore.HEADER), 1)
        marker = next((self.root / ".printed_once").iterdir())
        old = time.time() - 120
        os.utime(marker, (old, old))
        self.assertEqual(self.register(LITEHARNESS_HOOK_SOURCE="compact").count(lore.HEADER), 1)

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

    def test_a_lore_error_never_kills_session_start(self) -> None:
        with mock.patch.object(lore, "lore_block", side_effect=RuntimeError("boom")):
            out = self.register()
        self.assertIn("lore unavailable", out)
        self.assertIn("Inter-agent messaging active", out)


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
