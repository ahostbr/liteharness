"""The TEST-only adapter/HTTP fence fails locally and permits asyncio infrastructure."""
import asyncio
import http.client
import io
import socket
import urllib.request

import pytest

from liteharness import cli
from recovery_transport_guard_t0589 import network_fence


@pytest.mark.parametrize('method', ['connect', 'connect_ex'])
def test_unconfigured_transport_is_blocked_before_any_http_or_socket_contact(method):
    with network_fence() as attempts:
        with socket.socket() as peer:
            with pytest.raises(AssertionError, match='unconfigured network peer'):
                getattr(peer, method)(('127.0.0.1', 7423))
        assert attempts == ["('127.0.0.1', 7423)"]


@pytest.mark.parametrize('connection_type', [http.client.HTTPConnection, http.client.HTTPSConnection])
def test_http_is_blocked_before_physical_connect_even_on_non_bridge_loopback_port(connection_type):
    with network_fence() as attempts:
        peer = connection_type('127.0.0.1', 80)
        try:
            with pytest.raises(AssertionError, match='unconfigured network peer'):
                peer.request('GET', '/never-sent')
        finally:
            peer.close()
        assert attempts == ["('127.0.0.1', 80)"]


def test_windows_asyncio_internal_socketpair_is_not_misclassified_as_external_http():
    with network_fence() as attempts:
        asyncio.run(asyncio.sleep(0))
        assert attempts == []


@pytest.mark.parametrize('url', [None, '', '   '])
def test_cleared_env_real_bridge_entry_refuses_before_request_default_dispatch_or_socket(monkeypatch, url):
    for key in tuple(cli.os.environ):
        if key.startswith(('LITESUITE_', 'LITEHARNESS_', 'LITETUI_')):
            monkeypatch.delenv(key)
    if url is not None:
        monkeypatch.setenv('LITESUITE_BRIDGE_URL', url)
    constructed, dispatched = [], []
    real_request = urllib.request.Request

    def request(*args, **kwargs):
        constructed.append(args)
        return real_request(*args, **kwargs)

    def dispatch(*args, **kwargs):
        dispatched.append(args)
        raise AssertionError('No HTTP dispatch is allowed with missing explicit peer URL')

    monkeypatch.setattr(urllib.request, 'Request', request)
    monkeypatch.setattr(urllib.request, 'urlopen', dispatch)
    original_entry = cli._bridge_request
    with network_fence() as attempts:
        assert cli._bridge_request.__wrapped__ is original_entry
        with pytest.raises(ValueError, match='requires explicit LITESUITE_BRIDGE_URL'):
            cli._bridge_request('GET', '/harness/recovered-agent/locate?agentId=isolated-seat')
        assert constructed == [] and dispatched == [] and attempts == []


def test_explicit_http_peer_uses_real_adapter_while_no_reserved_slot_dispatch_peer_is_distinct(monkeypatch):
    # Only the external HTTP endpoint is a peer; REAL adapter constructs Request,
    # uses the explicit URL/token, decodes JSON and returns the actual receipt.
    monkeypatch.setenv('LITESUITE_BRIDGE_URL', 'http://t0589-explicit-peer.invalid')
    monkeypatch.setenv('LITESUITE_BRIDGE_TOKEN', 'isolated-test-token')
    seen = []

    def peer(request, **kwargs):
        seen.append((request.full_url, request.get_method()))
        return io.BytesIO(b'{"ok": true, "found": false}')

    monkeypatch.setattr(urllib.request, 'urlopen', peer)
    with network_fence() as attempts:
        result = cli._bridge_request('GET', '/harness/recovered-agent/locate?agentId=isolated-seat')
        assert result == {'ok': True, 'found': False}
        assert seen == [('http://t0589-explicit-peer.invalid/harness/recovered-agent/locate?agentId=isolated-seat', 'GET')]
        assert attempts == []
