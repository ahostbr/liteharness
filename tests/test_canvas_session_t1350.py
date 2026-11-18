"""T1350 part 3: canvas_session_id follows the PTY the seat's process runs in.

Measured 2026-11-18: a seat's row named a PTY that no longer existed, through many
re-registrations, while its CLI process was a direct child of another PTY's shell. The row
only ever kept its stored value (never-downgrade) or took the env value into an empty field.

Every process here is a row in a fake table read by the real ancestry walker, and the bridge
is a list handed to the matcher. No test reads a live process tree or calls a live bridge.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from ancestry_boundary import windows_processes
from liteharness import canvas_proof, checkpoint_restore, config, hooks

SEAT = "55555555-5555-4555-8555-555555555555"
HOST = "99999999-9999-4999-8999-999999999999"
ME = os.getpid()              # the registering process; in these tests it is also the seat's CLI
PANE, HOST_PANE = 2000, 2100  # pane shells: pids the bridge lists
TOOL_SHELL, HOST_CLI, DAEMON, STRANGER = 500, 1000, 3000, 4242
SHELL = "C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
T0 = 1_795_000_000.0          # Unix seconds; a bridge row's createdAt is the same clock in ms


def pty(session, pid, *, born=T0, **extra):
    return {"id": session, "pid": pid, "shell": SHELL, "createdAt": born * 1000 + 30, **extra}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hooks, "_LAST_PRESENCE", {})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: ME)
    monkeypatch.setattr(hooks, "_bridge_listening", lambda: True)

    def no_network(*args, **kwargs):  # the rest of registration (tab rename, canvas state) stays offline too
        raise OSError("tests never open a connection")

    monkeypatch.setattr(canvas_proof.urllib.request, "urlopen", no_network)
    (tmp_path / "agents").mkdir()
    (tmp_path / "names").mkdir()
    return tmp_path


@pytest.fixture
def bridge(monkeypatch):
    """What GET /pty/list answers. `calls` counts fetches; an Exception value is raised."""
    state = {"sessions": [], "calls": 0}

    def fetch(url, token, timeout=2.0):
        state["calls"] += 1
        if isinstance(state["sessions"], Exception):
            raise state["sessions"]
        return state["sessions"]

    monkeypatch.setattr(canvas_proof, "pty_sessions", fetch)
    return state


def register(source: str, **env_extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CODEX", "CLAUDE", "LITEHARNESS", "LITESUITE", "COPILOT", "GEMINI"))}
    env.update(LITEHARNESS_CLI="claude-code", LITEHARNESS_MODEL="test-model", LITEHARNESS_NO_PREAMBLE="1",
               CLAUDE_CODE_SESSION_ID=SEAT, LITESUITE_BRIDGE_TOKEN="fixture-token",
               LITESUITE_BRIDGE_URL="http://127.0.0.1:9")
    env.update(env_extra)
    with mock.patch.dict(os.environ, env, clear=True):
        hooks._apply_hook_context({
            "session_id": SEAT, "source": source, "hook_event_name": "SessionStart",
            "transcript_path": str(Path("transcripts") / f"{SEAT}.jsonl"),
        })
        hooks.register_presence()
    return json.loads((config.HARNESS_ROOT / "agents" / f"{SEAT}.json").read_text(encoding="utf-8"))


def stored(root, session):
    """The row as a spawner left it: a known pane, written before this registration."""
    (root / "agents" / f"{SEAT}.json").write_text(
        json.dumps({"agent_id": SEAT, "canvas_session_id": session, "session_pid": ME}), encoding="utf-8")


# ── the heal ─────────────────────────────────────────────────────────────────

def test_a_row_naming_a_dead_pty_moves_to_the_pty_its_cli_runs_in(root, bridge, monkeypatch):
    # The measured shape: CLI -> pane shell (listed as pty-3) -> the pty host. The row says pty-1.
    windows_processes(monkeypatch, {ME: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-3-live", PANE), pty("pty-9-other", HOST_PANE)]
    stored(root, "pty-1-dead")
    assert register("resume", LITESUITE_CANVAS_SESSION="pty-1-dead")["canvas_session_id"] == "pty-3-live"


def test_an_empty_field_takes_the_proven_pty_over_an_inherited_env(root, bridge, monkeypatch):
    windows_processes(monkeypatch, {ME: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-3-live", PANE)]
    assert register("startup", LITESUITE_CANVAS_SESSION="pty-7-inherited")["canvas_session_id"] == "pty-3-live"


def test_a_cli_that_is_the_ptys_own_process_is_proven_without_a_shell(root, bridge, monkeypatch):
    windows_processes(monkeypatch, {ME: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-5-native", ME, shell="claude.exe", harnessAgentId=SEAT)]
    stored(root, "pty-1-dead")
    assert register("resume")["canvas_session_id"] == "pty-5-native"


def test_control_a_row_that_is_already_right_is_left_alone(root, bridge, monkeypatch):
    windows_processes(monkeypatch, {ME: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-3-live", PANE)]
    stored(root, "pty-3-live")
    assert register("compact")["canvas_session_id"] == "pty-3-live"


# ── the inherited cases named at the never-downgrade rule: the row never moves ──

def test_a_cli_started_from_a_seats_tool_shell_does_not_take_the_seats_pane(root, bridge, monkeypatch):
    # nested CLI -> tool shell -> host seat's CLI -> host's pane shell. Its env names the host's pane.
    windows_processes(monkeypatch, {ME: (TOOL_SHELL, T0 + 60), TOOL_SHELL: (HOST_CLI, T0 + 50),
                                    HOST_CLI: (HOST_PANE, T0 + 6), HOST_PANE: (DAEMON, T0),
                                    DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-9-host", HOST_PANE)]
    stored(root, "pty-4-mine")
    assert register("compact", LITESUITE_CANVAS_SESSION="pty-9-host")["canvas_session_id"] == "pty-4-mine"


def test_a_seat_of_a_pty_daemon_the_host_started_does_not_take_the_hosts_pane(root, bridge, monkeypatch):
    # daemon seat -> the daemon's own shell (not a bridge PTY) -> daemon -> host CLI -> host's pane shell.
    daemon_shell, seat_daemon = 700, 800
    windows_processes(monkeypatch, {ME: (daemon_shell, T0 + 90), daemon_shell: (seat_daemon, T0 + 80),
                                    seat_daemon: (HOST_CLI, T0 + 70), HOST_CLI: (HOST_PANE, T0 + 6),
                                    HOST_PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-9-host", HOST_PANE)]
    stored(root, "pty-4-mine")
    assert register("resume", LITESUITE_CANVAS_SESSION="pty-9-host")["canvas_session_id"] == "pty-4-mine"


def test_a_seat_of_a_litesuite_relaunched_inside_a_seat_gets_its_own_pane_not_the_hosts(
        root, bridge, monkeypatch):
    # new seat -> its own pane shell (listed) -> the relaunched app -> host CLI -> host's old pane shell,
    # which the bridge also lists. Only the direct parent counts; the env names the host's pane.
    app = 900
    windows_processes(monkeypatch, {ME: (PANE, T0 + 200), PANE: (app, T0 + 190), app: (HOST_CLI, T0 + 100),
                                    HOST_CLI: (HOST_PANE, T0 + 6), HOST_PANE: (DAEMON, T0),
                                    DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-9-host", HOST_PANE), pty("pty-2-own", PANE, born=T0 + 190)]
    assert register("startup", LITESUITE_CANVAS_SESSION="pty-9-host")["canvas_session_id"] == "pty-2-own"


def test_a_cli_spawned_directly_by_another_seats_agent_pty_does_not_take_it(root, bridge, monkeypatch):
    # CLI -> agent process that IS the PTY (a native launch, so the row names its seat).
    windows_processes(monkeypatch, {ME: (HOST_CLI, T0 + 60), HOST_CLI: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-9-host", HOST_CLI, shell="litetui.exe", harnessAgentId=HOST)]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"


@pytest.mark.parametrize("row", [
    pty("pty-9-host", PANE, harnessAgentId=HOST),             # a pane shell created for another seat
    pty("pty-9-host", PANE, shell="litetui.exe"),             # the parent PTY is not a shell
    pty("pty-9-host", PANE, shell=None),                      # a parent PTY that does not say what it is
])
def test_a_parent_pty_that_is_not_this_seats_plain_shell_proves_nothing(root, bridge, monkeypatch, row):
    windows_processes(monkeypatch, {ME: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [row]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"


def test_a_session_pid_this_registration_does_not_descend_from_proves_nothing(root, bridge, monkeypatch):
    # The row's pid was reused by a stranger that really does run in a pane. This process is not under it.
    windows_processes(monkeypatch, {ME: (TOOL_SHELL, T0 + 60), TOOL_SHELL: (DAEMON, T0 + 50),
                                    STRANGER: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    monkeypatch.setattr(hooks, "_resolve_session_pid", lambda existing=None: STRANGER)
    bridge["sessions"] = [pty("pty-3-strangers", PANE)]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"
    assert bridge["calls"] == 0  # refused on the process walk, before any bridge call


def test_a_parent_pid_reused_after_the_cli_was_born_proves_nothing(root, bridge, monkeypatch):
    # T1337 walked upward: a "parent" younger than its child is a stranger that took a dead parent's pid.
    windows_processes(monkeypatch, {ME: (PANE, T0 + 6), PANE: (DAEMON, T0 + 500), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-3-reused", PANE, born=T0 + 500)]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"


def test_a_pty_row_older_than_the_process_holding_its_pid_proves_nothing(root, bridge, monkeypatch):
    # The bridge still lists a session whose shell died; its pid now belongs to this seat's real parent.
    windows_processes(monkeypatch, {ME: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-3-stale", PANE, born=T0 - 3600)]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"


def test_two_rows_with_the_parents_pid_prove_nothing(root, bridge, monkeypatch):
    windows_processes(monkeypatch, {ME: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)})
    bridge["sessions"] = [pty("pty-3-a", PANE), pty("pty-3-b", PANE)]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"


# ── failure is "not proven": today's value, no crash, no cleared field ─────────

LIVE_TREE = {ME: (PANE, T0 + 6), PANE: (DAEMON, T0), DAEMON: (0, T0 - 900)}


@pytest.mark.parametrize("answer", [
    None, [], "sessions", [None, 7], [{"id": "pty-3-live"}], [{"id": "pty-3-live", "pid": str(PANE)}],
    [{"id": "", "pid": PANE, "shell": SHELL, "createdAt": T0 * 1000}],
    [{"id": 7, "pid": PANE, "shell": SHELL, "createdAt": T0 * 1000}],            # an id that is not a string
    [{"id": "pty-3-live", "pid": PANE, "shell": SHELL}],                         # no createdAt
    [{"id": "pty-3-live", "pid": True, "shell": SHELL, "createdAt": T0 * 1000}],  # a bool is not a pid
    OSError("bridge went away"), TimeoutError("2 s"), ValueError("not json"), RuntimeError("anything"),
])
def test_an_unusable_bridge_answer_keeps_the_stored_value_and_registers(root, bridge, monkeypatch, answer):
    windows_processes(monkeypatch, LIVE_TREE)
    bridge["sessions"] = answer
    stored(root, "pty-4-mine")
    row = register("resume", LITESUITE_CANVAS_SESSION="pty-7-inherited")
    assert row["canvas_session_id"] == "pty-4-mine" and row["agent_id"] == SEAT


def test_an_unreadable_process_tree_keeps_the_stored_value_and_never_asks_the_bridge(
        root, bridge, monkeypatch):
    windows_processes(monkeypatch, LIVE_TREE, denied=(ME,))
    bridge["sessions"] = [pty("pty-3-live", PANE)]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"
    assert bridge["calls"] == 0


def test_a_bridge_that_is_not_listening_is_never_asked(root, bridge, monkeypatch):
    windows_processes(monkeypatch, LIVE_TREE)
    monkeypatch.setattr(hooks, "_bridge_listening", lambda: False)
    bridge["sessions"] = [pty("pty-3-live", PANE)]
    stored(root, "pty-4-mine")
    assert register("resume")["canvas_session_id"] == "pty-4-mine"
    assert bridge["calls"] == 0


def test_a_seat_outside_litesuite_keeps_todays_behaviour_and_makes_no_call(root, bridge, monkeypatch):
    windows_processes(monkeypatch, LIVE_TREE)
    bridge["sessions"] = [pty("pty-3-live", PANE)]
    row = register("startup", LITESUITE_BRIDGE_TOKEN="")
    assert "canvas_session_id" not in row and bridge["calls"] == 0
    row = register("resume", LITESUITE_BRIDGE_TOKEN="", LITESUITE_CANVAS_SESSION="pty-7-env")
    assert row["canvas_session_id"] == "pty-7-env" and bridge["calls"] == 0  # env still fills an empty field


@pytest.mark.parametrize("answer", [None, "sessions", 7, {"sessions": []}])
def test_the_proof_itself_answers_none_for_a_bridge_answer_that_is_not_a_list(monkeypatch, answer):
    # Not left to the caller's catch-all: the function is total on its own.
    windows_processes(monkeypatch, LIVE_TREE)
    assert canvas_proof.proven_session(SEAT, ME, lambda: answer) is None


def test_no_proof_leaves_the_env_fill_of_an_empty_field_as_it_was(root, bridge, monkeypatch):
    windows_processes(monkeypatch, LIVE_TREE)
    bridge["sessions"] = []
    assert register("startup", LITESUITE_CANVAS_SESSION="pty-7-env")["canvas_session_id"] == "pty-7-env"


# ── the fetch itself: any transport or shape failure is None, never an exception ──

class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


@pytest.mark.parametrize("outcome, expected", [
    (_Response(b'{"sessions": [{"id": "pty-1", "pid": 5}]}'), [{"id": "pty-1", "pid": 5}]),
    (_Response(b'{"sessions": "no"}'), None),
    (_Response(b'{"error": "unauthorized"}'), None),
    (_Response(b'[1, 2]'), None),
    (_Response(b'not json'), None),
    (_Response(b'\xff\xfe'), None),
    (OSError("refused"), None),
    (TimeoutError("timed out"), None),
])
def test_the_fetch_answers_a_list_or_none(monkeypatch, outcome, expected):
    seen = {}

    def urlopen(request, timeout):
        seen.update(url=request.full_url, auth=request.get_header("Authorization"), timeout=timeout)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(canvas_proof.urllib.request, "urlopen", urlopen)
    assert canvas_proof.pty_sessions("http://127.0.0.1:9", "tok") == expected
    assert seen == {"url": "http://127.0.0.1:9/pty/list", "auth": "Bearer tok", "timeout": 2.0}


def test_the_shell_names_are_the_ones_restore_already_trusts():
    assert canvas_proof.SHELLS == checkpoint_restore.SHELLS
