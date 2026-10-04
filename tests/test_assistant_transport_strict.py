"""Exercise real urllib opener/redirect dispatch without binding or network."""
import io
import json
from pathlib import Path
import urllib.error
import urllib.request
from email.message import Message
from types import SimpleNamespace

import pytest
from liteharness import assistant_transport as transport
from liteharness import assistant_identity, hooks, config


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('location', ['https://foreign.invalid/x', 'file:///tmp/x',
    'http://[::1]:7423/x', 'http://127.0.0.1:7423/other', '/same-origin'])
def test_actual_opener_redirect_handlers_refuse(status, location, monkeypatch):
    attempts = []
    class HTTP(urllib.request.HTTPHandler):
        def http_open(self, request):
            attempts.append(request)
            headers = Message()
            headers['Location'] = location
            response = urllib.response.addinfourl(io.BytesIO(b''), headers, request.full_url, status)
            response.msg = 'fixture redirect'
            return response
    original = urllib.request.build_opener
    built = []
    def build(*handlers):
        assert any(isinstance(h, transport.RefuseAssistantRedirect) for h in handlers)
        proxies = next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler))
        assert proxies.proxies == {}
        opener = original(*handlers, HTTP())
        built.append(opener)
        return opener
    monkeypatch.setattr(urllib.request, 'build_opener', build)
    monkeypatch.setenv('HTTP_PROXY', 'http://foreign.invalid:9999')
    monkeypatch.setenv('HTTPS_PROXY', 'http://foreign.invalid:9999')
    monkeypatch.setenv('LITESUITE_BRIDGE_URL', 'https://foreign.invalid')
    with pytest.raises(urllib.error.HTTPError) as error:
        transport.send_assistant(b'{}', 'synthetic-token')
    assert error.value.code == status
    assert len(attempts) == 1
    assert attempts[0].full_url == transport.ENDPOINT
    assert attempts[0].get_header('Authorization') == 'Bearer synthetic-token'
    assert not hasattr(built[0], 'redirect_dict')


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    (tmp_path / '.litesuite').mkdir()
    token_path = tmp_path / '.litesuite/bridge-token'
    token_path.write_text('synthetic-token')
    forbidden = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('side effect nomination'))
    monkeypatch.setattr(config, 'get_agent_id', forbidden)
    monkeypatch.setattr(hooks, '_bridge_url', forbidden)
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(assistant_identity, 'assistant_generation', lambda *args: (('own', 'pane-own'), ('fixture-generation', 20, 20000)))
    monkeypatch.setattr(hooks, '_last_assistant_event_id', lambda path: 'msg_fixture')
    calls = []
    monkeypatch.setattr(transport, 'send_assistant', lambda payload, token: calls.append((payload, token)) or 200)
    return SimpleNamespace(root=tmp_path, token=token_path, calls=calls,
                           payload={'session_id': 'own', 'last_assistant_message': 'hello', 'transcript_path': 'fixture'})


def test_bridge_original_fields_event_derivation_and_no_nomination(bridge, monkeypatch):
    monkeypatch.setenv(bytes.fromhex('4c49544553554954455f53454e54494e454c5f50414e455f4944').decode(), 'foreign')
    hooks.bridge_assistant_message(bridge.payload)
    assert len(bridge.calls) == 1
    payload, token = bridge.calls[0]
    assert json.loads(payload) == {'role': 'assistant', 'content': 'hello',
        'source': 'claude-code-hook', 'session_id': 'own', 'pane_id': 'pane-own',
        'agent_id': 'own', 'message_id': 'msg_fixture'}
    assert token == 'synthetic-token'


@pytest.mark.parametrize('mode', ['missing', 'empty', 'unreadable', 'bad-encoding'])
def test_credential_failure_no_http(bridge, monkeypatch, mode):
    if mode == 'missing': bridge.token.unlink()
    if mode == 'empty': bridge.token.write_text('  \n')
    if mode == 'bad-encoding': bridge.token.write_bytes(b'\xff')
    if mode == 'unreadable':
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == bridge.token: raise PermissionError()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', read)
    hooks.bridge_assistant_message(bridge.payload)
    assert bridge.calls == []


@pytest.mark.parametrize('value', ['', ' ', 1, {}, ['content']])
def test_empty_or_malformed_content_no_http(bridge, value):
    bridge.payload['last_assistant_message'] = value
    hooks.bridge_assistant_message(bridge.payload)
    assert bridge.calls == []


def test_final_identity_generation_recheck(bridge, monkeypatch):
    identities = iter([(('own', 'pane-own'), ('generation', 20, 20000)),
                       (('own', 'pane-own'), ('generation', 21, 21000))])
    monkeypatch.setattr(assistant_identity, 'assistant_generation', lambda *args: next(identities))
    hooks.bridge_assistant_message(bridge.payload)
    assert bridge.calls == []


def test_transport_failure_log_never_raw_exception(bridge, monkeypatch):
    def fail(*args): raise RuntimeError('synthetic-token private-url')
    monkeypatch.setattr(transport, 'send_assistant', fail)
    hooks.bridge_assistant_message(bridge.payload)
    text = (bridge.root / '.litesuite/assistant-bridge.log').read_text()
    assert 'synthetic-token' not in text and 'private-url' not in text
    assert 'transport refused' in text
