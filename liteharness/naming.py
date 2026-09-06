"""
Agent naming — deterministic UUID-seeded name generator + user override layer.

Design:
  1. Every agent gets a default name derived from its UUID (no storage needed)
  2. User can override with --name, stored in ~/.liteharness/names/<UUID>
  3. get_name() checks override first, falls back to generated name
  4. Cleanup ties to stale agent purge (same 12h TTL)
"""

import hashlib
import time
from pathlib import Path

from . import config

ADJECTIVES = [
    # original 50
    "swift", "iron", "shadow", "bright", "cold", "dark", "keen", "bold",
    "stark", "rust", "deep", "wild", "sharp", "pale", "grim", "red",
    "ash", "storm", "frost", "vivid", "stone", "glass", "black", "white",
    "silver", "amber", "jade", "cobalt", "copper", "steel", "burnt",
    "hollow", "ghost", "silent", "rapid", "wired", "raw", "dry", "thin",
    "dense", "neon", "void", "zero", "live", "dual", "hex", "arc",
    "prime", "flux", "pulse",
    # extended 50
    "acid", "apex", "bare", "brisk", "broad", "coiled", "crisp", "cyan",
    "dim", "dusk", "elite", "even", "faint", "far", "fast", "fell",
    "firm", "flat", "free", "full", "gold", "gray", "hard", "haze",
    "high", "hot", "long", "loud", "low", "mute", "nano", "neat",
    "null", "odd", "open", "pink", "plain", "pure", "rare", "rich",
    "rigid", "rough", "sheer", "slim", "slow", "soft", "solid", "sour",
    "stale", "true",
]

NOUNS = [
    # original 50
    "relay", "watch", "cairn", "ridge", "flint", "rivet", "bolt", "shard",
    "forge", "vault", "spire", "crest", "node", "wire", "blade", "drift",
    "gate", "mark", "lens", "core", "frame", "link", "port", "span",
    "grid", "rail", "edge", "root", "stem", "axis", "helm", "crow",
    "pike", "latch", "rune", "glyph", "prism", "orbit", "coil", "band",
    "lock", "scout", "ward", "clip", "notch", "wedge", "strut", "brace",
    "fuse", "loop",
    # extended 50
    "amp", "arch", "base", "beam", "bin", "bit", "byte", "cap",
    "cell", "chain", "chip", "choke", "clamp", "clause", "cluster", "codex",
    "cone", "crank", "crypt", "cube", "curve", "deck", "depth", "disc",
    "dome", "duct", "field", "flag", "flak", "flash", "fork", "hatch",
    "hook", "knob", "layer", "mesh", "mint", "mast", "pack", "pad",
    "path", "peak", "pipe", "plug", "pod", "rack", "ramp", "ring",
    "rod", "tile",
]


def generate_name(agent_id: str) -> str:
    """Deterministic two-word name from UUID. Same UUID always gets the same name.

    Uses 2 bytes per index (65536 values) for near-uniform distribution across
    100 adjectives and 100 nouns (10,000 combinations).
    """
    h = hashlib.sha256(agent_id.encode()).digest()
    adj_idx = int.from_bytes(h[0:2], "little") % len(ADJECTIVES)
    noun_idx = int.from_bytes(h[2:4], "little") % len(NOUNS)
    adj = ADJECTIVES[adj_idx]
    noun = NOUNS[noun_idx]
    return f"{adj.capitalize()}{noun.capitalize()}"


def get_override(agent_id: str) -> str | None:
    """Read user-override name from ~/.liteharness/names/<UUID>."""
    names_dir = config.get_root() / "names"
    path = names_dir / agent_id
    if path.exists():
        try:
            name = path.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass
    return None


def set_override(agent_id: str, name: str) -> None:
    """Write user-override name to ~/.liteharness/names/<UUID>."""
    names_dir = config.get_root() / "names"
    names_dir.mkdir(parents=True, exist_ok=True)
    (names_dir / agent_id).write_text(name, encoding="utf-8")


