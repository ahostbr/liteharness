"""T1025 — the fleet model/thinking floor is enforced in the spawn path.

the user 2026-09-26: "5.6 on med thinking is what did this ... this MUST NEVER happen
again" and "astra IS NOT THE ONLY gpt-6 model". Every path here is a tmp path
(conftest): no live policy, no live codex cache, no real codex, no real seat.
"""
import json
import os
import re
import sys

import pytest

from liteharness import cli, fleet_policy


def _write_policy(tmp_path, monkeypatch, policy):
    path = tmp_path / "fleet-policy.json"
    path.write_text(policy if isinstance(policy, str) else json.dumps(policy), encoding="utf-8")
    monkeypatch.setenv("LITESUITE_FLEET_POLICY", str(path))
    return path


# ── (1) pre-launch floor ────────────────────────────────────────────────────────

def test_codex_56_sol_medium_is_refused_and_names_the_floor():
    why = fleet_policy.check("codex", "gpt-5.6-sol", "medium")
    assert why.startswith("SPAWN REFUSED")
    assert "model 'gpt-5.6-sol' is below gpt-6.1-sol" in why
    assert "thinking_level 'medium' is below high" in why
    assert "model >= gpt-6.1-sol and thinking_level >= high" in why


@pytest.mark.parametrize("model,level", [("gpt-6.1-sol", "high"), ("gpt-6-astra", "xhigh"), ("gpt-6.1-sol", "ultra")])
def test_at_or_above_the_floor_passes(model, level):
    assert fleet_policy.check("codex", model, level) is None


@pytest.mark.parametrize("backend,model,level,expect", [
    ("codex", "gpt-6-nova", "high", "is not in the codex list"),        # unlisted model
    ("codex", "gpt-6-sol", None, "thinking_level None is not a listed level"),
    ("codex", "gpt-6-sol", "default", "thinking_level 'default' is not a listed level"),
    ("codex", None, "high", "model None is not in the codex list"),      # pin would decide
    (None, "gpt-5.6-sol", "high", "is below gpt-6.1-sol"),                  # gpt- prefix governs
    ("codex", "gpt-6.1-sol", "medium", "thinking_level 'medium' is below high"),   # listed now, still needs the level
])
def test_missing_or_unlisted_values_are_refused(backend, model, level, expect):
    why = fleet_policy.check(backend, model, level)
    assert why and expect in why, why


def test_claude_and_local_models_are_not_governed():
    assert fleet_policy.check(None, "opus", None) is None
    assert fleet_policy.check("lmstudio", "qwen3-27b", "medium") is None


def test_exempt_prefixes_and_the_codex_backend():
    """F4 + N1: gpt-oss-120b is exempt on any backend (LiteTUI test_free_tier.py:118),
    and a codex backend governs every model it runs (F3)."""
    policy = fleet_policy.DEFAULT_POLICY
    assert fleet_policy.floor_for(policy, "local", "gpt-oss-120b") is None
    assert fleet_policy.check("local", "gpt-oss-120b", "medium") is None
    assert fleet_policy.floor_for(policy, None, "gpt-oss-120b") is None
    assert fleet_policy.floor_for(policy, " Codex ", "gpt-oss-120b")[0] == "codex"
    assert fleet_policy.check("custom", "gpt-oss-120b", None) is None


@pytest.mark.parametrize("model", ["gpt-oss-120b", "gpt-oss-but-actually-5.6"])
def test_the_exemption_never_applies_under_a_codex_backend(model):
    """the orchestrator 9f69b387: the exemption reads the MODEL NAME only, and a named codex
    backend governs every model, so a gpt-oss- name cannot spoof its way past it."""
    why = fleet_policy.check("codex", model, "high")
    assert why and f"model {model!r} is not in the codex list" in why, why


@pytest.mark.parametrize("backend", ["custom", "cline"])
def test_a_gpt_model_is_governed_on_every_backend(backend):
    """N1 (Knuth7 2b7d04d5): the floor is about the model, not the transport.
    LiteTUI's custom backend can be OpenAI itself (llm_backend.py:2257-2266)."""
    why = fleet_policy.check(backend, "gpt-5.6-sol", "medium")
    assert why and "is below gpt-6.1-sol" in why and "is below high" in why, why
    assert fleet_policy.check(backend, "gpt-6.1-sol", "high") is None


def test_an_unlisted_model_names_the_one_edit_that_allows_it(tmp_path, monkeypatch):
    """the orchestrator af053094: a new gpt- model is one edit to the policy file in effect."""
    absent = tmp_path / "fleet-policy.absent.json"
    monkeypatch.setenv("LITESUITE_FLEET_POLICY", str(absent))
    why = fleet_policy.check("codex", "gpt-6.2-sol", "high")   # a model NOT in the list (gpt-6.1-sol now is)
    assert "Nothing was substituted" in why and "floors.codex.models" in why, why
    assert f"the built-in default; create {absent} to override" in why, why
    assert "ordered low->high, so insert it by rank (the floor is min_model gpt-6.1-sol)" in why, why
    path = _write_policy(tmp_path, monkeypatch, fleet_policy.DEFAULT_POLICY)
    why = fleet_policy.check("codex", "gpt-6.2-sol", "high")
    assert f"floors.codex.models in {path}." in why and "create" not in why, why


def test_model_and_level_are_trimmed_like_resolve_spawn_does():
    assert fleet_policy.check("codex", " gpt-6.1-sol ", " high ") is None
    assert "thinking_level None" in fleet_policy.check("codex", "gpt-6.1-sol", "   ")


def test_missing_policy_file_uses_the_built_in_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("LITESUITE_FLEET_POLICY", str(tmp_path / "nope.json"))
    policy, where = fleet_policy.load()
    assert policy is fleet_policy.DEFAULT_POLICY and "built-in default" in where
    assert "Policy: built-in default" in fleet_policy.check("codex", "gpt-5.6-sol", "high")


def test_current_sol_is_the_default_floor_when_policy_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("LITESUITE_FLEET_POLICY", str(tmp_path / "absent.json"))
    policy, where = fleet_policy.load()
    assert "built-in default" in where
    assert policy["floors"]["codex"]["min_model"] == "gpt-6.1-sol"
    assert fleet_policy.check("codex", "gpt-6.1-sol", "high") is None


def test_banned_sol_is_unlisted_even_at_ultra_when_policy_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("LITESUITE_FLEET_POLICY", str(tmp_path / "absent.json"))
    why = fleet_policy.check("codex", "gpt-6-sol", "ultra")
    assert why and "model 'gpt-6-sol' is not in the codex list" in why
    assert "Nothing was spawned" in why


def test_installed_policy_file_is_what_is_enforced(tmp_path, monkeypatch):
    lowered = json.loads(json.dumps(fleet_policy.DEFAULT_POLICY))
    lowered["floors"]["codex"].update(min_model="gpt-5.6-sol", min_thinking_level="medium")
    path = _write_policy(tmp_path, monkeypatch, lowered)
    assert fleet_policy.check("codex", "gpt-5.6-sol", "medium") is None
    assert f"Policy: {path}" in fleet_policy.check("codex", "gpt-5.6-luna", "medium")


@pytest.mark.parametrize("content", [
    "{not json",
    {"floors": {}},
    {"floors": {"codex": {"models": ["a"], "min_model": "b", "thinking_levels": ["high"], "min_thinking_level": "high"}}},
    {"floors": {"codex": {**fleet_policy.DEFAULT_POLICY["floors"]["codex"], "exempt_prefixes": "gpt-oss-"}}},
])
def test_malformed_policy_refuses_every_spawn(tmp_path, monkeypatch, content):
    path = _write_policy(tmp_path, monkeypatch, content)
    for backend, model in (("codex", "gpt-6-sol"), (None, "opus")):
        why = fleet_policy.check(backend, model, "high")
        assert why and why.startswith("SPAWN REFUSED") and str(path) in why, why


# ── (4) a missing model is refetched, then refused by name; never substituted ──

def _cache(tmp_path, slugs):
    path = tmp_path / "models_cache.json"
    path.write_text(json.dumps({"models": [{"slug": s} for s in slugs]}), encoding="utf-8")
    return path


def test_listed_model_needs_no_refetch(tmp_path):
    def refetch():
        raise AssertionError("refetched although the cache lists the model")
    assert fleet_policy.check_available("gpt-6-sol", _cache(tmp_path, ["gpt-6-sol"]), refetch) is None


def test_stale_cache_is_refetched_and_the_fresh_list_wins(tmp_path):
    calls = []
    def refetch():
        calls.append(1)
        return {"gpt-6-astra", "gpt-6-sol"}
    stale = _cache(tmp_path, ["gpt-6-astra", "gpt-5.6-sol"])  # 12:35's cache: no gpt-6-sol
    assert fleet_policy.check_available("gpt-6-sol", stale, refetch) is None
    assert calls == [1]


def test_model_missing_after_refetch_is_refused_by_name(tmp_path):
    why = fleet_policy.check_available("gpt-6-sol", _cache(tmp_path, ["gpt-6-astra"]),
                                       lambda: {"gpt-6-astra", "gpt-5.6-sol"})
    assert "model 'gpt-6-sol' is not in the codex model list even after a refetch" in why
    assert "Nothing was substituted" in why


def test_failed_refetch_is_a_refusal(tmp_path):
    def refetch():
        raise RuntimeError("codex not found")
    why = fleet_policy.check_available("gpt-6-sol", tmp_path / "absent.json", refetch)
    assert "refetch failed (codex not found)" in why and "gpt-6-sol" in why


