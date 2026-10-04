"""T0237 WS2: one committed AGENT_INDEX.md per project, checked and announced at spawn.

Fixed contract (the orchestrator) for `liteharness index --check --project <root>`, one
test per rule:
  1. a link is a markdown [text](target); backticked / bare paths are NOT checked
  2. http://, https://, mailto: and a pure #anchor are skipped
  3. the target resolves relative to the INDEX FILE, is URL-decoded, #fragment
     stripped; anchors are not validated
  4. a missing target exits 1 printing file:line + target; a missing index exits 1;
     all good exits 0
  5. the index is AGENT_INDEX.md at the repo root; over 12,000 chars exits 1
Plus: SessionStart (`register_presence`) prints the index path and first screen
right after the spawn brief for a session whose cwd is in a repo that has one.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from liteharness import config, hooks, project_index

GOOD_INDEX = """# Agent index

## Hard rules
- Never edit `src/core/engine.py` without reading [the design](docs/design.md#intro).
- Tests live in [tests](tests/) (see [the engine test](tests/test_engine.py)).

## Topics
| Area | Read | Traps |
|------|------|-------|
| Engine | [engine](src/core/engine.py), [design](docs/design.md) | [spaced name](docs/my%20notes.md) |

```bash
# fenced blocks are examples, not links: nothing below is checked
[example](does/not/exist.md)
```

External: [site](https://example.com/x), [mail](mailto:a@b.c), [top](#hard-rules).
"""


def make_repo(root: Path, index_text: str | None = GOOD_INDEX) -> Path:
    (root / "src" / "core").mkdir(parents=True)
    (root / ".git").mkdir()  # a repo root; the index is found by walking up to it
    (root / "src" / "core" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "design.md").write_text("# Intro\n", encoding="utf-8")
    (root / "docs" / "my notes.md").write_text("n\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_engine.py").write_text("", encoding="utf-8")
    if index_text is not None:
        (root / "AGENT_INDEX.md").write_text(index_text, encoding="utf-8")
    return root


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "liteharness.cli", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


class IndexCheckContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, text: str) -> None:
        (self.root / "AGENT_INDEX.md").write_text(text, encoding="utf-8")

    def check(self) -> subprocess.CompletedProcess:
        return run_cli("index", "--check", "--project", str(self.root))

    # rule 4 (all good)
    def test_clean_index_exits_zero(self) -> None:
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ok", r.stdout.lower())

    # rule 1
    def test_only_markdown_links_are_checked_not_backticked_or_bare_paths(self) -> None:
        self.write("`src/core/ghost.py` and bare docs/ghost.md and src/ghost/dir/ and `a/b.txt:12`\n")
        self.assertEqual(self.check().returncode, 0)
        self.write("[gone](docs/ghost.md)\n")
        self.assertEqual(self.check().returncode, 1)

    # not in the fixed contract: a documented choice, pinned here
    def test_links_inside_fenced_code_blocks_are_examples_and_are_skipped(self) -> None:
        self.write("```"+chr(10)+"[example](does/not/exist.md)"+chr(10)+"```"+chr(10)+"~~~"+chr(10)+"[e2](nor/this.md)"+chr(10)+"~~~"+chr(10))
        self.assertEqual(self.check().returncode, 0)

    def test_oversize_counts_raw_characters_so_crlf_counts_as_two(self) -> None:
        # 3,999 lines of "a"+CRLF = 11,997 raw chars (7,998 folded): passes either way;
        # 5,000 lines = 15,000 raw chars but only 10,000 folded: must FAIL (raw count).
        (self.root / "AGENT_INDEX.md").write_bytes(b"a\r\n" * 3_999)
        self.assertEqual(self.check().returncode, 0)
        (self.root / "AGENT_INDEX.md").write_bytes(b"a\r\n" * 5_000)
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("15000", r.stdout)

    # rule 2
    def test_http_https_mailto_and_pure_anchors_are_skipped(self) -> None:
        self.write("[a](http://x.invalid/p) [b](https://x.invalid/p) [c](mailto:a@b.c) [d](#nowhere)\n")
        self.assertEqual(self.check().returncode, 0)

    # rule 3
    def test_target_resolves_relative_to_the_index_file_not_the_cwd(self) -> None:
        self.write("[design](docs/design.md)\n")
        r = subprocess.run(
            [sys.executable, "-m", "liteharness.cli", "index", "--check", "--project", str(self.root)],
            capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=str(self.root / "tests"),
            env={**os.environ, "PYTHONPATH": str(Path(project_index.__file__).resolve().parents[1])},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_target_is_url_decoded(self) -> None:
        self.write("[n](docs/my%20notes.md)\n")
        self.assertEqual(self.check().returncode, 0)
        self.write("[n](docs/my%20other.md)\n")
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/my%20other.md", r.stdout)

    def test_fragment_is_stripped_and_anchors_are_not_validated(self) -> None:
        self.write("[d](docs/design.md#no-such-heading)\n")
        self.assertEqual(self.check().returncode, 0)
        self.write("[d](docs/ghost.md#intro)\n")
        self.assertEqual(self.check().returncode, 1)

    # rule 4 (dead link, missing index)
    def test_dead_links_exit_one_printing_file_line_and_target(self) -> None:
        self.write(GOOD_INDEX + "\nTrap: [gone](docs/missing.md) and [too](src/nope.py)\n")
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        lines = [l for l in r.stdout.splitlines() if "dead link" in l]
        self.assertEqual(len(lines), 2, r.stdout)
        gone = next(l for l in lines if "docs/missing.md" in l)
        self.assertTrue(gone.startswith("AGENT_INDEX.md:"), gone)
        self.assertRegex(gone, r"^AGENT_INDEX\.md:\d+:")
        self.assertNotIn("design.md", r.stdout)  # live links are not reported
        self.assertNotIn("does/not/exist.md", r.stdout)  # fenced code is not a link

    def test_missing_index_exits_one(self) -> None:
        (self.root / "AGENT_INDEX.md").unlink()
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("AGENT_INDEX.md", r.stdout + r.stderr)

    def test_missing_project_flag_is_usage_error(self) -> None:
        self.assertEqual(run_cli("index", "--check").returncode, 2)

    # rule 5
    def test_index_is_looked_up_at_the_repo_root_only(self) -> None:
        (self.root / "AGENT_INDEX.md").unlink()
        (self.root / "docs" / "AGENT_INDEX.md").write_text("# nested\n", encoding="utf-8")
        self.assertEqual(self.check().returncode, 1)

    def test_over_12k_chars_exits_one_and_exactly_12k_passes(self) -> None:
        self.write("a" * 12_000)
        self.assertEqual(self.check().returncode, 0)
        self.write("a" * 12_001)
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("12001", r.stdout)
        self.assertIn("12000", r.stdout)

    def test_oversize_with_dead_link_reports_both(self) -> None:
        self.write("[gone](docs/missing.md)\n" + "a" * 12_100)
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/missing.md", r.stdout)
        self.assertIn("over the 12000 cap", r.stdout)


class FindIndexTests(unittest.TestCase):
    def test_walks_up_to_the_git_root_and_never_past_it(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            outer = make_repo(base / "outer")
            inner = base / "outer" / "inner"
            inner.mkdir()
            (inner / ".git").write_text("gitdir: x" + chr(10), encoding="utf-8")
            sub = outer / "src" / "core"
            self.assertEqual(project_index.find_index(sub), outer / "AGENT_INDEX.md")
            self.assertIsNone(project_index.find_index(inner))

    def test_no_git_root_consults_only_the_start_directory(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "AGENT_INDEX.md").write_text("x", encoding="utf-8")
            (root / "sub").mkdir()
            self.assertEqual(project_index.find_index(root), root / "AGENT_INDEX.md")
            self.assertIsNone(project_index.find_index(root / "sub"))


class SessionStartBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "hroot"
        self.repo = make_repo(base / "repo")
        self.original_root = config.HARNESS_ROOT
        self.original_config_path = config.CONFIG_PATH
        config.HARNESS_ROOT = self.root
        config.CONFIG_PATH = self.root / "config.json"

    def tearDown(self) -> None:
        config.HARNESS_ROOT = self.original_root
        config.CONFIG_PATH = self.original_config_path
        self._tmp.cleanup()

    def register(self, cwd: Path, **env: str) -> str:
        buf = io.StringIO()
        full = {
            "LITEHARNESS_AGENT_ID": "agent-idx-1",
            "LITEHARNESS_CLI": "claude-code",
            "LITEHARNESS_MODEL": "claude-opus",
            **env,
        }
        with mock.patch.dict(hooks.os.environ, full, clear=False), \
                mock.patch.object(hooks.os, "getcwd", return_value=str(cwd)), \
                redirect_stdout(buf):
            hooks.register_presence()
        return buf.getvalue()

    def test_brief_carries_index_path_and_first_screen(self) -> None:
        out = self.register(self.repo / "src" / "core")  # cwd inside the repo, not at its root
        idx = str((self.repo / "AGENT_INDEX.md")).replace("\\", "/")
        self.assertIn(idx, out.replace("\\", "/"))
        self.assertIn("Never edit `src/core/engine.py`", out)  # first screen text

    def test_index_follows_the_spawn_brief_and_precedes_boilerplate(self) -> None:
        brief = self.repo.parent / "brief.txt"
        brief.write_text("DO THE THING\n", encoding="utf-8")
        out = self.register(self.repo, LITEHARNESS_SPAWN_BRIEF=str(brief))
        self.assertLess(out.index("DO THE THING"), out.index("AGENT_INDEX.md"))
        self.assertLess(out.index("AGENT_INDEX.md"), out.index("Inter-agent messaging active"))

    def test_no_index_no_block(self) -> None:
        (self.repo / "AGENT_INDEX.md").unlink()
        out = self.register(self.repo)
        self.assertNotIn("AGENT_INDEX.md", out)

    def test_long_index_is_cut_to_the_first_screen(self) -> None:
        lines = [f"- hard rule number {i}" for i in range(200)]
        (self.repo / "AGENT_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = self.register(self.repo)
        self.assertIn("hard rule number 0", out)
        self.assertNotIn("hard rule number 150", out)
        self.assertIn("more lines", out)  # tells the agent the file continues

    def test_compaction_gets_a_pointer_not_the_full_screen(self) -> None:
        out = self.register(self.repo, LITEHARNESS_HOOK_SOURCE="compact")
        self.assertIn("AGENT_INDEX.md", out)
        self.assertNotIn("Never edit `src/core/engine.py`", out)


class GameDirectorBriefTests(unittest.TestCase):
    START = "<!-- game-director:start -->"
    END = "<!-- game-director:end -->"
    MANIFEST = ".litesuite/game-director/addons.json"

    def render(self, text: str, *, compacting: bool = False) -> str:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "AGENT_INDEX.md").open("w", encoding="utf-8", newline="") as stream:
                stream.write(text)
            return project_index.brief_block(root, compacting=compacting)

    def block(self, content: str = "VibeUE installed; Director uses codex") -> str:
        return f"{self.START}\n{content}\n{self.END}"

    def test_end_block_is_delivered_after_first_screen_on_a_long_index(self) -> None:
        text = "# User index\n" + "User-owned rule\n" * 250 + self.block()
        out = self.render(text)
        self.assertIn("VibeUE installed; Director uses codex", out)
        self.assertLess(out.index("User-owned rule"), out.index("VibeUE installed"))

    def test_block_inside_first_screen_is_not_printed_twice(self) -> None:
        out = self.render("# User index\n" + self.block())
        self.assertEqual(out.count("VibeUE installed; Director uses codex"), 1)

    def test_oversize_block_is_bounded_and_ends_with_manifest_pointer(self) -> None:
        text = "User-owned rule\n" * 250 + self.block("Z" * 9000)
        out = self.render(text)
        self.assertLessEqual(out.count("Z"), 4000)
        self.assertIn("Z", out)
        self.assertIn(self.MANIFEST, out.splitlines()[-1])

    def test_oversize_block_inside_first_screen_cannot_bypass_the_bound(self) -> None:
        out = self.render(self.block("Z" * 9000))
        self.assertLessEqual(out.count("Z"), 4000)
        self.assertIn(self.MANIFEST, out.splitlines()[-1])

    def test_partly_visible_block_is_delivered_once_in_full(self) -> None:
        block = self.block("\n".join(f"Milestone work {i}" for i in range(20)))
        out = self.render("User rule\n" * 35 + block)
        self.assertEqual(out.count("Milestone work 0\n"), 1)
        self.assertIn("Milestone work 19", out)

    def test_malformed_block_in_first_screen_preserves_user_text_and_adds_only_pointer(self) -> None:
        out = self.render(self.START + "\nMALFORMED_PAYLOAD")
        self.assertIn("MALFORMED_PAYLOAD", out)
        self.assertEqual(out.count(self.MANIFEST), 1)

    def test_prose_marker_on_line_two_keeps_following_user_lines(self) -> None:
        out = self.render(f"# User index\nMention {self.START} in prose\nKeep this user rule\nAnd this one\n")
        self.assertIn("Keep this user rule", out)
        self.assertIn("And this one", out)
        self.assertEqual(out.count(self.MANIFEST), 1)
        self.assertNotIn("## Game Director - project knowledge", out)

    def test_prose_pair_beyond_screen_is_not_a_managed_block(self) -> None:
        out = self.render("User rule\n" * 250 + f"Mention {self.START} here\nPROSE_PAYLOAD\nMention {self.END} here\n")
        self.assertNotIn("PROSE_PAYLOAD", out)
        self.assertNotIn("## Game Director - project knowledge", out)
        self.assertEqual(out.count(self.MANIFEST), 1)

    def test_partial_block_keeps_tail_note_for_unshown_user_lines(self) -> None:
        block = self.block("\n".join(f"Milestone work {i}" for i in range(12)))
        out = self.render("User rule\n" * 33 + block + "\n" + "Trailing user rule\n" * 30)
        self.assertIn("Milestone work 11", out)
        self.assertIn("[... 30 more lines - read the file for the topic table]", out)

    def test_three_thousand_character_block_is_printed_whole(self) -> None:
        block = self.block("Z" * 3000)
        out = self.render("User rule\n" * 250 + block)
        self.assertIn(block, out)
        self.assertNotIn(self.MANIFEST, out)

    def test_malformed_or_duplicate_markers_get_one_pointer_without_crashing(self) -> None:
        invalid = [self.START, self.END, self.END + self.START,
                   self.block() + self.START, self.START + self.block(),
                   self.block() + self.END, self.block() + self.block()]
        for markers in invalid:
            with self.subTest(markers=markers):
                out = self.render("User-owned rule\n" * 250 + markers)
                self.assertEqual(out.count(self.MANIFEST), 1)
                self.assertNotIn("VibeUE installed; Director uses codex", out)

    def test_compaction_keeps_its_original_pointer_plus_one_manifest_pointer(self) -> None:
        out = self.render("User-owned rule\n" * 250 + self.block(), compacting=True)
        self.assertIn("re-read it after compaction", out)
        self.assertEqual(out.count(self.MANIFEST), 1)
        self.assertNotIn("VibeUE installed; Director uses codex", out)

    def test_no_markers_preserves_existing_output_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "# Unmanaged index\n" + "User-owned rule\n" * 250
            with (root / "AGENT_INDEX.md").open("w", encoding="utf-8", newline="") as stream:
                stream.write(text)
            where = str(root / "AGENT_INDEX.md").replace("\\", "/")
            expected = (f"## Project index - AGENT_INDEX.md (read before touching any area)\n"
                        f"Path: {where}\n"
                        "First screen below; the topic table says what to read, in order, per area.\n\n"
                        f"{project_index.first_screen(text)}\n")
            self.assertEqual(project_index.brief_block(root), expected)
            self.assertEqual(project_index.brief_block(root, compacting=True),
                             f"[LITEHARNESS] Project index: {where} - re-read it after compaction before touching an area.\n")


if __name__ == "__main__":
    unittest.main()
