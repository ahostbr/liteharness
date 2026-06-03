import tempfile
import threading
import time
import unittest
from pathlib import Path

from liteharness import copilot_waiter, inbox
from liteharness.hooks import _MtimeWatcher


class CopilotWaiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_paths = (
            inbox.INBOX_ROOT,
            inbox.INBOX_NEW,
            inbox.INBOX_CUR,
            inbox.INBOX_DONE,
            inbox.INBOX_TMP,
        )
        self.original_watcher_factory = copilot_waiter._create_watcher

        inbox.INBOX_ROOT = self.root / "inbox"
        inbox.INBOX_NEW = inbox.INBOX_ROOT / "new"
        inbox.INBOX_CUR = inbox.INBOX_ROOT / "cur"
        inbox.INBOX_DONE = inbox.INBOX_ROOT / "done"
        inbox.INBOX_TMP = inbox.INBOX_ROOT / "tmp"
        inbox.ensure_dirs()

        copilot_waiter._create_watcher = lambda path: _MtimeWatcher(path)

    def tearDown(self) -> None:
        (
            inbox.INBOX_ROOT,
            inbox.INBOX_NEW,
            inbox.INBOX_CUR,
            inbox.INBOX_DONE,
            inbox.INBOX_TMP,
        ) = self.original_paths
        copilot_waiter._create_watcher = self.original_watcher_factory
        self.temp_dir.cleanup()

    def test_waiter_claims_existing_message_and_formats_reply(self) -> None:
        agent_id = "145fe08e-ae94-4df3-88b1-cc38f4cc2046"
        sender_id = "fa88c542-d2ef-41ef-8a8c-370f4b246666"

        inbox.send(sender_id, agent_id, "hello from sentinel", cli="claude-code")

        message = copilot_waiter.wait_for_next_message(agent_id, timeout=0.2)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message["from"], sender_id)

        summary = copilot_waiter.format_message_summary(message, agent_id)
        self.assertIn("hello from sentinel", summary)
        self.assertIn(f"python -m liteharness.cli send {sender_id}", summary)

        completed = inbox.complete(message, result="unit test")
        self.assertTrue(completed)
        self.assertEqual(len(list(inbox.INBOX_DONE.glob("*.json"))), 1)

    def test_waiter_blocks_until_new_message_arrives(self) -> None:
        agent_id = "145fe08e-ae94-4df3-88b1-cc38f4cc2046"
        sender_id = "fa88c542-d2ef-41ef-8a8c-370f4b246666"

        def delayed_send() -> None:
            time.sleep(0.2)
            inbox.send(sender_id, agent_id, "delayed hello", cli="claude-code")

        sender_thread = threading.Thread(target=delayed_send, daemon=True)
        sender_thread.start()
        message = copilot_waiter.wait_for_next_message(agent_id, timeout=2.0)
        sender_thread.join(timeout=1.0)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message["body"], "delayed hello")

    def test_waiter_times_out_when_no_message_arrives(self) -> None:
        agent_id = "145fe08e-ae94-4df3-88b1-cc38f4cc2046"

        start = time.monotonic()
        message = copilot_waiter.wait_for_next_message(agent_id, timeout=0.3)
        elapsed = time.monotonic() - start

        self.assertIsNone(message)
        self.assertGreaterEqual(elapsed, 0.25)


if __name__ == "__main__":
    unittest.main()