def test_gate_runs_the_list_check_only_for_governed_seats(tmp_path):
    cache = _cache(tmp_path, ["gpt-6-astra"])
    assert "even after a refetch" in fleet_policy.gate("codex", "gpt-6.1-sol", "high", cache=cache,
                                                      refetch=lambda: {"gpt-6-astra"})
    def refetch():
        raise AssertionError("a claude seat has no codex list")
    assert fleet_policy.gate(None, "opus", None, cache=cache, refetch=refetch) is None


# ── (2) post-launch: what the SEAT resolved ─────────────────────────────────────

def _row(root, agent_id, **fields):
    (root / "agents").mkdir(exist_ok=True)
    (root / "agents" / f"{agent_id}.json").write_text(json.dumps({"agent_id": agent_id, **fields}),
                                                      encoding="utf-8")


class _Clock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def sleep(self, s):
        self.t += s


def test_seat_that_resolved_below_the_floor_fails_loudly(tmp_path):
    # The T1004 pin: launched with --model gpt-6-sol, the seat resolved gpt-5.6-sol.
    _row(tmp_path, "seat-1", model="gpt-5.6-sol", thinking_level="medium")
    clock = _Clock()
    why = fleet_policy.verify_seat("seat-1", tmp_path, sleep=clock.sleep, clock=clock)
    assert why.startswith("SEAT FAILED FLOOR: seat-1 resolved model=gpt-5.6-sol thinking_level=medium")
    assert "is below gpt-6.1-sol" in why


def test_seat_at_the_floor_verifies(tmp_path):
    _row(tmp_path, "seat-2", model="gpt-6.1-sol", thinking_level="high")
    clock = _Clock()
    assert fleet_policy.verify_seat("seat-2", tmp_path, sleep=clock.sleep, clock=clock) is None


def test_seat_waits_for_its_registration(tmp_path):
    clock = _Clock()
    polls = []
    def sleep(s):
        polls.append(s)
        clock.sleep(s)
        if len(polls) == 3:  # registers on the 4th read
            _row(tmp_path, "seat-3", model="gpt-6-astra", thinking_level="xhigh")
    assert fleet_policy.verify_seat("seat-3", tmp_path, sleep=sleep, clock=clock) is None
    assert len(polls) == 3


def test_seat_that_never_reports_thinking_is_refused(tmp_path):
    _row(tmp_path, "seat-4", model="gpt-6-sol")  # an old LiteTUI: model, no level
    clock = _Clock()
    why = fleet_policy.verify_seat("seat-4", tmp_path, wait=10, sleep=clock.sleep, clock=clock)
    assert "did not report its effective model and thinking_level within 10s" in why
    assert "thinking_level='<absent>'" in why


@pytest.mark.parametrize("model", ["o4-mini", "codex-mini-latest"])
def test_a_codex_seat_on_an_unlisted_model_is_refused(tmp_path, model):
    """F3: the spawner knows the backend; the seat's model alone would read as ungoverned."""
    _row(tmp_path, "seat-8", model=model, thinking_level="high")
    clock = _Clock()
    why = fleet_policy.verify_seat("seat-8", tmp_path, backend="codex", sleep=clock.sleep, clock=clock)
    assert why and f"model {model!r} is not in the codex list" in why, why


@pytest.mark.parametrize("asked_backend,row,expect", [
    # T0095 (Marquee ruling): the STRICTER of the spawner's and the reported backend governs.
    ("local", {"backend": "codex", "model": "o4-mini", "thinking_level": "high"},
     "model 'o4-mini' is not in the codex list"),        # pin turned local into codex/o4-mini
    ("codex", {"backend": "local", "model": "o4-mini", "thinking_level": "high"},
     "model 'o4-mini' is not in the codex list"),        # a lie or mismatch never loosens
    ("local", {"backend": "codex", "model": "codex-mini-latest", "thinking_level": "high"},
     "model 'codex-mini-latest' is not in the codex list"),
    ("local", {"backend": "codex", "model": "gpt-6-sol", "thinking_level": "low"},
     "thinking_level 'low' is below high"),
    (None, {"backend": "codex", "model": "o4-mini", "thinking_level": "high"},
     "model 'o4-mini' is not in the codex list"),        # the no-backend door
])
def test_the_stricter_of_spawner_and_reported_backend_governs(tmp_path, asked_backend, row, expect):
    _row(tmp_path, "seat-17", **row)
    clock = _Clock()
    why = fleet_policy.verify_seat("seat-17", tmp_path, backend=asked_backend, sleep=clock.sleep, clock=clock)
    assert why and why.startswith("SEAT FAILED FLOOR: seat-17") and expect in why, why


def test_a_truly_local_seat_still_passes(tmp_path):
    _row(tmp_path, "seat-18", backend="local", model="gpt-oss-120b")
    clock = _Clock()
    assert fleet_policy.verify_seat("seat-18", tmp_path, backend="local", allow_silent=True,
                                    sleep=clock.sleep, clock=clock) is None


@pytest.mark.parametrize("row", [None, {"model": "unknown"}])
def test_allow_silent_passes_a_seat_that_never_reports_a_model(tmp_path, row):
    """N3: an ungoverned request whose seat never reports (no model loaded) passes."""
    if row is not None:
        _row(tmp_path, "seat-10", **row)
    clock = _Clock()
    assert fleet_policy.verify_seat("seat-10", tmp_path, wait=10, allow_silent=True,
                                    sleep=clock.sleep, clock=clock) is None
    clock = _Clock()
    assert "did not report" in fleet_policy.verify_seat("seat-10", tmp_path, wait=10,
                                                         sleep=clock.sleep, clock=clock)


def test_allow_silent_never_excuses_a_reported_governed_model(tmp_path):
    """N3 (Knuth7 dc1bbb3b): the pin turns a local request into codex 5.6-sol/medium."""
    _row(tmp_path, "seat-11", model="gpt-5.6-sol", thinking_level="medium")
    clock = _Clock()
    why = fleet_policy.verify_seat("seat-11", tmp_path, allow_silent=True, sleep=clock.sleep, clock=clock)
    assert why and "is below gpt-6.1-sol" in why, why
    _row(tmp_path, "seat-12", model="gpt-6-sol")  # governed, but never reports its level
    clock = _Clock()
    why = fleet_policy.verify_seat("seat-12", tmp_path, wait=10, allow_silent=True,
                                   sleep=clock.sleep, clock=clock)
    assert why and "did not report" in why, why


@pytest.mark.parametrize("asked,row,expect", [
    # N5: card T1025 item (2) -- the spawn fails on MISMATCH, not only below the floor.
    ({"expect_model": "gpt-6-sol"}, {"model": "o4-mini", "thinking_level": "high"},
     "asked for model=gpt-6-sol thinking_level=<any> and resolved model=o4-mini"),
    ({"expect_model": "gpt-6-astra", "expect_thinking": "xhigh"},
     {"model": "gpt-6-sol", "thinking_level": "high"},
     "asked for model=gpt-6-astra thinking_level=xhigh and resolved model=gpt-6-sol thinking_level=high"),
    ({"expect_model": "gpt-6-astra", "expect_thinking": "xhigh"},
     {"model": "gpt-6-astra", "thinking_level": "high"}, "resolved model=gpt-6-astra thinking_level=high"),
    ({"expect_model": "qwen-x"}, {"model": "gpt-6-sol", "thinking_level": "high"}, "resolved model=gpt-6-sol"),
])
def test_a_seat_that_resolved_anything_but_its_request_fails(tmp_path, asked, row, expect):
    _row(tmp_path, "seat-14", **row)
    clock = _Clock()
    why = fleet_policy.verify_seat("seat-14", tmp_path, sleep=clock.sleep, clock=clock, **asked)
    assert why and why.startswith("SEAT FAILED MISMATCH: seat-14") and expect in why, why


@pytest.mark.parametrize("asked,row", [
    ({"expect_model": "gpt-6-astra", "expect_thinking": "xhigh"}, {"model": "gpt-6-astra", "thinking_level": "xhigh"}),
    ({"expect_model": " GPT-6.1-Sol "}, {"model": "gpt-6.1-sol", "thinking_level": "high"}),
    ({"expect_model": "qwen-x", "expect_thinking": "low"}, {"model": "qwen-x", "thinking_level": "low"}),
])
def test_an_exact_match_passes(tmp_path, asked, row):
    _row(tmp_path, "seat-15", **row)
    clock = _Clock()
    assert fleet_policy.verify_seat("seat-15", tmp_path, sleep=clock.sleep, clock=clock, **asked) is None


def test_seat_on_an_ungoverned_model_verifies_without_a_level(tmp_path):
    _row(tmp_path, "seat-5", model="claude-opus-5-5")
    clock = _Clock()
    assert fleet_policy.verify_seat("seat-5", tmp_path, sleep=clock.sleep, clock=clock) is None


# ── the CLI: `liteharness spawn`, `fleet-check`, `register`, `discover` ─────────

def _run_spawn(monkeypatch, **kw):
    from liteharness import pty_daemon
    def never(*_a, **_k):
        raise AssertionError("a refused spawn reached the PTY daemon")
    monkeypatch.setattr(pty_daemon, "ensure_daemon", never)
    monkeypatch.setattr(pty_daemon, "send_command", never)
    monkeypatch.setattr(cli, "_ensure_folder_trusted", lambda _d, **_k: None, raising=False)
    kw.setdefault("cwd", os.getcwd())  # T0236-T4: worker/leader spawns need an explicit --cwd
    try:
        cli.cmd_spawn(**kw)
    except SystemExit as exc:
        return exc.code
    return 0


