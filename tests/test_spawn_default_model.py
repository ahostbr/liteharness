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
