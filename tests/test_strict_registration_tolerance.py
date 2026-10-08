"""T0332: actual offline cmd_register, bounded neighbor reads and strict claims."""

import ast
import json
import os
from pathlib import Path

import pytest

from liteharness import announce, cli, config, seat_lifecycle, strict_registration
from liteharness.agent_store import StoreError

AID = "11111111-1111-4111-8111-111111111111"
BID = "22222222-2222-4222-8222-222222222222"
NAME = "PlainFixture"
PRIVATE = "FORBIDDEN_CORRUPT_ROW_PAYLOAD"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_root", lambda: tmp_path)
    monkeypatch.setattr(config, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(
        seat_lifecycle, "log_path", lambda: tmp_path / "lifecycle.jsonl"
    )
    monkeypatch.setattr(announce, "announce_registration", lambda *args, **kwargs: [])
    monkeypatch.setattr(strict_registration, "pid_alive", lambda _: True)
    waits = []
    monkeypatch.setattr(strict_registration.time, "sleep", waits.append)
    return tmp_path, waits


def register():
    cli.cmd_register(
        agent_id=AID,
        name=NAME,
        session_pid=os.getpid(),
        backend="codex",
        model="fixture",
        thinking_level="high",
        cli="litetui",
        strict_identity=True,
    )


def presence(root, *, identity=BID, name="Unrelated", malformed=False):
    agents = root / "agents"
    agents.mkdir(exist_ok=True)
    path = agents / f"{identity}.json"
    data = {"agent_id": identity, "name": name, "session_pid": os.getpid() + 1}
    text = '{"agent_id": "' + PRIVATE if malformed else json.dumps(data)
    path.write_text(text, encoding="utf-8")
    return path


def saved_index(root, *, name="Unrelated", identity=BID):
    data = {} if name is None else {name: {"agent_id": identity}}
    path = root / "names.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def reads_with_sharing_denial(monkeypatch, denied):
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        if path == denied:
            reads.append(path)
            error = PermissionError(13, PRIVATE)
            error.winerror = 32
            raise error
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    return reads


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("shape", ["sharing", "truncated-json"])
@pytest.mark.parametrize("known", [True, False])
def test_actual_cmd_register_tolerates_only_valid_index_unrelated_rows(
    registry, monkeypatch, capsys, shape, known
):
    root, waits = registry
    neighbor = presence(root, malformed=shape == "truncated-json")
    index = saved_index(root, name="Unrelated" if known else None)
    before_neighbor, before_index = neighbor.read_bytes(), index.read_bytes()
    reads = (
        reads_with_sharing_denial(monkeypatch, neighbor) if shape == "sharing" else None
    )
    register()
    receipt = capsys.readouterr()
    assert "Folder-owned identity: " in receipt.out
    assert f"id={BID}" in receipt.err
    assert (
        "reason=index-different-name" if known else "reason=valid-index-id-absent"
    ) in receipt.err
    assert PRIVATE not in receipt.out + receipt.err
    assert len(waits) == strict_registration._READ_ATTEMPTS - 1
    assert sum(waits) == 0.75
    if reads is not None:
        assert len(reads) == strict_registration._READ_ATTEMPTS
    actual = json.loads((root / "agents" / f"{AID}.json").read_text(encoding="utf-8"))
    assert (actual["agent_id"], actual["name"]) == (AID, NAME)
    assert (
        neighbor.read_bytes() == before_neighbor and index.read_bytes() == before_index
    )


@pytest.mark.parametrize("shape", ["sharing", "truncated-json"])
def test_unreadable_same_id_remains_immediate_prewrite_fatal(
    registry, monkeypatch, shape
):
    root, waits = registry
    own = presence(root, identity=AID, malformed=shape == "truncated-json")
    saved_index(root, name="OtherName", identity=AID)
    before = snapshot(root)
    reads = reads_with_sharing_denial(monkeypatch, own) if shape == "sharing" else None
    with pytest.raises(StoreError, match="Strict registry row is unreadable"):
        register()
    assert snapshot(root) == before and waits == []
    if reads is not None:
        assert len(reads) == 1


def test_readable_same_name_remains_prewrite_fatal(registry):
    root, waits = registry
    presence(root, name=NAME)
    saved_index(root, name="Unrelated")  # index cannot waive readable name claim
    before = snapshot(root)
    with pytest.raises(StoreError, match="held by another live owner"):
        register()
    assert snapshot(root) == before and waits == []


@pytest.mark.parametrize("shape", ["sharing", "truncated-json"])
def test_unreadable_saved_same_name_remains_prewrite_fatal(
    registry, monkeypatch, shape
):
    root, waits = registry
    neighbor = presence(root, malformed=shape == "truncated-json")
    saved_index(root, name=NAME.swapcase())
    before = snapshot(root)
    if shape == "sharing":
        reads_with_sharing_denial(monkeypatch, neighbor)
    with pytest.raises(StoreError, match="saved name is held by unreadable presence"):
        register()
    assert snapshot(root) == before and len(waits) == 3


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "denied",
        "json",
        "nonobject",
        "badentry",
        "badid",
        "badname",
        "duplicatekey",
        "duplicateid",
        "casecollision",
    ],
)
def test_bad_saved_index_is_not_unknown_id_permission(registry, monkeypatch, kind):
    root, _ = registry
    presence(root, malformed=True)
    index = root / "names.json"
    malformed = {
        "json": '{"broken"',
        "nonobject": "[]",
        "badentry": '{"Unrelated": 42}',
        "badid": '{"Unrelated": {"agent_id": ""}}',
        "badname": '{"CON": {"agent_id": "legacy"}}',
        "duplicatekey": '{"Unrelated": {}, "Unrelated": {}}',
        "duplicateid": json.dumps({"One": {"agent_id": BID}, "Two": {"agent_id": BID}}),
        "casecollision": json.dumps(
            {"One": {"agent_id": BID}, "one": {"agent_id": AID}}
        ),
    }
    if kind == "denied":
        saved_index(root)
        reads_with_sharing_denial(monkeypatch, index)
    elif kind != "missing":
        index.write_text(malformed[kind], encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(StoreError, match="Saved names index"):
        register()
    assert snapshot(root) == before


def test_transient_neighbor_read_recovers_without_index_fallback(
    registry, monkeypatch, capsys
):
    root, waits = registry
    neighbor = presence(root)
    original = Path.read_text
    calls = []

    def read(path, *args, **kwargs):
        if path == neighbor:
            calls.append(path)
            if len(calls) < 3:
                raise PermissionError(13, PRIVATE)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    register()  # no index: successful reread still validates the real row
    assert len(calls) == 3 and len(waits) == 2
    assert "skipped" not in capsys.readouterr().err


def test_unreadable_legacy_filename_is_not_diagnostic_payload(registry, capsys):
    root, _ = registry
    presence(root, identity=PRIVATE, malformed=True)
    saved_index(root, name=None)
    register()
    receipt = capsys.readouterr()
    assert "id=noncanonical-id" in receipt.err
    assert PRIVATE not in receipt.err + receipt.out


def test_reparse_error_during_retry_is_not_skippable_unreadability(
    registry, monkeypatch
):
    root, waits = registry
    neighbor = presence(root)
    saved_index(root, name=None)
    original = strict_registration._unlinked
    calls = []

    def checked(path):
        if path == neighbor:
            calls.append(path)
            if len(calls) == 3:
                raise StoreError("Linked storage paths are not supported")
        return original(path)

    monkeypatch.setattr(strict_registration, "_unlinked", checked)
    reads = reads_with_sharing_denial(monkeypatch, neighbor)
    before = snapshot(root)
    with pytest.raises(StoreError, match="Linked"):
        register()
    assert snapshot(root) == before and len(waits) == 1 and len(reads) == 1


def test_actual_canvas_and_pty_precreate_ast_use_atomic_helper_not_inplace_writes():
    """Static guard for both actual branches; never execute spawn or a live engine."""
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    atomic = []
    inplace = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "config"
            and node.func.attr == "atomic_write_json"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "presence_path"
        ):
            atomic.append(node)
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "presence_path"
            and node.func.attr == "write_text"
        ):
            inplace.append(node)
    assert len(atomic) == 2 and inplace == []
    modes = set()
    for node in atomic:
        assert isinstance(node.args[1], ast.Dict)
        payload = {
            ast.literal_eval(key): value
            for key, value in zip(node.args[1].keys, node.args[1].values, strict=True)
        }
        modes.add(ast.literal_eval(payload["spawn_mode"]))
    assert modes == {"canvas", "pty"}