@pytest.mark.parametrize("exec_cmd,expect", [
    ("litetui --backend codex --model gpt-5.6-sol --thinking-level medium", "is below gpt-6.1-sol"),
    ("litetui --backend codex --model gpt-6-sol", "thinking_level None"),
    ("litetui", "model None"),                                   # the pin would decide
    # F2: the program is found in ANY token, so a wrapper is no way round the floor.
    ("cmd /c litetui", "model None"),
    ("python -m litetui", "model None"),
    (r'cmd /c "C:\x\litetui.exe --backend codex --model gpt-5.6-sol --thinking-level medium"',
     "is below gpt-6.1-sol"),
    # T0088 (the orchestrator): the codex CLI stays refused exactly as on main; only the wording changed.
    # copilot and pi on a governed model keep their existing refusal. Nothing here is newly refused.
    ("codex -m gpt-5.6-sol", "codex floor: not verified (T0088-A)"),
    ("codex -m gpt-6-sol -c model_reasoning_effort=high", "codex floor: not verified (T0088-A)"),   # AT the floor: still refused
    ("codex -m gpt-6-sol -c 'model_reasoning_effort=\"xhigh\"'", "codex floor: not verified (T0088-A)"),
    ("pwsh -c codex", "codex floor: not verified (T0088-A)"),
    ("copilot --model gpt-6-sol", "codex floor: not verified (T0088-A)"),
    ("copilot --model gpt-6-sol --effort high", "codex floor: not verified (T0088-A)"),
])
def test_liteharness_spawn_refuses_below_the_floor(monkeypatch, capsys, exec_cmd, expect):
    assert _run_spawn(monkeypatch, pty_mode=True, exec_cmd=exec_cmd, name="Probe") == 2
    out = capsys.readouterr().out
    assert out.startswith("SPAWN REFUSED") and expect in out, out


@pytest.mark.parametrize("mode", [{"split_mode": True}, {}, {"split_mode": True, "pty_mode": True}])  # split, canvas/terminal, split+pty (split wins the route)
@pytest.mark.parametrize("exec_cmd", ["python -m worker", "litetui --backend codex --model gpt-6.1-sol --thinking-level high"])
def test_exec_without_pty_is_refused_not_silently_a_claude_seat(monkeypatch, capsys, mode, exec_cmd):
    # T0122: the floor gate passes these; the launcher would then ignore --exec and start claude.
    def never(*_a, **_k):
        raise AssertionError("a refused spawn reached the bridge")
    monkeypatch.setattr(cli, "_bridge_request", never)
    assert _run_spawn(monkeypatch, exec_cmd=exec_cmd, name="Probe", **mode) == 2
    out = capsys.readouterr().out
    assert out.startswith("SPAWN REFUSED") and "--exec runs only with --pty" in out, out


def test_liteharness_spawn_at_the_floor_reaches_the_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)  # F7: never the live agents dir
    _cache_dir = tmp_path / "codex-home"
    _cache_dir.mkdir()
    (_cache_dir / "models_cache.json").write_text(json.dumps({"models": [{"slug": "gpt-6.1-sol"}]}),
                                                  encoding="utf-8")
    with pytest.raises(AssertionError, match="reached the PTY daemon"):
        _run_spawn(monkeypatch, pty_mode=True, name="Probe",
                   exec_cmd="litetui --backend codex --model gpt-6.1-sol --thinking-level high")


def test_fleet_check_cli(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    _row(tmp_path, "seat-6", model="gpt-5.6-sol", thinking_level="medium")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_fleet_check(["--agent-id", "seat-6", "--wait", "0"])
    assert exc.value.code == 2 and "SEAT FAILED FLOOR" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        cli.cmd_fleet_check(["--backend", "codex", "--model", "gpt-5.6-sol", "--thinking-level", "medium"])
    assert exc.value.code == 2 and "is below gpt-6.1-sol" in capsys.readouterr().out
    _row(tmp_path, "seat-9", model="o4-mini", thinking_level="high")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_fleet_check(["--agent-id", "seat-9", "--backend", "codex", "--wait", "0"])
    assert exc.value.code == 2 and "'o4-mini' is not in the codex list" in capsys.readouterr().out
    cli.cmd_fleet_check(["--agent-id", "seat-absent", "--wait", "0", "--allow-silent"])
    assert capsys.readouterr().out.startswith("OK:")
    _row(tmp_path, "seat-13", model="gpt-5.6-sol", thinking_level="medium")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_fleet_check(["--agent-id", "seat-13", "--wait", "0", "--allow-silent"])
    assert exc.value.code == 2 and "is below gpt-6.1-sol" in capsys.readouterr().out
    _row(tmp_path, "seat-16", model="o4-mini", thinking_level="high")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_fleet_check(["--agent-id", "seat-16", "--wait", "0", "--expect-model", "gpt-6-sol",
                             "--expect-thinking", "high"])
    assert exc.value.code == 2 and "SEAT FAILED MISMATCH" in capsys.readouterr().out
    cli.cmd_fleet_check(["--backend", "claude", "--model", "opus"])
    assert capsys.readouterr().out.startswith("OK: ungoverned")
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "gpt-6.1-sol"}]}), encoding="utf-8")
    cli.cmd_fleet_check(["--backend", "codex", "--model", "gpt-6.1-sol", "--thinking-level", "high"])
    assert capsys.readouterr().out.startswith("OK: governed")


# ── T0088: DORMANT groundwork for the codex CLI (no spawn path calls it; T0088-A wires it) ──
# Recorded contract, no live seat: a real rollout's session_meta carries id/cwd/timestamp
# and its first turn_context carries the effective `model` and `effort` (measured on
# ~/.codex/sessions 2026-09-29: gpt-6-sol/high, gpt-6-astra/medium, gpt-5.6-sol/medium).

@pytest.mark.parametrize("exec_cmd,expect", [
    ("codex -m gpt-6-sol -c model_reasoning_effort=high", ("codex", None, "gpt-6-sol", "high")),
    ("codex -m gpt-6-sol -c 'model_reasoning_effort=\"xhigh\"'", ("codex", None, "gpt-6-sol", "xhigh")),
    ("codex -m gpt-6-sol -c model_reasoning_effort=high -c model_reasoning_effort=low",
     ("codex", None, "gpt-6-sol", "low")),                      # the last -c wins, as in codex
    ("codex -m gpt-6-sol", ("codex", None, "gpt-6-sol", None)),
    ("pwsh -c codex", ("codex", None, None, None)),
    ("copilot --model gpt-6-sol --effort high", (None, None, "gpt-6-sol", "high")),
    ("pi --model openai/gpt-5.6-sol:low", (None, None, "gpt-5.6-sol", "low")),
    ("pi --model openai/gpt-6-sol --thinking high", (None, None, "gpt-6-sol", "high")),
    ("litetui --backend codex --model openai/x --thinking-level high", ("litetui", "codex", "openai/x", "high")),
])
def test_exec_request_reads_program_model_and_level(exec_cmd, expect):
    assert cli._exec_request(None, exec_cmd) == expect


# ── T0088-A: attribution by a spawner-minted random marker (DORMANT until a live codex proves it) ──
# Recorded contract, no live seat. Record shapes measured on ~/.codex/sessions 2026-09-29: a user
# message is a response_item{message, role=user} whose internal_chat_message_metadata_passthrough
# carries turn_id, and that turn_id equals a turn_context turn_id (30 of 30 recent rollouts).
# The threat model is an ASSUMPTION (see verify_codex_rollout): this defends against
# mis-attribution, not a deliberate spoof, and none of it has run against a live codex.

MARK = f"{fleet_policy.SPAWN_MARKER_KEY}=0123456789abcdef0123456789abcdef"


def _rollout(root, name, model=None, effort=None, user=None, turn="t1", role="user", turn_ids=True,
             context_turn=None, extra_users=()):
    day = root / "2026" / "09" / "29"
    day.mkdir(parents=True, exist_ok=True)

    def message(text, who=role, at=turn):
        meta = {"turn_id": at} if turn_ids else {}
        return {"type": "response_item", "payload": {
            "type": "message", "role": who, "content": [{"type": "input_text", "text": text}],
            "internal_chat_message_metadata_passthrough": meta}}

    recs = [{"type": "session_meta", "payload": {"id": name, "cwd": "C:\\w", "timestamp": "2026-09-29T03:57:12.977Z"}}]
    if user is not None:
        recs.append(message("<environment_context>injected first</environment_context>", "user"))  # real rollouts open with one
        recs.append(message(user))
    for text, at in extra_users:
        recs.append(message(text, "user", at))
    if model:
        recs.append({"type": "turn_context", "payload": {"turn_id": context_turn or turn, "model": model, "effort": effort}})
    (day / f"rollout-{name}.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")


def _verify_rollout(tmp_path, asked=("gpt-6.1-sol", "high"), marker=MARK, since=None):
    clock = _Clock()
    return fleet_policy.verify_codex_rollout(marker, *asked, since=since, sessions=tmp_path, wait=10,
                                             sleep=clock.sleep, clock=clock)


