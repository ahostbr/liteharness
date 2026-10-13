"""T3: `liteharness spawn --owner <Title> --memory <dir>` boots a standing owner.

The memory folder holds SOUL.md (the charter), DOMAIN.md (what the owner knows) and NOW.md
(where it is right now). The spawn prompt is the charter plus an order to read NOW.md then
DOMAIN.md before anything. The seat is named by its title, and `--resume <Title>` is the
existing named-resume path. It all runs through cmd_spawn, so the harness spawn defaults apply.
"""
import pytest

from liteharness import cli, config, owner

MINTED = "11111111-2222-3333-4444-555555555555"
CHARTER = "You are a peer on the widgets team. Message colleagues directly."


def _folder(root, **files):
    memory = root / "mem"
    memory.mkdir()
    for name, text in {"SOUL.md": CHARTER, "DOMAIN.md": "widgets: how they are made",
                       "NOW.md": "# Updated: now", **files}.items():
        if text is not None:
            (memory / name).write_text(text, encoding="utf-8")
    return memory


@pytest.mark.parametrize("missing", ["SOUL.md", "DOMAIN.md", "NOW.md"])
def test_a_missing_file_is_refused_by_name(tmp_path, missing):
    memory = _folder(tmp_path, **{missing: None})
    with pytest.raises(owner.OwnerError) as exc:
        owner.owner_prompt("Alpha", memory)
    assert missing in str(exc.value)


def test_an_absent_folder_is_refused(tmp_path):
    with pytest.raises(owner.OwnerError, match="not a directory"):
        owner.owner_prompt("Alpha", tmp_path / "nowhere")


def test_an_empty_charter_is_refused(tmp_path):
    with pytest.raises(owner.OwnerError, match="SOUL.md is empty"):
        owner.owner_prompt("Alpha", _folder(tmp_path, **{"SOUL.md": "  \n"}))


def test_the_prompt_is_the_charter_then_read_now_then_domain(tmp_path):
    memory = _folder(tmp_path)
    prompt = owner.owner_prompt("Alpha", memory)
    assert prompt.startswith(CHARTER)
    now, domain = prompt.index("NOW.md"), prompt.index("DOMAIN.md")
    assert now < domain, "NOW.md is read first"
    assert str((memory / "NOW.md").resolve()) in prompt
    assert "before anything" in prompt.lower()


def test_the_parser_knows_the_owner_flags():
    kwargs = cli._parse_spawn_args(["--owner", "Alpha", "--memory", "somewhere", "--split"])
    assert kwargs == {"owner_title": "Alpha", "memory_dir": "somewhere", "split_mode": True}
    assert "--owner" in cli._spawn_usage() and "--memory" in cli._spawn_usage()


# ── cmd_spawn wiring: every launcher stubbed, the brief and launch line captured ───────────

@pytest.fixture
def seen(monkeypatch, tmp_path):
    for var in ("LITEHARNESS_SPAWN_MODEL", "LITEHARNESS_SPAWN_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    store = {"spawn_default_model": "claude-sonnet-5-5[1m]", "spawn_default_effort": "high"}
    monkeypatch.setattr(config, "load", lambda: dict(store))
    out: dict = {"launched": []}

    def bridge(method, path, body=None):
        body = body or {}
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-1", "leafCount": 1}]}
        if path == "/canvas/split":
            return {"ok": True, "newLeafId": "leaf-1", "newSessionId": MINTED}
        if path == "/pty/write":
            out["launched"].append(body["data"])
        return {"ok": True}

    def brief(text):
        out["brief"] = text
        return tmp_path / "brief.txt"

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    clock = iter(range(0, 10_000_000))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path / "h")
    (tmp_path / "h" / "agents").mkdir(parents=True)
    monkeypatch.setattr(cli, "_write_spawn_brief", brief)
    monkeypatch.setattr(cli, "is_inside_litesuite", lambda: True)
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "caller-under-test")
    return out


def _spawn(**kw):
    try:
        cli.cmd_spawn(**kw)
    except SystemExit as exc:
        return exc.code
    return None


