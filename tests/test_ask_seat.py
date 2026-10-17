"""T0583-A: `liteharness ask-seat`, the read path and its guards. No usage is spent: the reader
subprocess is faked, and the real ~/.liteharness and ~/.claude are never touched."""
import json
import os
import subprocess
import sys
import time

import pytest

from liteharness import agent_names, ask_seat, cli, config, inbox

SEAT_ID = "11111111-1111-4111-8111-111111111111"
CONVO = "22222222-2222-4222-8222-222222222222"
ASKER = "99999999-9999-4999-8999-999999999999"
CWD = "C:\\Projects\\demo.app"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "h"
    (root / "agents").mkdir(parents=True)
    (root / "retired-agents").mkdir()
    projects = tmp_path / "claude-projects"
    projects.mkdir()
    monkeypatch.setattr(config, "get_root", lambda: root)
    monkeypatch.setattr(ask_seat, "_claude_projects", lambda: projects)
    monkeypatch.setattr(cli, "_live_owner_pid", lambda presence: None)
    monkeypatch.setattr(ask_seat.shutil, "which", lambda name: name)
    return root, projects


def _claude_seat(root, projects, name="Mason", agent_id=SEAT_ID, retired=False,
                 registered_at="2026-10-01T10:00:00+00:00", messages=None, cwd=CWD, context=0, **fields):
    """A Claude seat as the registry holds it: a presence row, and a transcript named by its agent id."""
    _presence(root, agent_id, retired=retired, cli="claude-code", name=name, registered_at=registered_at,
              model="claude-opus-5-5", **fields)
    folder = projects / "some-project"
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (role, text) in enumerate(messages or [("user", "hello"), ("assistant", "hi")]):
        message = {"role": role, "content": text}
        if role == "assistant" and context:
            message["usage"] = {"input_tokens": 10, "cache_read_input_tokens": context - 15,
                                "cache_creation_input_tokens": 5, "output_tokens": 99}
        lines.append(json.dumps({"type": role, "timestamp": f"2026-10-01T10:00:{i:02d}Z", "cwd": str(cwd),
                                 "message": message}))
    (folder / f"{agent_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _presence(root, agent_id, retired=False, **fields):
    folder = root / ("retired-agents" if retired else "agents")
    (folder / f"{agent_id}.json").write_text(json.dumps({"agent_id": agent_id, **fields}), encoding="utf-8")


class FakeRun:
    """Stands in for subprocess.run; remembers the call and answers like `claude -p --output-format json`."""
    def __init__(self, result="The fix was X (#3).", usage=None):
        self.calls = []
        self.payload = json.dumps({"type": "result", "result": result,
                                   "usage": usage or {"input_tokens": 100, "output_tokens": 20}}).encode()

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=self.payload, stderr=b"")


def test_unknown_name_and_unknown_id_are_refused(env, tmp_path, monkeypatch):
    from liteharness import owned_launch
    (tmp_path / "litetui" / ".agents").mkdir(parents=True)               # a data root that holds no such seat
    monkeypatch.setattr(owned_launch, "data_root", lambda resolution=None: tmp_path / "litetui")
    with pytest.raises(ask_seat.AskRefused, match="no agent named Nobody"):
        ask_seat.resolve_seat("Nobody")
    with pytest.raises(ask_seat.AskRefused, match="no seat has agent id"):
        ask_seat.resolve_seat(SEAT_ID)


def test_unknown_name_with_no_litetui_root_says_the_store_was_not_searched(env, monkeypatch):
    from liteharness import owned_launch

    def unknown(resolution=None):
        raise owned_launch.RootUnknown("LiteTUI data root unknown; configure LITETUI_DATA_ROOT")
    monkeypatch.setattr(owned_launch, "data_root", unknown)
    with pytest.raises(ask_seat.AskRefused, match=r"no seat matches Nobody.*could not be searched.*data root unknown"):
        ask_seat.resolve_seat("Nobody")


