"""T515 — _wt_segment includes --title and --suppressApplicationTitle.

The Windows Terminal tab title must be the seat's name for life, not
the auto-summary Claude Code writes. --suppressApplicationTitle prevents
the application (claude.exe) from overriding the tab title set by --title.
"""

from liteharness.session_manager import _wt_segment, _terminal_title


class TestWtSegmentTitleFlags:
    def test_segment_contains_title_and_suppress(self):
        session = {
            "session_id": "abc12345-dead-beef-1234-567890abcdef",
            "cli": "claude",
            "cwd": "C:/Projects",
            "name": "TestSeat",
        }
        segment = _wt_segment(session)
        assert "--title" in segment
        assert "--suppressApplicationTitle" in segment
        title_idx = segment.index("--title")
        assert title_idx + 1 < len(segment)
        title_val = segment[title_idx + 1]
        assert "TestSeat" in title_val or "abc12345" in title_val

    def test_title_contains_name_and_id(self):
        session = {
            "session_id": "abc12345-dead-beef-1234-567890abcdef",
            "cli": "claude",
            "cwd": "C:/Projects",
            "name": "OpenBolt",
        }
        title = _terminal_title(session)
        assert "OpenBolt" in title

    def test_suppress_comes_after_title(self):
        session = {
            "session_id": "abc12345-dead-beef-1234-567890abcdef",
            "cli": "claude",
            "cwd": "C:/Projects",
        }
        segment = _wt_segment(session)
        title_idx = segment.index("--title")
        suppress_idx = segment.index("--suppressApplicationTitle")
        assert suppress_idx > title_idx
