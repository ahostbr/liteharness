"""T0238 WS3a: `liteharness handoffs --audit` read-only inventory of legacy handoffs.

Fixtures are temp trees only; nothing here touches ~/.liteharness or any real root.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from liteharness import handoff_audit as ha

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_STORE = "33333333-3333-4333-8333-333333333333"


def _write(path: Path, text: str = "x", binary: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary is not None:
        path.write_bytes(binary)
    else:
        path.write_bytes(text.encode("utf-8"))  # no newline translation: sizes are exact
    return path


@pytest.fixture
def world(tmp_path):
    """registry + convos + three legacy roots, all inside tmp_path."""
    reg = tmp_path / "registry"
    _write(reg / "agents" / f"{UUID_A}.json", json.dumps({"agent_id": UUID_A, "name": "Harbor"}))
    _write(reg / "names" / UUID_B, "DaVinci")  # named override, no presence file
    convos = tmp_path / "convos"
    _write(convos / UUID_STORE / "settings.json",
           json.dumps({"seat_name": "KeyStone", "seat_id": UUID_STORE}))
    _write(convos / UUID_STORE / "handoff.md", "# in-store handoff")  # inside a store: NOT a stray
    _write(convos / "stray-handoff.md", "# stray")

    sots = tmp_path / "sots" / "Docs" / "Handoffs"
    _write(sots / "HANDOFF_20260924_Harbor_v2.md", "# Harbor handoff\nbody")
    _write(sots / "HANDOFF_20260924_Harbor_v2.md.lock", "")
    _write(sots / "HANDOFF_DaVinci-Q8_20260927.md", "no header")
    _write(sots / "HANDOFF_Nobody_20260101.md", "no header")
    _write(sots / "notes.md", "Agent: KeyStone\nbody")  # header claim wins, name has none
    ckpt = tmp_path / "sots" / "DevTools" / "_checkpoints"
    _write(ckpt / "claude-opus" / "20260901" / "checkpoint_abc.json", json.dumps({"summary": "s"}))
    _write(ckpt / "claude-opus" / "20260901" / "index.json", "{}")
    _write(ckpt / "claude-opus" / "20260901" / "convo_log_s1.jsonl", "{}\n")
    _write(ckpt / "claude-opus" / "a.uasset", binary=b"\x00\x01")

    scratch = tmp_path / "scratch"
    _write(scratch / "KeyStone-handoff-2026-09-30" / "handoff.md", "# ks")
    _write(scratch / "random" / "a.png", binary=b"\x89PNG")
    _write(scratch / "random" / "unrelated.txt", "not a handoff")
    return dict(reg=reg, convos=convos, sots=sots, ckpt=ckpt, scratch=scratch)


def _audit(w, **kw):
    roots = kw.pop("roots", [
        ha.Root("sots_handoffs", w["sots"], "all", legacy=True),
        ha.Root("sots_checkpoints", w["ckpt"], "all", legacy=True),
        ha.Root("scratch", w["scratch"], "candidates"),
    ])
    return ha.audit(roots, registry_root=w["reg"], convos_root=w["convos"], **kw)


def _row(result, suffix):
    hits = [r for r in result["rows"] if r["path"].replace("\\", "/").endswith(suffix)]
    assert len(hits) == 1, (suffix, hits)
    return hits[0]


def test_row_fields_and_kinds(world):
    res = _audit(world)
    r = _row(res, "HANDOFF_20260924_Harbor_v2.md")
    assert r["size"] == len("# Harbor handoff\nbody")
    assert r["mtime"].endswith("Z")
    assert r["kind"] == "handoff_prose"
    assert _row(res, "HANDOFF_20260924_Harbor_v2.md.lock")["kind"] == "lock"
    assert _row(res, "checkpoint_abc.json")["kind"] == "checkpoint_json"
    assert _row(res, "index.json")["kind"] == "index"
    assert _row(res, "convo_log_s1.jsonl")["kind"] == "transcript"
    assert _row(res, "a.uasset")["kind"] == "binary"


def test_locks_binaries_transcripts_are_never_handoff_prose(world):
    res = _audit(world)
    for r in res["rows"]:
        if r["kind"] in {"lock", "binary", "transcript", "index"}:
            assert r["kind"] != "handoff_prose"
            assert r["owner"]["status"] == "not_applicable", r


def test_owner_resolution_against_registry_and_convos(world):
    res = _audit(world)
    harbor = _row(res, "HANDOFF_20260924_Harbor_v2.md")
    assert harbor["owner"]["claim"] == "Harbor"
    assert harbor["owner"]["source"] == "name"
    assert harbor["owner"]["status"] == "resolved"
    assert "registry" in harbor["owner"]["via"]
    assert harbor["owner"]["agent_ids"] == [UUID_A]

    # `names/<uuid>` override counts as a registry name even without a presence file
    davinci = _row(res, "HANDOFF_DaVinci-Q8_20260927.md")
    assert davinci["owner"]["claim"] == "DaVinci"
    assert davinci["owner"]["status"] == "resolved"
    assert davinci["owner"]["agent_ids"] == [UUID_B]
    assert davinci["owner"]["via"] == ["registry-name"]

    # header claim, resolved through the convo store's settings.json seat_name
    notes = _row(res, "notes.md")
    assert notes["owner"]["source"] == "header"
    assert notes["owner"]["claim"] == "KeyStone"
    assert notes["owner"]["via"] == ["convo"]
    assert notes["owner"]["convo_ids"] == [UUID_STORE]

    nobody = _row(res, "HANDOFF_Nobody_20260101.md")
    assert nobody["owner"]["claim"] == "Nobody"
    assert nobody["owner"]["status"] == "unresolved"

    # owner taken from the parent directory when the file name carries none
    ks = _row(res, "KeyStone-handoff-2026-09-30/handoff.md")
    assert ks["owner"]["source"] == "parent"
    assert ks["owner"]["status"] == "resolved"


def test_candidates_mode_skips_unrelated_files_but_all_mode_lists_everything(world):
    res = _audit(world)
    paths = [r["path"].replace("\\", "/") for r in res["rows"]]
    assert not any(p.endswith("unrelated.txt") for p in paths)
    assert not any(p.endswith("random/a.png") for p in paths)
    scratch = next(x for x in res["roots"] if x["label"] == "scratch")
    assert scratch["files_scanned"] == 3  # scanned all, reported only candidates
    assert scratch["rows"] == 1


def test_summary_counts(world):
    res = _audit(world)
    s = res["summary"]
    assert s["complete"] is True
    assert s["rows"] == len(res["rows"])
    assert s["by_kind"]["handoff_prose"] == 5
    assert s["by_kind"]["lock"] == 1
    assert s["owners"]["resolved"] + s["owners"]["unresolved"] + s["owners"]["unclaimed"] \
        + s["owners"]["not_applicable"] == s["rows"]


def test_unreadable_root_is_unknown_never_zero(world, tmp_path):
    missing = tmp_path / "does-not-exist"
    res = _audit(world, roots=[ha.Root("gone", missing, "all"), ha.Root("ok", world["sots"], "all")])
    gone = next(r for r in res["roots"] if r["label"] == "gone")
    assert gone["status"] == "UNKNOWN"
    assert gone["files_scanned"] is None and gone["rows"] is None
    assert gone["errors"]
    assert res["summary"]["complete"] is False
    assert res["summary"]["unknown_roots"] == ["gone"]
    ok = next(r for r in res["roots"] if r["label"] == "ok")
    assert ok["status"] == "ok"


def test_partial_when_a_subdirectory_errors(world, monkeypatch):
    real = os.scandir
    bad = str(world["scratch"] / "random")

    def flaky(path):
        if str(path) == bad:
            raise PermissionError(13, "denied", bad)
        return real(path)

    monkeypatch.setattr(ha.os, "scandir", flaky)
    res = _audit(world)
    scratch = next(r for r in res["roots"] if r["label"] == "scratch")
    assert scratch["status"] == "partial"
    assert any("denied" in e for e in scratch["errors"])
    assert res["summary"]["complete"] is False
    assert "scratch" in res["summary"]["partial_roots"]


def test_reparse_points_are_not_followed(world, tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "HANDOFF_leak.md", "leak")
    link = world["sots"] / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted here")
    res = _audit(world)
    assert not any("HANDOFF_leak" in r["path"] for r in res["rows"])
    root = next(r for r in res["roots"] if r["label"] == "sots_handoffs")
    assert root["reparse_skipped"] == 1


def test_audit_is_read_only(world):
    def snapshot():
        out = {}
        for base in (world["sots"], world["ckpt"], world["scratch"], world["reg"], world["convos"]):
            for p in base.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    out[str(p)] = (st.st_size, st.st_mtime_ns)
        return out

    before = snapshot()
    _audit(world)
    assert snapshot() == before


def test_convo_strays_exclude_store_contents(world):
    res = _audit(world, roots=[ha.Root("convos_strays", world["convos"], "strays")])
    paths = [r["path"].replace("\\", "/") for r in res["rows"]]
    assert any(p.endswith("stray-handoff.md") for p in paths)
    assert not any(UUID_STORE in p for p in paths)
    assert res["roots"][0]["stores_skipped"] == 1


def test_resolver_is_swappable(world):
    calls = []

    def fake(claim, index):
        calls.append(claim)
        return {"status": "resolved", "via": ["ws1"], "agent_ids": ["X"], "convo_ids": []}

    res = _audit(world, resolver=fake)
    assert calls
    assert _row(res, "HANDOFF_Nobody_20260101.md")["owner"]["via"] == ["ws1"]


def test_writable_legacy_reports_not_frozen_yet(world):
    res = _audit(world, writable_legacy=True)
    wl = res["writable_legacy"]
    assert wl["frozen"] is False
    assert wl["note"] == "not frozen yet"
    assert wl["rows"] == sum(1 for r in res["rows"] if r["root"] in ("sots_handoffs", "sots_checkpoints"))


def test_table_renders_every_row(world):
    res = _audit(world)
    text = ha.render_table(res)
    assert "HANDOFF_20260924_Harbor_v2.md" in text
    assert "handoff_prose" in text
    assert "Harbor" in text


def test_cli_json_output_and_out_file(world, tmp_path):
    out = tmp_path / "manifest.json"
    proc = subprocess.run(
        [sys.executable, "-m", "liteharness.cli", "handoffs", "--audit", "--format", "json",
         "--root", f"sots={world['sots']}@all", "--convos-root", str(world["convos"]),
         "--registry-root", str(world["reg"]), "--out", str(out)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["summary"]["rows"] == 5
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["rows"] == 5


def test_cli_requires_audit(world):
    proc = subprocess.run([sys.executable, "-m", "liteharness.cli", "handoffs"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "--audit" in (proc.stdout + proc.stderr)


def test_candidates_keep_convo_store_copies_but_count_source_files(world):
    copy = world["scratch"] / "restart" / "data" / ".convos" / UUID_STORE
    _write(copy / "handoff.md", "# copied store")
    _write(world["scratch"] / "tools" / "convo_search.py", "print()")
    res = _audit(world, roots=[ha.Root("scratch", world["scratch"], "candidates", legacy=True,
                                       skip_dirs=(".git",))])
    row = _row(res, f".convos/{UUID_STORE}/handoff.md")
    assert row["owner"]["source"] == "parent"
    assert row["owner"]["claim"] == UUID_STORE
    assert row["owner"]["convo_ids"] == [UUID_STORE]  # the copy's dir IS a live convo id
    assert not any(r["path"].endswith("convo_search.py") for r in res["rows"])
    assert res["roots"][0]["excluded_source"] == 1


def test_checkpoint_model_directory_is_not_an_owner_claim(world):
    ck = world["ckpt"] / "Harbor" / "20260901" / "checkpoint_zzz.json"
    _write(ck, json.dumps({"summary": "s"}))
    res = _audit(world)
    assert _row(res, "Harbor/20260901/checkpoint_zzz.json")["owner"]["status"] == "unclaimed"
    ck2 = world["ckpt"] / "m" / "checkpoint_named.json"
    _write(ck2, json.dumps({"agent_name": "Harbor", "summary": "s"}))
    res = _audit(world)
    assert _row(res, "m/checkpoint_named.json")["owner"]["status"] == "resolved"


def test_parse_root_spec_and_env_defaults(monkeypatch, tmp_path):
    r = ha.parse_root_spec(f"repo={tmp_path}@candidates!.worktrees,.convos")
    assert (r.label, r.mode, r.legacy) == ("repo", "candidates", False)
    assert ha.parse_root_spec(f"h={tmp_path}@all+legacy").legacy is True
    assert ha.parse_root_spec(f"h={tmp_path}!x+legacy").skip_dirs.count("x") == 1
    assert ".worktrees" in r.skip_dirs and ".git" in r.skip_dirs
    with pytest.raises(ValueError):
        ha.parse_root_spec("no-equals")
    with pytest.raises(ValueError):
        ha.parse_root_spec(f"x={tmp_path}@bogus")
    monkeypatch.setenv(ha.ENV_ROOTS, os.pathsep.join([f"a={tmp_path}@all", f"b={tmp_path}"]))
    assert [x.label for x in ha.default_roots()] == ["liteharness_handoffs", "a", "b"]


def test_missing_convos_root_is_reported_unavailable(world):
    res = ha.audit([ha.Root("s", world["sots"], "all")], registry_root=world["reg"], convos_root=None)
    assert any("UNAVAILABLE" in e for e in res["summary"]["index_errors"])


def _cli(world, *extra):
    return subprocess.run(
        [sys.executable, "-m", "liteharness.cli", "handoffs", "--audit", "--format", "json",
         "--convos-root", str(world["convos"]), "--registry-root", str(world["reg"]), *extra],
        capture_output=True, text=True, timeout=60)


def test_cli_exits_nonzero_when_a_root_is_unknown(world, tmp_path):
    proc = _cli(world, "--root", f"ok={world['sots']}@all", "--root", f"gone={tmp_path / 'nope'}@all")
    assert proc.returncode == ha.EXIT_INCOMPLETE
    assert json.loads(proc.stdout)["summary"]["unknown_roots"] == ["gone"]  # output still produced
    assert "INCOMPLETE" in proc.stderr


def test_cli_refuses_out_inside_a_scanned_root(world):
    target = world["sots"] / "manifest.json"
    proc = _cli(world, "--root", f"sots={world['sots']}@all", "--out", str(target))
    assert proc.returncode == 2
    assert "refusing --out" in proc.stderr
    assert not target.exists()


class _FakeNames:
    """Stand-in for WS1 `liteharness.agent_names` (contract: resolve_name / name_for_agent)."""
    class IndexCorrupt(Exception):
        pass

    def __init__(self, records=None, corrupt=False):
        self.records, self.corrupt = records or {}, corrupt

    def resolve_name(self, name):
        if self.corrupt:
            raise self.IndexCorrupt("bad json")
        return self.records.get(name.lower())

    def name_for_agent(self, agent_id):
        for rec in self.records.values():
            if rec["agent_id"] == agent_id:
                return rec["name"]
        return None


def test_resolver_consults_ws1_index_when_importable(world, monkeypatch):
    rec = {"name": "Nobody", "agent_id": "44444444-4444-4444-8444-444444444444",
           "convo_id": "55555555-5555-4555-8555-555555555555"}
    monkeypatch.setitem(sys.modules, "liteharness.agent_names", _FakeNames({"nobody": rec}))
    res = _audit(world)
    owner = _row(res, "HANDOFF_Nobody_20260101.md")["owner"]
    assert owner["status"] == "resolved" and owner["via"] == ["names-index"]
    assert owner["agent_ids"] == [rec["agent_id"]] and owner["convo_ids"] == [rec["convo_id"]]
    # a UUID claim goes through name_for_agent first
    index = ha.build_index(world["reg"], world["convos"])
    out = ha.resolve_name(rec["agent_id"], index)
    assert out["via"] == ["names-index"]


def test_resolver_falls_back_when_ws1_absent_and_reports_corrupt_index(world, monkeypatch):
    monkeypatch.setitem(sys.modules, "liteharness.agent_names", None)  # ImportError
    assert _row(_audit(world), "HANDOFF_20260924_Harbor_v2.md")["owner"]["via"] == ["registry"]
    monkeypatch.setitem(sys.modules, "liteharness.agent_names", _FakeNames(corrupt=True))
    res = _audit(world)
    assert _row(res, "HANDOFF_20260924_Harbor_v2.md")["owner"]["status"] == "resolved"  # local still answers
    errs = res["summary"]["index_errors"]
    assert sum("IndexCorrupt" in e for e in errs) == 1  # reported once, not per row
