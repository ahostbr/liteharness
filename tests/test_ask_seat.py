"""T0583-A: `liteharness ask-seat`, the read path and its guards. No usage is spent: the reader
subprocess is faked, and the real ~/.liteharness and ~/.claude are never touched."""
import json
import subprocess
import sys

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
                 registered_at="2026-10-01T10:00:00+00:00", messages=None, **fields):
    """A Claude seat as the registry holds it: a presence row, and a transcript named by its agent id."""
    _presence(root, agent_id, retired=retired, cli="claude-code", name=name, registered_at=registered_at,
              model="claude-opus-5-5", **fields)
    folder = projects / "some-project"
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (role, text) in enumerate(messages or [("user", "hello"), ("assistant", "hi")]):
        lines.append(json.dumps({"type": role, "timestamp": f"2026-10-01T10:00:{i:02d}Z", "cwd": CWD,
                                 "message": {"role": role, "content": text}}))
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


def test_unknown_name_and_unknown_id_are_refused(env):
    with pytest.raises(ask_seat.AskRefused, match="no agent named Nobody"):
        ask_seat.resolve_seat("Nobody")
    with pytest.raises(ask_seat.AskRefused, match="no seat has agent id"):
        ask_seat.resolve_seat(SEAT_ID)


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


def test_agents_layout_litetui_seat_is_read_through_the_exporter(env, tmp_path, monkeypatch):
    from liteharness import owned_launch
    data_root = tmp_path / "litetui"
    home = data_root / ".agents" / "Newer"
    (home / "conversations" / CONVO).mkdir(parents=True)
    (home / "settings.json").write_text(json.dumps({
        "schema_version": 1, "name": "Newer", "agent_id": SEAT_ID,
        "execution": {"backend": "codex", "model": "gpt", "thinking_level": "high"}}), encoding="utf-8")
    (home / "conversations" / CONVO / "convo.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(owned_launch, "data_root", lambda resolution=None: data_root)
    agent_names.record_name("Newer", SEAT_ID, CONVO, None, backend="codex", model="gpt")
    entry = agent_names.resolve_name("Newer")

    path = ask_seat.litetui_transcript(entry)
    assert path == home / "conversations" / CONVO / "convo.jsonl"

    def export(argv, **kwargs):
        assert argv[1:3] == ["--export-conversation", str(path)]
        open(argv[4], "w", encoding="utf-8").write(
            "# Conversation\n\n## You\n\n```\nwhy red?\n```\n\n## Assistant\n\n```\nbecause X\n```\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(ask_seat.subprocess, "run", export)
    messages = ask_seat.read_litetui(path)
    assert [(m.role, "because X" in m.text) for m in messages] == [("you", False), ("assistant", True)]


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
    for argv in (["Mason", "q", "--wake"], ["Mason"]):
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
