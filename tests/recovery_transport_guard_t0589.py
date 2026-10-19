"""Explicit HTTP/bridge fence for isolated T0589 Python contracts.
Block HTTP before connect, plus default bridge/non-loopback socket attempts.
Windows asyncio's INTERNAL loopback socketpair is permitted (not HTTP). The
REAL composed desktop lab HTTP pipeline is NOT replaced by this plugin.
"""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import socket
from unittest.mock import patch


@contextmanager
def network_fence():
    attempts = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def blocked(address):
        attempts.append(repr(address))
        raise AssertionError('T0589 isolated contract attempted an unconfigured network peer')

    def socket_connect(peer, address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1') or address[1] == 7423:
            return blocked(address)
        return real_connect(peer, address)

    def socket_connect_ex(peer, address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1') or address[1] == 7423:
            return blocked(address)
        return real_connect_ex(peer, address)

    def http_connect(connection):
        return blocked((connection.host, connection.port))

    with patch.object(socket.socket, 'connect', socket_connect), \
         patch.object(socket.socket, 'connect_ex', socket_connect_ex), \
         patch.object(http.client.HTTPConnection, 'connect', http_connect), \
         patch.object(http.client.HTTPSConnection, 'connect', http_connect):
        yield attempts


_context = None
_attempts = None


def pytest_sessionstart(session):
    global _context, _attempts
    _context = network_fence()
    _attempts = _context.__enter__()


def pytest_sessionfinish(session, exitstatus):
    _context.__exit__(None, None, None)
    if _attempts:
        session.exitstatus = 1
    result = {'scope': 'selected isolated OSS HTTP contracts ONLY; desktop lab HTTP unchanged',
              'allowed': 'non-HTTP loopback socketpair for Windows asyncio internals',
              'attempts': _attempts, 'attempt_count': len(_attempts), 'pytest_exit': int(session.exitstatus),
              'consumer_children': 'same fence explicitly entered in _owner_resume_consumer.py',
              'issuer_children': 'actual issuer action only; no bridge/client path invoked'}
    path = os.environ.get('T0589_TRANSPORT_RECEIPT')
    if path:
        Path(path).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('T0589_NETWORK_FENCE=' + json.dumps(result), flush=True)