def _agents_store(tmp_path, monkeypatch, name="Newer", agent_id=SEAT_ID, convo=CONVO):
    """A LiteTUI data root in the `.agents` layout, with one agent and one conversation."""
    from liteharness import owned_launch
    data_root = tmp_path / "litetui"
    home = data_root / ".agents" / name
    (home / "conversations" / convo).mkdir(parents=True)
    (home / "settings.json").write_text(json.dumps({
        "schema_version": 1, "name": name, "agent_id": agent_id,
        "execution": {"backend": "codex", "model": "gpt", "thinking_level": "high"}}), encoding="utf-8")
    (home / "conversations" / convo / "convo.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(owned_launch, "data_root", lambda resolution=None: data_root)
    return data_root, home


def test_an_agents_seat_unknown_to_names_json_resolves_from_the_store_newest_conversation(env, tmp_path, monkeypatch):
    data_root, home = _agents_store(tmp_path, monkeypatch)
    newer = "88888888-8888-4888-8888-888888888888"
    (home / "conversations" / newer).mkdir()
    (home / "conversations" / newer / "convo.jsonl").write_text("{}\n", encoding="utf-8")
    os.utime(home / "conversations" / CONVO / "convo.jsonl", (1, 1))      # the older one
    for target in ("newer", SEAT_ID):
        entry = ask_seat.resolve_seat(target)
        assert (entry["name"], entry["agent_id"], entry["convo_id"], entry["backend"]) \
            == ("Newer", SEAT_ID, newer, "codex")
    assert ask_seat.litetui_transcript(entry) == home / "conversations" / newer / "convo.jsonl"


def test_an_unknown_litetui_root_is_not_called_convos_era(env, monkeypatch):
    from liteharness import owned_launch

    def unknown(resolution=None):
        raise owned_launch.RootUnknown("LiteTUI data root unknown; configure LITETUI_DATA_ROOT")
    monkeypatch.setattr(owned_launch, "data_root", unknown)
    with pytest.raises(ask_seat.AskRefused, match="LiteTUI data root unknown") as refused:
        ask_seat.litetui_transcript({"name": "Old", "agent_id": SEAT_ID, "convo_id": CONVO})
    assert ".convos-era" not in str(refused.value)


def test_the_root_comes_from_the_bridge_when_the_resolver_has_none(env, tmp_path, monkeypatch):
    from liteharness import owned_launch, resume_seat

    def unknown(resolution=None):
        raise owned_launch.RootUnknown("unknown")
    monkeypatch.setattr(owned_launch, "data_root", unknown)
    monkeypatch.setattr(resume_seat, "_bridge_data_root", lambda value, **kw: tmp_path / "from-bridge")
    assert ask_seat.litetui_root("Newer") == tmp_path / "from-bridge"


def test_claude_seat_resolves_through_the_registry_case_insensitively(env):
    root, projects = env
    _claude_seat(root, projects)
    entry = ask_seat.resolve_seat("mason")
    assert (entry["agent_id"], entry["convo_id"], entry["name"], entry["model"])         == (SEAT_ID, SEAT_ID, "Mason", "claude-opus-5-5")
    assert ask_seat.resolve_seat(SEAT_ID)["name"] == "Mason"
    assert ask_seat.claude_transcript(entry).name == f"{SEAT_ID}.jsonl"


def test_a_retired_seat_resolves_from_retired_agents(env):
    root, projects = env
    _claude_seat(root, projects, retired=True, exited_at="2026-10-01T11:00:00+00:00")
    assert ask_seat.resolve_seat("Mason")["agent_id"] == SEAT_ID


def test_the_registry_beats_a_stale_names_json_claude_row(env):
    root, projects = env
    _claude_seat(root, projects)
    agent_names.record_name("Mason", "55555555-5555-4555-8555-555555555555", CONVO, None, backend="claude")
    assert ask_seat.resolve_seat("Mason")["agent_id"] == SEAT_ID


def test_of_several_rows_for_one_name_the_latest_registered_is_the_seat(env):
    root, projects = env
    newer = "66666666-6666-4666-8666-666666666666"
    _claude_seat(root, projects, retired=True, registered_at="2026-09-01T10:00:00+00:00")
    _claude_seat(root, projects, agent_id=newer, registered_at="2026-10-01T10:00:00+00:00")
    assert ask_seat.resolve_seat("Mason")["agent_id"] == newer


def test_an_older_row_asked_for_by_id_is_refused_naming_the_latest(env):
    root, projects = env
    newer = "66666666-6666-4666-8666-666666666666"
    _claude_seat(root, projects, retired=True, registered_at="2026-09-01T10:00:00+00:00")
    _claude_seat(root, projects, agent_id=newer, registered_at="2026-10-01T10:00:00+00:00")
    with pytest.raises(ask_seat.AskRefused, match=rf"superseded by {newer} \(Mason\); ask Mason instead"):
        ask_seat.resolve_seat(SEAT_ID)


def test_live_seat_is_refused_by_name_and_by_id(env, monkeypatch):
    root, projects = env
    _claude_seat(root, projects, session_pid=4242)
    monkeypatch.setattr(cli, "_live_owner_pid", lambda presence: 4242)
    for target in ("Mason", SEAT_ID):
        with pytest.raises(ask_seat.AskRefused, match="live: message Mason by inbox instead"):
            ask_seat.resolve_seat(target)


def test_a_seat_whose_pid_a_later_registration_took_is_refused_naming_it(env):
    root, projects = env
    successor = "33333333-3333-4333-8333-333333333333"
    _claude_seat(root, projects, session_pid=777)
    _presence(root, successor, cli="claude-code", name="Mason-2", session_pid=777,
              registered_at="2026-10-01T12:00:00+00:00")
    for target in ("Mason", SEAT_ID):
        with pytest.raises(ask_seat.AskRefused, match="superseded by a later registration; ask Mason-2 instead"):
            ask_seat.resolve_seat(target)


def test_transcript_is_found_by_session_id_and_ambiguity_is_refused_with_the_paths(env):
    root, projects = env
    _claude_seat(root, projects)
    entry = ask_seat.resolve_seat("Mason")
    second = projects / "another-project"
    second.mkdir()
    (second / f"{SEAT_ID}.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ask_seat.AskRefused, match="2 transcripts") as refused:
        ask_seat.claude_transcript(entry)
    assert "some-project" in str(refused.value) and "another-project" in str(refused.value)
    (second / f"{SEAT_ID}.jsonl").unlink()
    (projects / "some-project" / f"{SEAT_ID}.jsonl").unlink()
    with pytest.raises(ask_seat.AskRefused, match="0 transcripts"):
        ask_seat.claude_transcript(entry)


def test_convos_era_litetui_seat_is_refused_with_one_clear_line(env, tmp_path, monkeypatch):
    from liteharness import owned_launch
    data_root = tmp_path / "litetui"
    (data_root / ".convos" / CONVO).mkdir(parents=True)
    (data_root / ".convos" / CONVO / "convo.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(owned_launch, "data_root", lambda resolution=None: data_root)
    agent_names.record_name("Old", SEAT_ID, CONVO, None, backend="codex", model="gpt")
    with pytest.raises(ask_seat.AskRefused,
                       match=r"ask-seat supports \.agents-layout LiteTUI agents only; Old is a \.convos-era seat"):
        ask_seat.litetui_transcript(agent_names.resolve_name("Old"))


def test_agents_layout_litetui_seat_is_read_through_the_venv_python_not_a_shim(env, tmp_path, monkeypatch):
    data_root, home = _agents_store(tmp_path, monkeypatch)
    venv_python = data_root / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    agent_names.record_name("Newer", SEAT_ID, CONVO, None, backend="codex", model="gpt")
    path = ask_seat.litetui_transcript(agent_names.resolve_name("Newer"))
    assert path == home / "conversations" / CONVO / "convo.jsonl"

    calls = []

    def export(argv, **kwargs):
        calls.append((argv, kwargs))
        open(argv[-1], "w", encoding="utf-8").write(
            "# Conversation\n\n## You\n\n```\nwhy red?\n```\n\n## Assistant\n\n```\nbecause X\n```\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(ask_seat.subprocess, "run", export)
    messages = ask_seat.read_litetui(path)
    assert [(m.role, "because X" in m.text) for m in messages] == [("you", False), ("assistant", True)]
    argv, kwargs = calls[0]
    assert argv[0] == str(venv_python) and argv[1] == "-c" and argv[3:5] == [str(path), argv[-1]]
    assert not argv[0].lower().endswith((".cmd", ".bat")) and kwargs["cwd"] == str(data_root)


def test_no_exporter_is_a_clear_refusal(env, tmp_path, monkeypatch):
    data_root, home = _agents_store(tmp_path, monkeypatch)                 # no .venv under the root
    with pytest.raises(ask_seat.AskRefused, match="LiteTUI exporter unavailable.*no .venv"):
        ask_seat.read_litetui(home / "conversations" / CONVO / "convo.jsonl")


def test_excerpt_takes_the_hit_with_forty_messages_either_side_and_is_capped(env):
    messages = [ask_seat.Message(i, f"t{i}", "assistant", f"filler {i} " + "x" * 50) for i in range(300)]
    messages[150] = ask_seat.Message(150, "t150", "assistant", "we chose the zebra scheme")
    excerpt = ask_seat.build_excerpt(messages, "why the zebra scheme?")
    assert "[#110 " in excerpt and "[#190 " in excerpt     # +-40 around the hit
    assert "[#109 " not in excerpt and "[#191 " not in excerpt
    assert "81 of 300 messages" in excerpt

    big = [ask_seat.Message(i, "", "assistant", "zebra " + "y" * 5000) for i in range(2000)]
    capped = ask_seat.build_excerpt(big, "zebra", handoff="h" * 50_000)
    assert len(capped) <= ask_seat.EXCERPT_CHAR_CAP + 200   # the cap, plus the two section headers
    assert "=== HANDOFF ===" in capped and "h" * ask_seat.HANDOFF_CHAR_CAP in capped
    assert "h" * (ask_seat.HANDOFF_CHAR_CAP + 1) not in capped


def test_excerpt_without_a_hit_falls_back_to_the_tail(env):
    messages = [ask_seat.Message(i, "", "user", f"line {i}") for i in range(20)]
    excerpt = ask_seat.build_excerpt(messages, "qqqqq")
    assert "[#19 " in excerpt and "20 of 20 messages" in excerpt


def test_handoff_prefers_a_file_then_the_last_compaction_summary(env):
    root, _ = env
    summary = [ask_seat.Message(0, "t0", "summary", "S1"), ask_seat.Message(1, "t1", "summary", "S2")]
    assert "S2" in ask_seat.find_handoff("Mason", summary) and "S1" not in ask_seat.find_handoff("Mason", summary)
    (root / "handoffs").mkdir()
    (root / "handoffs" / "handoff-mason-2026.md").write_text("FILE HANDOFF", encoding="utf-8")
    assert "FILE HANDOFF" in ask_seat.find_handoff("Mason", summary)
    assert ask_seat.find_handoff("Nobody", []) == ""


def test_the_reader_has_no_tools_no_mcp_no_hooks_and_is_sonnet(env):
    argv = ask_seat.answer_argv(ask_seat.ANSWER_MODEL)
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--tools") + 1] == ""
    assert "haiku" not in " ".join(argv).lower()
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in argv
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}
    assert "--resume" not in argv and "--fork-session" not in argv
    assert ask_seat.answer_argv("claude-opus-5-5")[ask_seat.answer_argv("claude-opus-5-5").index("--model") + 1] \
        == "claude-opus-5-5"


def test_ask_answers_logs_and_mails_the_asker_from_the_seat(env, monkeypatch):
    root, projects = env
    _claude_seat(root, projects, messages=[("user", "why the zebra scheme?"), ("assistant", "zebra won: cheaper")])
    fake = FakeRun(result="Zebra won because it was cheaper (#1).")
    monkeypatch.setattr(ask_seat.subprocess, "run", fake)
    sent = []
    monkeypatch.setattr(inbox, "send", lambda *a, **k: sent.append((a, k)) or "msg-1")

    result = ask_seat.ask("Mason", "why the zebra scheme?", asker=ASKER)

    assert result.answer.startswith("Zebra won") and result.tokens == 120 and result.cited == ["#1"]
    argv, kwargs = fake.calls[0]
    assert "zebra won: cheaper" in kwargs["input"].decode()            # excerpt arrives on stdin
    assert "UNKNOWN" in kwargs["input"].decode()
    (from_id, to_id, body), mail = sent[0][0], sent[0][1]
    assert (from_id, to_id, mail["msg_type"]) == (SEAT_ID, ASKER, "ANSWER")
    assert "historical: as remembered by Mason at " in body and "Zebra won" in body
    row = json.loads((root / "ask-seat" / "asks.jsonl").read_text().splitlines()[0])
    assert (row["asker"], row["seat"], row["mode"], row["tokens"]) == (ASKER, "Mason", "read", 120)
    assert row["question"] == "why the zebra scheme?" and "seconds" in row


def test_ask_without_an_asker_prints_but_mails_nothing(env, monkeypatch, capsys):
    root, projects = env
    _claude_seat(root, projects)
    monkeypatch.setattr(ask_seat.subprocess, "run", FakeRun(result="plain answer"))
    monkeypatch.setattr(inbox, "send", lambda *a, **k: pytest.fail("no asker, nothing to mail"))
    monkeypatch.delenv("LITEHARNESS_AGENT_ID", raising=False)
    assert ask_seat.main(["Mason", "hello?"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "plain answer" and "not mailed" in captured.err


def test_reader_failure_is_a_refusal_not_a_traceback(env, monkeypatch, capsys):
    root, projects = env
    _claude_seat(root, projects)
    monkeypatch.setattr(ask_seat.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"boom"))
    assert ask_seat.main(["Mason", "hello?", "--from", ASKER]) == 1
    assert "ASK-SEAT REFUSED: the reader failed (1): boom" in capsys.readouterr().err


def test_unknown_flag_and_missing_question_are_usage_errors(env):
    for argv in (["Mason", "q", "--sleep"], ["Mason"]):
        with pytest.raises(SystemExit) as exc:
            ask_seat.main(argv)
        assert exc.value.code == 2


def test_cli_dispatches_ask_seat(monkeypatch):
    seen = []
    monkeypatch.setattr(ask_seat, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["liteharness", "ask-seat", "Mason", "why?"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0 and seen == [["Mason", "why?"]]


# ---- T0583-B: --wake ----------------------------------------------------------------------

def _wake_payload(result="I remember: X.", fork="77777777-7777-4777-8777-777777777777"):
    return {"type": "result", "result": result, "session_id": fork,
            "usage": {"input_tokens": 5, "cache_read_input_tokens": 100_000, "cache_creation_input_tokens": 20,
                      "output_tokens": 30}}


def _wake_run(payload=None):
    body = json.dumps(payload or _wake_payload()).encode()
    return lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout=body, stderr=b"")


def test_wake_command_is_the_design_command(env):
    root, projects = env
    _claude_seat(root, projects)
    entry = ask_seat.resolve_seat("Mason")
    argv = ask_seat.wake_argv(entry, "why X?", ASKER, None)
    assert argv[1] == "-p" and argv[2].endswith("why X?") and "Do not act." in argv[2]
    assert argv[3:6] == ["--resume", SEAT_ID, "--fork-session"]
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"          # the seat's own model
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True}
    assert argv[argv.index("--output-format") + 1] == "json"
    override = ask_seat.wake_argv(entry, "q", None, "claude-haiku-4-5")
    assert override[override.index("--model") + 1] == "claude-haiku-4-5"


def test_last_context_is_the_final_assistant_turns_input_plus_both_caches(tmp_path, monkeypatch):
    path = tmp_path / "t.jsonl"

    def turn(context):
        return json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 1, "cache_read_input_tokens": context, "cache_creation_input_tokens": 2,
            "output_tokens": 500}}})
    user = json.dumps({"type": "user", "message": {}})
    path.write_text("\n".join([turn(1000), user, turn(222_000), user]) + "\n", encoding="utf-8")
    assert ask_seat.last_context_tokens(path) == 222_003            # output tokens are not context
    monkeypatch.setattr(ask_seat, "TAIL_BYTES", 40)                 # last turn is outside the tail: scan forward
    assert ask_seat.last_context_tokens(path) == 222_003
    path.write_text("{}\n", encoding="utf-8")
    assert ask_seat.last_context_tokens(path) == 0