def test_the_seat_whose_first_message_is_the_marker_is_verified(tmp_path):
    _rollout(tmp_path, "ours", "gpt-6.1-sol", "high", user=MARK)
    assert _verify_rollout(tmp_path) is None


@pytest.mark.parametrize("ran,asked,expect", [
    (("gpt-5.6-sol", "medium"), ("gpt-5.6-sol", "medium"), "SEAT FAILED FLOOR"),        # 5.6/medium
    (("gpt-6-sol", "medium"), ("gpt-6-sol", "high"), "SEAT FAILED MISMATCH"),           # effort differs from the ask
    (("gpt-6-astra", "high"), ("gpt-6-sol", "high"), "SEAT FAILED MISMATCH"),           # model differs from the ask
    (("o4-mini", "high"), ("o4-mini", "high"), "is not in the codex list"),
])
def test_a_marked_seat_that_ran_something_else_fails(tmp_path, ran, asked, expect):
    _rollout(tmp_path, "ours", *ran, user=MARK)
    why = _verify_rollout(tmp_path, asked)
    assert why and expect in why, why


def test_a_compliant_neighbour_is_not_a_pass_for_a_seat_that_wrote_nothing(tmp_path):
    # THE false pass of the cwd+time check: a compliant rollout next door, none of ours.
    _rollout(tmp_path, "neighbour", "gpt-6.1-sol", "high", user="an unrelated prompt")
    why = _verify_rollout(tmp_path)
    assert why and "Unverified is refused" in why and MARK in why, why


def test_a_neighbour_never_rescues_a_sub_floor_seat_and_never_fails_a_compliant_one(tmp_path):
    _rollout(tmp_path, "neighbour", "gpt-6.1-sol", "high", user="an unrelated prompt", turn="t9")
    _rollout(tmp_path, "ours", "gpt-5.6-sol", "medium", user=MARK)
    assert "SEAT FAILED FLOOR" in _verify_rollout(tmp_path, ("gpt-5.6-sol", "medium"))
    (tmp_path / "2026" / "09" / "29" / "rollout-ours.jsonl").unlink()
    _rollout(tmp_path, "bad-neighbour", "gpt-5.6-sol", "low", user="an unrelated prompt", turn="t8")
    _rollout(tmp_path, "ours", "gpt-6.1-sol", "high", user=MARK)
    assert _verify_rollout(tmp_path) is None   # a sub-floor neighbour is not this seat's business


def test_the_marker_in_two_rollouts_or_two_turns_is_refused(tmp_path):
    _rollout(tmp_path, "a", "gpt-6-sol", "high", user=MARK)
    _rollout(tmp_path, "b", "gpt-6-sol", "high", user=MARK)
    why = _verify_rollout(tmp_path)
    assert why and "2 user messages" in why and "a" in why and "b" in why, why
    (tmp_path / "2026" / "09" / "29" / "rollout-b.jsonl").unlink()
    _rollout(tmp_path, "a", "gpt-6-sol", "high", user=MARK, extra_users=[(MARK, "t2")])   # again, in a later turn
    assert "2 user messages" in _verify_rollout(tmp_path)


def test_the_effort_is_read_from_the_marker_messages_own_turn(tmp_path):
    # The marker turn is t1 but the only turn_context is t2's: not "some context in the file".
    _rollout(tmp_path, "ours", "gpt-6-sol", "high", user=MARK, turn="t1", context_turn="t2")
    assert "did not record turn t1" in _verify_rollout(tmp_path)


def test_a_marker_with_no_turn_yet_waits_then_is_refused(tmp_path):
    _rollout(tmp_path, "ours", user=MARK)   # the user message is there, no turn_context
    assert "did not record turn t1" in _verify_rollout(tmp_path)


@pytest.mark.parametrize("kw,why_text", [
    ({"user": MARK + "0"}, "Unverified is refused"),                     # a longer token is not ours
    ({"user": "please run " + MARK + " now"}, "Unverified is refused"),  # exact message, not a substring
    ({"user": MARK, "role": "assistant"}, "Unverified is refused"),      # only a USER message counts
    ({"user": MARK, "turn_ids": False}, "cannot be tied to a turn"),     # no turn_id: unverifiable
])
def test_only_the_whole_marker_as_a_user_message_with_a_turn_id_counts(tmp_path, kw, why_text):
    _rollout(tmp_path, "r", "gpt-6-sol", "high", **kw)
    why = _verify_rollout(tmp_path)
    assert why and why_text in why, why


def test_a_whitespace_padded_marker_message_still_counts(tmp_path):
    _rollout(tmp_path, "ours", "gpt-6.1-sol", "high", user="\n" + MARK + "  \n")
    assert _verify_rollout(tmp_path) is None


def test_since_only_limits_the_scan_and_never_attributes(tmp_path):
    _rollout(tmp_path, "old", "gpt-6.1-sol", "high", user=MARK)
    assert "Unverified is refused" in _verify_rollout(tmp_path, since=4e9)   # predates the spawn: not scanned
    assert _verify_rollout(tmp_path, since=0.0) is None


def test_a_new_marker_is_fresh_random_and_shaped_for_the_daemon():
    import re
    a, b = fleet_policy.new_spawn_marker(), fleet_policy.new_spawn_marker()
    assert a != b and re.fullmatch(fleet_policy.SPAWN_MARKER_KEY + r"=[0-9a-f]{32}", a)


@pytest.mark.parametrize("exec_cmd", [
    "codex",
    "codex -m gpt-6-sol -c model_reasoning_effort=high",
    "codex --model=gpt-6-sol --config model_reasoning_effort=high --search --no-alt-screen",
    "codex -m gpt-6-sol -c 'model_reasoning_effort=\"high\"' -s workspace-write -a never -C C:\\w",
    "\"C:\\tools\\codex.exe\" -m gpt-6-sol",
])
def test_the_marker_is_appended_only_to_an_unambiguous_codex_command(tmp_path, exec_cmd):
    from liteharness import pty_daemon
    out = fleet_policy.with_spawn_marker(exec_cmd, MARK)
    assert out == f"{exec_cmd} {MARK}"
    assert pty_daemon._validate_spawn_cmd(out, str(tmp_path)) is None   # the daemon accepts the real launch line


@pytest.mark.parametrize("exec_cmd", [
    "",
    "codex -m gpt-6-sol \"do the thing\"",       # a positional prompt is already there
    "codex -m gpt-6-sol do-the-thing",
    "codex exec -m gpt-6-sol",                    # a subcommand
    "codex review",
    "cmd /c codex -m gpt-6-sol",                  # wrappers: their quoting is guessed at
    "pwsh -c codex",
    "python -m codex",
    "litetui --backend codex",
    "codex --not-a-real-flag",                    # unknown to this codex version: fail closed
    "codex -i shot.png",                          # variadic: would swallow the marker as a file
    "codex -m gpt-6-sol --",                      # a terminator
    "codex -m",                                   # a value flag with no value
    "codex -c -m gpt-6-sol",                      # its value looks like a flag
    "codex -m gpt-6-sol \"unterminated",
])
def test_an_ambiguous_command_gets_no_marker(exec_cmd):
    assert fleet_policy.with_spawn_marker(exec_cmd, MARK) is None


# ── T0088-A: `spawn --split --cli codex` (this branch only) ─────────────────────
# Every test mocks the bridge and the verifier: nothing here reaches LiteSuite, a daemon or a model.

_CODEX_OK = dict(spawn_cli="codex", split_mode=True, model="gpt-6.1-sol", thinking_level="high",
                 additional_args="-s read-only -a never", split_pane="canvas-pane-30", name="Probe",
                 spawned_by="leader-id")


def _codex_split(monkeypatch, tmp_path, verdict=None, split=None, write=None, delete=None):
    (tmp_path / "codex-home").mkdir(exist_ok=True)
    (tmp_path / "codex-home" / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "gpt-6.1-sol"}]}), encoding="utf-8")
    (tmp_path / "codex-home" / "config.toml").write_text(
        '[projects.' + json.dumps(str(tmp_path.resolve())) + ']\ntrust_level = "trusted"\n', encoding="utf-8")
    calls, verified = [], []
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)

    def bridge(method, path, body=None):
        # Existing provenance assertions record creation/input, not context reads.
        if path.startswith("/context?"):
            return {"activePanes": [{"id": "canvas-pane-30", "leafCount": 1},
                                    {"id": "canvas-pane-16", "leafCount": 1}]}
        calls.append((method, path, body))
        if path == "/canvas/split":
            return split if split is not None else {"newSessionId": "pty-99-1", "newLeafId": "leaf9"}
        if path == "/pty/write":
            return write if write is not None else {"ok": True}
        if method == "DELETE":
            return delete if delete is not None else {"success": True}
        raise AssertionError(f"unexpected bridge call {method} {path}")

    def verifier(marker, model, level, **kw):
        verified.append((marker, model, level, kw))
        if verdict is None:
            kw["result"].update(sid="codex-sess", model=model, effort=level, turn_id="turn-1")
        return verdict

    monkeypatch.setattr(cli, "_bridge_request", bridge)
    monkeypatch.setattr(cli, "_codex_failure_buffer", lambda _s: "own-session buffer unavailable (mock)")
    monkeypatch.setattr(cli, "_codex_session_process", lambda _s: object())
    monkeypatch.setattr(cli, "_codex_marker_descendants", lambda _s, _p, _m: [])
    monkeypatch.setattr(cli, "_reap_marker_processes", lambda _m, _targets: ([], []))
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cli, "_rename_canvas_seat", lambda *_a, **_k: None)
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verifier)
    monkeypatch.delenv("LITEHARNESS_AGENT_ID", raising=False)
    return calls, verified


