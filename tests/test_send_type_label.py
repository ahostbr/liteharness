"""T279-A — `send` can label a message, and refuses a label it does not know.

A message's ``type`` was free-form in the schema and UNSETTABLE from the CLI, so
``inbox.send``'s default was written for every CLI send. The field existed,
typechecked, and carried no sender intent whatsoever.

MEASURED DOWNSTREAM, which is how it surfaced: LiteSuite's voice announcer tried
to pick an announcement kind from ``type`` and found that across 105 live
messages on 2026-09-06 the field held only "seat-message" (93), "notification"
(11) and "message" (1) — three labels, not one of them chosen by a sender. Three
messages sent minutes earlier as PROGRESS, ANSWER and RESULT were all stored as
"notification".

    A FIELD NOBODY CAN SET IS A FIELD NOBODY CAN BUILD ON, AND IT LOOKS
    IDENTICAL TO ONE THAT WORKS.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from liteharness import cli, inbox


def _patched_maildir(root: Path):
    """Point every maildir constant at a temp tree, as tests/test_inbox.py does."""
    return (
        mock.patch.object(inbox, "INBOX_ROOT", root / "inbox"),
        mock.patch.object(inbox, "INBOX_NEW", root / "inbox" / "new"),
        mock.patch.object(inbox, "INBOX_CUR", root / "inbox" / "cur"),
        mock.patch.object(inbox, "INBOX_DONE", root / "inbox" / "done"),
        mock.patch.object(inbox, "INBOX_TMP", root / "inbox" / "tmp"),
    )


class CanonicalMsgTypeTests(unittest.TestCase):
    def test_known_labels_round_trip_to_their_canonical_spelling(self):
        for label in inbox.MESSAGE_TYPES:
            self.assertEqual(inbox.canonical_msg_type(label), label)

    def test_matching_is_case_and_whitespace_insensitive(self):
        # Five labels are upper-case and the default is not; a caller should not
        # have to remember which is which, but the STORED value must be stable.
        self.assertEqual(inbox.canonical_msg_type("result"), "RESULT")
        self.assertEqual(inbox.canonical_msg_type("  Question "), "QUESTION")
        self.assertEqual(inbox.canonical_msg_type("NOTIFICATION"), "notification")

    def test_an_unknown_label_is_None_rather_than_passed_through(self):
        # The whole point of the closed set. "REUSLT" stored verbatim would be
        # read as an ordinary message forever, and the send would report success.
        self.assertIsNone(inbox.canonical_msg_type("REUSLT"))
        self.assertIsNone(inbox.canonical_msg_type(""))
        self.assertIsNone(inbox.canonical_msg_type("seat-message"))


class SendWritesTheLabelTests(unittest.TestCase):
    def _send(self, argv, root):
        patches = _patched_maildir(root)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            cli.config, "get_agent_id", lambda: "sender-1"
        ), mock.patch.object(cli.config, "get_cli", lambda: "claude-code"), mock.patch.object(
            cli.config, "get_model", lambda: "test"
        ):
            cli.main()
        written = list(inbox.INBOX_NEW.glob("*.json"))
        self.assertEqual(len(written), 1, "expected exactly one message on disk")
        return json.loads(written[0].read_text(encoding="utf-8"))

    def test_the_default_is_unchanged_when_no_type_is_given(self):
        # 🔴 THE COMPATIBILITY ARM. Every existing caller passes no --type, and
        # every one of them must keep writing exactly what it wrote before.
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._send(
                ["liteharness", "send", "recipient-1", "hello", "--from", "sender-1", "--force"],
                Path(tmp),
            )
            self.assertEqual(payload["type"], inbox.DEFAULT_MSG_TYPE)
            self.assertEqual(payload["type"], "notification")

    def test_a_given_label_reaches_the_message_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._send(
                [
                    "liteharness", "send", "recipient-1", "the gate is green",
                    "--from", "sender-1", "--type", "RESULT", "--force",
                ],
                Path(tmp),
            )
            self.assertEqual(payload["type"], "RESULT")
            self.assertEqual(payload["body"], "the gate is green")

    def test_a_lower_case_label_is_stored_canonically(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._send(
                [
                    "liteharness", "send", "recipient-1", "which way?",
                    "--from", "sender-1", "--type", "question", "--force",
                ],
                Path(tmp),
            )
            self.assertEqual(payload["type"], "QUESTION")

    def test_the_flag_is_not_swallowed_into_the_body(self):
        # This file's recurring defect: flags consumed by string-matching the
        # joined body destroyed the message and still reported success. --type is
        # consumed by ARGV POSITION like every other flag, so a body that merely
        # mentions it must survive intact.
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._send(
                [
                    "liteharness", "send", "recipient-1",
                    "pass --type RESULT to label it", "--from", "sender-1",
                    "--type", "PROGRESS", "--force",
                ],
                Path(tmp),
            )
            self.assertEqual(payload["type"], "PROGRESS")
            self.assertEqual(payload["body"], "pass --type RESULT to label it")

    def test_body_file_still_works_alongside_a_type(self):
        # --body-file exists because the shell edits an inline body silently.
        # Adding a flag must not disturb it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = root / "body.txt"
            body.write_text("a body with `backticks` and $VARS", encoding="utf-8")
            payload = self._send(
                [
                    "liteharness", "send", "recipient-1", "--body-file", str(body),
                    "--from", "sender-1", "--type", "ANSWER", "--force",
                ],
                root,
            )
            self.assertEqual(payload["type"], "ANSWER")
            self.assertEqual(payload["body"], "a body with `backticks` and $VARS")


class SendRefusesAnUnknownLabelTests(unittest.TestCase):
    def test_an_unknown_type_sends_nothing_and_exits_nonzero(self):
        # 🔴 REFUSED, NOT CORRECTED, and nothing written. A typo'd label accepted
        # silently is the same class of defect as the emptied body this file
        # already guards twice: success is reported and the meaning is gone.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patches = _patched_maildir(root)
            for p in patches:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in patches])
            inbox.ensure_dirs()

            err = io.StringIO()
            argv = [
                "liteharness", "send", "recipient-1", "hello",
                "--from", "sender-1", "--type", "REUSLT", "--force",
            ]
            with mock.patch.object(sys, "argv", argv), redirect_stderr(err):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()

            self.assertNotEqual(raised.exception.code, 0)
            self.assertIn("unknown --type", err.getvalue())
            self.assertIn("RESULT", err.getvalue(), "the error must list the known labels")
            self.assertEqual(
                list(inbox.INBOX_NEW.glob("*.json")), [], "a refused send still wrote a message"
            )


if __name__ == "__main__":
    unittest.main()
