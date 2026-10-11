"""The status line's field order, bar text, env precedence and never-raise contract."""
from __future__ import annotations

import io
import json

import pytest

from liteharness import naming, statusline

PAYLOAD_ID = "11111111-aaaa-bbbb-cccc-000000000000"
ENV_ID = "22222222-aaaa-bbbb-cccc-000000000000"
PAYLOAD = {
    "session_id": PAYLOAD_ID,
    "model": {"display_name": "Claude Opus 5 (1M context)"},
    "context_window": {"used_percentage": 70, "context_window_size": 1_000_000},
    "effort": {"level": "high"},
}
NO_ID = {k: v for k, v in PAYLOAD.items() if k != "session_id"}
FULL = "70% 1M Alpha 11111111 O-5-1M high widgets"


@pytest.fixture(autouse=True)
def fixed_world(monkeypatch, tmp_path):
    monkeypatch.setattr(statusline, "NO_COLOR", True)
    monkeypatch.setattr(statusline, "git_branch", lambda cwd: "widgets")
    monkeypatch.setattr(naming, "get_name", lambda sid: "Alpha")
    # An empty home, so the mode file of whoever runs the suite never leaks in.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for var in ("LITEHARNESS_AGENT_ID", "LITEHARNESS_ROLE", "LITEHARNESS_STATUSLINE_MODE"):
        monkeypatch.delenv(var, raising=False)


def _mode_file(tmp_path, text):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "statusline-mode").write_text(text, encoding="utf-8")


def test_full_mode_order():
    assert statusline.build_line(PAYLOAD, "full") == FULL


def test_bar_prints_percent_and_window_never_used_tokens():
    line = statusline.build_line(PAYLOAD, "full")
    assert line.startswith("70% 1M ")
    assert "700K" not in line


def test_compact_and_minimal_orders():
    assert statusline.build_line(PAYLOAD, "compact") == "Alpha 11111111 70% 1M high widgets"
    assert statusline.build_line(PAYLOAD, "minimal") == "70% 1M high widgets"


def test_payload_id_beats_env_id(monkeypatch):
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", ENV_ID)
    assert statusline.build_line(PAYLOAD, "full") == FULL


def test_env_id_is_the_fallback_when_the_payload_has_none(monkeypatch):
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", ENV_ID)
    assert statusline.build_line(NO_ID, "full") == "70% 1M 22222222 O-5-1M high widgets"


def test_env_id_is_stripped_and_an_underscored_id_shows_its_last_segment(monkeypatch):
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", f"  {ENV_ID}  ")
    assert statusline.build_line(NO_ID, "full") == "70% 1M 22222222 O-5-1M high widgets"
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "seat_x_abcd1234efgh")
    assert statusline.build_line(NO_ID, "full") == "70% 1M abcd1234 O-5-1M high widgets"


def test_the_name_comes_from_the_payload_id_even_when_the_env_id_differs(monkeypatch):
    seen = []
    monkeypatch.setattr(naming, "get_name", lambda sid: seen.append(sid) or "Alpha")
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", ENV_ID)
    assert statusline.build_line(PAYLOAD, "full") == FULL
    assert seen == [PAYLOAD_ID]


def test_role_comes_from_the_harness_env(monkeypatch):
    monkeypatch.setenv("LITEHARNESS_ROLE", "builder")
    assert statusline.build_line(PAYLOAD, "full") == (
        "70% 1M Alpha 11111111 O-5-1M high BUILDER widgets"
    )


def test_mode_comes_from_the_harness_env(monkeypatch):
    monkeypatch.setenv("LITEHARNESS_STATUSLINE_MODE", " Minimal ")
    assert statusline.display_mode() == "minimal"
    assert statusline.build_line(PAYLOAD) == "70% 1M high widgets"


def test_mode_file_fallback(monkeypatch, tmp_path):
    assert statusline.display_mode() == "full"
    _mode_file(tmp_path, "Minimal\n")
    assert statusline.display_mode() == "minimal"
    assert statusline.build_line(PAYLOAD) == "70% 1M high widgets"
    monkeypatch.setenv("LITEHARNESS_STATUSLINE_MODE", "compact")
    assert statusline.display_mode() == "compact"


def test_an_unknown_mode_word_renders_the_full_line(tmp_path):
    _mode_file(tmp_path, "sideways")
    assert statusline.build_line(PAYLOAD) == FULL


def test_a_failing_name_lookup_degrades_to_the_bare_id(monkeypatch):
    def boom(sid):
        raise OSError("names directory unreadable")

    monkeypatch.setattr(naming, "get_name", boom)
    assert statusline.build_line(PAYLOAD, "full") == "70% 1M 11111111 O-5-1M high widgets"


@pytest.mark.parametrize("raw", ["", "{bad", "[]", "null"])
def test_main_prints_one_line_and_returns(monkeypatch, capsys, raw):
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    statusline.main()
    out = capsys.readouterr().out
    assert out.endswith("\n") and out.count("\n") == 1


def test_main_prints_one_blank_line_when_rendering_raises(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"model": "x"}'))
    statusline.main()
    assert capsys.readouterr().out == "\n"


def test_main_renders_a_real_payload(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(PAYLOAD)))
    statusline.main()
    assert capsys.readouterr().out == FULL + "\n"