def test_a_complete_folder_spawns_a_named_owner_through_cmd_spawn(seen, tmp_path):
    memory = _folder(tmp_path)
    # The stubbed pane never registers, so cmd_spawn ends at its registration wait (exit 1)
    # AFTER launching; exit 2 would be a refusal.
    assert _spawn(owner_title="Alpha", memory_dir=str(memory), cwd=str(tmp_path), split_mode=True) != 2
    brief = seen["brief"]
    assert CHARTER in brief and str((memory / "NOW.md").resolve()) in brief
    assert "YOUR ROLE: OWNER" in brief
    assert "registered as Alpha" in brief, "the seat is named by its title"
    (line,) = seen["launched"]
    assert "--model claude-sonnet-5-5[1m] " in line and "--effort high" in line, \
        "the harness spawn defaults must still apply"


@pytest.mark.parametrize("kw,why", [
    ({"memory_dir": "x"}, "--owner"),
    ({"owner_title": "Alpha"}, "--memory"),
])
def test_owner_and_memory_travel_together(seen, tmp_path, capsys, kw, why):
    assert _spawn(cwd=str(tmp_path), split_mode=True, **kw) == 2
    out = capsys.readouterr().out
    assert "SPAWN REFUSED" in out and why in out
    assert seen["launched"] == []


def test_a_missing_file_refuses_before_anything_launches(seen, tmp_path, capsys):
    memory = _folder(tmp_path, **{"NOW.md": None})
    assert _spawn(owner_title="Alpha", memory_dir=str(memory), cwd=str(tmp_path), split_mode=True) == 2
    out = capsys.readouterr().out
    assert "SPAWN REFUSED" in out and "NOW.md" in out
    assert seen["launched"] == [] and "brief" not in seen


@pytest.mark.parametrize("extra", [
    {"prompt": "something else"},
    {"tier": "builder"},
    {"spawn_cli": "codex"},
    {"cognitive": "feynman"},
])
def test_owner_flags_refuse_what_would_override_the_charter(seen, tmp_path, capsys, extra):
    memory = _folder(tmp_path)
    assert _spawn(owner_title="Alpha", memory_dir=str(memory), cwd=str(tmp_path), split_mode=True, **extra) == 2
    assert "SPAWN REFUSED: --owner takes its prompt from SOUL.md and its name from the title" in capsys.readouterr().out
    assert seen["launched"] == [] and "brief" not in seen


# The supported LiteTUI route is covered by test_owner_litetui_spawn.py.
def test_an_owner_spawn_still_needs_an_explicit_cwd(seen, tmp_path, capsys):
    memory = _folder(tmp_path)
    assert _spawn(owner_title="Alpha", memory_dir=str(memory), split_mode=True) == 2
    assert "--cwd" in capsys.readouterr().out


def test_owner_tier_given_as_owner_is_accepted(seen, tmp_path):
    memory = _folder(tmp_path)
    assert _spawn(owner_title="Alpha", memory_dir=str(memory), cwd=str(tmp_path), split_mode=True,
                  tier="owner") != 2
    assert seen["launched"]


def test_resume_by_title_is_the_existing_named_resume_path(monkeypatch, tmp_path):
    called = {}
    from liteharness import resume_seat
    monkeypatch.setattr(resume_seat, "spawn_resume", lambda **kw: called.update(kw))
    assert _spawn(resume_agent_id="Alpha", split_mode=True, cwd=str(tmp_path)) is None
    assert called["agent_id"] == "Alpha"


@pytest.mark.parametrize("title", ["", "a b\nc", "x" * 65, 'q"uote'])
def test_a_bad_title_is_refused(tmp_path, title):
    with pytest.raises(owner.OwnerError, match="title"):
        owner.owner_prompt(title, _folder(tmp_path))


# ── the shipped templates: the generic charter and the NOW/DOMAIN skeletons ─────────────────

