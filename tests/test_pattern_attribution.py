"""A recorded pattern must name its author, or not be recorded at all.

🔴 THE DEFECT, MEASURED 2026-09-02 ON THE LIVE STORE. `session` fell back to the
literal string "cli" whenever no `--agent-id` reached `cmd_record_pattern`, and
`.liteharness/patterns.jsonl` in LiteSuite held 172 such rows out of 328 — 52% of
the collective memory with no author at all.

That is not untidiness. A RECORD WITH NO AUTHOR FIELD CANNOT BE ATTRIBUTED BY ITS
CONTENT: the reader's own recent experience supplies the match, so an agent
reading a row that resembles its own last mistake will believe it wrote it. This
seat came one sentence short of asserting a peer's row as its own the same day,
on nothing but content and a similar timestamp.

Refusing costs one row. An anonymous row costs every attribution question the
store is ever asked.
"""

from __future__ import annotations

import json

import pytest

from liteharness import cli


AGENT = "a98678ea-26ad-4f05-8188-1c89b6fad334"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def rows_in(project) -> list[dict]:
    path = project / ".liteharness" / "patterns.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestAttribution:
    def test_an_explicit_agent_id_is_recorded_as_the_author(self, project):
        cli.cmd_record_pattern(outcome="success", agent_id=AGENT, task_desc="t", project=str(project))
        (row,) = rows_in(project)
        assert row["session"] == AGENT
        assert row["agent_id"] == AGENT, "agent_id must be a FIELD, not only a prefix on task_id"

    def test_the_row_is_never_written_as_the_literal_cli(self, project):
        cli.cmd_record_pattern(outcome="success", agent_id=AGENT, task_desc="t", project=str(project))
        assert rows_in(project)[0]["session"] != "cli"

    def test_an_unattributable_row_is_REFUSED_and_nothing_is_written(self, project, monkeypatch):
        """🔴 The arm the fix exists for: refuse, do not write anonymously."""
        monkeypatch.setattr(cli.config, "get_agent_id", lambda: "")
        with pytest.raises(SystemExit) as exc:
            cli.cmd_record_pattern(outcome="success", task_desc="t", project=str(project))
        assert exc.value.code == 2
        assert rows_in(project) == [], "a refused record must leave the store untouched"

    def test_a_non_authoritative_derived_id_is_also_refused(self, project, monkeypatch):
        # A fallback id is not an author. Accepting one would restore the defect
        # under a different string than "cli", which is worse: it LOOKS attributed.
        monkeypatch.setattr(cli.config, "get_agent_id", lambda: "unknown")
        with pytest.raises(SystemExit) as exc:
            cli.cmd_record_pattern(outcome="success", task_desc="t", project=str(project))
        assert exc.value.code == 2
        assert rows_in(project) == []

    def test_a_registered_session_supplies_the_author_without_a_flag(self, project, monkeypatch):
        # The convenience half: a caller inside a registered session does not have
        # to repeat its own id, and still never produces an anonymous row.
        monkeypatch.setattr(cli.config, "get_agent_id", lambda: AGENT)
        cli.cmd_record_pattern(outcome="failure", task_desc="t", project=str(project))
        (row,) = rows_in(project)
        assert row["session"] == AGENT
        assert row["agent_id"] == AGENT

    def test_the_refusal_names_its_own_remedy(self, project, monkeypatch, capsys):
        # An error that does not name the fix is read as a dead end and routed
        # around — that cost this fleet a day of unrecorded traffic once.
        monkeypatch.setattr(cli.config, "get_agent_id", lambda: "")
        with pytest.raises(SystemExit):
            cli.cmd_record_pattern(outcome="success", task_desc="t", project=str(project))
        err = capsys.readouterr().err
        assert "--agent-id" in err
        assert "register" in err


class TestRecordShapeUnchanged:
    def test_a_pattern_is_still_born_unverified_with_a_uuid(self, project):
        cli.cmd_record_pattern(outcome="success", agent_id=AGENT, task_desc="t", project=str(project))
        row = rows_in(project)[0]
        assert row["verified"] == "unverified"
        assert len(row["pattern_id"]) == 36

    def test_supersedes_still_rides_along(self, project):
        """
        The ride-along contract, re-seated on a REAL id (T878).

        🔴 THIS ARM PREDATED THE CONTRACT AND ENCODED THE OLD ONE. It passed a
        fabricated `some-earlier-id` into a fresh tmp project, which by
        construction can contain no such record — and since T878 that is exactly
        the call the writer refuses (`SystemExit: 2`, nothing written). The arm
        was not wrong about its SUBJECT: `supersedes` must still reach the row.
        It was wrong about the only INPUT it had ever been given, because an
        unresolvable id is no longer a legal way to ask the question.

            AN ARM THAT PREDATES A CONTRACT ENCODES THE OLD ONE, AND IT FAILS AT
            THE MOMENT THE CONTRACT IMPROVES — which reads exactly like a
            regression and is the opposite of one.

        So it now records a first pattern, reads ITS id back out of the store,
        and supersedes that. The assertion is unchanged and is finally being
        asked with an input the writer accepts. The refusal of an unresolvable
        id has its own arms in `test_pattern_supersedes_reference.py`; this file
        keeps its own subject.
        """
        cli.cmd_record_pattern(
            outcome="success", agent_id=AGENT, task_desc="the record being retired",
            project=str(project),
        )
        earlier = rows_in(project)[0]["pattern_id"]

        cli.cmd_record_pattern(
            outcome="failure", agent_id=AGENT, task_desc="t", project=str(project),
            supersedes=[earlier],
        )
        assert rows_in(project)[1]["supersedes"] == [earlier]
