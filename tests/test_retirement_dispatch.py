"""Actual main dispatcher, injected command bodies, never live lifecycle."""
import io
import urllib.error
import urllib.request

import pytest

from liteharness import cli, retirement_cli, retirement_leader


@pytest.mark.parametrize("command, argv, module, function", [
    ("ack-idle", ["--handoff", "saved.md"], retirement_cli, "command_ack_idle"),
    ("retire", ["agent", "--force"], retirement_leader, "command_retire"),
])
def test_main_passes_exact_argv_and_authenticated_bridge(monkeypatch, command, argv, module, function):
    seen = []
    def handler(args, bridge):
        seen.append((args, bridge))
        return 1
    monkeypatch.setattr(module, function, handler)
    monkeypatch.setattr(cli.sys, "argv", ["liteharness", command, *argv])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 1 and seen == [(argv, cli._retirement_bridge_request)]


@pytest.mark.parametrize("command", ["ack-idle", "retire"])
def test_help_never_calls_identity_or_bridge(monkeypatch, command, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["liteharness", command, "--help"])
    monkeypatch.setattr(cli, "_bridge_request", lambda *a: pytest.fail("help called live bridge"))
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 0 and command in capsys.readouterr().out


@pytest.mark.parametrize("command,module,function,path", [
    ("ack-idle", retirement_cli, "command_ack_idle", "/ack-test-only"),
    ("retire", retirement_leader, "command_retire", "/pty/retire"),
])
def test_actual_dispatch_stale_env_401_sends_one_post_no_file_fallback(
        monkeypatch, command, module, function, path):
    attempts = []
    monkeypatch.setenv("LITESUITE_BRIDGE_TOKEN", "stale-test-token")
    monkeypatch.setattr(cli, "_bridge_token_file", lambda: pytest.fail("retirement retried file credential"))

    def urlopen(request, **kwargs):
        attempts.append((request.get_method(), request.full_url, request.get_header("Authorization")))
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error":"unauthorized"}'))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    def handler(argv, bridge):
        assert bridge("POST", path, {"fixture": True}) == {"ok": False, "error": "unauthorized"}
        return 1
    monkeypatch.setattr(module, function, handler)
    monkeypatch.setattr(cli.sys, "argv", ["liteharness", command])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 1
    assert len(attempts) == 1 and attempts[0][0] == "POST"
    assert attempts[0][1].endswith(path) and attempts[0][2] == "Bearer stale-test-token"


def test_other_bridge_callers_keep_stale_env_401_file_token_retry(monkeypatch):
    attempts = []
    reads = []
    monkeypatch.setenv("LITESUITE_BRIDGE_TOKEN", "stale-test-token")
    monkeypatch.setattr(cli, "_bridge_token_file", lambda: reads.append(True) or "fresh-test-token")
    def urlopen(request, **kwargs):
        attempts.append(request.get_header("Authorization"))
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b'{"error":"unauthorized"}'))
        return io.BytesIO(b'{"ok":true}')
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert cli._bridge_request("GET", "/fixture-only") == {"ok": True}
    assert attempts == ["Bearer stale-test-token", "Bearer fresh-test-token"] and reads == [True]