def test_wake_runs_in_the_seats_cwd_prints_context_and_mails_the_fork(env, tmp_path, monkeypatch, capsys):
    root, projects = env
    _claude_seat(root, projects, cwd=tmp_path, context=90_000)
    calls = []
    run = _wake_run()
    monkeypatch.setattr(ask_seat.subprocess, "run", lambda argv, **k: calls.append((argv, k)) or run(argv))
    sent = []
    monkeypatch.setattr(inbox, "send", lambda *a, **k: sent.append((a, k)) or "m")

    result = ask_seat.ask("Mason", "why X?", asker=ASKER, wake=True)

    argv, kwargs = calls[0]
    assert "--fork-session" in argv and "--resume" in argv and kwargs["cwd"] == str(tmp_path)
    assert result.mode == "wake" and result.fork_id.startswith("77777777") and result.tokens == 100_055
    assert "last-turn context 90,000 tokens" in capsys.readouterr().err
    (_, _, body), mail = sent[0][0], sent[0][1]
    assert mail["msg_type"] == "ANSWER" and "Fork session: 77777777" in body and "wake" in body
    row = json.loads((root / "ask-seat" / "asks.jsonl").read_text().splitlines()[0])
    assert (row["mode"], row["fork"]) == ("wake", result.fork_id)
    assert not (root / "ask-seat" / f"{SEAT_ID}.lock").exists()      # released


