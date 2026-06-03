import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liteharness import inbox


class InboxSendTests(unittest.TestCase):
    def test_send_preserves_optional_surface_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(inbox, "INBOX_ROOT", root / "inbox"), mock.patch.object(
                inbox, "INBOX_NEW", root / "inbox" / "new"
            ), mock.patch.object(inbox, "INBOX_CUR", root / "inbox" / "cur"), mock.patch.object(
                inbox, "INBOX_DONE", root / "inbox" / "done"
            ), mock.patch.object(
                inbox, "INBOX_TMP", root / "inbox" / "tmp"
            ):
                inbox.send(
                    "codex-123",
                    "sentinel",
                    "hello",
                    cli="codex-desktop",
                    surface="desktop",
                    originator="Codex Desktop",
                )

                messages = list(inbox.INBOX_NEW.glob("*.json"))
                self.assertEqual(len(messages), 1)
                payload = json.loads(messages[0].read_text(encoding="utf-8"))
                self.assertEqual(payload["cli"], "codex-desktop")
                self.assertEqual(payload["surface"], "desktop")
                self.assertEqual(payload["originator"], "Codex Desktop")


if __name__ == "__main__":
    unittest.main()
