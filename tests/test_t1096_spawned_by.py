"""A CLI-registered LiteTUI seat keeps its explicit spawner on heartbeat/discover."""
import json
import sys

from liteharness import cli, config


def test_explicit_parent_persists_and_discover_reports_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "get_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["liteharness", "register", "--agent-id", "child-1",
                                  "--cli", "litetui", "--name", "Child", "--spawned-by", "leader-1"])
    cli.main()
    path = tmp_path / "agents" / "child-1.json"
    assert json.loads(path.read_text(encoding="utf-8"))["spawned_by"] == "leader-1"
    cli.cmd_register("child-1", cli="litetui", name="Child")
    assert json.loads(path.read_text(encoding="utf-8"))["spawned_by"] == "leader-1"
    cli.cmd_discover()
    assert "spawned by leader-1" in capsys.readouterr().out


def test_unmarked_register_does_not_infer_parent_from_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_root", lambda: tmp_path)
    monkeypatch.setenv("LITEHARNESS_SPAWNED_BY", "stale-leader")
    cli.cmd_register("child-2", cli="litetui")
    row = json.loads((tmp_path / "agents" / "child-2.json").read_text(encoding="utf-8"))
    assert "spawned_by" not in row
