"""T0262: human-turn candidates, never flattened tool prose or inferred intent."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / "liteharness/catalog/skills/ls-conversation-lookup"
sys.path.insert(0, str(SKILL))
try:
    import ruling_index as rulings
except ModuleNotFoundError:
    rulings = None


class RulingIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "index/convo_index.db"
        self.source = self.root / "claude/projects/demo/session.jsonl"
        self.env = patch.dict(os.environ, {
            "LITEHARNESS_CONVO_HOME": str(self.db.parent),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "CODEX_HOME": str(self.root / "codex"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = {"sources": [str(self.source)], "repositories": [], "cards": []}

    def index(self):
        self.assertIsNotNone(rulings, "opt-in ruling index is not implemented")
        return rulings.index_rulings(self.db, self.config)

    def write(self, rows, path=None):
        path = path or self.source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def human(self, uuid="ask", text="amber choose local only", **extra):
        return {"type": "user", "uuid": uuid, "timestamp": "2026-10-01T00:00:00Z",
                "origin": {"kind": "human"}, "promptSource": "typed", "turnOrigin": "human",
                "message": {"role": "user", "content": text}, **extra}

    def search(self, term="amber"):
        return rulings.search_rulings(self.db, term)

    def test_human_provenance_and_legacy_are_distinct_from_injections(self):
        rows = [self.human(), {"type": "user", "uuid": "old", "message": {"content": "amber old choice"}}]
        for n, text in enumerate(("<task-notification>amber", "<system-reminder>amber",
                                  "This session is being continued amber", "[inbox from x] amber",
                                  "Base directory for this skill amber", "<artifact-content-authored-by-others/>amber",
                                  "# AGENTS.md instructions for amber")):
            rows.append({"type": "user", "uuid": str(n), "message": {"content": text}})
        rows += [self.human("system", "amber", origin={"kind": "task-notification"}),
                 self.human("hidden", "amber", promptSource="system"),
                 self.human("side", "amber", isSidechain=True),
                 self.human("meta", "amber", isMeta=True),
                 {"type": "user", "uuid": "tool", "message": {"content": [
                     {"type": "tool_result", "content": "amber Avery chose yes"}]}},
                 {"type": "assistant", "message": {"content": "amber Avery chose yes"}}]
        self.write(rows)
        self.index()
        hits = self.search()
        self.assertEqual({h["origin_class"] for h in hits}, {"human-typed", "legacy-human-candidate"})
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(h["commits"] == [] for h in hits))
        self.assertTrue(all(h["citation"]["line"] for h in hits))

    def test_names_in_human_quotes_do_not_determine_origin(self):
        self.write([
            self.human('quote', 'amber [Harbor] proposed this; I approve'),
            self.human('agent', 'amber approved', origin={'kind': 'agent', 'name': 'Harbor'}),
            self.human('renamed', 'amber approved', origin={'kind': 'agent', 'name': 'Cedar'}),
        ])
        self.index()
        hits = self.search()
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['text'], 'amber [Harbor] proposed this; I approve')
        self.assertEqual(hits[0]['origin_class'], 'human-typed')

    def test_form_answers_resolve_the_question_without_indexing_tool_prose(self):
        question = {"question": "amber Ship locally?", "options": [{"label": "Yes"}, {"label": "No"}]}
        call = {"type": "assistant", "uuid": "question", "message": {"content": [
            {"type": "tool_use", "id": "call-1", "name": "AskUserQuestion", "input": {"questions": [question]}}]}}
        answer = {"type": "user", "uuid": "answer", "sourceToolAssistantUUID": "question",
                  "toolUseResult": {"questions": [question], "answers": {question["question"]: "Yes"}},
                  "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1",
                                             "content": "amber fabricated transcript prose"}]}}
        self.write([call, answer, {**answer, "uuid": "unresolved", "sourceToolAssistantUUID": "wrong"}])
        self.index()
        hits = self.search()
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["origin_class"], "form-answer")
        self.assertIn("Ship locally?", hits[0]["text"])
        self.assertIn("Yes", hits[0]["text"])
        self.assertNotIn("fabricated", hits[0]["text"])
        self.assertEqual(hits[0]["question_citation"]["line"], 1)

    def test_codex_visible_user_text_only_no_event_mirrors_or_tools(self):
        self.source = self.root / "codex/archived_sessions/rollout-test.jsonl"
        self.config["sources"] = [str(self.source)]
        self.write([
            {"type": "session_meta", "payload": {"id": "codex-seat", "cwd": "demo"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "amber duplicate"}},
            {"type": "response_item", "payload": {"id": "ask", "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "amber use local"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": "amber hidden"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "amber tool prose"}},
        ])
        self.index()
        hit, = self.search()
        self.assertEqual((hit["provider"], hit["conversation_id"], hit["origin_class"]),
                         ("codex", "codex-seat", "legacy-human-candidate"))

    def test_explicit_card_mapping_and_allowlisted_task_trailers_only(self):
        repo = self.root / "repo"
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
        git("init", "-q")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--allow-empty", "-qm", "fixture\n\nTask-id: T0262")
        sha = git("rev-parse", "HEAD")
        self.write([self.human()])
        self.config["repositories"] = [str(repo)]
        self.index()
        self.assertEqual(self.search()[0]["commits"], [])
        self.config["cards"] = [{"source_path": str(self.source), "record_uuid": "ask", "card_id": "T0262"}]
        self.index()
        commit, = self.search()[0]["commits"]
        self.assertEqual(commit["sha"], sha)
        self.assertEqual(commit["card_id"], "T0262")
        self.assertEqual(commit["basis"], "explicit-card-map+Task-id")
        self.assertEqual(commit["repo"], str(repo.resolve()))

    def test_idempotency_source_update_and_read_only_search(self):
        self.write([self.human()])
        self.index()
        self.index()
        self.assertEqual(len(self.search()), 1)
        self.write([self.human(text="violet changed choice")])
        self.index()
        self.assertEqual(self.search(), [])
        hit, = self.search("violet")
        self.assertEqual(hit["citation"]["line"], 1)
        before = self.db.read_bytes()
        self.search("violet")
        self.assertEqual(self.db.read_bytes(), before)
        self.source.unlink()
        self.assertIsNone(self.search("violet")[0]["citation"]["line"])

    def test_relocated_or_duplicate_records_never_get_a_stale_line(self):
        row = self.human()
        self.write([{}, {}, row])
        self.index()
        self.write([{}, row])
        self.assertEqual(self.search()[0]["citation"]["line"], 2)
        self.write([row, row])
        self.assertIsNone(self.search()[0]["citation"]["line"])

    def test_bad_mapping_aborts_without_mutating_existing_index(self):
        self.write([self.human()])
        self.index()
        self.config["cards"] = [{"source_path": str(self.source), "record_uuid": "unknown", "card_id": "T0262"}]
        with self.assertRaises(ValueError):
            self.index()
        self.assertEqual(len(self.search()), 1)

    def test_default_search_output_and_general_tables_are_unchanged(self):
        spec = importlib.util.spec_from_file_location("lookup_ruling_test", SKILL / "find_conversation.py")
        lookup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lookup)
        self.write([self.human()])
        lookup.cmd_index()
        with closing(sqlite3.connect(self.db)) as conn:
            before = conn.execute("SELECT * FROM messages").fetchall()
        command = [sys.executable, str(SKILL / "find_conversation.py"), "--search", "amber", "--no-refresh"]
        prior = subprocess.check_output(command, env=os.environ.copy())
        self.index()
        after = subprocess.check_output(command, env=os.environ.copy())
        self.assertEqual(prior, after)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM messages").fetchall(), before)

    def test_failure_during_replacement_rolls_back_own_tables(self):
        self.write([self.human()])
        self.index()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TRIGGER fail_ruling BEFORE INSERT ON rulings BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.index()
        self.assertEqual(len(self.search()), 1)

    def test_verification_budget_and_source_hash_mismatch_are_unknown(self):
        self.write([self.human()])
        self.index()
        with patch.object(rulings, "CITE_BYTES", 5):
            self.assertIsNone(self.search()[0]["citation"]["line"])
        self.write([self.human(text="amber edited")])
        self.assertIsNone(self.search()[0]["citation"]["line"])

    def test_injection_after_human_text_and_malformed_ids_are_rejected(self):
        self.write([
            self.human("valid"),
            self.human("mixed", "amber human text\n<system-reminder>injected"),
            self.human("blocks", [{"type": "text", "text": "amber human"},
                                  {"type": "text", "text": "<task-notification>injected"}]),
            {"type": "assistant", "uuid": [], "message": {"content": []}},
            {"type": "user", "sourceToolAssistantUUID": {}, "message": {"content": []}},
        ])
        self.index()
        self.assertEqual(len(self.search()), 1)

    def test_invalid_explicit_hash_is_not_a_wildcard(self):
        self.write([self.human()])
        self.index()
        for value in ("", False, 0, [], "not-sha256"):
            with self.subTest(value=value):
                self.config["cards"] = [{"source_path": str(self.source), "record_uuid": "ask",
                                         "card_id": "T0262", "source_hash": value}]
                with self.assertRaises(ValueError):
                    self.index()
                self.assertEqual(self.search()[0]["cards"], [])

    def test_form_resolution_rejects_duplicate_refs_and_marks_stale_question(self):
        question = {"question": "amber local?"}
        call = {"type": "assistant", "uuid": "question", "message": {"content": [
            {"type": "tool_use", "id": "c", "name": "AskUserQuestion", "input": {"questions": [question]}}]}}
        answer = {"type": "user", "uuid": "answer", "sourceToolAssistantUUID": "question",
                  "toolUseResult": {"questions": [question], "answers": {"amber local?": "yes"}},
                  "message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": "ignored"}]}}
        self.write([call, answer])
        self.index()
        self.assertTrue(self.search()[0]["resolution_verified"])
        self.write([{}, answer])
        hit, = self.search()
        self.assertFalse(hit["resolution_verified"])
        self.assertIsNone(hit["question_citation"]["line"])
        self.write([call, {"type": "assistant", "uuid": "question", "message": {"content": "duplicate"}}, answer])
        self.index()
        self.assertEqual(self.search(), [])

    def test_conflicting_cli_modes_or_missing_value_cannot_write(self):
        command = [sys.executable, str(SKILL / "find_conversation.py")]
        for args in (["--index-rulings", "--search-rulings", "amber"],
                     ["--search-rulings", "--index"], ["--index-rulings", "--ruling-config"],
                     ["--search-rulings", "amber", "--backfill-provenance"]):
            with self.subTest(args=args):
                out = subprocess.run(command + args, env=os.environ.copy(), capture_output=True, text=True)
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertFalse(self.db.exists())

    def test_cli_is_opt_in_and_search_does_not_create_an_index(self):
        self.assertIsNotNone(rulings)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        command = [sys.executable, str(SKILL / "find_conversation.py")]
        out = subprocess.run(command + ["--search-rulings", "amber"], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self.db.exists())
        self.assertIn("not indexed", out.stdout)
        self.write([self.human()])
        config = self.root / "config.json"
        config.write_text(json.dumps(self.config), encoding="utf-8")
        out = subprocess.run(command + ["--index-rulings", "--ruling-config", str(config)],
                             env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run(command + ["--search-rulings", "amber"], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("human-typed", out.stdout)
        self.assertIn("candidate", out.stdout)
        self.assertNotIn("auto-index", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
