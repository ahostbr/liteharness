"""T841 — `--from` is checked, because the id that says WHO SPOKE was not.

🔴 THE SPECIMEN. On 2026-09-17 message `874dd34b` reached the orchestrator
`--from c8f7ae56-4748-41e4-abba-d977ea56e7ec` — NeonRack's own id with the last
twelve characters wrong. The send printed a normal "Sent message …" and nothing
else. the orchestrator asked whether an unknown sender was using a near-copy of a live
seat's id, which is exactly the right question and one the channel could not
answer.

`--to` has been verified since T238 (two reads, did-you-mean, refuse, --force).
`--from` was passed straight to `inbox.send`.

    A RECIPIENT TYPO FAILS LOUDLY; A SENDER TYPO SUCCEEDS AND CREATES A GHOST.

T0285 supersedes T841's warning-only distant-id exception: every unregistered
sender is refused, with actionable recovery and no maildir write. Explicit
--force remains the escape hatch; registered senders continue to work.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from liteharness import cli, config, inbox

LIVE = "c8f7ae56-4748-41e4-abba-d977ea61a70e"
TYPO = "c8f7ae56-4748-41e4-abba-d977ea56e7ec"  # the real specimen: 12 chars off
FAR = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"  # a genuinely different id
PEER = "858eb4ce-87f4-432d-bba0-85b24ac897ec"


def _run(home: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Drive the real CLI in a private HOME so no live registry is touched."""
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        "LITEHARNESS_HOME": str(home / ".liteharness"),
    }
    return subprocess.run(
        [sys.executable, "-m", "liteharness.cli", *args],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _register(home: Path, agent_id: str) -> None:
    agents = home / ".liteharness" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{agent_id}.json").write_text(
        json.dumps({"agent_id": agent_id, "name": "Seat", "tier": "worker"}),
        encoding="utf-8",
    )


def test_registered_sender_is_silent(tmp_path: Path) -> None:
    """(a) The normal case says nothing new. A check that chatters gets muted."""
    _register(tmp_path, LIVE)
    _register(tmp_path, PEER)
    result = _run(tmp_path, ["send", PEER, "hello", "--from", LIVE])
    assert "not registered" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("sender", [FAR, "my-script"])
@pytest.mark.parametrize("recipient", [PEER, "broadcast"])
def test_unregistered_far_sender_is_refused_without_writing_mail(
    tmp_path: Path, sender: str, recipient: str,
) -> None:
    """T0285 causal control: a valid recipient cannot excuse an unknown sender."""
    _register(tmp_path, PEER)
    result = _run(tmp_path, ["send", recipient, "hello", "--from", sender])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Sent message" not in result.stdout, result.stdout
    assert "not registered" in result.stderr, result.stderr
    assert "NOTHING WAS SENT" in result.stderr, result.stderr
    assert "register" in result.stderr and "--agent-id" in result.stderr, result.stderr
    assert "--force" in result.stderr, result.stderr
    assert not list((tmp_path / ".liteharness" / "inbox").rglob("*.json"))


def test_near_miss_sender_is_refused_and_names_its_neighbour(tmp_path: Path) -> None:
    """(c) The specimen. Nobody's first registration is 12 characters off a live id."""
    _register(tmp_path, LIVE)
    _register(tmp_path, PEER)
    result = _run(tmp_path, ["send", PEER, "hello", "--from", TYPO])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NOTHING WAS SENT" in result.stderr, result.stderr
    # Naming the neighbour is what turns "wrong" into "fixable".
    assert LIVE in result.stderr, result.stderr
    assert "Sent message" not in result.stdout


@pytest.mark.parametrize("sender", [TYPO, FAR, "my-script"])
def test_unregistered_sender_still_sends_under_force(tmp_path: Path, sender: str) -> None:
    """(d) The escape hatch, same as `--to`'s.

    ⬜ An error that offers a remedy in its own text is not a blocker — and that
    remedy has to WORK, or the next person routes around the whole check.
    """
    _register(tmp_path, LIVE)
    _register(tmp_path, PEER)
    result = _run(tmp_path, ["send", PEER, "hello", "--from", sender, "--force"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Sent message" in result.stdout, result.stdout
    mail = list((tmp_path / ".liteharness" / "inbox" / "new").glob("*.json"))
    assert len(mail) == 1
    assert json.loads(mail[0].read_text(encoding="utf-8"))["from"] == sender


@pytest.fixture
def isolated_send(tmp_path: Path, monkeypatch):
    root = tmp_path / ".liteharness"
    monkeypatch.setattr(config, "get_root", lambda: root)
    for suffix in ("ROOT", "NEW", "CUR", "DONE", "TMP"):
        path = root / "inbox" if suffix == "ROOT" else root / "inbox" / suffix.lower()
        monkeypatch.setattr(inbox, f"INBOX_{suffix}", path)
    return tmp_path


def test_distant_sender_reappearing_on_recheck_is_sent(isolated_send, monkeypatch, capsys):
    _register(isolated_send, PEER)
    sleeps = []

    def restore_sender(delay):
        sleeps.append(delay)
        _register(isolated_send, FAR)

    monkeypatch.setattr(cli.time, "sleep", restore_sender)
    cli.cmd_send(PEER, "hello", from_id=FAR)
    assert sleeps == [cli.RECIPIENT_RECHECK_DELAY_S]
    assert "Sent message" in capsys.readouterr().out
    assert len(list(inbox.INBOX_NEW.glob("*.json"))) == 1


@pytest.mark.parametrize("registry", ["missing", "empty"])
def test_unverifiable_sender_warns_even_for_broadcast(isolated_send, capsys, registry):
    if registry == "empty":
        (isolated_send / ".liteharness" / "agents").mkdir(parents=True)
    cli.cmd_send("broadcast", "hello", from_id=FAR)
    out, err = capsys.readouterr()
    assert "Sent message" in out
    assert "SENDER NOT VERIFIED" in err
    assert "not registered" not in err
    assert len(list(inbox.INBOX_NEW.glob("*.json"))) == 1


def test_recheck_becoming_unverifiable_warns_and_sends(isolated_send, monkeypatch, capsys):
    record_root = isolated_send / ".liteharness" / "agents"
    _register(isolated_send, PEER)
    monkeypatch.setattr(cli.time, "sleep", lambda _: (record_root / f"{PEER}.json").unlink())
    cli.cmd_send("broadcast", "hello", from_id=FAR)
    out, err = capsys.readouterr()
    assert "Sent message" in out
    assert "SENDER NOT VERIFIED" in err
    assert "not registered" not in err
