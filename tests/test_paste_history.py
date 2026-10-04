"""Generated local fixtures only; no bridge listener, real home or live hook."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from liteharness import hooks, paste_history as pastes

# A tiny valid PNG fixture; never a human's pasted image.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def image(data=PNG, mime="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                          "data": base64.b64encode(data).decode()}}


def record(*blocks, role="user", **extra):
    return {"type": role, "message": {"role": role, "content": list(blocks)}, **extra}


def append(path, *records):
    with path.open("ab") as stream:
        for entry in records:
            stream.write(json.dumps(entry).encode() + b"\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.touch()
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "seat.json").write_text('{"name":"Fixture","tier":"worker"}')
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    opened = []
    monkeypatch.setattr(pastes, "_open", lambda *args: opened.append(args) or True)
    payload = {"cwd": str(project), "transcript_path": str(transcript), "session_id": "fixture"}
    directory = project / ".litesuite" / "theater" / "pastes"

    def run(event, action=None):
        action = action or {"SessionStart": "check", "UserPromptSubmit": "memory-nudge",
                            "PostToolUse": "check", "Stop": "obs"}[event]
        pastes.capture({**payload, "hook_event_name": event}, action, "seat", agents)
    return transcript, directory, run, opened, payload, agents


def entries(directory):
    return json.loads((directory / ".history.json").read_text())["entries"]


def test_cwd_inside_git_repo_writes_at_toplevel_never_subfolder(env, tmp_path):
    transcript, _, _, opened, payload, agents = env
    repo = tmp_path / "repo"
    sub = repo / "sub" / "deeper"
    sub.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    payload = {**payload, "cwd": str(sub), "hook_event_name": "SessionStart"}
    pastes.capture(payload, "check", "seat", agents)
    assert list((repo / ".litesuite" / "theater" / "pastes").glob(".cursor-*.json"))
    assert not (repo / "sub" / ".litesuite").exists() and not (sub / ".litesuite").exists()


def test_prompt_history_newest_first_persists_dedupes_and_opens_once(env):
    transcript, directory, run, opened, _, _ = env
    append(transcript, record(image(GIF, "image/gif")))  # historical image must not replay
    run("SessionStart")
    ownership = {name: '{"harbor":true}' for name in ("page.json", "session.json", "sent.json")}
    for name, value in ownership.items():
        (directory / name).write_text(value)
    append(transcript, record({"type": "text", "text": "</script>{dangerous()}"}, image()))
    run("UserPromptSubmit")
    first = entries(directory)[0]["file"]
    assert first == hashlib.sha256(PNG).hexdigest() + ".png"
    assert (directory / "assets" / first).read_bytes() == PNG
    run("PostToolUse")
    run("Stop")
    append(transcript, record(image(GIF, "image/gif"), image()))
    run("UserPromptSubmit")
    assert [entry["file"] for entry in entries(directory)] == [hashlib.sha256(GIF).hexdigest() + ".gif", first]
    assert len(opened) == 1
    source = (directory / "page.tsx").read_text()
    assert "dangerous" not in source and str(transcript) not in source
    assert "#090d16" in source
    for name, value in ownership.items():
        assert (directory / name).read_text() == value


@pytest.mark.parametrize("fallback", ["PostToolUse", "Stop"])
def test_image_only_append_after_prompt_uses_next_event(env, fallback):
    transcript, directory, run, _, _, _ = env
    run("SessionStart")
    run("UserPromptSubmit")  # empty prompt and transcript at this point
    assert not (directory / "page.tsx").exists()
    append(transcript, record(image()))
    run(fallback)
    assert len(entries(directory)) == 1
    run("Stop")
    assert len(entries(directory)) == 1


def test_no_scan_tool_or_assistant_images(env):
    transcript, directory, run, opened, _, _ = env
    run("SessionStart")
    append(transcript, record(image(), role="assistant"), record(image(), isMeta=True),
           record({"type": "tool_result", "content": [image()]}))
    run("UserPromptSubmit")
    run("Stop")
    assert not (directory / "page.tsx").exists() and not opened


def test_initial_unprimed_session_refuses_historical_replay(env):
    transcript, directory, run, _, _, _ = env
    append(transcript, record(image()))
    with pytest.raises(pastes.CaptureError, match="baseline reset"):
        run("UserPromptSubmit")
    assert not (directory / "page.tsx").exists()
    append(transcript, record(image(GIF, "image/gif")))
    run("UserPromptSubmit")
    assert entries(directory)[0]["file"].endswith(".gif")


@pytest.mark.parametrize("replacement", ["truncate", "replace", "rewrite"])
def test_transcript_identity_reset_never_replays(env, replacement):
    transcript, directory, run, _, _, _ = env
    append(transcript, record({"type": "text", "text": "old"}))
    run("SessionStart")
    if replacement == "replace":
        replacement_path = transcript.with_suffix(".new")
        append(replacement_path, record(image()))
        replacement_path.replace(transcript)
    elif replacement == "rewrite":
        transcript.write_bytes(b"")
        append(transcript, record(image()))
    else:
        transcript.write_bytes(b"{}\n")
    with pytest.raises(pastes.CaptureError, match="baseline reset"):
        run("UserPromptSubmit")
    assert not (directory / "page.tsx").exists()


def test_partial_jsonl_append_retried_without_losing_image(env):
    transcript, directory, run, _, _, _ = env
    run("SessionStart")
    raw = json.dumps(record(image())).encode() + b"\n"
    split = len(raw) // 2
    transcript.write_bytes(raw[:split])
    run("UserPromptSubmit")
    assert not (directory / "page.tsx").exists()
    with transcript.open("ab") as stream:
        stream.write(raw[split:])
    run("Stop")
    assert len(entries(directory)) == 1


@pytest.mark.parametrize("block", [image(b"not a png"), image(PNG, "image/svg+xml"),
                                     {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "!!!"}}])
def test_untrusted_images_fail_without_executable_artifacts(env, block):
    transcript, directory, run, _, payload, agents = env
    run("SessionStart")
    append(transcript, record(block))
    pastes.handle({**payload, "hook_event_name": "UserPromptSubmit"}, "memory-nudge", "seat", agents)
    assert not (directory / "page.tsx").exists()
    assert not list(directory.glob("assets/*"))


def test_remote_image_never_fetched(env, monkeypatch):
    transcript, directory, run, _, _, _ = env
    monkeypatch.setattr(pastes.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("remote fetch"))
    run("SessionStart")
    append(transcript, record({"type": "image", "source": {"type": "url", "url": "https://example.invalid/x"}}))
    run("UserPromptSubmit")
    assert not (directory / "page.tsx").exists()


@pytest.mark.parametrize("limit", ["IMAGE_BYTES", "SCAN_BYTES", "STORAGE_BYTES", "HISTORY_LIMIT"])
def test_limits_are_visible_and_never_delete_user_assets(env, monkeypatch, limit):
    transcript, directory, run, _, _, _ = env
    run("SessionStart")
    append(transcript, record(image()))
    monkeypatch.setattr(pastes, limit, 0)
    with pytest.raises(pastes.CaptureError):
        run("UserPromptSubmit")
    assert not (directory / "page.tsx").exists()
    assert not (directory / ".capture.lock").exists()


def test_time_budget_checks_before_scanning(env, monkeypatch):
    transcript, directory, run, _, _, _ = env
    run("SessionStart")
    append(transcript, record(image()))
    clock = iter([0, 3])
    monkeypatch.setattr(pastes.time, "monotonic", lambda: next(clock))
    with pytest.raises(pastes.CaptureError, match="scan budget"):
        run("UserPromptSubmit")
    assert not (directory / "page.tsx").exists()


def test_assets_exist_before_page_publication_and_page_repair(env, monkeypatch):
    transcript, directory, run, _, _, _ = env
    run("SessionStart")
    append(transcript, record(image()))
    original = pastes._atomic
    calls = []
    def atomic(path, data):
        if path.name == "page.tsx":
            assert list((directory / "assets").glob("*.png"))
            calls.append("page")
        original(path, data)
    monkeypatch.setattr(pastes, "_atomic", atomic)
    run("UserPromptSubmit")
    (directory / "page.tsx").unlink()
    run("Stop")
    assert calls == ["page", "page"]
    assert len(entries(directory)) == 1


def test_bridge_failure_retains_artifacts_retries_and_redacts(env, monkeypatch, capsys):
    transcript, directory, run, opened, payload, agents = env
    run("SessionStart")
    append(transcript, record(image()))
    monkeypatch.setattr(pastes, "_open", lambda *a: (_ for _ in ()).throw(ValueError("secret")))
    pastes.handle({**payload, "hook_event_name": "UserPromptSubmit"}, "memory-nudge", "seat", agents)
    assert "secret" not in capsys.readouterr().err
    assert len(entries(directory)) == 1 and (directory / "page.tsx").exists()
    monkeypatch.setattr(pastes, "_open", lambda *a: opened.append(a) or True)
    run("Stop")
    assert len(opened) == 1


def test_two_sessions_shared_history_only_capturing_agent_retries_open(env, monkeypatch):
    transcript, directory, run, _, payload, agents = env
    other_transcript = transcript.with_name("session-b.jsonl")
    other_transcript.touch()
    other = {**payload, "session_id": "session-b", "transcript_path": str(other_transcript)}
    attempted = []

    def unavailable(*args):
        attempted.append(args)
        raise OSError("fixture bridge unavailable")

    monkeypatch.setenv("LITESUITE_LEAF_ID", "capturing-leaf")
    monkeypatch.setattr(pastes, "_open", unavailable)
    run("SessionStart")
    append(transcript, record(image(), image(GIF, "image/gif")))
    with pytest.raises(OSError):
        run("UserPromptSubmit")
    assert len(entries(directory)) == 2
    assert len(attempted) == 1

    opened = []
    monkeypatch.setattr(pastes, "_open", lambda *args: opened.append(args) or True)
    monkeypatch.setenv("LITESUITE_LEAF_ID", "later-leaf")
    for event, action in [("SessionStart", "check"), ("UserPromptSubmit", "memory-nudge"),
                          ("PostToolUse", "check"), ("Stop", "obs")]:
        pastes.capture({**other, "hook_event_name": event}, action, "later-agent", agents)
    assert opened == []  # B did not paste; it cannot claim/open A's pending page.
    assert not json.loads((directory / ".history.json").read_text()).get("opened")

    # Even a later agent invoking A's cursor must not adopt its image ownership.
    pastes.capture({**payload, "hook_event_name": "Stop"}, "obs", "later-agent", agents)
    assert opened == []
    run("Stop")
    assert len(opened) == 1
    assert opened[0][1] == "seat"
    assert opened[0][3] == "capturing-leaf"
    assert json.loads((directory / ".history.json").read_text())["opened"] is True


def test_official_bridge_body_file_first_token_bounded_timeout(env, monkeypatch):
    _, _, _, _, payload, agents = env
    # Undo fixture fake; retain direct reference to the actual implementation.
    monkeypatch.setattr(Path, "home", lambda: agents.parent / "fixture-home")
    token_file = Path.home() / ".litesuite" / "bridge-token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("fixture-current-token")
    monkeypatch.setenv("LITESUITE_BRIDGE_TOKEN", "fixture-stale-token")
    monkeypatch.setenv("LITESUITE_BRIDGE_URL", "http://127.0.0.1:7423")
    monkeypatch.setenv("LITESUITE_LEAF_ID", "fixture-leaf")
    def urlopen(request, timeout):
        assert timeout == 0.75
        assert request.get_header("Authorization") == "Bearer fixture-current-token"
        assert request.full_url.endswith("/theater/open")
        body = json.loads(request.data)
        assert body == {"projectDir": payload["cwd"], "pageId": "pastes", "title": "Pasted images",
                        "owner": {"id": "seat", "name": "Fixture", "tier": "worker"}, "leafId": "fixture-leaf"}
        return io.BytesIO(b'{"ok":true}')
    from types import SimpleNamespace
    monkeypatch.setattr(pastes.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=urlopen))
    monkeypatch.setenv("LITESUITE_LEAF_ID", "later-hook-leaf")
    assert REAL_OPEN(Path(payload["cwd"]), "seat", agents, "fixture-leaf")


REAL_OPEN = pastes._open


@pytest.mark.parametrize("event,action,expected", [("SessionStart", "check", ["inbox", "heartbeat"]),
                                                   ("UserPromptSubmit", "memory-nudge", ["nudge"]),
                                                   ("PostToolUse", "check", ["inbox", "heartbeat"]),
                                                   ("Stop", "obs", ["stop", "obs"])])
def test_existing_dispatch_handlers_survive_display_failure(monkeypatch, event, action, expected):
    from liteharness import stop_forward
    calls = []
    monkeypatch.setattr(hooks, "_read_hook_stdin", lambda: {"hook_event_name": event})
    monkeypatch.setattr(hooks, "_apply_hook_context", lambda value: None)
    monkeypatch.setattr(hooks.config, "get_agent_id", lambda: "seat")
    monkeypatch.setattr(pastes, "handle", lambda *a: (_ for _ in ()).throw(OSError("failure")))
    monkeypatch.setattr(hooks, "check_inbox", lambda: calls.append("inbox"))
    monkeypatch.setattr(hooks, "update_heartbeat", lambda: calls.append("heartbeat"))
    monkeypatch.setattr(hooks, "memory_nudge", lambda: calls.append("nudge"))
    monkeypatch.setattr(stop_forward, "forward_stop", lambda value: calls.append("stop"))
    monkeypatch.setattr(hooks, "emit_obs_event", lambda *a: calls.append("obs"))
    monkeypatch.setattr(sys, "argv", ["hooks", action])
    hooks.main()
    assert calls == expected


def test_helper_does_not_swallow_interrupts(env, monkeypatch):
    _, _, _, _, payload, agents = env
    monkeypatch.setattr(pastes, "capture", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        pastes.handle(payload, "check", "seat", agents)