def test_wake_above_150k_refuses_without_yes_and_names_the_read_cost(env, tmp_path, monkeypatch):
    root, projects = env
    _claude_seat(root, projects, cwd=tmp_path, context=200_000,
                 messages=[("user", "why X?"), ("assistant", "because X")])
    monkeypatch.setattr(ask_seat.subprocess, "run", lambda *a, **k: pytest.fail("a refused wake must not run"))
    with pytest.raises(ask_seat.AskRefused, match=r"re-reads 200,000 tokens uncached.*read path costs about \d") as r:
        ask_seat.ask("Mason", "why X?", asker=ASKER, wake=True)
    assert "--yes" in str(r.value)

    monkeypatch.setattr(ask_seat.subprocess, "run", _wake_run())
    monkeypatch.setattr(inbox, "send", lambda *a, **k: "m")
    assert ask_seat.ask("Mason", "why X?", asker=ASKER, wake=True, yes=True).mode == "wake"


def test_the_limit_itself_is_allowed(env, tmp_path, monkeypatch):
    root, projects = env
    _claude_seat(root, projects, cwd=tmp_path, context=ask_seat.WAKE_CONTEXT_LIMIT)
    monkeypatch.setattr(ask_seat.subprocess, "run", _wake_run())
    assert ask_seat.ask("Mason", "q", wake=True).mode == "wake"


