"""
Agent naming — deterministic UUID-seeded name generator + user override layer.

Design:
  1. Every agent gets a default name derived from its UUID (no storage needed)
  2. User can override with --name, stored in ~/.liteharness/names/<UUID>
  3. get_name() checks override first, falls back to generated name
  4. Cleanup ties to stale agent purge (same 12h TTL)
"""

import hashlib
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


def is_name_taken(name: str, exclude_id: str | None = None) -> str | None:
    """Check if a name is already in use by an active agent. Returns the agent_id or None."""
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
            last_seen = datetime.fromisoformat(data.get("last_seen", "")).timestamp()
            if now - last_seen > 43200:
                continue
            agent_name = get_name(agent_id)
            if agent_name.lower() == name.lower():
                return agent_id
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    return None


def cleanup_stale_names() -> int:
    """Remove name overrides for agents that no longer have presence files."""
    names_dir = config.get_root() / "names"
    agents_dir = config.get_root() / "agents"

    if not names_dir.exists():
        return 0

    removed = 0
    for f in names_dir.iterdir():
        if not f.is_file():
            continue
        agent_id = f.name
        presence = agents_dir / f"{agent_id}.json"
        if not presence.exists():
            f.unlink(missing_ok=True)
            removed += 1
    return removed
