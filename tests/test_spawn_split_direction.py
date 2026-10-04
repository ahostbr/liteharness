"""T906 — `spawn --split` stops forcing a vertical split.

the user 2026-09-25: "we need the system to be smart enough to spawn terminals where
they should be." LiteSuite puts a split that names no placement in the pane's
next standard-grid slot, so the CLI must stop naming one unless asked.
"""
import inspect

from liteharness import cli


def test_no_direction_leaves_the_key_out():
    body = cli._split_request_body(None, "agent-1", None)
    assert body == {"paneId": "self", "agentId": "agent-1"}
    assert "direction" not in body


def test_explicit_direction_is_still_sent():
    assert cli._split_request_body("pane-7", "agent-1", "horizontal") == {
        "paneId": "pane-7",
        "agentId": "agent-1",
        "direction": "horizontal",
    }


def test_cmd_spawn_defaults_to_no_direction():
    # CONTROL for the arm above: the default is where "vertical" used to live.
    default = inspect.signature(cli.cmd_spawn).parameters["split_direction"].default
    assert default is None


def test_the_argv_default_is_none_too():
    # The `spawn` argv parser must not seed a direction of its own; a
    # "vertical" there would reach cmd_spawn and bypass the signature.
    assert "split_direction" not in cli._parse_spawn_args(["--split"])
    assert cli._parse_spawn_args(["--direction", "horizontal"])["split_direction"] == "horizontal"
