"""Agent role vocabulary: one table, read by every site that used to hold a tier set.

New writes use ROLES. The five older tier words are accepted as aliases on INPUT for one
release and are always RESOLVED on read, so a presence file, an env var or a running seat
that still says `worker` keeps working indefinitely. Nothing here rewrites stored data.

Presence rows and the spawn env carry BOTH spellings for one release: `role` / LITEHARNESS_ROLE
(the new word, read first) and `tier` / LITEHARNESS_TIER (the old word), because other
readers of those stores (the desktop app) still compare against the old words. The wire to the
desktop bridge speaks the old words for the same reason (`wire_tier`).

`role` is the authoritative key. Both spellings of a row are always written from one value
(`presence_fields`), and the heartbeat rewrites a row whose two keys differ or that carries
only one, so they cannot stay apart. Consequences, stated exactly: a `tier`-only edit made by
the desktop app or an older CLI is overruled on the next heartbeat when the row also has a
`role`; a row whose `role` is a valid role but whose `tier` is some other word (for example
{role: builder, tier: librarian}) is rewritten to the role's own old word (worker); a word
outside the table is left alone only when the row has no `role`, or its `role` is itself
outside the table. `LITEHARNESS_ROLE` is read before `LITEHARNESS_TIER`;
the spawner sets both from one value, so a mismatch can only come from an inherited stale
`LITEHARNESS_ROLE`, which wins by design (the newer key outranks the older).
"""
from __future__ import annotations

import os

ROLES = ("orchestrator", "owner", "builder", "scout", "critic")

#: roles that must be given an explicit --cwd on a fresh spawn.
CWD_REQUIRED_ROLES = ("builder", "owner")

#: old tier word -> role. Input aliases for one release; history stays readable.
#: `orchestrator` is not here: it is a role in its own right, the one word that did not change.
ROLE_ALIASES = {
    "leader": "owner",
    "worker": "builder",
    "thinker": "scout",
    "reviewer": "critic",
}

#: role -> the old tier word (file names and stored keys that predate the rename).
LEGACY_TIER = {"orchestrator": "orchestrator", **{new: old for old, new in ROLE_ALIASES.items()}}

DEFAULT_ROLE = "builder"


def normalize_role(value) -> str | None:
    """A role or an old tier word -> the role. None when it is neither."""
    word = value.strip().lower() if isinstance(value, str) else ""
    if word in ROLES:
        return word
    return ROLE_ALIASES.get(word)


def deprecation_note(value) -> str | None:
    """One line when `value` is an old tier word; None for a role or an unknown word."""
    word = value.strip().lower() if isinstance(value, str) else ""
    new = ROLE_ALIASES.get(word)
    if new is None:
        return None
    return f"Note: tier word '{word}' is deprecated; use role '{new}'."


def canonical(value, default: str = DEFAULT_ROLE) -> str:
    """Resolve a STORED value for display or comparison. Unknown words pass through
    unchanged (reading never destroys data); empty falls back to `default`."""
    if not isinstance(value, str) or not value.strip():
        return default
    return normalize_role(value) or value.strip()


def row_role(row, default: str = DEFAULT_ROLE) -> str:
    """The role a presence row declares: `role` first, then the older `tier` key."""
    if not isinstance(row, dict):
        return default
    return canonical(row.get("role") or row.get("tier"), default)


def wire_tier(value) -> str | None:
    """The old tier word for a role, for readers that have not learned the new words.
    An unknown word passes through unchanged."""
    word = normalize_role(value)
    if word:
        return LEGACY_TIER[word]
    return value.strip() if isinstance(value, str) and value.strip() else None


def presence_fields(value) -> dict:
    """The presence-row keys that declare `value`: the new `role` plus the old `tier`."""
    word = normalize_role(value)
    if word is None:
        return {"tier": value} if value else {}
    return {"role": word, "tier": LEGACY_TIER[word]}


def env_role(environ=None, default: str = "") -> str:
    """The role named by the environment: LITEHARNESS_ROLE, else LITEHARNESS_TIER."""
    env = os.environ if environ is None else environ
    return canonical(env.get("LITEHARNESS_ROLE") or env.get("LITEHARNESS_TIER"), default)