def _spawn_codex_split(**overrides):
    return cli.cmd_spawn(**{**_CODEX_OK, **overrides})


def _typed(calls):
    return next(body["data"] for method, path, body in calls if path == "/pty/write")


def test_codex_split_types_the_marker_command_and_reports_the_verified_turn(monkeypatch, tmp_path, capsys):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    _spawn_codex_split(cwd=str(tmp_path))
    marker = verified[0][0]
    data = _typed(calls)
    assert re.fullmatch(fleet_policy.SPAWN_MARKER_KEY + r"=[0-9a-f]{32}", marker)
    assert "codex -m gpt-6.1-sol -c model_reasoning_effort=high -s read-only -a never " + marker + "\r" in data
    assert data.endswith(marker + "\r") and "claude" not in data       # the marker is the whole prompt
    assert calls[0][1] == "/canvas/split" and calls[0][2]["paneId"] == "canvas-pane-30"
    assert [c for c in calls if c[0] == "DELETE"] == []                # a verified seat is kept
    (_, model, level, kw), = [v for v in verified]
    assert (model, level) == ("gpt-6.1-sol", "high") and isinstance(kw["since"], float) and kw["wait"] == cli.CODEX_VERIFY_WAIT
    out = capsys.readouterr().out
    assert "Verified: codex session codex-sess ran model=gpt-6.1-sol effort=high" in out and "turn-1" in out


def test_every_codex_launch_gets_its_own_marker(monkeypatch, tmp_path):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    _spawn_codex_split(cwd=str(tmp_path))
    _spawn_codex_split(cwd=str(tmp_path))
    assert verified[0][0] != verified[1][0]


@pytest.mark.parametrize("verdict", ["SEAT FAILED MISMATCH: ran medium", "SEAT FAILED FLOOR: no rollout holds the marker"])
def test_a_failed_verification_kills_only_the_session_it_created_and_never_relaunches(monkeypatch, tmp_path, capsys, verdict):
    calls, _verified = _codex_split(monkeypatch, tmp_path, verdict=verdict)
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2
    assert [c[1] for c in calls if c[0] == "DELETE"] == ["/pty/pty-99-1"]      # exactly the session it made
    assert len([c for c in calls if c[1] == "/canvas/split"]) == 1              # and no second launch
    out = capsys.readouterr().out
    assert verdict in out and "Attribution is NOT verified" in out and "NOT relaunched" in out


def test_a_verifier_that_raises_is_a_refusal_and_a_kill(monkeypatch, tmp_path, capsys):
    calls, _ = _codex_split(monkeypatch, tmp_path)
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2 and [c[1] for c in calls if c[0] == "DELETE"] == ["/pty/pty-99-1"]
    assert "OSError before the seat was verified (disk)" in capsys.readouterr().out


def test_an_uncertain_split_failure_launches_nothing(monkeypatch, tmp_path, capsys):
    calls, verified = _codex_split(monkeypatch, tmp_path,
                                   split={"error": "launch_state_unknown", "paneId": "canvas-pane-16"})
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path), split_pane="canvas-pane-16")
    assert exc.value.code == 1 and verified == []
    assert [c[1] for c in calls] == ["/canvas/split"]                            # no write, no delete
    assert "launch_state_unknown" in capsys.readouterr().out


def test_a_pane_that_cannot_be_typed_into_is_killed_and_nothing_is_verified(monkeypatch, tmp_path):
    calls, verified = _codex_split(monkeypatch, tmp_path, write={"ok": False, "error": "no such session"})
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2 and verified == []
    assert [c[1] for c in calls if c[0] == "DELETE"] == ["/pty/pty-99-1"]


def test_a_kill_that_fails_is_reported_as_fatal(monkeypatch, tmp_path, capsys):
    _codex_split(monkeypatch, tmp_path, verdict="SEAT FAILED FLOOR: x", delete={"success": False})
    with pytest.raises(SystemExit):
        _spawn_codex_split(cwd=str(tmp_path))
    assert "FATAL CLEANUP FAILED for session pty-99-1" in capsys.readouterr().out


@pytest.mark.parametrize("kw,expect", [
    ({"split_mode": False}, "takes only --split"),
    ({"pty_mode": True}, "takes only --split"),                    # --pty is closed to codex: no second daemon
    ({"exec_cmd": "codex -m gpt-6-sol"}, "takes only --split"),
    ({"prompt": "do it"}, "takes only --split"),                   # no hook, so no brief to deliver
    ({"tier": "worker"}, "takes only --split"),
    ({"worktree": True}, "takes only --split"),
    ({"model": None}, "needs --model and --thinking-level"),
    ({"thinking_level": None}, "needs --model and --thinking-level"),
    ({"model": "gpt-6-sol;calc"}, "must be plain names"),
    ({"thinking_level": "high;x"}, "must be plain names"),
    ({"model": "gpt-5.6-sol"}, "is below gpt-6.1-sol"),
    ({"thinking_level": "medium"}, "thinking_level 'medium' is below high"),
    ({"model": "gpt-6-nova"}, "is not in the codex list"),
    # --args carries safety flags only: anything that could change what the floor judged is refused.
    ({"additional_args": "\"do the thing\""}, "may only carry -s/--sandbox"),    # an existing prompt
    ({"additional_args": "--not-a-real-flag"}, "may only carry -s/--sandbox"),
    ({"additional_args": "-s read-only ; whoami"}, "may only carry -s/--sandbox"),
    ({"additional_args": "-a never & calc"}, "may only carry -s/--sandbox"),
    ({"additional_args": "-c $env:USERNAME"}, "may only carry -s/--sandbox"),
    ({"additional_args": "-c model_reasoning_effort=low"}, "may only carry -s/--sandbox"),  # last -c wins in codex
    ({"additional_args": "-c model=gpt-5.5"}, "may only carry -s/--sandbox"),
    ({"additional_args": "-m gpt-5.5"}, "may only carry -s/--sandbox"),
    ({"additional_args": "--model gpt-5.5"}, "may only carry -s/--sandbox"),
    ({"additional_args": "-p other-profile"}, "may only carry -s/--sandbox"),      # a profile can set model and level
    ({"additional_args": "-C C:\\elsewhere"}, "may only carry -s/--sandbox"),
    ({"additional_args": "--oss"}, "may only carry -s/--sandbox"),
    ({"additional_args": "--dangerously-bypass-approvals-and-sandbox"}, "may only carry -s/--sandbox"),
    ({"additional_args": "-s"}, "may only carry -s/--sandbox"),                    # a value flag with no value
    ({"additional_args": "-s --search"}, "may only carry -s/--sandbox"),           # its value looks like a flag
    ({"spawned_by": None}, "needs --spawned-by"),
    ({"cwd": "relative/dir"}, "absolute existing directory"),
])
def test_a_codex_split_that_is_not_governed_or_not_markable_is_refused_before_the_bridge(monkeypatch, tmp_path, capsys, kw, expect):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(**{"cwd": str(tmp_path), **kw})
    assert exc.value.code == 2 and calls == [] and verified == []
    out = capsys.readouterr().out
    assert out.startswith("SPAWN REFUSED") and expect in out, out


@pytest.mark.parametrize("extra", ["-s read-only -a never", "--search", "--no-alt-screen", "-s workspace-write",
                                   "-a on-request --search --no-alt-screen", None])
def test_the_safety_flags_a_codex_split_may_carry_are_allowed(monkeypatch, tmp_path, extra):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    _spawn_codex_split(cwd=str(tmp_path), additional_args=extra)
    assert len(verified) == 1 and _typed(calls).endswith(verified[0][0] + "\r")


def test_a_marker_that_cannot_be_placed_is_still_refused_before_the_bridge(monkeypatch, tmp_path, capsys):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    monkeypatch.setattr(fleet_policy, "with_spawn_marker", lambda *_a, **_k: None)
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2 and calls == [] and verified == []
    assert "cannot be given a spawn marker" in capsys.readouterr().out


