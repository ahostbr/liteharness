"""The HTTP/bridge fence fails locally while permitting asyncio infrastructure."""
import asyncio
import http.client
import socket

import pytest

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
