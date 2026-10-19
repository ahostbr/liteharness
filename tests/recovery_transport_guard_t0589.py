"""Explicit TEST-ONLY bridge-entry/HTTP fence for isolated T0589 contracts.
Missing URL refuses before the real adapter's production fallback dispatch.
Production defaults and the REAL composed desktop lab HTTP remain unchanged.
Windows asyncio's internal non-HTTP loopback socketpair is permitted.
"""
from contextlib import contextmanager, nullcontext
from functools import wraps
import http.client
import json
import os
from pathlib import Path
import socket
from unittest.mock import patch


@contextmanager
def network_fence(*, guard_bridge=True):
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

    bridge_patch = nullcontext()
    if guard_bridge:
        from liteharness import cli
        real_bridge = cli._bridge_request

        @wraps(real_bridge)
        def isolated_bridge(*args, **kwargs):
            if not os.environ.get('LITESUITE_BRIDGE_URL', '').strip():
                raise ValueError('T0589 TEST isolation requires explicit LITESUITE_BRIDGE_URL; no default bridge dispatch')
            return real_bridge(*args, **kwargs)

        bridge_patch = patch.object(cli, '_bridge_request', isolated_bridge)

    with patch.object(socket.socket, 'connect', socket_connect), \
         patch.object(socket.socket, 'connect_ex', socket_connect_ex), \
         patch.object(http.client.HTTPConnection, 'connect', http_connect), \
         patch.object(http.client.HTTPSConnection, 'connect', http_connect), bridge_patch:
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
              'missing_url': 'TEST-only real adapter entry guard refuses locally before Request/default dispatch',
              'allowed': 'non-HTTP loopback socketpair for Windows asyncio internals',
              'attempts': _attempts, 'attempt_count': len(_attempts), 'pytest_exit': int(session.exitstatus),
              'consumer_children': 'same HTTP/socket fence; no CLI bridge entry (LITETUI_NO_HARNESS)',
              'issuer_children': 'actual issuer action only; no bridge/client path invoked'}
    path = os.environ.get('T0589_TRANSPORT_RECEIPT')
    if path:
        Path(path).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('T0589_NETWORK_FENCE=' + json.dumps(result), flush=True)
