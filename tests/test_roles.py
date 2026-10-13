"""T1: the role vocabulary replaces the tier words.

New writes use orchestrator/owner/builder/scout/critic. The old tier words are accepted as
aliases on INPUT (with a one-line deprecation note) and a presence file that still says
`worker` must keep resolving; `discover` prints the new words only.
"""
import json
from datetime import datetime, timezone

import pytest

from liteharness import cli, config, fleet_policy, roles

ALIASES = {
    "leader": "owner",
    "worker": "builder",
    "thinker": "scout",
    "reviewer": "critic",
}


def test_role_constants():
    assert roles.ROLES == ("orchestrator", "owner", "builder", "scout", "critic")
    assert roles.CWD_REQUIRED_ROLES == ("builder", "owner")


@pytest.mark.parametrize("old,new", sorted(ALIASES.items()))
def test_old_words_are_aliases(old, new):
    assert roles.normalize_role(old) == new
    assert roles.normalize_role(old.upper()) == new
    note = roles.deprecation_note(old)
    assert note and old in note and new in note and "\n" not in note


@pytest.mark.parametrize("new", roles.ROLES)
def test_new_words_pass_through_silently(new):
    assert roles.normalize_role(new) == new
    assert roles.deprecation_note(new) is None


@pytest.mark.parametrize("bad", ["", None, "janitor", "lead er"])
def test_unknown_role_is_refused(bad):
    assert roles.normalize_role(bad) is None
    assert roles.deprecation_note(bad) is None


def test_canonical_is_for_reading_stored_values():
    assert roles.canonical("worker") == "builder"
    assert roles.canonical("orchestrator") == "orchestrator"
    assert roles.canonical("scout") == "scout"
    assert roles.canonical(None) == "builder"
    assert roles.canonical("something-odd") == "something-odd"  # never destroys data on read


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "get_root", lambda: tmp_path)
    monkeypatch.setenv("LITEHARNESS_NO_ANNOUNCE", "1")
    (tmp_path / "agents").mkdir()
    return tmp_path


def _presence(home, agent_id):
    return json.loads((home / "agents" / f"{agent_id}.json").read_text(encoding="utf-8"))


def test_register_with_an_old_word_stores_the_new_word_and_says_so(home, capsys):
    cli.cmd_register("seat-1", cli="claude-code", tier="leader", name="Alpha")
    row = _presence(home, "seat-1")
    assert row["role"] == "owner"
    assert row["tier"] == "leader", "the old key keeps the old word for readers that have not moved"
    out = capsys.readouterr()
    assert "deprecated" in out.err.lower() and "leader" in out.err and "owner" in out.err
    assert "deprecated" not in out.out.lower(), "stdout stays clean: spawn/register output is parsed"


def test_register_with_a_new_word_prints_no_deprecation(home, capsys):
    cli.cmd_register("seat-1", cli="claude-code", tier="critic", name="Alpha")
    assert _presence(home, "seat-1")["role"] == "critic"
    captured = capsys.readouterr()
    assert "deprecated" not in (captured.out + captured.err).lower()


def test_register_unknown_role_refuses_without_registry_changes(home, capsys):
    """Unknown role input is refused, not silently changed to the default role."""
    def snapshot():
        return {str(p.relative_to(home)): p.read_bytes() if p.is_file() else None
                for p in home.rglob("*")}
    before = snapshot()
    with pytest.raises(SystemExit) as exc:
        cli.cmd_register("seat-1", cli="claude-code", tier="janitor", name="Alpha")
    assert exc.value.code != 0
    assert snapshot() == before
    captured = capsys.readouterr()
    assert captured.out == "" and len(captured.err.splitlines()) == 1
    assert "janitor" in captured.err
    assert all(word in captured.err for word in (*roles.ROLES, *roles.ROLE_ALIASES))


def test_register_default_is_builder(home):
    cli.cmd_register("seat-1", cli="claude-code", name="Alpha")
    assert _presence(home, "seat-1")["role"] == "builder"


