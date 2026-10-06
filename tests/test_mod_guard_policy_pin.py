"""Source-only guard pin checks: fake metadata, inert temp files, no hook install."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "liteharness/catalog/hooks/mods/guard_backend.py"


@pytest.fixture
def backend():
    spec = importlib.util.spec_from_file_location("test_mod_guard_backend", BACKEND)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Distribution:
    def __init__(self, root, version="0.4.5"):
        self.root = root
        self.version = version

    def locate_file(self, relative):
        return self.root / relative

    def read_text(self, name):
        raise AssertionError("No real/editable distribution metadata may be read")


def installed_policy(tmp_path):
    root = tmp_path / "installed"
    package = root / "liteharness"
    package.mkdir(parents=True)
    for name in ("deny_gate.py", "deny_floor.py"):
        # Copy source bytes; do not import a machine-installed package.
        (package / name).write_bytes((ROOT / "liteharness" / name).read_bytes())
    return root


def bind(monkeypatch, backend, distribution):
    def lookup(name):
        assert name == "liteharness"
        return distribution
    monkeypatch.setattr(backend.importlib.metadata, "distribution", lookup)


def test_candidate_version_and_exact_policy_load(tmp_path, monkeypatch, backend):
    root = installed_policy(tmp_path)
    bind(monkeypatch, backend, Distribution(root))
    gate = backend.load_gate()
    payload = {"tool_name": "Bash", "tool_input": {"command": "run"},
               "cwd": str(tmp_path)}
    assert gate.decide(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"
    payload["tool_input"]["command"] = "echo portable"
    assert gate.decide(payload) is None


@pytest.mark.parametrize("version", ["0.4.4", "0.4.6", "0.4.5.dev1", ""])
def test_unverified_version_is_refused(tmp_path, monkeypatch, backend, version):
    root = installed_policy(tmp_path)
    bind(monkeypatch, backend, Distribution(root, version))
    with pytest.raises(ValueError, match="Unverified installed policy version"):
        backend.load_gate()


@pytest.mark.parametrize("name", ["deny_gate.py", "deny_floor.py"])
def test_changed_policy_bytes_are_refused_before_execution(tmp_path, monkeypatch, backend, name):
    root = installed_policy(tmp_path)
    marker = tmp_path / "must-not-exist"
    path = root / "liteharness" / name
    # Even executable-looking mismatched content must never run.
    path.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    bind(monkeypatch, backend, Distribution(root))
    with pytest.raises(ValueError, match="Unverified installed policy source"):
        backend.load_gate()
    assert not marker.exists()


def test_crlf_policy_is_normalized_to_the_reviewed_lf_bytes(tmp_path, monkeypatch, backend):
    root = installed_policy(tmp_path)
    for name in ("deny_gate.py", "deny_floor.py"):
        path = root / "liteharness" / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    bind(monkeypatch, backend, Distribution(root))
    assert backend.load_gate().decide({"tool_name": "Bash",
        "tool_input": {"command": "run"}, "cwd": str(tmp_path)}) is not None