def test_only_one_wake_at_a_time_per_seat(env, tmp_path, monkeypatch):
    root, projects = env
    _claude_seat(root, projects, cwd=tmp_path, context=1000)
    lock = root / "ask-seat" / f"{SEAT_ID}.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    monkeypatch.setattr(ask_seat.subprocess, "run", lambda *a, **k: pytest.fail("a locked seat must not wake"))
    with pytest.raises(ask_seat.AskRefused, match="Mason is already being woken"):
        ask_seat.ask("Mason", "q", wake=True)
    assert lock.exists()                                             # the other asker's lock is not ours to remove

    old = time.time() - ask_seat.WAKE_TIMEOUT_SECONDS - 120          # a crashed asker's lock is reclaimed
    os.utime(lock, (old, old))
    monkeypatch.setattr(ask_seat.subprocess, "run", _wake_run())
    assert ask_seat.ask("Mason", "q", wake=True).mode == "wake" and not lock.exists()


def test_a_failed_wake_still_releases_its_lock(env, tmp_path, monkeypatch):
    root, projects = env
    _claude_seat(root, projects, cwd=tmp_path, context=1000)
    monkeypatch.setattr(ask_seat.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"boom"))
    with pytest.raises(ask_seat.AskRefused, match="the wake failed"):
        ask_seat.ask("Mason", "q", wake=True)
    assert not (root / "ask-seat" / f"{SEAT_ID}.lock").exists()