def test_a_stored_old_word_survives_a_reregister_without_a_role(home):
    (home / "agents" / "seat-1.json").write_text(json.dumps({
        "agent_id": "seat-1", "tier": "worker",
        "last_seen": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    cli.cmd_register("seat-1", cli="claude-code", name="Alpha")
    assert roles.row_role(_presence(home, "seat-1")) == "builder"


def test_discover_prints_new_words_only(home, capsys):
    now = datetime.now(timezone.utc).isoformat()
    for i, old in enumerate([*ALIASES, "orchestrator"]):
        (home / "agents" / f"seat-{i}.json").write_text(json.dumps({
            "agent_id": f"seat-{i}", "tier": old, "cli": "claude-code", "model": "m",
            "name": f"Seat{i}", "last_seen": now}), encoding="utf-8")
    capsys.readouterr()
    cli.cmd_discover()
    out = capsys.readouterr().out
    for old, new in ALIASES.items():
        assert f" {new} " in out, f"{new} missing from discover"
        assert f" {old} " not in out, f"{old} leaked into discover"
    assert " orchestrator " in out


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "caller-1")
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path / "h")
    monkeypatch.setattr(cli, "_bridge_request",
                        lambda m, p, b=None: calls.append((m, p, b)) or {"ok": False, "error": "stop"})
    monkeypatch.setattr(fleet_policy, "gate", lambda backend, model, thinking: None)
    return calls


def _spawn(**kw):
    base = dict(spawn_cli="litetui", name="Seat", split_mode=True, spawned_by="caller-1",
                backend="codex", model="gpt-6-sol", thinking_level="high")
    base.update(kw)
    try:
        cli.cmd_spawn(**base)
    except SystemExit as exc:
        return exc.code
    return None


@pytest.mark.parametrize("role", ["builder", "owner", "worker", "leader"])
def test_builder_and_owner_need_a_cwd_old_words_too(role, bridge, capsys):
    assert _spawn(tier=role) == 2
    assert "--cwd" in capsys.readouterr().out
    assert bridge == []


@pytest.mark.parametrize("role", ["orchestrator", "scout", "critic", "thinker", "reviewer"])
def test_other_roles_are_exempt_from_the_cwd_gate(role, bridge, capsys):
    assert _spawn(tier=role) == 2, "the resolver stub refuses only after the cwd gate"
    assert "--cwd" not in capsys.readouterr().out
    (method, path, body), = bridge
    assert (method, path) == ("POST", "/harness/spawn/resolve")
    assert body["tier"] == roles.wire_tier(role)


def test_spawn_with_an_unknown_role_is_refused(bridge, capsys):
    assert _spawn(tier="janitor", cwd=".") == 2
    assert "janitor" in capsys.readouterr().out
    assert bridge == []


def test_row_role_prefers_role_and_falls_back_to_tier():
    assert roles.row_role({"role": "owner", "tier": "worker"}) == "owner"
    assert roles.row_role({"tier": "reviewer"}) == "critic"
    assert roles.row_role({}) == "builder"
    assert roles.row_role(None, "") == ""


def test_wire_and_presence_helpers_keep_the_old_spelling_for_old_readers():
    assert roles.wire_tier("owner") == "leader"
    assert roles.wire_tier("leader") == "leader"
    assert roles.presence_fields("critic") == {"role": "critic", "tier": "reviewer"}
    assert roles.presence_fields("librarian") == {"tier": "librarian"}
    assert roles.env_role({"LITEHARNESS_ROLE": "scout", "LITEHARNESS_TIER": "worker"}) == "scout"
    assert roles.env_role({"LITEHARNESS_TIER": "thinker"}) == "scout"
    assert roles.env_role({}) == ""


# ── T6: the words handed to agents ───────────────────────────────────────────────────────────

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_WORDS = re.compile(r"\b(leader|worker|thinker|reviewer)s?\b", re.I)


def test_bootstrap_text_teaches_the_roles_and_the_role_trailer():
    text = cli.BOOTSTRAP_HARNESS_CONTENT
    assert "Agent-Role:" in text and "Agent-Tier" not in text
    assert not OLD_WORDS.search(text), OLD_WORDS.search(text)
    assert "five-role" in text


@pytest.mark.parametrize("rel", [
    "ls-init-liteharness/architecture-template.md", "ls-gauntlet/SKILL.md", "ls-eval-gate/SKILL.md"])
def test_the_named_skills_use_the_role_words(rel):
    text = (ROOT / "liteharness" / "catalog" / "skills" / rel).read_text(encoding="utf-8")
    # File and folder names that predate the rename stay as they are; prose does not.
    prose = re.sub(r"(prompts/[\w./<>-]*|orchestrator-role\.md|\b(?:workers|thinkers|reviewers|leaders|"
                   r"orchestrator)/|[\w-]*-orchestrator)", "", text)
    assert not re.findall(r"\b(leader|worker|thinker|reviewer)s?\b", prose, re.I)
    assert "Agent-Tier" not in text


def test_the_tier_cards_speak_the_roles_and_owners_are_peers():
    import json
    data = json.loads((ROOT / "liteharness" / "tier_cards.json").read_text(encoding="utf-8"))
    rules = [r["rule"] for r in data["loop"]] + [r["rule"] for e in data["tiers"].values() for r in e["rules"]]
    stale = [r for r in rules if OLD_WORDS.search(r.replace("RECOMMEND-REVIEWER", ""))]
    assert stale == []
    owner_rules = " ".join(r["rule"] for r in data["tiers"]["leader"]["rules"]).lower()
    assert "peers" in owner_rules and "directly" in owner_rules


def test_orchestrator_is_the_identity_word_not_an_alias_and_the_older_name_is_not_accepted():
    assert "orchestrator" in roles.ROLES and "orchestrator" not in roles.ROLE_ALIASES
    assert roles.deprecation_note("orchestrator") is None
    renamed = "senti" + "nel"  # the shipped harness must not carry a personal default name
    assert roles.normalize_role(renamed) is None
    assert renamed not in roles.ROLES and renamed not in roles.ROLE_ALIASES
    assert roles.wire_tier("orchestrator") == "orchestrator"
    assert roles.presence_fields("orchestrator") == {"role": "orchestrator", "tier": "orchestrator"}


# ── critic round 1, N2: a row that carries only `role` must resolve everywhere ─────────────────

def test_resume_takes_the_role_from_a_role_only_registry_row(monkeypatch, tmp_path):
    from liteharness import resume_seat
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path / "h")
    (tmp_path / "h" / "agents").mkdir(parents=True)
    (tmp_path / "h" / "agents" / "seat-1.json").write_text(
        json.dumps({"agent_id": "seat-1", "role": "owner"}), encoding="utf-8")
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _: None)
    captured = {}
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: captured.update(kw))
    entry = {"agent_id": "seat-1", "name": "Alpha", "cwd": str(tmp_path),
             "convo_id": "11111111-2222-3333-4444-555555555555"}
    resume_seat._spawn_claude_resume(entry, convo_id=None, cwd=None, pane=None, direction=None, name=None,
                                     tier=None, model=None, backend=None, thinking_level=None,
                                     spawned_by=None, prompt=None)
    assert captured["tier"] == "owner", "a resumed owner must not come back as a builder"