def test_a_cancel_while_verifying_kills_the_seat_and_still_propagates(monkeypatch, tmp_path, capsys):
    calls, _ = _codex_split(monkeypatch, tmp_path)

    def cancelled(*_a, **_k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", cancelled)
    with pytest.raises(KeyboardInterrupt):
        _spawn_codex_split(cwd=str(tmp_path))
    assert [c[1] for c in calls if c[0] == "DELETE"] == ["/pty/pty-99-1"]      # nothing unverified is left running
    out = capsys.readouterr().out
    assert "KeyboardInterrupt before the seat was verified" in out and "NOT relaunched" in out


def test_a_process_that_survives_the_pane_kill_is_reported_and_the_spawn_still_fails(monkeypatch, tmp_path, capsys):
    calls, _ = _codex_split(monkeypatch, tmp_path, verdict="SEAT FAILED FLOOR: x")
    monkeypatch.setattr(cli, "_reap_marker_processes", lambda _m, _targets: ([4321], [4321]))
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2
    assert "process(es) [4321] survived or could not be checked" in capsys.readouterr().out


class _Process:
    def __init__(self, pid, args=(), birth=1):
        self.pid, self.args, self.birth = pid, list(args), birth
        self.running, self.terminated = True, False
        self.descendants, self.ancestors = [], []
        self.failure = None

    def __eq__(self, other):
        return isinstance(other, _Process) and (self.pid, self.birth) == (other.pid, other.birth)

    def create_time(self):
        return self.birth

    def is_running(self):
        return self.running

    def parents(self):
        return self.ancestors

    def children(self, recursive=False):
        assert recursive
        return self.descendants

    def cmdline(self):
        return self.args

    def terminate(self):
        if self.failure:
            raise self.failure
        self.terminated, self.running = True, False


def _process_boundary(monkeypatch, rows=None):
    import os
    import psutil
    caller, shell = _Process(os.getpid()), _Process(987654)
    processes = {caller.pid: caller, shell.pid: shell}
    monkeypatch.setattr(psutil, "Process", lambda pid: processes[pid])
    monkeypatch.setattr(psutil, "process_iter", lambda *_a: (_ for _ in ()).throw(AssertionError("global scan")))
    monkeypatch.setattr(psutil, "wait_procs", lambda victims, timeout: (
        [p for p in victims if not p.running], [p for p in victims if p.running]))
    monkeypatch.setattr(cli, "_bridge_request", lambda *_a, **_k: {
        "sessions": rows if rows is not None else [{"id": "new-session", "pid": shell.pid}]})
    return caller, shell, processes


def test_the_reap_ends_only_proven_exact_marker_descendants(monkeypatch):
    _, shell, _ = _process_boundary(monkeypatch)
    ours = _Process(987655, ["codex", MARK])
    partial = _Process(987656, ["codex", MARK + "0"])
    neighbour = _Process(987657, ["codex", MARK])  # same token OUTSIDE this shell's tree
    shell.descendants = [ours, partial]
    owned = cli._codex_session_process("new-session")
    targets = cli._codex_marker_descendants("new-session", owned, MARK)
    assert targets == [ours]
    found, alive = cli._reap_marker_processes(MARK, targets)
    assert found == [ours.pid] and alive == [] and ours.terminated
    assert not partial.terminated and not neighbour.terminated and not shell.terminated


def test_the_launcher_names_the_code_it_loaded_and_the_marker_it_used(monkeypatch, tmp_path, capsys):
    import os as _os

    calls, verified = _codex_split(monkeypatch, tmp_path)
    _spawn_codex_split(cwd=str(tmp_path))
    out = capsys.readouterr().out
    assert "Loaded: " + _os.path.abspath(cli.__file__) in out
    receipt = json.loads(out.strip().splitlines()[-1])
    assert receipt["marker"] == verified[0][0] and receipt["canvas_session"] == "pty-99-1"
    assert receipt["codex_session"] == "codex-sess" and receipt["turn_id"] == "turn-1"


def test_the_codex_split_never_widens_the_other_routes(monkeypatch, capsys):
    # --pty --exec codex is still refused as on main, and --exec still cannot ride --split.
    assert _run_spawn(monkeypatch, pty_mode=True, exec_cmd="codex -m gpt-6-sol -c model_reasoning_effort=high",
                      name="Probe") == 2
    assert "codex floor: not verified (T0088-A)" in capsys.readouterr().out
    assert _run_spawn(monkeypatch, split_mode=True, exec_cmd="codex -m gpt-6-sol", name="Probe") == 2
    assert "--exec runs only with --pty alone" in capsys.readouterr().out


def test_a_verified_result_is_reported_to_the_caller_and_a_failure_leaves_it_empty(tmp_path):
    _rollout(tmp_path, "ours", "gpt-6.1-sol", "high", user=MARK)
    got, clock = {}, _Clock()
    assert fleet_policy.verify_codex_rollout(MARK, "gpt-6.1-sol", "high", sessions=tmp_path, wait=10, result=got,
                                             sleep=clock.sleep, clock=clock) is None
    assert got == {"sid": "ours", "model": "gpt-6.1-sol", "effort": "high", "turn_id": "t1"}
    bad, clock = {}, _Clock()
    assert fleet_policy.verify_codex_rollout(MARK, "gpt-6.1-sol", "medium", sessions=tmp_path, wait=10, result=bad,
                                             sleep=clock.sleep, clock=clock)
    assert bad == {}


def test_register_records_and_discover_prints_thinking_level(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.config, "get_root", lambda: tmp_path)
    from liteharness import naming
    monkeypatch.setattr(naming, "get_name", lambda _id: "SolSeat")
    monkeypatch.setattr(naming, "set_override", lambda *_a: None)
    monkeypatch.setattr(naming, "is_name_taken", lambda *_a, **_k: None)
    from liteharness import announce, terminal_automation
    monkeypatch.setattr(announce, "announce_registration", lambda *_a, **_k: None)  # live inbox
    monkeypatch.setattr(terminal_automation, "list_panes", lambda: [])               # live windows
    monkeypatch.setattr(sys, "argv", ["liteharness", "register", "--agent-id", "seat-7", "--cli", "litetui",
                                      "--model", "gpt-6-sol", "--thinking-level", "high"])
    cli.main()
    row = json.loads((tmp_path / "agents" / "seat-7.json").read_text(encoding="utf-8"))
    assert row["thinking_level"] == "high" and row["model"] == "gpt-6-sol"
    capsys.readouterr()
    cli.cmd_discover()
    assert "litetui/gpt-6-sol think:high" in capsys.readouterr().out


@pytest.mark.parametrize("record", [None, [], 7, "text", {"payload": []},
    {"type": "turn_context", "payload": {"turn_id": []}},
    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": 7}]}},
    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [None]}}])
def test_hostile_neighbour_record_shapes_do_not_crash_or_attribute(tmp_path, record):
    (tmp_path / "rollout-hostile.jsonl").write_text(json.dumps(record) + "\n{partial", encoding="utf-8")
    assert "Unverified is refused" in _verify_rollout(tmp_path)
    _rollout(tmp_path, "ours", "gpt-6.1-sol", "high", user=MARK)
    assert _verify_rollout(tmp_path) is None


@pytest.mark.parametrize("meta", [None, [], 5, {"turn_id": []}, {"turn_id": ""}])
def test_marker_with_malformed_turn_metadata_is_refused(tmp_path, meta):
    record = {"type": "response_item", "payload": {"type": "message", "role": "user",
        "content": [{"text": MARK}], "internal_chat_message_metadata_passthrough": meta}}
    (tmp_path / "rollout-meta.jsonl").write_text(json.dumps(record), encoding="utf-8")
    assert "cannot be tied to a turn" in _verify_rollout(tmp_path)


@pytest.mark.parametrize("model,effort", [("gpt-5.6-sol", "medium"), ("gpt-6-sol", "high")])
@pytest.mark.parametrize("prepend", [True, False])
def test_duplicate_context_is_refused_regardless_of_order_or_equality(tmp_path, model, effort, prepend):
    _rollout(tmp_path, "ours", "gpt-6-sol", "high", user=MARK)
    path = next(tmp_path.rglob("rollout-ours.jsonl"))
    duplicate = json.dumps({"type": "turn_context", "payload": {"turn_id": "t1", "model": model, "effort": effort}})
    original = path.read_text(encoding="utf-8")
    path.write_text(duplicate + "\n" + original if prepend else original + "\n" + duplicate, encoding="utf-8")
    assert "duplicate turn_context" in _verify_rollout(tmp_path)


