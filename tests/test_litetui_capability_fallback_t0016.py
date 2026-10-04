"""Older/non-TUI parent compatibility never overturns a connected refusal."""
import json

import pytest
from liteharness import litetui_client as client


@pytest.mark.parametrize("parent", ["ClaudeCode", "OldTui"])
def test_parent_without_endpoint_uses_tui_owned_readonly_metadata(monkeypatch, tmp_path, parent):
    root = tmp_path / "harness"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / f"{parent}.json").write_text(json.dumps({"agent_id": parent, "cli": "claude" if parent == "ClaudeCode" else "litetui"}))
    monkeypatch.setattr(client.config, "get_root", lambda: root)
    calls = []
    def offline(backend, model, timeout):
        calls.append((backend, model, timeout))
        return {"thinking": {"levels": ["default", "high"]}}
    monkeypatch.setattr(client, "_offline_capabilities", offline)
    monkeypatch.setattr(client, "request", lambda *a, **k: pytest.fail("No endpoint, no attach"))
    client.validate_spawn(parent, "claude", "sonnet", "high")
    assert calls == [("claude", "sonnet", 30)]


@pytest.mark.parametrize("result", [{"thinking": {"levels": ["default"]}}, ValueError("unknown connected model"), ValueError("stale endpoint")])
def test_connected_negative_unknown_stale_never_fallback(monkeypatch, result):
    monkeypatch.setattr(client, "_parent_has_endpoint", lambda *a: True)
    monkeypatch.setattr(client, "_offline_capabilities", lambda *a: pytest.fail("Connected answer is final"))
    def peer(*a, **k):
        if isinstance(result, Exception): raise result
        return result
    monkeypatch.setattr(client, "request", peer)
    with pytest.raises(ValueError):
        client.validate_spawn("parent", "claude", "haiku", "high")


def test_offline_unknown_is_loud_and_no_launch(monkeypatch):
    monkeypatch.setattr(client, "_parent_has_endpoint", lambda *a: False)
    def unavailable(*a): raise ValueError("offline metadata unavailable")
    monkeypatch.setattr(client, "_offline_capabilities", unavailable)
    with pytest.raises(ValueError, match="unavailable"):
        client.validate_spawn("parent", "codex", "unknown", "high")


def test_tui_cli_subprocess_is_probe_only(monkeypatch):
    import builtins
    import shutil
    import subprocess
    original = builtins.__import__
    def imports(name, *args, **kwargs):
        if name == "litetui.model_capabilities": raise ImportError("different Python environment")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", imports)
    monkeypatch.setattr(shutil, "which", lambda name: "fake-litetui")
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps({"thinking": {"levels": ["high"]}}), "")
    monkeypatch.setattr(subprocess, "run", run)
    result = client._offline_capabilities("claude", "sonnet", 30)
    assert result["thinking"]["levels"] == ["high"]
    assert calls[0][0] == ["fake-litetui", "--capabilities", "--backend", "claude", "--model", "sonnet"]
    assert calls[0][1]["timeout"] == 30 and calls[0][1]["capture_output"]
