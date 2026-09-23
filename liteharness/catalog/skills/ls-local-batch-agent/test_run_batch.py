"""Focused contract tests; no model is loaded and no LiteTUI child is spawned."""

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("run_batch", Path(__file__).with_name("run_batch.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BatchTests(unittest.TestCase):
    def test_headless_child_has_no_tools_and_never_loads(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("".join(json.dumps(event) + "\n" for event in [
                    {"type": "ready", "launch_status": "ready", "backend": "lmstudio", "model": "resident",
                     "base_url": "http://127.0.0.1:1234/v1"},
                    {"type": "response", "id": "batch-item", "ok": True},
                    {"type": "text_delta", "text": '{"state":"HELD"}'},
                    {"type": "turn_end", "stopReason": "stop"},
                ]))

            def poll(self):
                return None

            def wait(self, timeout):
                return 0

        observed = {}

        def fake_popen(argv, **kwargs):
            observed["argv"] = argv
            observed["settings"] = json.loads((Path(kwargs["env"]["LITETUI_DATA_ROOT"]) / "settings.json").read_text())
            observed["process"] = FakeProcess()
            return observed["process"]

        with tempfile.TemporaryDirectory() as scratch, patch.object(module.subprocess, "Popen", side_effect=fake_popen):
            answer = module.run_litetui("litetui", "resident", "Read only.", "Facts about branch",
                                        Path(scratch), 24000, 5)
        self.assertEqual(answer, '{"state":"HELD"}')
        self.assertFalse(observed["settings"]["tools_enabled"])
        self.assertEqual(observed["argv"][observed["argv"].index("--server-mode") + 1], "connect")
        self.assertNotIn("--load-model", observed["argv"])
        self.assertNotIn("--prompt", observed["argv"])
        self.assertIn('"type": "prompt"', observed["process"].stdin.getvalue())

    def test_read_only_git_command_validation(self):
        repo = Path("C:/repo")
        argv = module.evidence_argv(["git", "-C", "{repo}", "diff", "develop...{item}"], repo, "feat/a")
        self.assertEqual(argv[3:6], ["diff", "--no-ext-diff", "--no-textconv"])
        with self.assertRaises(ValueError):
            module.evidence_argv(["git", "-C", "{repo}", "reset", "--hard"], repo, "feat/a")
        with self.assertRaises(ValueError):
            module.evidence_argv(["git", "-C", "{repo}", "worktree", "remove", "x"], repo, "feat/a")
        with self.assertRaises(ValueError):
            module.evidence_argv(["git", "-C", "{repo}", "diff", "--ext-diff"], repo, "feat/a")
        with self.assertRaises(ValueError):
            module.evidence_argv(["git", "-C", "{repo}", "branch", "-D", "{item}"], repo, "feat/a")
        self.assertEqual(module.evidence_argv(["git", "-C", "{repo}", "branch", "--merged", "develop"], repo, "feat/a")[3], "branch")

    def test_output_requires_complete_json(self):
        self.assertEqual(module.parse_answer('{"state":"HELD","reason":"waiting"}', ["state", "reason"]),
                         {"state": "HELD", "reason": "waiting"})
        for answer in ("", "the answer is HELD", '{"state":"HELD"}'):
            with self.assertRaises(ValueError):
                module.parse_answer(answer, ["state", "reason"])

    def test_no_resident_model_means_no_child_or_output(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            preamble = root / "holmes.md"
            preamble.write_text("Read evidence carefully.", encoding="utf-8")
            manifest = root / "job.json"
            manifest.write_text(json.dumps({
                "repo": str(root), "preamble": str(preamble), "task": "judge", "output_schema": "{}",
                "required_keys": ["state"], "items": [{"id": "branch"}],
                "evidence_argv": [["git", "-C", "{repo}", "log", "-1", "{item}"]],
            }), encoding="utf-8")
            output = root / "result.json"
            with patch.object(module, "resident_models", return_value=[]), patch.object(module, "run_litetui") as child:
                with self.assertRaisesRegex(RuntimeError, "No LM Studio model is resident"):
                    module.run(manifest, output, None, "litetui", 1)
                child.assert_not_called()
            self.assertFalse(output.exists())

    def test_batch_uses_isolated_child_and_writes_only_after_validation(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            preamble = root / "holmes.md"
            preamble.write_text("Method: Holmes", encoding="utf-8")
            manifest = root / "job.json"
            manifest.write_text(json.dumps({
                "repo": str(root), "preamble": str(preamble), "task": "judge", "output_schema": "{state:string}",
                "required_keys": ["state"], "items": [{"id": "branch"}],
                "evidence_argv": [["git", "-C", "{repo}", "log", "-1", "{item}"]],
            }), encoding="utf-8")
            output = root / "result.json"
            with patch.object(module, "resident_models", return_value=["resident"]), \
                 patch.object(module, "collect_evidence", return_value="facts"), \
                 patch.object(module, "run_litetui", return_value='{"state":"HELD"}') as child:
                module.run(manifest, output, "resident", "litetui", 1)
                self.assertIn("READ-ONLY", child.call_args.args[2])
                self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["results"][0]["result"]["state"], "HELD")


if __name__ == "__main__":
    unittest.main()
