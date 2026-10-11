"""T0463-B — a spawn never boots a GENERIC seat by accident.

Three defects, measured by the leader against `resolve_cognitive_file(name, "worker")`:
  * `--cognitive <unknown>` only printed a WARNING and launched anyway;
  * `pure/` was never searched, so matas and poirot (files only there) resolved to None;
  * a card-suffixed `--name` (Gamma-T0463-L, Carmack-T0306S-W2) resolved to None.

Every test runs against a private prompt library and user overlay in tmp, so no verdict
here depends on what the LiteSuite sibling checkout happens to ship.
"""
import os

import pytest

from liteharness import cli, prompts
from liteharness.prompts import resolve_cognitive_file

MINTED = "pty-1-2"


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def lib(monkeypatch, tmp_path):
    """A private shipped library and an empty user overlay."""
    root = tmp_path / "prompts" / "cognitive-architectures"
    for rel in ("workers/linus.md", "workers/carmack.md", "workers/gamma.md",
                "thinkers/holmes.md", "orchestrator/default.md",
                "pure/linus.md", "pure/matas.md", "pure/poirot.md"):
        _write(root / rel, f"# {rel}\n")
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    monkeypatch.setenv("LITEHARNESS_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("LITEHARNESS_USER_PROMPTS_DIR", str(overlay))
    return root, overlay / "cognitive-architectures"


# ── b) pure/ is searched, and searched LAST ──────────────────────────────────

@pytest.mark.parametrize("name", ["matas", "poirot"])
def test_a_pure_only_architecture_resolves(lib, name):
    root, _ = lib
    assert resolve_cognitive_file(name, "worker") == root / "pure" / f"{name}.md"


def test_control_a_tier_file_beats_the_pure_file_of_the_same_name(lib):
    root, _ = lib
    assert resolve_cognitive_file("linus", "worker") == root / "workers" / "linus.md"


def test_an_overlay_pure_file_does_not_shadow_a_shipped_tier_file(lib):
    root, overlay = lib
    _write(overlay / "pure" / "linus.md")
    assert resolve_cognitive_file("linus", "reviewer") == root / "workers" / "linus.md"


def test_an_overlay_pure_file_is_found_when_nothing_else_matches(lib):
    _, overlay = lib
    own = _write(overlay / "pure" / "muratori.md")
    assert resolve_cognitive_file("muratori", "worker") == own


# ── c) a card-suffixed --name resolves its polymath ──────────────────────────

@pytest.mark.parametrize("name, want", [
    ("Gamma-T0463-L", "workers/gamma.md"),
    ("Carmack-T0306S-W2", "workers/carmack.md"),
    ("Holmes-T0463-W", "thinkers/holmes.md"),
    ("Matas-T12", "pure/matas.md"),
])
def test_a_card_suffix_is_stripped_on_a_full_name_miss(lib, name, want):
    root, _ = lib
    assert resolve_cognitive_file(name, "worker") == root / want


def test_a_full_name_file_wins_over_the_stripped_name(lib):
    root, _ = lib
    own = _write(root / "workers" / "gamma-t0463-l.md")
    assert resolve_cognitive_file("Gamma-T0463-L", "worker") == own


@pytest.mark.parametrize("name", ["BottleRender", "OpenBolt", "PassLink-Turing", "Nobody-T0463-W"])
def test_plain_and_unknown_fleet_names_still_miss_quietly(lib, name):
    assert resolve_cognitive_file(name, "worker") is None


@pytest.mark.parametrize("name, stem", [
    ("Gamma-T0463-L", "Gamma"), ("Carmack-T0306S-W2", "Carmack"), ("Holmes-T0463-W", "Holmes"),
    ("Shannon-T0463B-W", "Shannon"), ("Linus-T7", "Linus"),
    ("BottleRender", None), ("PassLink-Turing", None), ("T0463", None), ("-T1-W", None),
])
def test_card_suffix_stem(name, stem):
    assert prompts.card_suffix_stem(name) == stem


# ── d) the orchestrator default.md fallback is unchanged ─────────────────────

@pytest.mark.parametrize("name", ["Gustave", "Nobody-T0463-L"])
def test_an_unknown_orchestrator_still_falls_back_to_default(lib, name):
    root, _ = lib
    assert resolve_cognitive_file(name, "orchestrator") == root / "orchestrator" / "default.md"


def test_a_worker_never_takes_the_orchestrator_default(lib):
    assert resolve_cognitive_file("Gustave", "worker") is None


# ── the valid-name list a refusal prints ─────────────────────────────────────

def test_valid_names_are_the_sorted_union_over_overlay_and_library(lib):
    _, overlay = lib
    _write(overlay / "workers" / "muratori.md")
    assert prompts.list_cognitive_names() == [
        "carmack", "default", "gamma", "holmes", "linus", "matas", "muratori", "poirot",
    ]


# ── a) spawn REFUSES an explicit --cognitive that resolves to nothing ─────────

@pytest.fixture
def split(monkeypatch, tmp_path, lib):
    """Fake bridge + presence dir (as in test_spawn_t916); records every call."""
    calls: list[tuple[str, str, dict]] = []
    made: list[str] = []

    def fake(method, path, body=None):
        calls.append((method, path, body or {}))
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-26", "leafCount": 1}]}
        if path == "/canvas/split":
            return {"ok": True, "newLeafId": "leaf-9", "newSessionId": MINTED}
        return {"ok": True}

    monkeypatch.setattr(cli, "_bridge_request", fake)
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    monkeypatch.setattr(cli, "_handle_worktree", lambda d: made.append(d) or d)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    clock = iter(range(0, 10_000_000))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "leader-under-test")
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    (tmp_path / "agents").mkdir()
    return calls, made, tmp_path / "agents"


