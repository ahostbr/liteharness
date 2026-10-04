"""T0236-T4: workers and leaders are spawned in an explicit --cwd; never the caller's cwd."""
import pytest

from liteharness import cli, fleet_policy


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "caller-1")
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path / "h")

    def fake(method, path, body=None):
        calls.append((method, path, body))
        return {"ok": False, "error": "stop-here"}

    monkeypatch.setattr(cli, "_bridge_request", fake)
    monkeypatch.setattr(fleet_policy, "gate", lambda backend, model, thinking: None)
    return calls


def _spawn(**kw):
    base = dict(spawn_cli="litetui", name="Seat", split_mode=True, spawned_by="caller-1",
                backend="codex", model="gpt-6-sol", thinking_level="high")
    base.update(kw)
    try:
        cli.cmd_spawn(**base)
    except SystemExit as exc:
        return exc.code
    return None


@pytest.mark.parametrize("tier", ["worker", "leader"])
def test_worker_and_leader_without_cwd_are_refused(tier, bridge, capsys):
    assert _spawn(tier=tier) == 2
    out = capsys.readouterr().out
    assert "SPAWN REFUSED" in out and "--cwd" in out
    assert bridge == [], "nothing may reach the bridge before the refusal"


def test_claude_default_tier_without_cwd_is_refused(bridge, capsys):
    assert _spawn(spawn_cli="claude", tier=None, split_mode=False, name=None, spawned_by=None,
                  backend=None, model=None, thinking_level=None) == 2
    assert "--cwd" in capsys.readouterr().out


@pytest.mark.parametrize("tier", ["worker", "leader"])
def test_explicit_cwd_passes_the_gate(tier, bridge, tmp_path, capsys):
    _spawn(tier=tier, cwd=str(tmp_path))
    assert "--cwd" not in capsys.readouterr().out
    assert bridge and bridge[0][1] == "/harness/spawn/resolve"
    assert bridge[0][2]["cwd"] == str(tmp_path.resolve())


@pytest.mark.parametrize("tier", ["orchestrator", "thinker", "reviewer"])
def test_other_tiers_are_exempt(tier, bridge, capsys):
    _spawn(tier=tier)
    assert "--cwd" not in capsys.readouterr().out