def test_wake_needs_the_original_cwd(env, monkeypatch):
    root, projects = env
    _claude_seat(root, projects, cwd="C:\\gone\\forever", context=1000)
    monkeypatch.setattr(ask_seat.subprocess, "run", lambda *a, **k: pytest.fail("no cwd, no wake"))
    with pytest.raises(ask_seat.AskRefused, match="original cwd is unknown or gone"):
        ask_seat.ask("Mason", "q", wake=True)


def test_wake_on_a_litetui_seat_is_refused_and_reading_is_still_offered(env, monkeypatch, capsys):
    agent_names.record_name("Lite", SEAT_ID, CONVO, None, backend="codex", model="gpt")
    monkeypatch.setattr(ask_seat.subprocess, "run", lambda *a, **k: pytest.fail("refused before any process"))
    with pytest.raises(ask_seat.AskRefused, match="wake is Claude-only in v1; reading works"):
        ask_seat.ask("Lite", "q", wake=True)
    assert ask_seat.main(["Lite", "q", "--wake"]) == 1
    assert "reading works" in capsys.readouterr().err


# ---- excerpt recall (critic finding 1 and 2) ----------------------------------------------

def _generic_fixture(count=1200, target=700):
    """Every message says the generic words; one message holds the rare term. Ties used to go to message 0."""
    messages = [ask_seat.Message(i, "", "assistant",
                                 f"We changed the work and it happened again in step {i}. " + "filler " * 40)
                for i in range(count)]
    messages[target] = ask_seat.Message(target, "", "assistant",
                                        "The zorblax module was changed because of the race. " + "filler " * 40)
    return messages


