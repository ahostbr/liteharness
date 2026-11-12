"""Prove the role ships and is read from a locally installed wheel, not source."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def test_installed_wheel_contains_and_loads_clerk_role(tmp_path):
    wheels, target, home = tmp_path / "wheels", tmp_path / "installed", tmp_path / "home"
    home.mkdir()
    build = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheels)],
                           cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert build.returncode == 0, build.stdout + build.stderr
    artifacts = list(wheels.glob("*.whl"))
    assert len(artifacts) == 1
    # Positive-control archive member before asserting the new role's presence.
    with zipfile.ZipFile(artifacts[0]) as archive:
        assert archive.read("liteharness/inbox.py")
        assert archive.read("liteharness/prompts/clerk.md") == (ROOT / "liteharness/prompts/clerk.md").read_bytes()
    install = subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                              "--disable-pip-version-check", "--target", str(target), str(artifacts[0])],
                             cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert install.returncode == 0, install.stdout + install.stderr
    probe = r'''
import json, os
from pathlib import Path
from liteharness import clerk, cli, config, prompts
root = Path(os.environ["EXPECTED_TARGET"]).resolve()
assert Path(clerk.__file__).resolve().is_relative_to(root), clerk.__file__
assert Path(prompts.__file__).resolve() == root / "liteharness/prompts.py"
role = Path(clerk.__file__).parent / "prompts/clerk.md"
assert role.is_relative_to(root) and role.is_file()
clerk.enable("coordinator")
seen = []
def fake_spawn(**kwargs):
    seen.append(kwargs)
    assert "Clerk (not coordinator):" in kwargs["prompt"]
    assert "Never approve, grant, merge, assign or decide." in Path(kwargs["system_prompt_file"]).read_text(encoding="utf-8")
    raise SystemExit(99)  # substituted external boundary: NO app/seat starts
cli.cmd_spawn = fake_spawn
try: clerk.start(pane="installed-clerk-pane", cwd=str(Path.cwd()))
except SystemExit as exc: assert exc.code == 99
assert len(seen) == 1
assert seen[0]["split_pane"] == "installed-clerk-pane"
print(json.dumps({"module": str(Path(clerk.__file__).resolve()), "role": str(role.resolve()), "spawn_substituted": True}))
'''
    # The Clerk only starts for a LIVE orchestrator: this test process is its owner, alive for the probe.
    now = datetime.now(timezone.utc).isoformat()
    agents = home / ".liteharness" / "agents"
    agents.mkdir(parents=True)
    (agents / "coordinator.json").write_text(json.dumps({
        "agent_id": "coordinator", "role": "orchestrator", "tier": "orchestrator",
        "session_pid": os.getpid(), "last_seen": now, "registered_at": now}), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(target), "HOME": str(home), "USERPROFILE": str(home),
           "EXPECTED_TARGET": str(target)}
    result = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert Path(receipt["module"]).is_relative_to(target)
    assert Path(receipt["role"]).is_relative_to(target)
    assert receipt["spawn_substituted"] is True
    print("INSTALLED_ROLE_RECEIPT=" + json.dumps(receipt))