@pytest.mark.parametrize("model,effort", [([], "high"), ("gpt-6-sol", {}), (7, None)])
def test_malformed_context_model_effort_is_refused(tmp_path, model, effort):
    _rollout(tmp_path, "ours", "gpt-6-sol", "high", user=MARK)
    path = next(tmp_path.rglob("rollout-ours.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[-1]["payload"].update(model=model, effort=effort)
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    assert "malformed model/effort" in _verify_rollout(tmp_path)


@pytest.mark.parametrize("rows", [[], [{"id": "other", "pid": 987654}],
    [{"id": "new-session", "pid": True}], [{"id": "new-session", "pid": -1}],
    [{"id": "new-session", "pid": 987654}] * 2,
    [{"id": "new-session", "pid": 987654}, {"id": "other", "pid": 987654}], [None]])
def test_missing_invalid_or_ambiguous_session_provenance_is_refused(monkeypatch, rows):
    _process_boundary(monkeypatch, rows)
    with pytest.raises(ValueError):
        cli._codex_session_process("new-session")


def test_new_shell_cannot_be_caller_or_caller_ancestor(monkeypatch):
    caller, shell, processes = _process_boundary(monkeypatch)
    caller.ancestors = [shell]
    with pytest.raises(ValueError, match="caller"):
        cli._codex_session_process("new-session")
    caller.ancestors = []
    monkeypatch.setattr(cli, "_bridge_request", lambda *_a: {"sessions": [{"id": "new-session", "pid": caller.pid}]})
    with pytest.raises(ValueError, match="caller"):
        cli._codex_session_process("new-session")


def test_shell_pid_reuse_or_fresh_list_drift_never_reaps_neighbour(monkeypatch):
    _, shell, processes = _process_boundary(monkeypatch)
    retained = cli._codex_session_process("new-session")
    replacement = _Process(shell.pid, [MARK], birth=2)
    processes[shell.pid] = replacement
    with pytest.raises(ValueError, match="identity changed"):
        cli._codex_marker_descendants("new-session", retained, MARK)
    assert not replacement.terminated
    monkeypatch.setattr(cli, "_bridge_request", lambda *_a: {"sessions": []})
    with pytest.raises(ValueError, match="unique shell PID"):
        cli._codex_marker_descendants("new-session", retained, MARK)


def test_reap_reports_access_failure_survivor_and_reused_identity_without_guessing(monkeypatch):
    import psutil
    _process_boundary(monkeypatch)
    denied, reused, changed = _Process(10, [MARK]), _Process(11, [MARK]), _Process(12, [MARK + "0"])
    denied.failure = psutil.AccessDenied(10)
    reused.running = False
    found, alive = cli._reap_marker_processes(MARK, [denied, reused, changed])
    assert found == [10] and alive == [10, 12]
    assert not reused.terminated and not changed.terminated


@pytest.mark.parametrize("delete", [{"success": False}, {"success": True, "ok": False}, "transport"])
def test_delete_failure_still_reaps_only_snapshot_targets(monkeypatch, tmp_path, capsys, delete):
    calls, _ = _codex_split(monkeypatch, tmp_path, verdict="SEAT FAILED FLOOR: x", delete=delete)
    bridge = cli._bridge_request
    targets, order = [object()], []
    monkeypatch.setattr(cli, "_codex_marker_descendants", lambda *_a: (order.append("snapshot") or targets))
    def request(method, path, body=None):
        if method == "DELETE":
            order.append("delete")
            if delete == "transport":
                raise OSError("bridge down")
        return bridge(method, path, body)
    def reap(marker, owned):
        assert owned is targets
        order.append("reap")
        return [123], []
    monkeypatch.setattr(cli, "_bridge_request", request)
    monkeypatch.setattr(cli, "_reap_marker_processes", reap)
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2 and order == ["snapshot", "delete", "reap"]
    assert "FATAL CLEANUP FAILED" in capsys.readouterr().out


def test_both_delete_and_owned_reap_failures_are_reported(monkeypatch, tmp_path, capsys):
    calls, _ = _codex_split(monkeypatch, tmp_path, verdict="SEAT FAILED FLOOR: x", delete={"success": False})
    monkeypatch.setattr(cli, "_reap_marker_processes", lambda *_a: (_ for _ in ()).throw(OSError("reap down")))
    with pytest.raises(SystemExit):
        _spawn_codex_split(cwd=str(tmp_path))
    out = capsys.readouterr().out
    assert "FATAL CLEANUP FAILED" in out and "proven process cleanup failed: reap down" in out
    assert "residual processes UNVERIFIED" in out and "NOT relaunched" in out


@pytest.mark.parametrize("initial", [True, False])
def test_missing_provenance_deletes_named_session_but_never_guesses_targets(monkeypatch, tmp_path, capsys, initial):
    calls, verified = _codex_split(monkeypatch, tmp_path, verdict="SEAT FAILED FLOOR: x")
    def unavailable(*_a):
        raise ValueError("no provenance")
    monkeypatch.setattr(cli, "_codex_session_process" if initial else "_codex_marker_descendants", unavailable)
    targets = []
    monkeypatch.setattr(cli, "_reap_marker_processes", lambda _m, owned: (targets.extend(owned) or ([], [])))
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2 and targets == []
    assert [c[1] for c in calls if c[0] == "DELETE"] == ["/pty/pty-99-1"]
    if initial:
        assert verified == [] and not any(c[1] == "/pty/write" for c in calls)
    out = capsys.readouterr().out
    assert "residual processes UNVERIFIED; no guessed kill" in out and "NOT relaunched" in out


@pytest.mark.parametrize("mode", ["missing", "untrusted", "parent-only", "malformed", "unreadable", "relative", "bad-entry"])
def test_codex_non_system_cwd_needs_no_trust_entry_and_never_writes_config(monkeypatch, tmp_path, mode):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    path = tmp_path / "codex-home" / "config.toml"
    key = str(tmp_path.resolve())
    if mode == "missing":
        path.unlink()
    elif mode == "unreadable":
        path.unlink()
        path.mkdir()
    elif mode == "malformed":
        path.write_text("[broken", encoding="utf-8")
    elif mode == "bad-entry":
        path.write_text("[projects]\n" + json.dumps(key) + " = 'trusted'\n", encoding="utf-8")
    else:
        if mode == "parent-only": key = str(tmp_path.parent)
        if mode == "relative": key = "."
        path.write_text('[projects.' + json.dumps(key) + ']\ntrust_level = '
                        + json.dumps("untrusted" if mode == "untrusted" else "trusted"), encoding="utf-8")
    before = path.read_bytes() if path.is_file() else None
    _spawn_codex_split(cwd=str(tmp_path))
    assert len(verified) == 1 and calls[0][1] == "/canvas/split"
    assert (path.read_bytes() if path.is_file() else None) == before


def test_exact_trust_accepts_canonical_target_and_codex_home(monkeypatch, tmp_path):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    _spawn_codex_split(cwd=str(tmp_path / ".." / tmp_path.name))
    assert len(verified) == 1
    assert str(tmp_path.resolve()).replace("'", "''") in _typed(calls)


def test_non_system_launch_does_not_depend_on_tomllib(monkeypatch, tmp_path):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "tomllib", None)
    _spawn_codex_split(cwd=str(tmp_path))
    assert len(verified) == 1


class _BufferResponse:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *_a): pass
    def read(self, limit): return self.data[:limit]


