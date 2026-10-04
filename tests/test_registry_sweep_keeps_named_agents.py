"""T0236-T3: the registry sweep never DELETES an agent that owns a conversation.

Reproduces a named agent's presence file being deleted: `_purge_stale_agents` ran with
reason `stale-agent-sweep:owner-process-gone`, archived, then unlinked
`agents/<id>.json`. An agent that owns a conversation dir (named in names.json,
or matched by `seat_id`) is marked offline instead; one that owns nothing is still swept.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from liteharness import config, hooks, resume_seat, seat_lifecycle as lifecycle

AID = "11111111-1111-4111-8111-111111111111"
CONVO = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "harness"
    (root / "agents").mkdir(parents=True)
    monkeypatch.setattr(config, "get_root", lambda: root)
    monkeypatch.setattr(config, "get_agent_id", lambda: "janitor")
    monkeypatch.setattr(lifecycle, "log_path", lambda: tmp_path / "seat-lifecycle.jsonl")
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(hooks, "_pid_alive", lambda _pid: False)
    from liteharness import agent_names
    assert tmp_path in agent_names.index_path().parents, "names.json would hit the real ~/.liteharness"
    return root, tmp_path


def _presence(root, agent_id=AID, **extra):
    row = {"agent_id": agent_id, "name": "Mason", "tier": "worker", "model": "m",
           "backend": "codex", "thinking_level": "high", "session_pid": 4242,
           "last_seen": datetime.now(timezone.utc).isoformat(), **extra}
    path = root / "agents" / f"{agent_id}.json"
    path.write_text(json.dumps(row))
    return path


def _convo(tmp_path, seat_id=AID, convo_id=CONVO):
    d = tmp_path / "data" / ".convos" / convo_id
    d.mkdir(parents=True)
    (d / "settings.json").write_text(json.dumps({"seat_id": seat_id}))
    (d / "convo.jsonl").write_text('{"type":"meta"}\n')


def _index(root, agent_id=AID):
    (root / "names.json").write_text(json.dumps({"Mason": {
        "agent_id": agent_id, "convo_id": CONVO, "cwd": "C:/work/game", "backend": "codex",
        "model": "m", "created_at": "t", "last_active_at": "t"}}))


def test_dead_owner_named_in_index_is_marked_offline_not_deleted(world):
    root, _ = world
    path = _presence(root)
    _index(root)
    hooks._purge_stale_agents()
    assert path.exists(), "a named agent's presence file must survive the sweep"
    kept = json.loads(path.read_text())
    assert kept["agent_id"] == AID and kept["exited_at"] and kept["status"] == "offline"
    assert kept["retirement_reason"] == "stale-agent-sweep:owner-process-gone"


def test_dead_owner_owning_a_convo_by_seat_id_is_kept_offline(world):
    root, tmp = world
    path = _presence(root)
    _convo(tmp)
    hooks._purge_stale_agents()
    assert json.loads(path.read_text())["status"] == "offline"


def test_idle_and_recap_branches_also_keep_a_named_agent(world, monkeypatch):
    root, _ = world
    monkeypatch.setattr(hooks, "_pid_alive", lambda _pid: True)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    path = _presence(root, last_seen=old, session_pid=None)
    _index(root)
    hooks._purge_stale_agents()
    assert path.exists() and json.loads(path.read_text())["status"] == "offline"


def test_agent_without_a_conversation_is_still_swept(world):
    root, _ = world
    path = _presence(root, agent_id="scratch-seat")
    hooks._purge_stale_agents()
    assert not path.exists()


def test_resume_lookup_works_after_the_sweep(world):
    root, tmp = world
    _presence(root)
    _convo(tmp)
    _index(root)
    hooks._purge_stale_agents()
    convo_id, _settings, registry = resume_seat.lookup(AID, None)
    assert convo_id == CONVO and registry["agent_id"] == AID


def test_session_end_deregister_keeps_a_named_agent(world, monkeypatch):
    root, _ = world
    monkeypatch.setattr(config, "get_agent_id", lambda: AID)
    path = _presence(root)
    _index(root)
    hooks.deregister()
    assert json.loads(path.read_text())["status"] == "offline"


def test_offline_record_is_not_live_and_reregistering_clears_it(world):
    root, _ = world
    path = _presence(root)
    _index(root)
    hooks._purge_stale_agents()
    from liteharness import naming
    assert naming.is_name_taken("Mason") is None  # exited_at: not a live holder
    from liteharness import cli
    cli.cmd_register(AID, cli="litetui", model="m", tier="worker")  # a resumed seat registers
    back = json.loads(path.read_text())
    assert "exited_at" not in back and "status" not in back


# -- unknown is not "owns nothing": the sweep keeps when it cannot tell --------------

def _unresolvable_root(monkeypatch):
    from liteharness import resume_seat

    def unresolvable():
        raise ValueError("LiteTUI data root unknown: set LITETUI_DATA_ROOT")

    monkeypatch.setattr(resume_seat, "_convo_root", unresolvable)


def test_unresolvable_litetui_data_root_keeps_a_litetui_seat_offline(world, monkeypatch):
    root, _ = world
    path = _presence(root, cli="litetui")
    _unresolvable_root(monkeypatch)
    hooks._purge_stale_agents()
    assert path.exists() and json.loads(path.read_text())["status"] == "offline"


def test_unresolvable_data_root_keeps_a_seat_whose_backend_is_litetui(world, monkeypatch):
    root, _ = world
    path = _presence(root, cli="claude-code", backend="litetui")
    _unresolvable_root(monkeypatch)
    hooks._purge_stale_agents()
    assert path.exists()


def test_unresolvable_data_root_does_not_protect_a_non_litetui_agent(world, monkeypatch):
    """A Claude Code session cannot own a LiteTUI conversation: no data root -> old behaviour."""
    root, _ = world
    path = _presence(root, agent_id="claude-seat", cli="claude-code")
    _unresolvable_root(monkeypatch)
    hooks._purge_stale_agents()
    assert not path.exists()


def test_a_non_litetui_agent_named_in_the_index_is_still_kept(world, monkeypatch):
    root, _ = world
    path = _presence(root, cli="claude-code")
    _index(root)
    _unresolvable_root(monkeypatch)
    hooks._purge_stale_agents()
    assert path.exists() and json.loads(path.read_text())["status"] == "offline"


def test_a_corrupt_index_keeps_even_a_non_litetui_agent(world, monkeypatch):
    root, _ = world
    path = _presence(root, agent_id="claude-seat", cli="claude-code")
    (root / "names.json").write_text("{not json")
    hooks._purge_stale_agents()
    assert path.exists()


def test_an_unreadable_conversation_settings_file_keeps_a_litetui_agent(world):
    root, tmp = world
    path = _presence(root, cli="litetui")
    bad = tmp / "data" / ".convos" / "corrupt-convo"
    bad.mkdir(parents=True)
    (bad / "settings.json").write_text("{not json")   # could be this agent's; cannot tell
    hooks._purge_stale_agents()
    assert path.exists() and json.loads(path.read_text())["status"] == "offline"


def test_a_readable_data_root_without_this_agent_still_sweeps(world):
    root, tmp = world
    path = _presence(root, agent_id="scratch-seat")
    _convo(tmp, seat_id="someone-else")
    hooks._purge_stale_agents()
    assert not path.exists()