def test_templates_ship_in_the_package_data():
    import re
    from pathlib import Path
    pyproject = (Path(cli.__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"owner_templates/\*\.md"', pyproject)
    for name in owner.OWNER_FILES:
        assert (Path(owner.__file__).parent / "owner_templates" / name).is_file()


def test_the_charter_template_is_generic_and_takes_a_project(tmp_path):
    raw = (owner.Path(owner.__file__).parent / "owner_templates" / "SOUL.md").read_text(encoding="utf-8")
    assert "{{project}}" in raw
    text = owner.render_template("SOUL.md", "widgets")
    assert "{{" not in text and "widgets" in text
    lowered = text.lower()
    for idea in ("peer", "directly", "evidence", "money", "undone"):
        assert idea in lowered, idea


def test_a_rendered_folder_boots_an_owner_and_its_now_passes_the_handoff_template(tmp_path):
    memory = tmp_path / "mem"
    memory.mkdir()
    for name in owner.OWNER_FILES:
        (memory / name).write_text(owner.render_template(name, "widgets", date="2026-01-01 00:00"),
                                   encoding="utf-8")
    assert "peer" in owner.owner_prompt("Alpha", memory).lower()
    assert owner.validate_now((memory / "NOW.md").read_text(encoding="utf-8")) == []


def test_an_unknown_template_is_refused():
    with pytest.raises(owner.OwnerError, match="template"):
        owner.render_template("EXTRA.md", "widgets")


# ── B1: the brief names the right doctrine file for each role ──────────────────────────────

DOCTRINE = {
    "orchestrator": "orchestrator-role.md",
    "owner": "preambles/leader-preamble.md",
    "builder": "preambles/worker-preamble.md",
    "scout": "preambles/thinker-preamble.md",
    "critic": "preambles/reviewer-preamble.md",
}


@pytest.mark.parametrize("role,filename", sorted(DOCTRINE.items()))
def test_the_doctrine_lookup_resolves_every_role(role, filename):
    assert cli._role_doctrine_file(role) == filename


@pytest.mark.parametrize("role,filename", sorted((r, f) for r, f in DOCTRINE.items() if r != "builder"))
def test_the_spawn_brief_names_the_roles_own_doctrine_file(seen, tmp_path, role, filename):
    # builder is the default role, whose brief carries no override line; its lookup is
    # asserted above.
    _spawn(tier=role, name="Probe", prompt="go", cwd=str(tmp_path), split_mode=True)
    brief = seen["brief"]
    assert f"YOUR ROLE: {role.upper()}." in brief
    assert f"your real doctrine is {filename} " in brief, brief
    assert "worker-preamble" not in brief or role == "builder"


def test_an_old_tier_word_gets_the_same_doctrine_file(seen, tmp_path):
    _spawn(tier="leader", name="Probe", prompt="go", cwd=str(tmp_path), split_mode=True)
    assert "your real doctrine is preambles/leader-preamble.md " in seen["brief"]


# ── N4: an owner's title must not be taken for a polymath name ─────────────────────────────

@pytest.fixture
def polymath(monkeypatch, tmp_path):
    """Any name resolves to a cognitive file, as 'Feynman' or 'Linus' would in a real library."""
    from liteharness import prompts
    arch = tmp_path / "feynman.md"
    arch.write_text("# polymath architecture\n", encoding="utf-8")
    asked = []
    monkeypatch.setattr(prompts, "resolve_cognitive_file", lambda name, *a, **k: asked.append(name) or arch)
    return asked


def test_control_a_plain_named_spawn_does_pick_up_a_polymath_by_name(seen, tmp_path, polymath):
    _spawn(tier="builder", name="Feynman", prompt="go", cwd=str(tmp_path), split_mode=True)
    assert polymath == ["Feynman"] and "COGNITIVE ARCHITECTURE" in seen["brief"]


def test_an_owner_titled_like_a_polymath_gets_no_architecture(seen, tmp_path, polymath):
    memory = _folder(tmp_path)
    _spawn(owner_title="Feynman", memory_dir=str(memory), cwd=str(tmp_path), split_mode=True)
    assert polymath == [], "the owner path must not run the name-to-architecture resolver"
    assert "COGNITIVE ARCHITECTURE" not in seen["brief"]
    assert CHARTER in seen["brief"]
