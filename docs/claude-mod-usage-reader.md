# Claude mod telemetry reader (T0314)

The separate public `liteharness-plugin` source supplies a Claude-only
cache/context band and writes schema-v1 numeric snapshots under
`~/.liteharness/mods/usage/<sessionId>.json`. This Python package supplies a
read-only retrieval path for an external the orchestrator tick:

```sh
python -m liteharness.mod_usage --session SESSION_ID
python -m liteharness.mod_usage --all
```

No hook, CLI installer, transcript parser, installed setting, or maildir is
changed. A plugin-only update does not install this companion Python module.
Python package version 0.4.3 and plugin version 1.0.18 remain independent and
unchanged; release/install are separate approvals.

`--session` validates IDs (`[A-Za-z0-9][A-Za-z0-9_-]{0,127}`), reads at most 64 KiB,
checks source/schema/session identity and numeric bounds, excludes symlinks and
whitelists output fields. It reports `ok`, `missing`, `unavailable`, `stale` or
`ended`, not fabricated zero usage. Partial JSON from the mod's non-atomic write
is unavailable; retry on the next tick. More than 60 seconds since sampledAt is
stale (the mod samples every 15 seconds). Neither stale nor ended is fresh seat
presence evidence. Missing telemetry must not be interpreted as a cold cache.

`metrics.context` holds nullable `tokens`, `window`, and `percent`.
`metrics.cache` holds `estimated: true`,
`basis: "successful-main-turn-step-start"`, `ttlMs: 3600000`, nullable
`lastRequestAt`/`expiresAt`/`remainingMs`, and `state: warm|cold|unknown`.
The reader recalculates remainingMs from expiry at read time. This is an assumed
one-hour cache estimate based on successful main-session request initiation,
not provider expiry/cache-hit proof. It never authorizes a model request or
compaction. Transcript, prompts, cwd, secrets, and arbitrary extra JSON fields
are never returned.

`--all` enumerates file metadata, selects the newest 256 candidates by mtime
with a session-ID tiebreaker, and reads only those. `truncated` and `candidates`
make incomplete discovery explicit. Files older than 24 hours are filtered by
mtime and valid sample age, not deleted. Exact `--session` reads bypass this cap
and discovery age filter. Body reads and selection memory are bounded; metadata
enumeration and physical file count are not. Explicit authorized maintenance
pruning is follow-up work. Store/JSON data is untrusted local telemetry, not a
security or fleet authority.

Focused reader tests exercise real temporary files and the CLI function. They
also cover existing Python installation and hook-dispatch contracts. They do
not prove an installed Claude seat or UI; the plugin's host fixture tests and
leader-scheduled runtime probe provide separate evidence.
