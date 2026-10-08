"""Removed close protocol must not dispatch; ordinary help remains available."""
import sys

import pytest

from liteharness import cli


@pytest.mark.parametrize("module", ["retirement", "retirement_cli", "retirement_handoff", "retirement_leader"])
def test_removed_modules_are_not_distributed(module):
    import importlib.util
    assert importlib.util.find_spec("liteharness." + module) is None


@pytest.mark.parametrize("command", ["retire", "ack-idle"])
def test_removed_close_commands_never_reach_bridge(monkeypatch, capsys, command):
    monkeypatch.setattr(sys, "argv", ["liteharness", command])
    monkeypatch.setattr(cli, "_bridge_request", lambda *a, **kw: pytest.fail("bridge called"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code != 0
    assert "Unknown command" in capsys.readouterr().out


def test_help_does_not_offer_removed_protocol(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["liteharness", "--help"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "discover" in output
    assert "ack-idle" not in output
    assert "retire <agent-id>" not in output
