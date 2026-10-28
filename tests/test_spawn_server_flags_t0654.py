"""T0654: command plumbing only; no process, server, model or real bridge."""
import subprocess

import pytest

from liteharness import cli


@pytest.fixture(autouse=True)
def no_launch(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("test attempted a real launch or bridge call")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(cli, "_bridge_request", forbidden)
    monkeypatch.setattr(cli, "_place_split", forbidden)
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)


@pytest.mark.parametrize("route", ["--split", "--pty"])
@pytest.mark.parametrize("options", [
    [],
    ["--start-server"],
    ["--model-path", r"C:\Models With Spaces\artifact.json"],
    ["--start-server", "--model-path", r"D:\Models With Spaces\artifact.json"],
    ["--start-server", "--model-path", "E:/models/../Models With Spaces/artifact.json"],
])
def test_fresh_ninfer_built_command(monkeypatch, tmp_path, route, options):
    launches = []

    def capture(launch):
        launches.append(launch)
        return {"error": "grid_full"}  # stop at the mocked launch boundary

    def bridge(method, path, body):
        if (method, path) == ("POST", "/harness/spawn/resolve"):
            assert body["backend"] == "ninfer"
            return {"ok": True, "agentId": "11111111-1111-4111-8111-111111111111",
                    "liteTuiDataRoot": str(tmp_path), "request": {
                        "shell": "litetui", "args": [], "env": {},
                        "harnessAgentId": "11111111-1111-4111-8111-111111111111"}}
        assert (method, path) == ("POST", "/pty/create")
        return capture(body)

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    monkeypatch.setattr(cli, "_place_split", lambda *a, **kw: capture(kw["launch"]))
    kwargs = cli._parse_spawn_args([
        "--cli", "litetui", route, "--name", "ServerFlagSeat", "--tier", "builder",
        "--cwd", str(tmp_path), "--spawned-by", "test-caller", "--backend", "ninfer",
        "--model", "fixture-model", "--thinking-level", "high", *options,
    ])
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(**kwargs)
    assert exc.value.code == 2  # mocked launch refusal, never an actual child
    assert len(launches) == 1
    assert launches[0]["shell"] == "litetui"
    args = launches[0]["args"]
    assert args[args.index("--backend") + 1] == "ninfer"
    assert args.count("--start-server") == options.count("--start-server")
    assert args.count("--model-path") == options.count("--model-path")
    if "--model-path" in options:
        assert args[args.index("--model-path") + 1] == options[options.index("--model-path") + 1]


@pytest.mark.parametrize("spawn_cli", ["claude", "codex"])
@pytest.mark.parametrize("option", [{"start_server": True}, {"model_path": "C:/models/artifact.json"}])
def test_server_flags_refuse_other_clis(capsys, spawn_cli, option):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(spawn_cli=spawn_cli, **option)
    assert exc.value.code == 2
    text = capsys.readouterr().out
    assert "--start-server / --model-path require a fresh --cli litetui spawn" in text
    assert "Nothing was spawned" in text


@pytest.mark.parametrize("model_path", [
    r"F:\models\artifact.json", "f:/models/artifact.json", "F:artifact.json",
    r"\\?\F:\models\artifact.json", r"\\.\f:\models\artifact.json",
])
def test_storage_drive_refused_before_resolution(capsys, model_path):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(spawn_cli="litetui", backend="ninfer", start_server=True,
                      model_path=model_path)
    assert exc.value.code == 2
    text = capsys.readouterr().out
    assert "--model-path" in text and "F: is storage, never a load source" in text
    assert "Nothing was spawned" in text


@pytest.mark.parametrize("resume", [{"resume_agent_id": "Seat"}, {"resume_convo_id": "convo"}])
@pytest.mark.parametrize("option", [{"start_server": True}, {"model_path": "C:/models/artifact.json"}])
def test_server_flags_do_not_silently_disappear_on_resume(capsys, resume, option):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_spawn(spawn_cli="litetui", **resume, **option)
    assert exc.value.code == 2
    assert "require a fresh --cli litetui spawn" in capsys.readouterr().out


def test_help_documents_fresh_server_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        cli._parse_spawn_args(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "--start-server" in text
    assert "--model-path VALUE" in text
    assert "F: is storage" in text
    backend_help = next(line for line in text.splitlines() if line.strip().startswith("--backend "))
    assert "fresh" in backend_help and "resume" in backend_help
