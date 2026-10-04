"""T916 — `liteharness spawn` refuses what it does not understand and never reports
a seat it did not verifiably start.

The bridge, the presence dir and the clock are all fakes: nothing here opens a
pane, types into a terminal or starts a session.
"""
import json
import os

import pytest

from liteharness import cli

MINTED = "pty-1-2"


@pytest.fixture
def split(monkeypatch, tmp_path):
    """A fake bridge whose /canvas/split mints MINTED; `on_launch` runs when the
    launch line is typed (that is when a real seat would boot and register)."""
    calls: list[tuple[str, str, dict]] = []
    state = {"on_launch": lambda: None}

    def fake(method, path, body=None):
        calls.append((method, path, body or {}))
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-26", "leafCount": 1}]}
        if path == "/canvas/split":
            return {"ok": True, "newLeafId": "leaf-9", "newSessionId": MINTED}
        if path == "/pty/write":
            state["on_launch"]()
        return {"ok": True}

    monkeypatch.setattr(cli, "_bridge_request", fake)
    # Never the real ~/.claude.json (item 5 has its own tests).
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    clock = iter(range(0, 10_000_000))  # 1s per call: a 90s wait is ~45 polls
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    monkeypatch.setenv("LITEHARNESS_AGENT_ID", "leader-under-test")
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    (tmp_path / "agents").mkdir()
    return calls, state, tmp_path / "agents"


def _presence(agents, agent_id, **fields):
    (agents / f"{agent_id}.json").write_text(
        json.dumps({"agent_id": agent_id, **fields}), encoding="utf-8"
    )


def _run(**kw):
    """cmd_spawn's exit code (0 when it returns).

    T0236-T4: a worker/leader spawn must be given --cwd, so tests that are not ABOUT the
    cwd rule pass the process cwd explicitly (`inherit=True` opts out, for exempt tiers)."""
    if not kw.pop("inherit", False) and "cwd" not in kw:
        kw["cwd"] = os.getcwd()
    try:
        cli.cmd_spawn(**kw)
    except SystemExit as exc:
        return exc.code
    return 0


def _typed_launch(calls) -> str:
    writes = [b["data"] for _, p, b in calls if p == "/pty/write"]
    assert len(writes) == 1, writes
    return writes[0]


# ── item 6: a split adopts ONLY the registration of the session it minted ──────

def test_split_adopts_only_its_own_registration(split, capsys):
    calls, state, agents = split

    def boot():
        # Another seat in the fleet registers in the same window (sorted FIRST),
        # then ours registers with the session this spawn minted.
        _presence(agents, "aaaa-foreign", canvas_session_id="pty-9-9", tier="worker")
        _presence(agents, "zzzz-ours", canvas_session_id=MINTED)

    state["on_launch"] = boot
    cli.cmd_spawn(split_mode=True, split_pane="canvas-pane-26", prompt="x", tier="reviewer")

    assert "Agent ID: zzzz-ours" in capsys.readouterr().out
    foreign = json.loads((agents / "aaaa-foreign.json").read_text(encoding="utf-8"))
    assert foreign["tier"] == "worker"  # never stamped with OUR spawn's tier
    assert foreign["canvas_session_id"] == "pty-9-9"
    ours = json.loads((agents / "zzzz-ours.json").read_text(encoding="utf-8"))
    assert ours["tier"] == "reviewer"


# ── item 2: --split with no --cwd opens in the SPAWNER's cwd ───────────────────

def test_split_without_cwd_opens_in_the_spawners_cwd_for_exempt_tiers(split, monkeypatch, tmp_path):
    calls, _, _ = split
    here = tmp_path / "spawner cwd"
    here.mkdir()
    monkeypatch.chdir(here)
    _run(split_mode=True, split_pane="canvas-pane-26", prompt="x", tier="reviewer", inherit=True)
    launch = _typed_launch(calls)
    assert f"Set-Location -LiteralPath '{os.path.abspath(os.getcwd())}'" in launch


def test_split_worker_without_cwd_is_refused_not_inherited(split, monkeypatch, tmp_path):
    calls, _, _ = split
    monkeypatch.chdir(tmp_path)
    assert _run(split_mode=True, split_pane="canvas-pane-26", prompt="x", inherit=True) == 2
    assert not [c for c in calls if c[1] == "/pty/write"]


