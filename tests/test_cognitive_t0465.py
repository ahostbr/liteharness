"""T0465 — the T0463-B --cognitive gate guards EVERY spawn route, and a resume carries it.

Two gaps the leader measured at dbbfd21:
  * `--cli litetui` returned before the unknown-name refusal, so it never ran there;
  * the resume branch never passed `cognitive` on, so `--cognitive` on a resume was dropped.

Fixtures (`lib`, `split`) come from the T0463-B tests: a private prompt library in tmp.
"""
import os

import pytest

from liteharness import agent_names, cli, owned_launch, resume_seat
from liteharness.agent_ownership import AgentSession
from liteharness.agent_store import AgentStore
from test_cognitive_t0463 import MINTED, _launch, lib, split  # noqa: F401 — fixtures

AID = '11111111-1111-4111-8111-111111111111'
BID = '22222222-2222-4222-8222-222222222222'
CID = '33333333-3333-4333-8333-333333333333'

ROUTES = {
    "fresh claude": dict(name="Muratori-T0465-W"),
    "fresh litetui": dict(spawn_cli="litetui", name="Muratori-T0465-W", tier="worker"),
    "resume": dict(resume_agent_id="Muratori-T0465-W"),
}


@pytest.fixture
def routes(split, monkeypatch):
    """Every route's launcher replaced by a recorder; the bridge is the T0463 fake."""
    launched: list[tuple[str, dict]] = []
    monkeypatch.setattr(cli, "_spawn_litetui_agent", lambda **kw: launched.append(("litetui", kw)))
    monkeypatch.setattr(resume_seat, "spawn_resume", lambda **kw: launched.append(("resume", kw)))
    calls, _, _ = split
    return calls, launched


def _spawn(**kw):
    kw.setdefault("cwd", os.getcwd())
    try:
        cli.cmd_spawn(split_mode=True, split_pane="canvas-pane-26", prompt="x", **kw)
    except SystemExit as exc:
        return exc.code
    return 0


@pytest.mark.parametrize("route", list(ROUTES))
def test_an_unknown_cognitive_is_refused_on_every_route(routes, capsys, route):
    calls, launched = routes
    assert _spawn(cognitive="muratori", **ROUTES[route]) == 2
    out = capsys.readouterr().out
    assert "SPAWN REFUSED: --cognitive 'muratori' matches no cognitive architecture" in out
    assert "carmack, default, gamma, holmes, linus, matas, poirot" in out
    assert "Nothing was spawned" in out
    assert calls == [] and launched == []


@pytest.mark.parametrize("route", ["fresh litetui", "resume"])
def test_control_a_name_only_miss_still_launches_on_every_route(routes, route):
    _, launched = routes
    assert _spawn(**ROUTES[route]) == 0
    assert len(launched) == 1 and launched[0][1].get("cognitive") is None


def test_a_valid_cognitive_reaches_the_resume_dispatcher(routes):
    _, launched = routes
    assert _spawn(cognitive="matas", resume_agent_id="QuietHelm") == 0
    assert launched == [("resume", launched[0][1])] and launched[0][1]["cognitive"] == "matas"


# ── a VALID --cognitive is delivered to the resumed seat, by the existing carriers ──

@pytest.fixture
def claude_seat(split, monkeypatch, tmp_path):
    """A named Claude seat with no owned LiteTUI home: the legacy Claude resume route."""
    def unknown():
        raise owned_launch.RootUnknown("uninstalled")
    monkeypatch.setattr(owned_launch, "data_root", unknown)
    entry = {"agent_id": AID, "convo_id": CID, "name": "ClaudeSeat", "backend": "claude",
             "cwd": str(tmp_path)}
    monkeypatch.setattr(agent_names, "resolve_name", lambda _n: entry)
    monkeypatch.setattr(resume_seat, "_registry", lambda _i: {})
    monkeypatch.setattr(cli, "_live_owner_pid", lambda _r: None)
    return split[0]


def test_a_claude_resume_launches_with_the_cognitive_file(claude_seat, lib, tmp_path):
    root, _ = lib
    matas = root / "pure" / "matas.md"
    _spawn(cognitive="matas", resume_agent_id="ClaudeSeat", cwd=None)  # no seat registers: exit 1
    launch = _launch(claude_seat)
    assert f"--resume {CID}" in launch
    assert f"$env:LITEHARNESS_COGNITIVE_FILE='{matas}'" in launch  # the hook's injection
    briefs = [p.read_text(encoding="utf-8") for p in (tmp_path / "briefs").glob("*.md")]
    assert len(briefs) == 1 and f"YOUR COGNITIVE ARCHITECTURE: {matas}" in briefs[0]  # the brief's


def test_control_a_claude_resume_without_cognitive_keeps_name_resolution(claude_seat):
    _spawn(resume_agent_id="ClaudeSeat", cwd=None)
    assert "LITEHARNESS_COGNITIVE_FILE" not in _launch(claude_seat)


@pytest.fixture
def owned_seat(lib, monkeypatch, tmp_path):
    """An owned LiteTUI home (as in test_owned_launch); records the bridge resolve body."""
    from liteharness import fleet_policy
    data = tmp_path / "data"
    data.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(data))
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", BID)
    monkeypatch.setattr(fleet_policy, "gate", lambda *a: None)
    monkeypatch.setattr(fleet_policy, "verify_seat", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_rename_canvas_seat", lambda *a: None)
    monkeypatch.setattr(cli, "_place_split", lambda *a, **kw: {"newSessionId": "fixture"})
    with AgentSession.create_fresh(AgentStore(data), name="QuietHelm", agent_id=AID,
                                   backend="codex", model="fixture", thinking_level="high") as session:
        target = session.conversation_directory(CID) / "convo.jsonl"
        target.parent.mkdir()
        target.write_text('{"type":"meta"}\n')
    resolved: list[dict] = []

    def bridge(method, path, body=None):
        if path == "/pty/list":
            return {"sessions": []}
        if path == "/harness/spawn/resolve":
            resolved.append(body)
            return {"ok": True, "agentId": AID, "liteTuiDataRoot": str(data), "request": {
                "shell": "litetui.exe", "args": ["--backend", "codex", "--model", "fixture",
                                                 "--thinking-level", "high"],
                "env": {"LITETUI_DATA_ROOT": str(data)}, "harnessAgentId": AID}}
        raise AssertionError(path)
    monkeypatch.setattr(cli, "_bridge_request", bridge)
    return project, resolved


def test_an_owned_litetui_resume_hands_cognitive_to_the_resolver(owned_seat):
    project, resolved = owned_seat
    cli.cmd_spawn(split_mode=True, resume_agent_id="QuietHelm", resume_convo_id=CID,
                  cwd=str(project), tier="worker", spawned_by=BID, cognitive="matas")
    assert len(resolved) == 1 and resolved[0]["cognitive"] == "matas"


def test_control_an_owned_litetui_resume_without_cognitive_sends_none(owned_seat):
    project, resolved = owned_seat
    cli.cmd_spawn(split_mode=True, resume_agent_id="QuietHelm", resume_convo_id=CID,
                  cwd=str(project), tier="worker", spawned_by=BID)
    assert len(resolved) == 1 and "cognitive" not in resolved[0]