def _run(**kw):
    kw.setdefault("cwd", os.getcwd())
    try:
        cli.cmd_spawn(split_mode=True, split_pane="canvas-pane-26", prompt="x", **kw)
    except SystemExit as exc:
        return exc.code
    return 0


def _launch(calls) -> str:
    writes = [b["data"] for _, p, b in calls if p == "/pty/write"]
    assert len(writes) == 1, writes
    return writes[0]


def test_an_unknown_cognitive_is_refused_before_anything_launches(split, capsys):
    calls, made, agents = split
    assert _run(cognitive="muratori", name="Muratori-T0463-W", worktree=True) == 2
    out = capsys.readouterr().out
    assert "SPAWN REFUSED" in out and "'muratori'" in out and "Nothing was spawned" in out
    assert "carmack, default, gamma, holmes, linus, matas, poirot" in out
    assert calls == [] and made == []
    assert list(agents.iterdir()) == []


def test_an_unknown_cognitive_never_evicts_a_name_holder(split, monkeypatch):
    calls, _, agents = split
    evicted = []
    monkeypatch.setattr(cli, "_evict_agent_records", lambda h: evicted.append(h) or "x")
    from liteharness import naming
    monkeypatch.setattr(naming, "is_name_taken", lambda _n: "old-row")
    monkeypatch.setattr(cli, "_superseded_by_later_registration", lambda _i, _d: True)
    assert _run(cognitive="sweeney", name="Sweeney") == 2
    assert evicted == [] and calls == []


def test_a_raising_resolution_with_cognitive_is_refused(split, monkeypatch, capsys):
    calls, made, _ = split

    def boom(_n, _t):
        raise OSError("library unreadable")

    monkeypatch.setattr(prompts, "resolve_cognitive_file", boom)
    assert _run(cognitive="linus", worktree=True) == 2
    out = capsys.readouterr().out
    assert "SPAWN REFUSED" in out and "library unreadable" in out
    assert calls == [] and made == []


def test_control_a_raising_resolution_without_cognitive_still_spawns(split, monkeypatch):
    calls, _, _ = split

    def boom(_n, _t):
        raise OSError("library unreadable")

    monkeypatch.setattr(prompts, "resolve_cognitive_file", boom)
    _run(name="BottleRender")
    assert "LITEHARNESS_COGNITIVE_FILE" not in _launch(calls)


def test_control_a_name_only_miss_is_never_a_refusal(split):
    calls, _, _ = split
    _run(name="BottleRender")
    assert "LITEHARNESS_COGNITIVE_FILE" not in _launch(calls)


def test_a_pure_only_cognitive_reaches_the_launch(split, lib):
    calls, _, _ = split
    root, _ = lib
    _run(cognitive="matas", name="Matas-T0463-W")
    assert f"$env:LITEHARNESS_COGNITIVE_FILE='{root / 'pure' / 'matas.md'}'" in _launch(calls)


def test_a_card_suffixed_name_alone_reaches_its_polymath(split, lib):
    calls, _, _ = split
    root, _ = lib
    _run(name="Holmes-T0463-W")
    assert f"$env:LITEHARNESS_COGNITIVE_FILE='{root / 'thinkers' / 'holmes.md'}'" in _launch(calls)