def test_a_relative_cwd_is_typed_absolute(split, monkeypatch, tmp_path):
    calls, _, _ = split
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    _run(split_mode=True, split_pane="canvas-pane-26", cwd="sub", prompt="x")
    assert f"Set-Location -LiteralPath '{tmp_path / 'sub'}'" in _typed_launch(calls)


# ── item 8 / T916-A: the seat gets its identity and doctrine env on the split ──

def test_the_typed_split_launch_carries_tier_name_cognitive_and_parent(split, monkeypatch, tmp_path):
    calls, _, _ = split
    arch = tmp_path / "turing.md"
    arch.write_text("x", encoding="utf-8")
    from liteharness import prompts
    monkeypatch.setattr(prompts, "resolve_cognitive_file", lambda _n, _t: arch)
    _run(split_mode=True, split_pane="canvas-pane-26", prompt="x",
         name="PassLink-Turing", cognitive="turing", tier="worker")
    launch = _typed_launch(calls)
    for want in (
        "$env:LITEHARNESS_TIER='worker'",
        "$env:LITEHARNESS_REQUESTED_NAME='PassLink-Turing'",
        f"$env:LITEHARNESS_COGNITIVE_FILE='{arch}'",
        "$env:LITEHARNESS_SPAWNED_BY='leader-under-test'",
        f"$env:LITESUITE_CANVAS_SESSION='{MINTED}'",
    ):
        assert want in launch, want


# ── item 1 / T916-C: the parser refuses what it does not understand ───────────

@pytest.fixture
def no_spawn(monkeypatch):
    """cmd_spawn replaced by a recorder: any call is a spawn that happened."""
    spawned: list[dict] = []
    monkeypatch.setattr(cli, "cmd_spawn", lambda **kw: spawned.append(kw))
    return spawned


