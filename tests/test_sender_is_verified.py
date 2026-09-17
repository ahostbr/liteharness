"""T841 — `--from` is checked, because the id that says WHO SPOKE was not.

🔴 THE SPECIMEN. On 2026-09-17 message `874dd34b` reached Sentinel
`--from c8f7ae56-4748-41e4-abba-d977ea56e7ec` — NeonRack's own id with the last
twelve characters wrong. The send printed a normal "Sent message …" and nothing
else. Sentinel asked whether an unknown sender was using a near-copy of a live
seat's id, which is exactly the right question and one the channel could not
answer.

`--to` has been verified since T238 (two reads, did-you-mean, refuse, --force).
`--from` was passed straight to `inbox.send`.

    A RECIPIENT TYPO FAILS LOUDLY; A SENDER TYPO SUCCEEDS AND CREATES A GHOST.

⚠️ AND THE FIX IS DELIBERATELY NOT SYMMETRIC. A seat that has not registered yet
really does send under an unknown id, so refusing every unknown `--from` would
close a live path to shut a typo hole. Two tiers instead (Sentinel 716463bb):
far id warns and sends; near miss refuses and names its neighbour.
"""

import json
import subprocess
import sys
from pathlib import Path

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


def test_unregistered_far_sender_warns_and_still_sends(tmp_path: Path) -> None:
    """(b) 🔴 THE NOT-YET-REGISTERED SEAT MUST STILL GET ITS MESSAGE OUT.

    This is the case that makes refusing every unknown `--from` wrong, and it is
    asserted BEFORE the refusal arm below so the refusal can never be widened
    into it without this going red.
    """
    _register(tmp_path, PEER)
    result = _run(tmp_path, ["send", PEER, "hello", "--from", FAR])
    assert result.returncode == 0, result.stderr
    assert "Sent message" in result.stdout, result.stdout
    assert "not registered" in result.stderr, "the warning is the whole point"


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


def test_near_miss_still_sends_under_force(tmp_path: Path) -> None:
    """(d) The escape hatch, same as `--to`'s.

    ⬜ An error that offers a remedy in its own text is not a blocker — and that
    remedy has to WORK, or the next person routes around the whole check.
    """
    _register(tmp_path, LIVE)
    _register(tmp_path, PEER)
    result = _run(tmp_path, ["send", PEER, "hello", "--from", TYPO, "--force"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Sent message" in result.stdout, result.stdout
