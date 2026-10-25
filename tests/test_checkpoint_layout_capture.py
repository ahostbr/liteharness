"""Current renderer layout capture: real adapter, substituted HTTP boundary only."""
import pytest

from liteharness import checkpoint_save, cli


def capture(monkeypatch, context):
    def request(method, path):
        assert (method, path) == ('GET', '/context')
        return context
    monkeypatch.setattr(cli, '_bridge_request', request)
    return checkpoint_save._layout()


def test_wire_leaf_ids_include_hidden_surface(monkeypatch):
    result = capture(monkeypatch, {
        'activePanes': [{'id': 'visible', 'leaves': [{'leafId': 'leaf-a', 'sessionIds': ['pty-a']}]}],
        'hiddenPanes': [{'id': 'hidden', 'leaves': [{'leafId': 'leaf-b', 'sessionIds': ['pty-b']}]}],
    })
    assert result is not None and result[1] == {'leaf-a', 'leaf-b'}


@pytest.mark.parametrize('pane', [None, {}, {'id': 'p', 'leaves': [{}]},
                                  {'id': 'p', 'leaves': [{'leafId': None}]},
                                  {'id': 'p', 'leaves': 'bad'}])
def test_malformed_binding_is_unknown_not_a_literal_none_leaf(monkeypatch, pane):
    assert capture(monkeypatch, {'activePanes': [pane]}) is None


@pytest.mark.parametrize('context', [None, {}, {'activePanes': None},
    {'activePanes': [], 'hiddenPanes': None}, {'activePanes': [], 'hiddenPanes': [{}]},
    {'activePanes': [], 'ok': False}, {'activePanes': [], 'error': 'unavailable'}])
def test_unknown_or_failed_context_is_not_an_empty_layout(monkeypatch, context):
    assert capture(monkeypatch, context) is None


def test_legacy_leaf_key_supported_without_inventing_names(monkeypatch):
    result = capture(monkeypatch, {'activePanes': [{'id': 'p', 'leaves': [{'id': 'old-wire-leaf'}]}]})
    assert result is not None and result[1] == {'old-wire-leaf'}


def test_layout_revision_ignores_listing_order_but_changes_when_leaf_moves(monkeypatch):
    a = {'id': 'a', 'leaves': [{'leafId': 'first'}, {'leafId': 'second'}]}
    b = {'id': 'b', 'leaves': []}
    first = capture(monkeypatch, {'activePanes': [a, b]})
    assert capture(monkeypatch, {'activePanes': [b, a]}) == first
    moved = capture(monkeypatch, {'activePanes': [
        {'id': 'a', 'leaves': [{'leafId': 'first'}]},
        {'id': 'b', 'leaves': [{'leafId': 'second'}]},
    ]})
    assert moved[0] != first[0]
    assert moved[1] == first[1]