@pytest.mark.parametrize("question", ["zorblax", "why was zorblax changed?",
                                      "what happened with zorblax in this work?",
                                      "why did we change the zorblax work that happened?"])
def test_a_generic_word_question_still_finds_the_rare_term(question):
    messages = _generic_fixture()
    excerpt = ask_seat.build_excerpt(messages, question)
    assert "[#700 " in excerpt and len(excerpt) <= ask_seat.EXCERPT_CHAR_CAP + 300


def test_rarer_terms_outrank_more_terms():
    messages = [ask_seat.Message(i, "", "user", "alpha beta gamma delta " + "x " * 10) for i in range(300)]
    messages[250] = ask_seat.Message(250, "", "user", "only quokka here")
    excerpt = ask_seat.build_excerpt(messages, "alpha beta gamma delta quokka")
    assert "[#250 " in excerpt


def test_every_hit_survives_when_the_windows_cannot_all_fit():
    messages = [ask_seat.Message(i, "", "assistant", f"line {i} " + "y" * 1900) for i in range(400)]
    messages[100] = ask_seat.Message(100, "", "assistant", "the quokka was renamed")
    messages[300] = ask_seat.Message(300, "", "assistant", "the wombat was deleted")
    excerpt = ask_seat.build_excerpt(messages, "what happened to the quokka and the wombat?")
    assert "[#100 " in excerpt and "[#300 " in excerpt           # both hits, each trimmed to fit
    assert "NO MESSAGE MATCHES" not in excerpt and len(excerpt) <= ask_seat.EXCERPT_CHAR_CAP + 300
    assert "[#99 " in excerpt and "[#301 " in excerpt            # trimmed windows, not dropped ones


def test_a_question_that_matches_nothing_says_so_instead_of_pretending():
    messages = [ask_seat.Message(i, "", "user", f"line {i}") for i in range(30)]
    excerpt = ask_seat.build_excerpt(messages, "quokka wombat")
    assert "NO MESSAGE MATCHES THE QUESTION'S TERMS" in excerpt and "[#29 " in excerpt
