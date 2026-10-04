"""T0236-T1: the persistent name index (~/.liteharness/names.json)."""
import json

import pytest

from liteharness import agent_names, config


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_root", lambda: tmp_path / "harness")
    # index_path() must resolve through config.get_root() at CALL time: if it did not, every
    # test here would write the real ~/.liteharness/names.json.
    assert agent_names.index_path() == tmp_path / "harness" / "names.json"
    assert tmp_path in agent_names.index_path().parents
    return tmp_path / "harness"


def test_record_then_resolve_returns_the_documented_shape(root):
    agent_names.record_name("Mason", "aid-1", "convo-1", "C:/work/game", backend="codex", model="gpt-6-sol")
    entry = agent_names.resolve_name("Mason")
    assert set(entry) == {"name", "agent_id", "convo_id", "cwd", "backend", "model",
                          "created_at", "last_active_at"}
    assert (entry["agent_id"], entry["convo_id"], entry["cwd"]) == ("aid-1", "convo-1", "C:/work/game")
    on_disk = json.loads((root / "names.json").read_text())
    assert list(on_disk) == ["Mason"] and on_disk["Mason"]["model"] == "gpt-6-sol"
    assert not (root / "names.json.lock").exists()


def test_lookup_is_case_insensitive_and_stores_the_name_as_given(root):
    agent_names.record_name("Mason", "aid-1", "convo-1", "C:/x")
    assert agent_names.resolve_name("mason")["name"] == "Mason"
    assert agent_names.resolve_name("  MASON ")["agent_id"] == "aid-1"
    assert agent_names.resolve_name("nobody") is None
    assert agent_names.resolve_name("") is None


def test_refresh_keeps_created_at_and_updates_activity(root):
    first = agent_names.record_name("A", "aid-1", "convo-1", "C:/old")
    again = agent_names.record_name("a", "aid-1", "convo-1", "C:/new", backend="claude")
    assert again["name"] == "A"  # first spelling wins
    assert again["created_at"] == first["created_at"]
    assert again["cwd"] == "C:/new" and again["backend"] == "claude"
    assert len(agent_names.list_names()) == 1


def test_a_taken_name_is_refused_for_another_agent_unless_takeover(root):
    agent_names.record_name("Alpha", "aid-1", "convo-1", "C:/x")
    with pytest.raises(agent_names.NameTaken, match="aid-1"):
        agent_names.record_name("alpha", "aid-2", "convo-2", "C:/y")
    assert agent_names.resolve_name("Alpha")["agent_id"] == "aid-1"
    agent_names.record_name("Alpha", "aid-2", "convo-2", "C:/y", takeover=True)
    assert agent_names.resolve_name("Alpha")["agent_id"] == "aid-2"


def test_one_name_owns_one_conversation(root):
    agent_names.record_name("Alpha", "aid-1", "convo-1", "C:/x")
    with pytest.raises(agent_names.NameTaken, match="convo-1"):
        agent_names.record_name("Alpha", "aid-1", "convo-2", "C:/x")
    assert agent_names.resolve_name("Alpha")["convo_id"] == "convo-1"


def test_an_agent_carries_exactly_one_name(root):
    agent_names.record_name("Old", "aid-1", "convo-1", "C:/x")
    agent_names.record_name("New", "aid-1", "convo-1", "C:/x")
    assert [e["name"] for e in agent_names.list_names()] == ["New"]
    assert agent_names.name_for_agent("aid-1") == "New"


def test_list_is_sorted_and_empty_when_no_index(root):
    assert agent_names.list_names() == []
    agent_names.record_name("b", "2", "c2", "x")
    agent_names.record_name("A", "1", "c1", "x")
    assert [e["name"] for e in agent_names.list_names()] == ["A", "b"]


def test_a_corrupt_index_is_reported_and_never_overwritten(root):
    root.mkdir(parents=True)
    (root / "names.json").write_text("{not json")
    with pytest.raises(agent_names.IndexCorrupt):
        agent_names.resolve_name("x")
    with pytest.raises(agent_names.IndexCorrupt):
        agent_names.record_name("x", "1", "c", "d")
    assert (root / "names.json").read_text() == "{not json"
    assert agent_names.owns_conversation("anything") is True  # fails toward keeping


def test_owns_conversation_by_index_or_by_seat_id(root, tmp_path, monkeypatch):
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(tmp_path / "data"))
    assert agent_names.owns_conversation("aid-1") is False
    agent_names.record_name("A", "aid-1", "c1", "x")
    assert agent_names.owns_conversation("aid-1") is True
    convo = tmp_path / "data" / ".convos" / "c9"
    convo.mkdir(parents=True)
    (convo / "settings.json").write_text(json.dumps({"seat_id": "aid-9"}))
    assert agent_names.owns_conversation("aid-9") is True
    assert agent_names.owns_conversation("") is False


def test_a_stale_lock_from_a_crashed_writer_is_broken(root, monkeypatch):
    import os, time
    root.mkdir(parents=True)
    lock = root / "names.json.lock"
    lock.write_text("")
    old = time.time() - 120
    os.utime(lock, (old, old))
    agent_names.record_name("A", "1", "c", "d")
    assert agent_names.resolve_name("A") and not lock.exists()


# -- owns_conversation when the data root cannot be resolved ----------------------

def _no_root(monkeypatch):
    from liteharness import resume_seat

    def unresolvable():
        raise ValueError("LiteTUI data root unknown")

    monkeypatch.setattr(resume_seat, "_convo_root", unresolvable)


def test_unknown_data_root_protects_litetui_seats_only(root, monkeypatch):
    _no_root(monkeypatch)
    assert agent_names.owns_conversation("a", {"cli": "litetui"}) is True
    assert agent_names.owns_conversation("a", {"cli": "claude-code", "backend": "litetui"}) is True
    assert agent_names.owns_conversation("a", {"cli": "claude-code"}) is False
    assert agent_names.owns_conversation("a", {"cli": "codex"}) is False
    assert agent_names.owns_conversation("a") is True        # no presence given: cannot tell, keep


def test_the_index_protects_any_agent_whatever_its_cli(root, monkeypatch):
    _no_root(monkeypatch)
    agent_names.record_name("A", "aid-1", "c1", "x")
    assert agent_names.owns_conversation("aid-1", {"cli": "claude-code"}) is True
