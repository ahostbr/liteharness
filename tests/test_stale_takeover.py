"""T512 — a stale takeover record must not resurrect a dead predecessor id.

After /clear the predecessor's takeover record survives while the successor
registers under its own id on the same pid. The PostCompact hook then finds
the stale record via _authoritative_owner_of_pid and would adopt it,
orphaning the live watcher and maildir.

Arms:
1. Stale takeover + newer live session on same pid → register keeps the newer id
2. Control: takeover with no newer session → adoption proceeds
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def registry(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    with patch("liteharness.config.get_root", return_value=tmp_path):
        yield agents_dir


def _write_presence(agents_dir: Path, agent_id: str, **kwargs):
    data = {"session_pid": 12345, "cli": "claude-code", **kwargs}
    (agents_dir / f"{agent_id}.json").write_text(json.dumps(data), encoding="utf-8")


class TestStaleTakeoverGuard:
    def test_stale_takeover_does_not_replace_newer_session(self, registry: Path):
        from liteharness.hooks import _current_session_is_newer

        # Old predecessor with a takeover record
        _write_presence(
            registry,
            "dead-predecessor",
            registered_at="2026-09-08T10:00:00Z",
            registration_source="takeover",
        )
        # Newer live session on the same pid
        _write_presence(
            registry,
            "live-successor",
            registered_at="2026-09-08T11:00:00Z",
        )

        assert _current_session_is_newer("live-successor", "dead-predecessor", 12345)

    def test_no_newer_session_allows_adoption(self, registry: Path):
        from liteharness.hooks import _current_session_is_newer

        # Takeover record
        _write_presence(
            registry,
            "takeover-owner",
            registered_at="2026-09-08T11:00:00Z",
            registration_source="takeover",
        )
        # Older session
        _write_presence(
            registry,
            "old-session",
            registered_at="2026-09-08T10:00:00Z",
        )

        assert not _current_session_is_newer("old-session", "takeover-owner", 12345)

    def test_exited_session_does_not_block_adoption(self, registry: Path):
        from liteharness.hooks import _current_session_is_newer

        _write_presence(
            registry,
            "takeover-owner",
            registered_at="2026-09-08T10:00:00Z",
            registration_source="takeover",
        )
        _write_presence(
            registry,
            "exited-session",
            registered_at="2026-09-08T11:00:00Z",
            exited_at="2026-09-08T11:30:00Z",
        )

        assert not _current_session_is_newer("exited-session", "takeover-owner", 12345)

    def test_different_pid_does_not_block_adoption(self, registry: Path):
        from liteharness.hooks import _current_session_is_newer

        _write_presence(
            registry,
            "takeover-owner",
            registered_at="2026-09-08T10:00:00Z",
            registration_source="takeover",
        )
        _write_presence(
            registry,
            "other-pid-session",
            registered_at="2026-09-08T11:00:00Z",
            session_pid=99999,
        )

        assert not _current_session_is_newer("other-pid-session", "takeover-owner", 12345)
