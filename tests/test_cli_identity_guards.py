"""T0083: invalid register identities cannot become presence filenames."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from liteharness import cli, config

# Artifact verification can point imports at an extracted wheel, never install it.
IMPORT_ROOT = Path(os.environ.get("GUARDTURING_ARTIFACT_ROOT", Path(__file__).resolve().parents[1]))


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LITEHARNESS", "LITESUITE", "CLAUDE", "CODEX"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(IMPORT_ROOT),
               LITEHARNESS_NO_ANNOUNCE="1", PYTHONIOENCODING="utf-8")
    probe = subprocess.run(
        [sys.executable, "-c", "from liteharness import config; print(config.get_root())"],
        cwd=home, env=env, text=True, capture_output=True, check=True, timeout=15,
    )
    assert Path(probe.stdout.strip()).resolve() == (home / ".liteharness").resolve()
    return subprocess.run(
        [sys.executable, "-m", "liteharness.cli", *args], cwd=home, env=env,
        text=True, encoding="utf-8", capture_output=True, timeout=15,
    )


@pytest.mark.parametrize("argv", [
    ("register",),
    ("register", "--agent-id"),
    ("register", "--agent-id", ""),
    ("register", "--agent-id", "--cli", "codex"),
    ("register", "--agent-id", "-ghost"),
    ("register", "--agent-id", "--"),
])
def test_register_rejects_missing_or_flag_like_agent_id(tmp_path, argv):
    result = _run(tmp_path, *argv)
    assert result.returncode != 0
    assert "agent-id" in (result.stdout + result.stderr)
    # The old test checked tmp_path/agents, one level above the actual root.
    assert not (tmp_path / ".liteharness" / "agents").exists()
    assert not (tmp_path / ".liteharness" / "names").exists()


@pytest.mark.parametrize("agent_id", [None, "", "--cli", "-ghost"])
def test_direct_register_rejects_before_any_root_access(agent_id, monkeypatch, capsys):
    def forbidden_root():
        pytest.fail("invalid identity reached registration filesystem")
    monkeypatch.setattr(config, "get_root", forbidden_root)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_register(agent_id)
    assert exc.value.code != 0
    assert "agent-id" in capsys.readouterr().err


def test_valid_registration_still_records_its_own_identity(tmp_path):
    agent_id = "12345678-1234-4234-8234-123456789abc"
    result = _run(tmp_path, "register", "--agent-id", agent_id, "--cli", "litetui",
                  "--model", "test-model", "--name", "ExampleSeat", "--tier", "worker")
    assert result.returncode == 0, result.stderr
    root = tmp_path / ".liteharness"
    row = json.loads((root / "agents" / f"{agent_id}.json").read_text(encoding="utf-8"))
    assert row["agent_id"] == agent_id
    assert row["cli"] == "litetui" and row["model"] == "test-model"
    assert row["tier"] == "worker" and row["name"] == "ExampleSeat"
    assert list((root / "agents").glob("*.json")) == [root / "agents" / f"{agent_id}.json"]
    assert f"Registered agent {agent_id}" in result.stdout