def clear_override(agent_id: str) -> None:
    """Remove user-override name."""
    path = config.get_root() / "names" / agent_id
    path.unlink(missing_ok=True)


def get_name(agent_id: str) -> str:
    """Get agent name — override first, then generated."""
    override = get_override(agent_id)
    if override:
        return override
    return generate_name(agent_id)


def _pid_alive(pid: int | None) -> bool:
    """Return True if a PID currently maps to a running process."""
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def is_name_taken(name: str, exclude_id: str | None = None) -> str | None:
    """Check if a name is already in use by a live agent. Returns the agent_id or None.

    An agent is considered live only if it has a recent heartbeat AND an alive
    owning session_pid — matching the liveness bar used by cmd_discover and the
    takeover path. A dead ghost (fresh heartbeat, dead PID) no longer blocks
    plain ``register --name``."""
    import json
    import time
    from datetime import datetime

    agents_dir = config.get_root() / "agents"
    if not agents_dir.exists():
        return None

    now = time.time()
    for f in agents_dir.glob("*.json"):
        agent_id = f.stem
        if agent_id == exclude_id:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("exited_at"):
                continue
            last_seen = datetime.fromisoformat(data.get("last_seen", "")).timestamp()
            if now - last_seen > 43200:
                continue
            session_pid = data.get("session_pid")
            if session_pid and not _pid_alive(session_pid):
                continue
            agent_name = get_name(agent_id)
            if agent_name.lower() == name.lower():
                return agent_id
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    return None


#: How long a name override outlives the presence file it belonged to.
#:
#: 🔴 T418-A. This used to be zero: the sweep deleted an override the instant no
#: presence file existed for that id. But a presence file is removed by ORDINARY
#: SESSION END — `hooks.deregister()`'s own docstring says "Remove agent presence
#: file when the SESSION ends" — so a seat that shut down cleanly and resumed came
#: back under `generate_name(<uuid>)` instead of its name. Measured: this repo's
#: own seats held their overrides only for the two minutes their humans spent
#: re-taking the names by hand on 2026-09-06.
#:     A CLEANUP KEYED ON "THE PRESENCE FILE IS GONE" CANNOT TELL A SEAT THAT
#:     ENDED FROM A SEAT THAT DIED, BECAUSE ENDING IS HOW A SEAT STOPS.
#: Days rather than hours because the thing protected is an identity a human
#: chose. The cost of keeping a dead name too long is that nobody can reuse that
#: word for a while — against a live seat silently losing its name overnight.
NAME_OVERRIDE_RETENTION_DAYS = 30


def cleanup_stale_names(
    retention_days: float = NAME_OVERRIDE_RETENTION_DAYS,
    now: float | None = None,
) -> int:
    """Remove name overrides long abandoned by agents that no longer exist.

    ⚠️ ABSENCE OF A PRESENCE FILE IS NOT EVIDENCE OF DEATH — it is the normal
    state of every seat between sessions. The discriminator is AGE: an override
    whose owner has not registered for `retention_days` is a ghost's; a younger
    one belongs to a seat that may still come back, and it is kept.

    ⬜ THIS IS NOT THE URGENT PATH FOR RECLAIMING A NAME, deliberately.
    `cli._evict_agent_records` moves the override out with the presence file the
    moment an explicit `--takeover` reclaims a dead ghost's name, so nobody has
    to wait out this window to get a name back.
    """
    names_dir = config.get_root() / "names"
    agents_dir = config.get_root() / "agents"

    if not names_dir.exists():
        return 0

    cutoff = (now if now is not None else time.time()) - retention_days * 86400
    removed = 0
    for f in names_dir.iterdir():
        if not f.is_file():
            continue
        agent_id = f.name
        presence = agents_dir / f"{agent_id}.json"
        if presence.exists():
            continue
        try:
            touched = f.stat().st_mtime
        except OSError:
            continue  # vanished or unreadable mid-sweep — not ours to judge
        if touched > cutoff:
            continue  # recent enough that its seat may simply be between sessions
        f.unlink(missing_ok=True)
        removed += 1
    return removed