def test_failure_buffer_reads_exact_session_strips_controls_and_caps_text(monkeypatch):
    import urllib.request
    requests = []
    text = "\x1b[31mred\x1b[0m\x1b]0;SECRET_TITLE\x07\r\n\x00\u202e" + "x" * 5000
    def opened(request, timeout):
        requests.append((request, timeout))
        return _BufferResponse(json.dumps({"output": text}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", opened)
    monkeypatch.setattr(cli, "_bridge_token_file", lambda: "test-token")
    result = cli._codex_failure_buffer("exact-new-session")
    parsed = json.loads(result.split(": ", 1)[1])
    assert parsed == "red" + "x" * 4093 and len(parsed) == 4096
    assert "SECRET_TITLE" not in result
    (request, timeout), = requests
    assert request.full_url.endswith("/pty/read") and request.method == "POST" and timeout == 2
    assert json.loads(request.data) == {"session_id": "exact-new-session"}


@pytest.mark.parametrize("raw", [b"broken", b"[]", b'{"error":"gone"}', b'{"output":7}', b"x" * 65537],
                         ids=["invalid-json", "nonobject", "gone", "bad-output", "oversized"])
def test_failure_buffer_malformed_or_oversized_is_explicitly_unavailable(monkeypatch, raw):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _BufferResponse(raw))
    monkeypatch.setattr(cli, "_bridge_token_file", lambda: "test-token")
    assert "buffer unavailable" in cli._codex_failure_buffer("own-session")


def test_failure_buffer_unauthorized_never_retries(monkeypatch):
    import urllib.error
    import urllib.request
    attempts = []
    def fail(*_a, **_k):
        attempts.append(1)
        raise urllib.error.HTTPError("http://localhost/pty/read", 401, "unauthorized", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    monkeypatch.setattr(cli, "_bridge_token_file", lambda: "test-token")
    assert "buffer unavailable (HTTPError)" in cli._codex_failure_buffer("own-session")
    assert attempts == [1]


def test_failure_buffer_deadline_includes_thread_startup(monkeypatch):
    import queue
    import threading
    starts, waits = [], []
    class NoRun:
        def __init__(self, target, daemon): assert daemon
        def start(self): starts.append(1)
    class Replies:
        def __init__(self, maxsize): assert maxsize == 1
        def get(self, timeout): waits.append(timeout); raise queue.Empty
    clock = iter([10, 10.5])
    monkeypatch.setattr(threading, "Thread", NoRun)
    monkeypatch.setattr(queue, "Queue", Replies)
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    assert "2s deadline" in cli._codex_failure_buffer("own-session")
    assert starts == [1] and waits == [1.5]


@pytest.mark.parametrize("capture", ["success", "unavailable", "raises"])
def test_buffer_capture_precedes_delete_and_cannot_prevent_cleanup(monkeypatch, tmp_path, capsys, capture):
    calls, _ = _codex_split(monkeypatch, tmp_path, verdict="SEAT FAILED FLOOR: x")
    order, bridge = [], cli._bridge_request
    def diagnostic(session):
        assert session == "pty-99-1"
        order.append("buffer")
        if capture == "raises": raise OSError("diagnostic")
        return "own-session buffer unavailable (2s deadline)" if capture == "unavailable" else "own-session buffer: bounded"
    def request(method, path, body=None):
        if method == "DELETE": order.append("delete")
        return bridge(method, path, body)
    def reap(*_a): order.append("reap"); return [], []
    monkeypatch.setattr(cli, "_codex_failure_buffer", diagnostic)
    monkeypatch.setattr(cli, "_bridge_request", request)
    monkeypatch.setattr(cli, "_reap_marker_processes", reap)
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2 and order == ["buffer", "delete", "reap"]
    assert "own-session buffer" in capsys.readouterr().out


@pytest.mark.parametrize("cwd", ["C:/", "c:\\Windows", "C:/WINDOWS/System32", "C:/Program Files/tool",
    "C:/Program Files (x86)/tool", "C:/ProgramData/tool", "C:/safe/../Windows/System32", "\\\\?\\C:\\Windows\\Temp"])
def test_codex_system_cwd_boundary_is_canonical_descendant_aware(cwd):
    from pathlib import Path
    assert "banned system directory" in cli._codex_cwd_refusal(Path(cwd))


@pytest.mark.parametrize("cwd", ["C:/workspace", "C:/WindowsOther", "C:/Program FilesOther", "C:/Users/Example",
    "C:/safe/../workspace", "D:/ordinary"])
def test_codex_cwd_boundary_is_not_a_blanket_c_drive_ban(cwd):
    from pathlib import Path
    assert cli._codex_cwd_refusal(Path(cwd)) is None


def test_codex_banned_roots_include_environment_overrides(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("SystemRoot", "D:/OS")
    monkeypatch.setenv("ProgramW6432", "E:/Programs")
    assert cli._codex_cwd_refusal(Path("D:/os/system32"))
    assert cli._codex_cwd_refusal(Path("E:/PROGRAMS/tool"))
    assert cli._codex_cwd_refusal(Path("D:/OSOther")) is None


def test_banned_cwd_is_refused_before_bridge(monkeypatch, tmp_path):
    from pathlib import Path
    calls, verified = _codex_split(monkeypatch, tmp_path)
    original = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda p: True if str(p).lower().startswith("c:\\windows") else original(p))
    with pytest.raises(SystemExit) as exc:
        _spawn_codex_split(cwd="C:/Windows/System32")
    assert exc.value.code == 2 and calls == [] and verified == []


def _trust_screen(_cwd=None):
    from pathlib import Path
    # ACTUAL probe3 buffer slice, personal path scrubbed; no invented Open restricted option.
    return (Path(__file__).parent / "fixtures" / "codex-0159-folder-trust-probe3.txt").read_text(encoding="utf-8")


def test_recorded_native_folder_trust_screen_is_recognized_without_display_path_guess():
    assert cli._codex_folder_trust_screen(_trust_screen())


@pytest.mark.parametrize("change", ["approval", "missing-option", "title-only", "missing-hint", "other-description"])
def test_only_actual_known_folder_trust_screen_is_recognized(tmp_path, change):
    screen = _trust_screen()
    if change == "approval": screen = "Approve command? 1. Trust and continue 2.Quit enter continue esc quit"
    if change == "missing-option": screen = screen.replace("2.Quit", "")
    if change == "title-only": screen = "Trust this folder?"
    if change == "missing-hint": screen = screen.replace("enter continue", "")
    if change == "other-description": screen = screen.replace("Codex can read, edit, and run", "This command can read, edit, and run")
    assert not cli._codex_folder_trust_screen(screen)


def test_folder_trust_answer_is_one_enter_owned_session_not_arbitrary_prompt(monkeypatch, tmp_path, capsys):
    calls, _ = _codex_split(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_codex_session_buffer", lambda s: (_trust_screen(tmp_path), None))
    def verify(marker, model, effort, **kw):
        kw["sleep"](2)
        kw["sleep"](2)  # same stale screen: NEVER a second Enter
        kw["result"].update(sid="ours", model=model, effort=effort, turn_id="t1")
        return None
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verify)
    _spawn_codex_split(cwd=str(tmp_path))
    answers = [body for method, path, body in calls if path == "/pty/write" and body["data"] == "\r"]
    assert answers == [{"session_id": "pty-99-1", "data": "\r"}]
    assert "Answered Codex folder-trust prompt once" in capsys.readouterr().out


@pytest.mark.parametrize("screen", ["Approve a command?", "Permission requested", "unknown", None])
def test_unrecognized_or_unavailable_screen_never_gets_enter_and_failure_cleans_up(monkeypatch, tmp_path, screen):
    calls, _ = _codex_split(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_codex_session_buffer", lambda s: (screen, None if screen else "unavailable"))
    def verify(*_a, **kw): kw["sleep"](2); return "not verified"
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verify)
    with pytest.raises(SystemExit): _spawn_codex_split(cwd=str(tmp_path))
    assert not any(path == "/pty/write" and body["data"] == "\r" for _, path, body in calls)
    assert [path for method, path, _ in calls if method == "DELETE"] == ["/pty/pty-99-1"]


def test_folder_trust_enter_failure_is_not_retried_and_still_cleans_up(monkeypatch, tmp_path):
    calls, _ = _codex_split(monkeypatch, tmp_path)
    bridge = cli._bridge_request
    monkeypatch.setattr(cli, "_codex_session_buffer", lambda s: (_trust_screen(tmp_path), None))
    def request(method, path, body=None):
        response = bridge(method, path, body)
        return {"ok": False} if path == "/pty/write" and body["data"] == "\r" else response
    monkeypatch.setattr(cli, "_bridge_request", request)
    def verify(*_a, **kw): kw["sleep"](2); return None
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verify)
    with pytest.raises(SystemExit) as exc: _spawn_codex_split(cwd=str(tmp_path))
    assert exc.value.code == 2
    assert len([body for _, path, body in calls if path == "/pty/write" and body["data"] == "\r"]) == 1
    assert [path for method, path, _ in calls if method == "DELETE"] == ["/pty/pty-99-1"]


@pytest.mark.parametrize("later", ["Would you like to run this command?", "Would you like to allow access?",
    "Permission requested", "Approve this command?", "Allow once", "Allow always", "This requires your approval"])
def test_old_trust_screen_with_later_approval_is_never_answered(tmp_path, later):
    assert not cli._codex_folder_trust_screen(_trust_screen(tmp_path) + later)


def test_folder_trust_read_cannot_send_enter_after_verification_deadline(monkeypatch, tmp_path):
    calls, _ = _codex_split(monkeypatch, tmp_path)
    clock = _Clock()
    monkeypatch.setattr(cli.time, "monotonic", clock)
    def late_buffer(_s):
        clock.t += cli.CODEX_VERIFY_WAIT + 1
        return _trust_screen(tmp_path), None
    monkeypatch.setattr(cli, "_codex_session_buffer", late_buffer)
    def verify(*_a, **kw): kw["sleep"](2); return "not verified"
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verify)
    with pytest.raises(SystemExit): _spawn_codex_split(cwd=str(tmp_path))
    assert not any(path == "/pty/write" and body["data"] == "\r" for _, path, body in calls)


def test_actual_rollout_verifier_still_joins_marker_turn_after_one_trust_enter(monkeypatch, tmp_path):
    real_verify = fleet_policy.verify_codex_rollout
    calls, _ = _codex_split(monkeypatch, tmp_path)
    clock = _Clock()
    monkeypatch.setattr(cli.time, "monotonic", clock)
    monkeypatch.setattr(cli.time, "sleep", clock.sleep)
    monkeypatch.setattr(cli, "_codex_session_buffer", lambda _s: (_trust_screen(tmp_path), None))
    sessions = tmp_path / "recorded-sessions"
    bridge = cli._bridge_request
    def request(method, path, body=None):
        response = bridge(method, path, body)
        if path == "/pty/write" and body["data"] == "\r":
            marker = _typed(calls).strip().split()[-1]
            _rollout(sessions, "ours", "gpt-6.1-sol", "high", user=marker)
            file = next(sessions.rglob("rollout-ours.jsonl"))
            records = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
            records[0]["payload"]["cwd"] = str(tmp_path.resolve())
            file.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        return response
    monkeypatch.setattr(cli, "_bridge_request", request)
    def verify(marker, model, effort, **kw):
        return real_verify(marker, model, effort, sessions=sessions, clock=clock, **kw)
    monkeypatch.setattr(fleet_policy, "verify_codex_rollout", verify)
    _spawn_codex_split(cwd=str(tmp_path))
    assert len([body for _, path, body in calls if path == "/pty/write" and body["data"] == "\r"]) == 1
    assert not any(method == "DELETE" for method, _, _ in calls)


@pytest.mark.parametrize("reported", [None, "C:\\other", "C:\\w-other", "file:///C:/w", "c:\\w", "C:/w"])
def test_same_marker_model_turn_with_nonexact_session_cwd_is_refused(tmp_path, reported):
    _rollout(tmp_path, "ours", "gpt-6-sol", "high", user=MARK)
    path = next(tmp_path.rglob("rollout-ours.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["payload"]["cwd"] = reported
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    clock, result = _Clock(), {}
    why = fleet_policy.verify_codex_rollout(MARK, "gpt-6-sol", "high", sessions=tmp_path,
        wait=10, sleep=clock.sleep, clock=clock, expect_cwd="C:\\w", result=result)
    assert "session_meta.cwd" in why and result == {}


def test_exact_session_cwd_passes_and_duplicate_metadata_is_refused(tmp_path):
    _rollout(tmp_path, "ours", "gpt-6.1-sol", "high", user=MARK)
    clock = _Clock()
    assert fleet_policy.verify_codex_rollout(MARK, "gpt-6.1-sol", "high", sessions=tmp_path,
        wait=10, sleep=clock.sleep, clock=clock, expect_cwd="C:\\w") is None
    path = next(tmp_path.rglob("rollout-ours.jsonl"))
    first = path.read_text(encoding="utf-8").splitlines()[0]
    with path.open("a", encoding="utf-8") as f: f.write("\n" + first)
    assert "session_meta.cwd" in fleet_policy.verify_codex_rollout(MARK, "gpt-6.1-sol", "high", sessions=tmp_path,
        wait=10, sleep=clock.sleep, clock=clock, expect_cwd="C:\\w")


def test_launcher_passes_resolved_exact_cwd_to_same_marker_verifier(monkeypatch, tmp_path):
    calls, verified = _codex_split(monkeypatch, tmp_path)
    _spawn_codex_split(cwd=str(tmp_path))
    assert verified[0][3]["expect_cwd"] == str(tmp_path.resolve())