# ── critic round 1, N3: role and tier are written together and cannot stay apart ───────────────

@pytest.fixture
def beat(tmp_path, monkeypatch):
    import os
    from liteharness import hooks
    for var in ("LITEHARNESS_ROLE", "LITEHARNESS_TIER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "get_agent_id", lambda: "seat")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    path = tmp_path / "agents" / "seat.json"
    path.parent.mkdir()

    def run(row):
        path.write_text(json.dumps({"agent_id": "seat", "cli": "claude-code", "model": "m",
                                    "session_pid": os.getpid(),
                                    "last_seen": datetime.now(timezone.utc).isoformat(), **row}))
        hooks.update_heartbeat("seat")
        stored = json.loads(path.read_text(encoding="utf-8"))
        return stored.get("role"), stored.get("tier")
    return run


def test_a_row_whose_role_and_tier_disagree_is_healed_to_the_role(beat, monkeypatch):
    monkeypatch.setenv("LITEHARNESS_ROLE", "owner")
    assert beat({"role": "owner", "tier": "worker"}) == ("owner", "leader")


def test_the_heal_does_not_depend_on_the_environment(beat):
    assert beat({"role": "owner", "tier": "worker"}) == ("owner", "leader")


@pytest.mark.parametrize("row,expected", [
    ({"tier": "leader"}, ("owner", "leader")),
    ({"role": "scout"}, ("scout", "thinker")),
    ({"tier": "worker"}, ("builder", "worker")),
    ({"role": "orchestrator", "tier": "orchestrator"}, ("orchestrator", "orchestrator")),
])
def test_a_one_sided_row_gains_the_other_spelling(beat, row, expected):
    assert beat(row) == expected


def test_a_word_outside_the_table_is_left_alone(beat):
    assert beat({"tier": "librarian"}) == (None, "librarian")


def test_the_stronger_environment_word_still_lifts_a_default_row(beat, monkeypatch):
    monkeypatch.setenv("LITEHARNESS_TIER", "reviewer")
    assert beat({"role": "builder", "tier": "worker"}) == ("critic", "reviewer")


def test_a_valid_role_overrules_a_stray_tier_word_but_two_unknown_words_are_left(beat):
    # Exactly what the roles.py docstring says: a valid role rewrites a tier that is some other word.
    assert beat({"role": "builder", "tier": "librarian"}) == ("builder", "worker")
    assert beat({"role": "librarian", "tier": "librarian"}) == ("librarian", "librarian")
