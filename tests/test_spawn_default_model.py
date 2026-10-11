"""T0471: a Claude spawn that names no --model must not silently boot settings.json's model.

Claude Code starts the model in the user's settings.json when `claude` is launched without
--model (and xhigh effort without --effort), which is the most expensive pair. The harness is
shipped, so it names no model of its own: the operator sets spawn_default_model / spawn_default_effort
in the harness config (or the LITEHARNESS_SPAWN_MODEL / LITEHARNESS_SPAWN_EFFORT variables)
and every Claude launch route passes them.
"""
import pytest

from liteharness import cli, config


@pytest.fixture
def cfg(monkeypatch):
    for var in ("LITEHARNESS_SPAWN_MODEL", "LITEHARNESS_SPAWN_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    store: dict = {}
    monkeypatch.setattr(config, "load", lambda: dict(store))
    return store


def _cmd(**kw) -> str:
    return cli._build_claude_cmd(**kw)[0]


def test_configured_default_model_is_passed_when_none_is_named(cfg):
    cfg["spawn_default_model"] = "sonnet"
    assert "--model claude-sonnet-5-5 " in _cmd()


def test_configured_default_effort_is_passed(cfg):
    cfg["spawn_default_model"] = "claude-sonnet-5-5[1m]"
    cfg["spawn_default_effort"] = "high"
    cmd = _cmd()
    assert "--model claude-sonnet-5-5[1m] " in cmd and "--effort high " in cmd


def test_an_explicit_model_beats_the_default(cfg):
    cfg["spawn_default_model"] = "sonnet"
    cmd = _cmd(model="opus")
    assert "--model claude-opus-5-5[1m] " in cmd and "claude-sonnet" not in cmd


def test_an_explicit_effort_in_args_beats_the_default(cfg):
    cfg["spawn_default_effort"] = "high"
    cmd = _cmd(additional_args="--effort max")
    assert cmd.count("--effort") == 1 and "--effort max" in cmd


def test_environment_overrides_config(cfg, monkeypatch):
    cfg["spawn_default_model"] = "sonnet"
    monkeypatch.setenv("LITEHARNESS_SPAWN_MODEL", "haiku")
    assert "--model claude-haiku" in _cmd()


def test_nothing_configured_names_no_model_and_says_so(cfg, capsys):
    cmd = _cmd()
    assert cmd.startswith("claude --permission-mode ") and "--effort" not in cmd
    assert "no --model" in capsys.readouterr().out


def test_effort_default_reaches_the_other_launch_routes_args(cfg):
    # cmd_spawn's split, canvas and pty routes assemble their own claude line from additional_args.
    assert cli._with_default_effort(None, "high") == "--effort high"
    assert cli._with_default_effort("--resume abc", "high") == "--effort high --resume abc"
    assert cli._with_default_effort("--effort max", "high") == "--effort max"
    assert cli._with_default_effort("--resume abc", None) == "--resume abc"


def test_a_bad_default_effort_is_refused_not_passed_through(cfg):
    cfg["spawn_default_effort"] = "high; rm -rf"
    with pytest.raises(SystemExit):
        _cmd()


def test_a_bad_default_model_is_refused(cfg):
    cfg["spawn_default_model"] = "not a model"
    with pytest.raises(SystemExit):
        _cmd()


# ── cmd_spawn route wiring (critic finding A): the default must reach each launch line ──────

MINTED = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def routes(monkeypatch, tmp_path, cfg):
    """cmd_spawn with every launcher stubbed. Returns the captured launch lines per route."""
    cfg["spawn_default_model"] = "claude-sonnet-5-5[1m]"
    cfg["spawn_default_effort"] = "high"
    seen: dict[str, str] = {}

    def bridge(method, path, body=None):
        body = body or {}
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if path == "/canvas/split":
            return {"ok": True, "newLeafId": "leaf-1", "newSessionId": MINTED}
        if path == "/pty/write":
            seen["split"] = body["data"]
        if path == "/pty/create":
            seen["canvas"] = body["shell"]
            return {"error": "stop-here"}  # no session_id: cmd_spawn exits before delivery
        return {"ok": True}

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    clock = iter(range(0, 10_000_000))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_write_spawn_brief", lambda _b: tmp_path / "brief.txt")
    monkeypatch.setattr(cli, "is_inside_litesuite", lambda: True)
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "leader-under-test")
    (tmp_path / "agents").mkdir()

    from liteharness import pty_daemon
    monkeypatch.setattr(pty_daemon, "ensure_daemon", lambda: True)

    def daemon(cmd):
        seen["pty"] = cmd["cli_cmd"]
        return {"ok": False, "error": "stop-here"}

    monkeypatch.setattr(pty_daemon, "send_command", daemon)
    return seen, tmp_path


def _spawn_to_the_launcher(tmp_path, **kw):
    base = dict(cwd=str(tmp_path), tier="thinker", name="Probe")
    base.update(kw)
    try:
        cli.cmd_spawn(**base)
    except SystemExit:
        pass


def _assert_defaults(line: str):
    assert "--model claude-sonnet-5-5[1m] " in line and "--effort high" in line, line


def test_split_route_carries_the_default_model_and_effort(routes):
    seen, tmp_path = routes
    _spawn_to_the_launcher(tmp_path, split_mode=True)
    _assert_defaults(seen["split"])


def test_canvas_route_carries_the_default_model_and_effort(routes):
    seen, tmp_path = routes
    _spawn_to_the_launcher(tmp_path)
    _assert_defaults(seen["canvas"])


def test_pty_route_carries_the_default_model_and_effort(routes):
    seen, tmp_path = routes
    _spawn_to_the_launcher(tmp_path, pty_mode=True)
    _assert_defaults(seen["pty"])


def test_a_resume_line_keeps_resume_behind_the_default_effort(routes):
    seen, tmp_path = routes
    _spawn_to_the_launcher(tmp_path, split_mode=True, additional_args=f"--resume {MINTED}")
    assert f"--effort high --resume {MINTED}" in seen["split"], seen["split"]


def test_an_exec_pty_spawn_is_left_alone_by_the_defaults(routes, cfg, capsys):
    seen, tmp_path = routes
    cfg["spawn_default_model"] = "not a model"  # would be refused on a claude launch
    cfg["spawn_default_effort"] = "high"
    _spawn_to_the_launcher(tmp_path, pty_mode=True, exec_cmd="python -c pass")
    assert seen["pty"] == "python -c pass"
    assert "Note: no --model" not in capsys.readouterr().out


def test_a_trailing_newline_in_the_default_model_is_stripped_not_passed(cfg):
    cfg["spawn_default_model"] = "claude-sonnet-5-5\n"
    assert "--model claude-sonnet-5-5 " in _cmd()


def test_a_non_string_default_is_refused_cleanly(cfg):
    cfg["spawn_default_model"] = 5
    with pytest.raises(SystemExit):
        _cmd()


def test_an_explicit_model_with_a_trailing_newline_is_refused(cfg):
    with pytest.raises(SystemExit):
        _cmd(model="claude-sonnet-5-5\n")
