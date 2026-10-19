# One-call fleet save and restore (cards C, D, I)

`liteharness sessions checkpoint <save|restore|status|cancel|retry>`, the same thing as the
`checkpoint` tool on the Agent Bridge (`/mcp`). Built on the card A contract
(`checkpoint-contract.md`); legacy `sessions save/restore/list/status` are untouched.

```
liteharness sessions checkpoint save    --coordinator-handoff <abs path> [--include-offline ID ...] [--wait N]
liteharness sessions checkpoint restore --checkpoint-id <id|latest> [--dry-run] [--keep-shells] [--wait N]
liteharness sessions checkpoint status  [--operation-id ID | --request-id ID | --checkpoint-id ID]
liteharness sessions checkpoint cancel  --operation-id ID
liteharness sessions checkpoint retry   --operation-id ID --seat-id ID [--seat-id ID ...]
common: [--request-id ID] [--json]
```

`--json` prints exactly `wire(Result)`. A mutating call returns `accepted` with an operation id
within seconds; the work runs in a detached worker (`python -m liteharness.checkpoint_worker`),
so a killed tool call or a closed terminal never stops it. A dead worker with unfinished state
reports `unknown`, never `running`. One mutating operation may be active per store; a second is
`rejected` naming the first. `incomplete`, `rejected` and `unknown` exit non-zero and print
`NOT READY` (or `REFUSED`).

## Authority

The CLI is the trust anchor. `save`, `restore`, `cancel` and `retry` (not `--dry-run`, not
`status`) need an actor id that resolves to a live registry ORCHESTRATOR
(`recovered_destination.issuer`: online, owner process alive, not superseded). `--dry-run` exists
only for `save` and `restore`; on any other action it is refused. Outside a LiteSuite terminal (no `LITESUITE_LEAF_ID`) the actor is the caller's own environment
identity and `--actor-id`/`--leaf-id` are ignored. Where the bridge or a LiteSuite terminal set
`LITESUITE_LEAF_ID`, `--actor-id` is honoured, but only because that leaf must equal the actor
row's `leaf_id`; a `--leaf-id` that differs from the environment's is refused, and `--require-leaf`
(always passed by the tool) makes the leaf mandatory. Residual risk: a leaf id is discoverable by other seats through the
bridge, so this narrows impersonation without making it impossible; a scoped tool-only bridge
token is the real fix and is out of this card.

## Save (`checkpoint_save.py`, roster in `checkpoint_roster.py`)

1. Every live registry seat plus `--include-offline` ids, resolved from the registry row and the
   native transcript (Claude) or the owned home (LiteTUI). Unresolvable seats stay in the
   denominator as blocked rows. The caller is the manifest coordinator: never auto-launched,
   closed or compacted.
2. An `incomplete` manifest is published BEFORE any seat is asked for anything.
3. Per seat: a cooperative inbox request (`CHECKPOINT-HANDOFF <path> <idle|waiting|held>`);
   `checkpoint.compact_command` (argv list, `{agent_id}`/`{name}` placeholders, no shell) for
   Claude seats, and LiteTUI's native `gui.checkpoint.*` verbs. No configured command means a
   `compact-unsupported` blocker, never a silent pass. An unknown disposition is `held`.
4. `complete` only when the contract accepts it. A partial save is retained and restorable.
5. Layout receipts are durable only when the seat's leaf is observed in `/context` at the frozen
   revision. That is observation, not a flush proof; LiteSuite exposes no flush endpoint.
6. Never commits, pushes, stashes, kills or reboots.

## Restore (`checkpoint_restore.py`)

A pure `plan(manifest, liveness, coordinator)` then an idempotent driver. Owners before crew,
parents before children, one launch at a time; a missing parent, a cycle or a failed parent blocks
only its own subtree. A live seat is reattached, never relaunched; unknown liveness is `unknown`.
A launch intent is journaled before `spawn --resume` runs (a crash between intent and result is
`unknown` and is never retried; so is a timeout). Never `--kill-old`, a new name or a takeover.
Works from an incomplete checkpoint. The caller must be the saved coordinator, or the same named
seat under a new id.

Placement never puts a seat in the coordinator's pane: the coordinator's panes ride
`LITEHARNESS_EXCLUDE_PANES` into `cli._place_split`, which also resolves pane aliases
(id, syncId, mirrorId) and drops an orchestrator caller's own pane. A reserved-slot seat takes no
`--prompt`: its pointer goes by inbox first, then ONE fixed wake line is typed into its exact new
session (`pointer-not-delivered` when no turn is confirmed). Bare restored shells (no child
process, no seat, not the coordinator's pane, at least one leaf kept per pane) are closed after
the seats are up; `--dry-run` lists them, `--keep-shells` skips it.

## Known limits

- Restore refuses to launch a seat that another operation on the same checkpoint started and never
  reported on (`prior-attempt-unknown`); reconcile it first. A seat's `live` needs the registry row
  and a hosting PTY to agree, otherwise it is `unknown` and is not reattached.
- Bare-shell cleanup closes only a leaf that a SAVED seat slot names (where LiteSuite put a
  placeholder back), whose PTY root process is a shell with no child and is not a seat. A leaf no saved
  slot names is never closed, and the PTY's cwd is not consulted. Anything unreadable stays.
- A mid-work (`held`) seat keeps its handoff but is never compacted: `held-not-compacted`.
- Compaction needs `checkpoint.compact_command` for Claude seats; none ships.
- Layout receipts are observation, not a flush proof (LiteSuite has no flush endpoint).
- An orchestrator caller of any `spawn --split` no longer falls into its own pane by default.