def _main(monkeypatch, *argv):
    monkeypatch.setattr(cli.sys, "argv", ["liteharness", "spawn", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        return exc.code
    return 0


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_usage_exits_zero_and_spawns_nothing(monkeypatch, capsys, no_spawn, flag):
    assert _main(monkeypatch, "--split", flag) == 0
    out = capsys.readouterr().out
    assert "Usage: liteharness spawn" in out
    assert no_spawn == []


def test_help_lists_every_flag_the_parser_accepts(capsys):
    usage = cli._spawn_usage()
    for flag, *_ in cli.SPAWN_FLAGS:
        assert f"  {flag}" in usage, flag


def test_an_unknown_flag_exits_nonzero_names_it_and_spawns_nothing(monkeypatch, capsys, no_spawn):
    assert _main(monkeypatch, "--split", "--nmae", "Carmack") == 2
    out = capsys.readouterr().out
    assert "'--nmae'" in out and "Nothing was spawned" in out
    assert no_spawn == []


def test_a_stray_positional_is_refused(monkeypatch, no_spawn):
    assert _main(monkeypatch, "--split", "Carmack") == 2
    assert no_spawn == []


@pytest.mark.parametrize("argv", [["--split", "--name"], ["--name", "--split"]])
def test_a_value_flag_without_its_value_is_refused(monkeypatch, capsys, no_spawn, argv):
    assert _main(monkeypatch, *argv) == 2
    assert "--name needs a value" in capsys.readouterr().out
    assert no_spawn == []


def test_help_as_a_prompt_value_is_a_value_not_a_flag(monkeypatch, no_spawn):
    assert _main(monkeypatch, "--prompt", "--help") == 0
    assert no_spawn == [{"prompt": "--help"}]


def test_control_a_valid_line_reaches_cmd_spawn_intact(monkeypatch, no_spawn):
    assert _main(monkeypatch, "--split", "--pane", "p-1", "--name", "Carmack",
                 "--tier", "thinker", "--cognitive", "carmack", "--cwd", "C:/x") == 0
    assert no_spawn == [{
        "split_mode": True, "split_pane": "p-1", "name": "Carmack",
        "tier": "thinker", "cognitive": "carmack", "cwd": "C:/x",
    }]


# ── item E: no registration is a FAILED spawn, never a reported success ───────

def test_no_registration_exits_nonzero_and_says_failed(split, capsys):
    assert _run(split_mode=True, split_pane="canvas-pane-26", prompt="x") == 1
    out = capsys.readouterr().out
    assert "SPAWN FAILED" in out and MINTED in out
    assert "Spawned" not in out  # no success headline before registration is known


def test_control_a_registration_reports_spawned_and_exits_zero(split, capsys):
    _, state, agents = split
    state["on_launch"] = lambda: _presence(agents, "zzzz-ours", canvas_session_id=MINTED)
    assert _run(split_mode=True, split_pane="canvas-pane-26", prompt="x") == 0
    out = capsys.readouterr().out
    assert "Spawned: registered as zzzz-ours" in out
    assert "SPAWN FAILED" not in out


# ── item 5: folder trust, extended only from a folder already trusted ─────────

@pytest.fixture
def claude_cfg(monkeypatch, tmp_path):
    """A private CLAUDE_CONFIG_DIR whose .claude.json trusts <tmp>/trusted."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    cfg = cfg_dir / ".claude.json"
    cfg.write_text(json.dumps({
        "numStartups": 7,
        "projects": {
            str(trusted).replace("\\", "/"): {"hasTrustDialogAccepted": True, "allowedTools": ["x"]},
            "C:/elsewhere": {"hasTrustDialogAccepted": False},
        },
    }, indent=2), encoding="utf-8")
    return cfg, trusted


def _key(p) -> str:
    return str(p).replace("\\", "/")


def test_a_worktree_under_a_trusted_folder_is_trusted_at_its_git_root(claude_cfg):
    cfg, trusted = claude_cfg
    wt = trusted / "repo" / ".worktrees" / "t1"
    (wt / "sub").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere", encoding="utf-8")  # a worktree's .git FILE
    assert cli._ensure_folder_trusted(str(wt / "sub")) is None
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["projects"][_key(wt)]["hasTrustDialogAccepted"] is True
    assert data["projects"][_key(trusted)] == {"hasTrustDialogAccepted": True, "allowedTools": ["x"]}
    assert data["numStartups"] == 7 and "C:/elsewhere" in data["projects"]
    assert not cfg.with_name(".claude.json.lock").exists()
    assert [p.name for p in cfg.parent.iterdir()] == [".claude.json"]  # no temp file left


def test_a_folder_claude_already_sees_as_trusted_is_not_rewritten(claude_cfg):
    cfg, trusted = claude_cfg
    (trusted / "plain" / "deeper").mkdir(parents=True)  # no .git: Claude walks up to `trusted`
    before = cfg.read_bytes()
    assert cli._ensure_folder_trusted(str(trusted / "plain" / "deeper")) is None
    assert cfg.read_bytes() == before


def test_no_trusted_folder_above_is_refused_and_nothing_is_written(claude_cfg, tmp_path):
    cfg, _ = claude_cfg
    lone = tmp_path / "untrusted"
    lone.mkdir()
    before = cfg.read_bytes()
    refusal = cli._ensure_folder_trusted(str(lone))
    assert refusal and "SPAWN REFUSED" in refusal and "Nothing was spawned" in refusal
    assert cfg.read_bytes() == before


def test_a_held_lock_is_waited_on_then_refused_never_raced(claude_cfg):
    cfg, trusted = claude_cfg
    repo = trusted / "repo"
    (repo / ".git").mkdir(parents=True)
    lock = cfg.with_name(".claude.json.lock")
    lock.mkdir()  # a live claude is saving
    before = cfg.read_bytes()
    refusal = cli._ensure_folder_trusted(str(repo))
    assert refusal and "stayed held" in refusal
    assert cfg.read_bytes() == before
    assert lock.is_dir()  # not ours to remove


def test_an_untrusted_cwd_spawns_nothing(claude_cfg, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "_bridge_request", lambda *a, **k: calls.append(a) or {"ok": True})
    lone = tmp_path / "untrusted"
    lone.mkdir()
    code = _run(split_mode=True, split_pane="canvas-pane-26", cwd=str(lone), prompt="x")
    assert code == 2
    assert calls == []


# ── item 3: the nudge is typed ONCE; a retry is Enter only; a stamp stops it ──

@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    clock = iter(range(0, 10_000_000))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    monkeypatch.setattr(cli, "_wait_brief_consumed", lambda _p, _t: True)


def test_a_running_turn_is_never_retyped_into(fast):
    writes, polls = [], iter([False, False, True])
    status = cli._deliver_prompt(writes.append, lambda: next(polls), None, "NUDGE", "FULL", 1)
    assert writes == ["NUDGE"]
    assert "turn confirmed on attempt 1" in status


def test_no_turn_retries_press_enter_only_and_say_unverified(fast):
    writes = []
    status = cli._deliver_prompt(writes.append, lambda: False, None, "NUDGE", "FULL", 1)
    assert writes == ["NUDGE", "", ""]  # the text once, then Enter, Enter
    assert status.startswith("UNVERIFIED")


def test_turn_seen_reads_only_this_spawns_stamp_after_t0(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    agents = tmp_path / "agents"
    agents.mkdir()
    t0 = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc).timestamp()
    old = datetime(2026, 9, 25, 19, 59, tzinfo=timezone.utc).isoformat()
    new = datetime(2026, 9, 25, 20, 1, tzinfo=timezone.utc).isoformat()
    _presence(agents, "other", provisional_id="pty-2", turn_seen_at=new)
    _presence(agents, "ours", provisional_id="pty-1", turn_seen_at=old)
    assert not cli._turn_seen_since("provisional_id", "pty-1", t0)  # stale stamp, other seat
    _presence(agents, "ours", provisional_id="pty-1", turn_seen_at=new)
    assert cli._turn_seen_since("provisional_id", "pty-1", t0)


def test_canvas_spawn_tags_the_seat_and_stops_on_its_stamp(fast, monkeypatch, tmp_path):
    from datetime import datetime, timezone
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    (tmp_path / "agents").mkdir()
    monkeypatch.setattr(cli, "is_inside_litesuite", lambda: True)
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    env, typed = {}, []

    def fake(method, path, body=None):
        if path == "/pty/create":
            env.update(body["env"])
            return {"session_id": "s-1"}
        if path == "/pty/write":
            typed.append(body["data"])
            _presence(tmp_path / "agents", "seat", provisional_id=env.get("LITEHARNESS_PROVISIONAL_ID"),
                      turn_seen_at=datetime.now(timezone.utc).isoformat())
        if path == "/pty/read":
            return {"ok": True, "output": ""}  # a busy pane's repaint bytes match nothing
        return {"ok": True}

    monkeypatch.setattr(cli, "_bridge_request", fake)
    cli.cmd_spawn(prompt="x", cwd=str(tmp_path))
    assert len(typed) == 1, typed  # base 6c944f9 typed the nudge 3x into the running turn
    assert env["LITEHARNESS_PROVISIONAL_ID"].startswith("canvas-spawn-")


# ── item 7: pty-kill resolves any id to its seat; kills the owning claude ─────

@pytest.fixture
def kill(monkeypatch, tmp_path):
    """Fake bridge, daemon and OS kill; a real presence dir under tmp."""
    from liteharness import pty_daemon
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    (tmp_path / "agents").mkdir()
    rec = {"bridge": [], "daemon": [], "os": []}
    monkeypatch.setattr(cli, "_bridge_request",
                        lambda m, p, b=None, **_kw: rec["bridge"].append((m, p)) or {"success": True})
    monkeypatch.setattr(pty_daemon, "is_daemon_running", lambda: True)
    monkeypatch.setattr(pty_daemon, "send_command",
                        lambda c: rec["daemon"].append(c["agent_id"]) or {"ok": True})
    monkeypatch.setattr(cli, "_kill_pid_tree", lambda pid: rec["os"].append(pid) or True,
                        raising=False)
    # Belt and braces: no code path in this file may reach a real taskkill/kill.
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda args, **k: rec["os"].append(("run", args)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: rec["os"].append(("kill", pid)))
    return rec, tmp_path / "agents"


def _dead_pid() -> int:
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _registered_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def test_a_bare_canvas_session_id_finds_its_seat(kill):
    rec, agents = kill
    _presence(agents, "uuid-seat", canvas_session_id="pty-14-179", session_pid=os.getpid(),
              pid=_dead_pid(), started_at=_registered_now())
    cli.cmd_pty_kill("pty-14-179")
    assert rec["os"] == [os.getpid()]  # the live owning session, not the dead hook pid
    assert ("DELETE", "/pty/pty-14-179") in rec["bridge"]
    assert rec["daemon"] == []  # a canvas session is not a daemon session


def test_a_dead_hook_pid_is_never_a_kill_target(kill):
    rec, agents = kill
    dead = _dead_pid()
    _presence(agents, "uuid-seat", canvas_session_id="pty-1-1", session_pid=os.getpid(),
              pid=dead, started_at=_registered_now())
    cli.cmd_pty_kill("uuid-seat")
    assert dead not in rec["os"] and rec["os"] == [os.getpid()]


def test_a_reused_session_pid_is_not_killed(kill):
    # The recorded session_pid now belongs to a process created AFTER the seat
    # registered: the number was reused, so it is not the seat's claude.
    rec, agents = kill
    _presence(agents, "uuid-seat", canvas_session_id="pty-1-1", session_pid=os.getpid(),
              started_at="2020-01-01T00:00:00+00:00", spawn_mode="split")
    cli.cmd_pty_kill("uuid-seat")
    assert rec["os"] == []
    assert ("DELETE", "/pty/pty-1-1") in rec["bridge"]


def test_a_daemon_id_resolves_through_provisional_id(kill):
    rec, agents = kill
    _presence(agents, "uuid-seat", provisional_id="pty-1790-42", session_pid=os.getpid(),
              started_at=_registered_now())
    cli.cmd_pty_kill("pty-1790-42")
    assert rec["os"] == [os.getpid()]
    assert rec["daemon"] == ["pty-1790-42"]


def test_nothing_resolvable_exits_nonzero(kill, monkeypatch):
    rec, _ = kill
    monkeypatch.setattr(cli, "_bridge_request", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(__import__("liteharness.pty_daemon").pty_daemon, "send_command",
                        lambda c: {"ok": False})
    with pytest.raises(SystemExit) as exc:
        cli.cmd_pty_kill("pty-9-9")
    assert exc.value.code == 1
    assert rec["os"] == []


# ── item 4: no second LIVE holder of a name ───────────────────────────────────

def _holder(agents, agent_id, name, registered_at, session_pid=None):
    from liteharness import naming
    _presence(agents, agent_id, last_seen=_registered_now(), registered_at=registered_at,
              session_pid=session_pid or os.getpid())
    naming.set_override(agent_id, name)


def test_a_live_holder_refuses_the_name_and_spawns_nothing(split, capsys):
    calls, _, agents = split
    _holder(agents, "live-carmack", "Carmack", _registered_now())
    assert _run(split_mode=True, split_pane="canvas-pane-26", prompt="x", name="carmack") == 2
    out = capsys.readouterr().out
    assert "SPAWN REFUSED" in out and "live-carmack" in out
    assert calls == []


def test_a_superseded_holder_is_moved_aside_and_the_name_is_applied(split, capsys, monkeypatch):
    from liteharness import naming
    _, state, agents = split
    # Same live process, registered again later under another id: the roster
    # (and _superseded_by_later_registration) calls the older row superseded.
    from datetime import datetime, timedelta, timezone
    newer = datetime.now(timezone.utc)
    # Mock a process old enough to own both records; real PID creation time
    # must precede their registration for the PID-reuse guard to trust them.
    older = newer - timedelta(minutes=5)
    from liteharness import hooks
    monkeypatch.setattr(hooks, "_record_belongs_to_process", lambda _id, _pid: True)
    _holder(agents, "old-row", "Carmack", older.isoformat())
    _presence(agents, "new-row", last_seen=_registered_now(),
              registered_at=newer.isoformat(), session_pid=os.getpid())
    state["on_launch"] = lambda: _presence(agents, "zzzz-ours", canvas_session_id=MINTED)
    assert _run(split_mode=True, split_pane="canvas-pane-26", prompt="x", name="Carmack") == 0
    assert not (agents / "old-row.json").exists()
    assert naming.get_name("zzzz-ours") == "Carmack"
    assert "Name: Carmack" in capsys.readouterr().out


# ── T916-D: a stale env bridge token is retried ONCE with the file's token ────

@pytest.fixture
def bridge_http(monkeypatch, tmp_path):
    """urlopen fake: accepts only `state['good']`; records every token sent."""
    import io
    import urllib.error
    (tmp_path / ".litesuite").mkdir()
    (tmp_path / ".litesuite" / "bridge-token").write_text("NEW\n", encoding="utf-8")
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    sent: list[str] = []
    state = {"good": "NEW", "fail_code": 401}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        token = req.get_header("Authorization").split(" ", 1)[1]
        sent.append(token)
        if token != state["good"]:
            raise urllib.error.HTTPError(req.full_url, state["fail_code"], "no", {},
                                         io.BytesIO(b'{"error": "unauthorized"}'))
        return Resp(b'{"ok": true}')

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return sent, state


def test_a_rejected_env_token_is_retried_once_with_the_file_token(bridge_http, monkeypatch):
    sent, _ = bridge_http
    monkeypatch.setenv("LITESUITE_BRIDGE_TOKEN", "OLD")
    assert cli._bridge_request("GET", "/context") == {"ok": True}
    assert sent == ["OLD", "NEW"]


def test_control_a_good_env_token_is_sent_once(bridge_http, monkeypatch):
    sent, state = bridge_http
    state["good"] = "OLD"
    monkeypatch.setenv("LITESUITE_BRIDGE_TOKEN", "OLD")
    assert cli._bridge_request("GET", "/context") == {"ok": True}
    assert sent == ["OLD"]


def test_no_retry_when_the_file_holds_the_same_token_or_the_error_is_not_401(bridge_http, monkeypatch):
    sent, state = bridge_http
    monkeypatch.setenv("LITESUITE_BRIDGE_TOKEN", "NEW")
    state["good"] = "NEVER"
    assert cli._bridge_request("GET", "/context")["ok"] is False
    assert sent == ["NEW"]  # same token on disk: nothing new to try
    sent.clear()
    monkeypatch.setenv("LITESUITE_BRIDGE_TOKEN", "OLD")
    state["fail_code"] = 409
    assert cli._bridge_request("POST", "/canvas/split", {})["ok"] is False
    assert sent == ["OLD"]  # a refusal is not an auth failure


# ── pane-arm: a split into a pane whose only leaf was pty-killed ──────────────

def test_a_split_into_a_dead_leaf_pane_is_a_loud_error_and_nothing_else(monkeypatch, tmp_path, capsys):
    # The bridge's answer measured live (Marquee e3948dfa, the orchestrator 5f453aea):
    # canvas-pane-37's only leaf had been pty-killed. Whether LiteSuite should
    # reuse that dead leaf is T917's call; this pins what the CLI does with
    # the refusal it gets today: the refusal text, exit 1, no other call.
    calls = []

    def fake(method, path, body=None):
        calls.append(path)
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-37", "leafCount": 1}]}
        return {"ok": False, "error": "Split failed - no result from store"}

    monkeypatch.setattr(cli, "_bridge_request", fake)
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    (tmp_path / "agents").mkdir()
    assert _run(split_mode=True, split_pane="canvas-pane-37", prompt="x", name="VanGogh") == 1
    out = capsys.readouterr().out
    assert "Error: split spawn failed — Split failed - no result from store." in out
    assert "Creation was not retried" in out
    assert calls[0].startswith("/context?")
    assert calls[1:] == ["/canvas/split"]  # no new pane or typed launch after uncertain failure
    assert list((tmp_path / "agents").iterdir()) == []


def test_a_save_that_lands_before_our_lock_survives_our_write(claude_cfg, monkeypatch):
    # Fix cycle 1, M-1: the reason the write re-reads under the lock. A live
    # seat saves ~/.claude.json between our first read and our lock; writing
    # the pre-lock snapshot would silently drop its save.
    cfg, trusted = claude_cfg
    repo = trusted / "repo"
    (repo / ".git").mkdir(parents=True)
    real_mkdir = os.mkdir

    def seat_saves_first(path, *a, **k):
        if str(path).endswith(".claude.json.lock"):
            data = json.loads(cfg.read_text(encoding="utf-8"))
            data["projects"]["C:/seat-saved"] = {"hasTrustDialogAccepted": True}
            data["numStartups"] = 8
            cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return real_mkdir(path, *a, **k)

    monkeypatch.setattr(cli.os, "mkdir", seat_saves_first)
    assert cli._ensure_folder_trusted(str(repo)) is None
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["projects"]["C:/seat-saved"] == {"hasTrustDialogAccepted": True}
    assert data["numStartups"] == 8
    assert data["projects"][_key(repo)]["hasTrustDialogAccepted"] is True


def test_a_resumed_seat_is_killed_by_its_session_pid(kill):
    # Fix cycle 1, M-2: a resume keeps the row's first started_at, but its
    # claude.exe is newer. registered_at is rebuilt on every registration, so
    # it is the anchor; started_at would call a live resumed seat "reused".
    rec, agents = kill
    _presence(agents, "uuid-seat", canvas_session_id="pty-1-1", session_pid=os.getpid(),
              started_at="2020-01-01T00:00:00+00:00", registered_at=_registered_now())
    cli.cmd_pty_kill("uuid-seat")
    assert rec["os"] == [os.getpid()]


def test_an_ambiguous_id_names_every_match_and_kills_nothing(kill, capsys):
    # Fix cycle 1, M-3: two seats carry the same canvas id (one inherited it).
    rec, agents = kill
    for seat in ("uuid-a", "uuid-b"):
        _presence(agents, seat, canvas_session_id="pty-3-3", session_pid=os.getpid(),
                  registered_at=_registered_now(), spawn_mode="split")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_pty_kill("pty-3-3")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "uuid-a" in out and "uuid-b" in out and "Nothing was killed" in out
    assert rec == {"bridge": [], "daemon": [], "os": []}


def test_a_pty_seat_never_closes_a_pane_it_only_inherited(kill):
    # A daemon child inherits its starter's LITESUITE_CANVAS_SESSION, which the
    # hook records. Killing the child must not DELETE that other seat's pane.
    rec, agents = kill
    _presence(agents, "uuid-pty", provisional_id="pty-1790-42", canvas_session_id="pty-3-foreign",
              spawn_mode="pty", session_pid=os.getpid(), registered_at=_registered_now())
    cli.cmd_pty_kill("pty-1790-42")
    assert rec["bridge"] == []
    assert rec["daemon"] == ["pty-1790-42"]


# ── fix cycle 1, F-2: a refused spawn creates no worktree ─────────────────────

def test_an_untrusted_worktree_spawn_is_refused_before_the_worktree(claude_cfg, monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(cli, "_handle_worktree", lambda d: made.append(d) or d)
    monkeypatch.setattr(cli, "_bridge_request", lambda *a, **k: {"ok": True})
    lone = tmp_path / "untrusted"
    lone.mkdir()
    assert _run(split_mode=True, cwd=str(lone), worktree=True, prompt="x") == 2
    assert made == []


def test_a_live_name_holder_is_refused_before_the_worktree(split, monkeypatch):
    calls, _, agents = split
    made = []
    monkeypatch.setattr(cli, "_handle_worktree", lambda d: made.append(d) or d)
    _holder(agents, "live-carmack", "Carmack", _registered_now())
    assert _run(split_mode=True, split_pane="canvas-pane-26", worktree=True, prompt="x", name="Carmack") == 2
    assert made == [] and calls == []


def test_control_a_trusted_worktree_spawn_trusts_the_new_worktree(claude_cfg, monkeypatch):
    cfg, trusted = claude_cfg
    (trusted / "repo" / ".git").mkdir(parents=True)
    wt = trusted / "repo" / ".worktrees" / "spawn-1"

    def make(d):
        (wt / ".git").parent.mkdir(parents=True, exist_ok=True)
        (wt / ".git").write_text("gitdir: x", encoding="utf-8")
        return str(wt)

    monkeypatch.setattr(cli, "_handle_worktree", make)
    monkeypatch.setattr(cli, "_bridge_request", lambda *a, **k: {"ok": False, "error": "stop here"})
    assert _run(split_mode=True, cwd=str(trusted / "repo"), worktree=True, prompt="x") == 1
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["projects"][_key(wt)]["hasTrustDialogAccepted"] is True


# ── F-1 ruling (the orchestrator 4d80ce96): drive roots and home are not trust sources ─

def _trust_only(cfg, *paths):
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["projects"] = {_key(p): {"hasTrustDialogAccepted": True} for p in paths}
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _repo(path):
    (path / ".git").mkdir(parents=True)
    return path


def test_a_trusted_drive_root_is_not_a_trust_source(claude_cfg, tmp_path):
    cfg, _ = claude_cfg
    _trust_only(cfg, tmp_path.anchor)  # e.g. C:/
    new_dir = _repo(tmp_path / "NewDir")
    before = cfg.read_bytes()
    refusal = cli._ensure_folder_trusted(str(new_dir))
    assert refusal and "SPAWN REFUSED" in refusal and "drive root" in refusal
    assert cfg.read_bytes() == before


def test_a_trusted_home_dir_is_not_a_trust_source(claude_cfg, tmp_path, monkeypatch):
    cfg, _ = claude_cfg
    home = tmp_path / "home"
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: home))
    _trust_only(cfg, home)
    child = _repo(home / "repo")
    before = cfg.read_bytes()
    assert "SPAWN REFUSED" in (cli._ensure_folder_trusted(str(child)) or "")
    assert cfg.read_bytes() == before


def test_a_trusted_project_folder_still_admits_its_children(claude_cfg, tmp_path):
    cfg, _ = claude_cfg
    projects = tmp_path / "Projects"
    _trust_only(cfg, tmp_path.anchor, projects)
    x = _repo(projects / "x")
    assert cli._ensure_folder_trusted(str(x)) is None
    assert json.loads(cfg.read_text(encoding="utf-8"))["projects"][_key(x)]["hasTrustDialogAccepted"] is True


def test_claude_s_own_unbounded_walk_to_a_trusted_root_writes_nothing(claude_cfg, tmp_path):
    # Not a repo, so Claude's own walk is unbounded and reaches the trusted
    # root: Claude will not prompt. We neither refuse nor write.
    cfg, _ = claude_cfg
    _trust_only(cfg, tmp_path.anchor)
    plain = tmp_path / "plain"
    plain.mkdir()
    before = cfg.read_bytes()
    assert cli._ensure_folder_trusted(str(plain)) is None
    assert cfg.read_bytes() == before


# ── B-1, fix cycle 2: a `--resume <id>` split is adopted BY ID ────────────────

RESUMED = "66666666-6666-4666-8666-666666666666"


def _hook_register(source: str, **env_extra):
    """Drive the REAL SessionStart register hook for RESUMED."""
    from pathlib import Path
    from unittest import mock
    from liteharness import hooks
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI"))}
    env.update(LITEHARNESS_CLI="claude-code", LITEHARNESS_MODEL="test-model",
               LITEHARNESS_NO_PREAMBLE="1", CLAUDE_CODE_SESSION_ID=RESUMED, **env_extra)
    with mock.patch.dict(os.environ, env, clear=True):
        hooks._apply_hook_context({
            "session_id": RESUMED, "source": source, "hook_event_name": "SessionStart",
            "transcript_path": str(Path("t") / f"{RESUMED}.jsonl"),
        })
        hooks.register_presence()


@pytest.fixture
def resumable(split, monkeypatch, tmp_path):
    """The split fixture plus a real hook: RESUMED registered earlier in pane
    pty-1-old, with its registration pushed into the past."""
    from liteharness import hooks
    monkeypatch.setattr(cli.config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: os.getpid())
    (tmp_path / "names").mkdir(exist_ok=True)
    _hook_register("startup", LITESUITE_CANVAS_SESSION="pty-1-old")
    row_path = tmp_path / "agents" / f"{RESUMED}.json"
    row = json.loads(row_path.read_text(encoding="utf-8"))
    row["registered_at"] = "2026-01-01T00:00:00+00:00"
    row_path.write_text(json.dumps(row), encoding="utf-8")
    return split, row_path


def test_a_resumed_split_is_adopted_and_repointed_at_its_new_pane(resumable, capsys):
    # Linus round 2, probe (i), end to end: the seat resumes into the new
    # split; the hook keeps its old pane (never-downgrade); the spawner adopts
    # it by the --resume id and writes the new pane itself.
    (calls, state, _), row_path = resumable
    state["on_launch"] = lambda: _hook_register("resume", LITESUITE_CANVAS_SESSION=MINTED)
    code = _run(split_mode=True, split_pane="canvas-pane-26", prompt="x",
                additional_args=f"--resume {RESUMED}")
    assert code == 0, capsys.readouterr().out
    assert f"Spawned: registered as {RESUMED}" in capsys.readouterr().out
    assert json.loads(row_path.read_text(encoding="utf-8"))["canvas_session_id"] == MINTED


def test_a_resume_row_registered_before_the_spawn_is_not_adopted(resumable, capsys):
    # The pre-restart row exists but never re-registers: it must not count.
    (calls, state, _), row_path = resumable
    assert _run(split_mode=True, split_pane="canvas-pane-26", prompt="x",
                additional_args=f"--resume {RESUMED}") == 1
    assert "SPAWN FAILED" in capsys.readouterr().out
    assert json.loads(row_path.read_text(encoding="utf-8"))["canvas_session_id"] == "pty-1-old"


def test_continue_and_a_bare_resume_name_no_seat_to_adopt():
    assert cli._resumed_id("--continue") is None
    assert cli._resumed_id("--resume") is None
    assert cli._resumed_id(f"--resume {RESUMED}") == RESUMED
