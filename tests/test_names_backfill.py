"""T0236: `liteharness names --backfill --data-root <dir> [--apply]`.

Existing LiteTUI seats are protected through names.json, which needs no data root at hook
time. The backfill reads <dir>/.convos/*/settings.json (seat_id + seat_name) and records the
UNAMBIGUOUS ones. Dry run by default; ambiguous names are reported, never guessed.
"""
import json

import pytest

from liteharness import agent_names, cli, config


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "harness"
    monkeypatch.setattr(config, "get_root", lambda: root)
    assert tmp_path in agent_names.index_path().parents
    data = tmp_path / "litetui-copy"
    (data / ".convos").mkdir(parents=True)
    return root, data


def _convo(data, convo_id, seat_id, seat_name, *, jsonl=True, backend="claude", model="m1", raw=None):
    d = data / ".convos" / convo_id
    d.mkdir()
    (d / "settings.json").write_text(raw if raw is not None else json.dumps(
        {"seat_id": seat_id, "seat_name": seat_name, "backend": backend, "model": model}))
    if jsonl:
        (d / "convo.jsonl").write_text('{"type":"meta"}\n')


def test_dry_run_reports_and_writes_nothing(world):
    root, data = world
    _convo(data, "c1", "aid-1", "Alpha")
    report = agent_names.backfill_from_convos(data)
    assert report["applied"] is False
    assert [r["name"] for r in report["recorded"]] == ["Alpha"]
    assert not (root / "names.json").exists()


def test_apply_records_unambiguous_seats_with_convo_backend_model(world):
    root, data = world
    _convo(data, "c1", "aid-1", "Alpha", backend="codex", model="gpt-6-sol")
    _convo(data, "c2", "aid-2", "Beta")
    report = agent_names.backfill_from_convos(data, apply=True)
    assert report["applied"] is True and len(report["recorded"]) == 2
    alpha = agent_names.resolve_name("alpha")
    assert (alpha["agent_id"], alpha["convo_id"], alpha["backend"], alpha["model"]) == (
        "aid-1", "c1", "codex", "gpt-6-sol")
    assert alpha["cwd"] is None                      # settings.json carries no cwd
    assert agent_names.owns_conversation("aid-2") is True
    # idempotent
    again = agent_names.backfill_from_convos(data, apply=True)
    assert again["recorded"] == [] and len(again["already"]) == 2


def test_a_name_shared_by_several_conversations_is_a_reported_conflict_not_a_guess(world):
    root, data = world
    _convo(data, "c1", "aid-1", "OpenBolt")
    _convo(data, "c2", "aid-2", "openbolt")          # same name, case-insensitively
    _convo(data, "c3", "aid-3", "Solo")
    report = agent_names.backfill_from_convos(data, apply=True)
    assert [r["name"] for r in report["recorded"]] == ["Solo"]
    conflict = report["conflicts"][0]
    assert conflict["name"].lower() == "openbolt" and sorted(conflict["convos"]) == ["c1", "c2"]
    assert agent_names.resolve_name("OpenBolt") is None


def test_one_seat_with_several_conversations_is_a_conflict(world):
    root, data = world
    _convo(data, "c1", "aid-1", "Alpha")
    _convo(data, "c2", "aid-1", "Alpha")
    report = agent_names.backfill_from_convos(data, apply=True)
    assert report["recorded"] == [] and report["conflicts"]
    assert agent_names.resolve_name("Alpha") is None


def test_an_existing_index_entry_wins_and_a_clashing_candidate_is_reported(world):
    root, data = world
    agent_names.record_name("Alpha", "aid-existing", "c-existing", "C:/x")
    _convo(data, "c1", "aid-1", "Alpha")             # name taken by another agent
    _convo(data, "c2", "aid-existing", "Renamed")    # agent already carries another name
    report = agent_names.backfill_from_convos(data, apply=True)
    assert report["recorded"] == []
    assert {c["reason"] for c in report["conflicts"]} == {"name-in-index", "agent-in-index"}
    assert agent_names.resolve_name("Alpha")["agent_id"] == "aid-existing"


def test_conversations_without_a_seat_a_jsonl_or_readable_settings_are_skipped_and_counted(world):
    root, data = world
    _convo(data, "no-seat", "", "")
    _convo(data, "no-jsonl", "aid-9", "Ghost", jsonl=False)
    _convo(data, "bad", "x", "x", raw="{not json")
    _convo(data, "good", "aid-1", "Alpha")
    report = agent_names.backfill_from_convos(data, apply=True)
    assert [r["name"] for r in report["recorded"]] == ["Alpha"]
    assert report["skipped"] == {"no_seat": 1, "no_convo_jsonl": 1, "unreadable": 1}
    assert report["scanned"] == 4


def test_a_data_root_without_convos_is_an_error(tmp_path, world):
    with pytest.raises(agent_names.NameIndexError, match="no .convos"):
        agent_names.backfill_from_convos(tmp_path / "nowhere")


def test_a_corrupt_index_is_not_overwritten_by_a_backfill(world):
    root, data = world
    root.mkdir(parents=True)
    (root / "names.json").write_text("{not json")
    _convo(data, "c1", "aid-1", "Alpha")
    with pytest.raises(agent_names.IndexCorrupt):
        agent_names.backfill_from_convos(data, apply=True)
    assert (root / "names.json").read_text() == "{not json"


def test_cli_dry_run_then_apply(world, capsys):
    root, data = world
    _convo(data, "c1", "aid-1", "Alpha")
    cli.cmd_names(["--backfill", "--data-root", str(data)])
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Alpha" in out and not (root / "names.json").exists()
    cli.cmd_names(["--backfill", "--data-root", str(data), "--apply"])
    assert "applied" in capsys.readouterr().out.lower()
    assert agent_names.resolve_name("Alpha")["convo_id"] == "c1"


def test_cli_backfill_needs_a_data_root_and_a_real_dir(world, capsys, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_names(["--backfill"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        cli.cmd_names(["--backfill", "--data-root", str(tmp_path / "missing")])
    assert exc.value.code == 2


def test_the_backfilled_name_resumes_without_a_cwd_in_the_index(world):
    """cwd None falls through to the old PTY / explicit --cwd, as for any resume."""
    root, data = world
    _convo(data, "c1", "aid-1", "Alpha")
    agent_names.backfill_from_convos(data, apply=True)
    from liteharness import resume_seat
    agent_id, convo_id, entry = resume_seat.resolve_target("Alpha", None)
    assert (agent_id, convo_id, entry["cwd"]) == ("aid-1", "c1", None)
