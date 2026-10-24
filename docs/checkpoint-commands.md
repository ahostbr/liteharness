# One-call fleet save and restore (cards C, D, I)

`liteharness sessions checkpoint <save|restore|status|cancel|retry>`, the same thing as the
`checkpoint` tool on the Agent Bridge (`/mcp`). Built on the card A contract
(`checkpoint-contract.md`); legacy `sessions save/restore/list/status` are untouched.

```
liteharness sessions checkpoint save    --coordinator-handoff <abs path> [--include-offline ID ...] [--exclude NAME|ID ...] [--wait N]
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
   denominator as blocked rows. The caller is the manifest coordinator: never auto-launched
   or closed. The detached SAVE worker collects it last, using `--coordinator-handoff` rather
   than asking the initiating seat to answer its own call. Claude compaction is requested and
   its boundary observed; LiteTUI uses native prepare/compact/flush. Normal completion needs
   no manual coordinator compaction/retry. A refused or unobserved boundary remains a blocker.
   **Seats the user started himself are left out.** A seat nobody spawned (empty `spawned_by`) that is not
   the coordinator is not ours to hand off or compact: it is recorded in the manifest's `exclusions` with
   reason `started-directly`, never asked, never compacted, never restored. `--exclude NAME|ID` (repeatable)
   leaves out any other seat (`excluded-by-request`); `--include-offline ID` names a seat explicitly and
   wins over the default. The coordinator is never excluded. A caller that is not itself a live seat
   excludes nothing by default (save refuses such a caller anyway). `save --dry-run` prints an
   `excluded:` line with each name and reason.
2. An `incomplete` manifest is published BEFORE any seat is asked for anything. SAVE resolves
   exact current workspace/thread/pane/leaf/tab from the authenticated renderer slot inventory,
   including retained hidden canvas panes. Current session or retained agent identity must resolve
   uniquely; legacy Claude leaf-only bindings require one tab. Registry pane coordinates are not a
   fallback. Missing, ambiguous, conflicting, non-canvas or ownerless bindings retain the seat with
   a `layout-unresolved` blocker even after handoff collection. Inventory is observation, not durability.
3. Per seat: a cooperative inbox request (`CHECKPOINT-HANDOFF <path> <idle|waiting|held>`);
   `checkpoint.compact_command` (argv list, `{agent_id}`/`{name}` placeholders, no shell) for
   Claude seats, and LiteTUI's native `gui.checkpoint.*` verbs. No configured command means a
   `compact-unsupported` blocker, never a silent pass. An unknown disposition is `held`.
4. `complete` only when the contract accepts it. A partial save is retained and restorable.
5. Layout durability requires both the frozen `/context` observation and an acknowledged
   `/harness/checkpoint/layout/flush` write. The collector reads back the returned file, checks its
   SHA-256 and checkpoint ID, and verifies the exact workspace/thread/pane/leaf (and tab if recorded).
   The verified layout snapshot is retained in checkpoint evidence, so later live layout writes do
   not erase it. Observation alone, a missing endpoint, a lost acknowledgement, or a mismatched file
   stays unsupported.
6. Never commits, pushes, stashes, kills or reboots.

## Restore (`checkpoint_restore.py`)

A pure `plan(manifest, liveness, coordinator)` then an idempotent driver. Owners before crew,
parents before children, one launch at a time; a missing parent, a cycle or a failed parent blocks
only its own subtree. A live seat is reattached, never relaunched; unknown liveness is `unknown`.
A launch intent is journaled before `spawn --resume` runs (a crash between intent and result is
`unknown` and is never retried; so is a timeout). Never `--kill-old`, a new name or a takeover.
Works from an incomplete checkpoint. The caller must be the saved coordinator, or the same named
seat under a new id. Resume receives the saved CLI, conversation, cwd and tier explicitly.
A known model is passed exactly, including its context suffix; LiteTUI also receives the saved
backend, thinking level and data root, and its owned home must still agree. Reserved-slot
execution assertions require host contract v2 and are revalidated against the current authority
fingerprint before allocation. An older host refuses rather than silently ignoring the assertion.
LiteTUI recovery uses its native inbox consumer; it never requests an external monitor.

Checkpoint restore requires the exact saved workspace/thread/pane/leaf/placeholder session.
`LITEHARNESS_REQUIRED_RECOVERY_SLOT` restricts the existing native recovery path; host contract
v3 must enforce `expectedSlot` before allocation. A missing reservation, older host, moved slot,
or coordinator-protected pane refuses rather than choosing another pane, leaf, or tab. A full or
hidden pane is not a reason to relocate. Presentation aliases never rewrite the saved identity;
coordinator exclusions still cover aliases. Execution assertions remain independent of placement.

Each seat gets one launch without `--prompt`; even legacy prompt-refusal text never causes a
second spawn. Current registry/PTY identity must join the saved owner/pane/leaf in the current
slot inventory, with a new session after launch, before readiness or pointer delivery. The pointer
goes by inbox first, then ONE fixed wake line to that verified session (`pointer-not-delivered`
when no turn is confirmed). Bare shells satisfying the existing cleanup rules may be closed,
but recorded placeholder session IDs are retained even when their registry row is absent.
`--dry-run` lists cleanup candidates; `--keep-shells` skips cleanup.

## Managed cognitive observation (not seed or launch authority)

An existing LiteTUI home resolved by exact agent ID remains authoritative and resumes as-is.
Checkpoint restore does not pass cognitive seed arguments or replace managed prompt edits.
A `profile` receipt observes only the **cognitive role+method digest**, never the full prompt
revision, conversation context, or original `.prompt-launch.json` seed hashes. SAVE and RESTORE
use the same normalized text hashing contract; legitimate edits produce one nonblocking
`cognitive role+method digest: profile changed since save` notice in the receipt and restore
report, not a seat error. Older checkpoints without a profile receipt have an unavailable saved
digest, not an invented change.

Observation uses a normal optional import of the installed LiteTUI `prompt_overrides` owner
helpers, with their read-only scoped resolution, metadata validation and newline normalization.
Only active scoped `role.md` and `method.md` contribute. Missing/inactive entries are explicit,
not empty text and not guessed global fallbacks. Empty active text is distinct from a missing
file. Invalid metadata, unstable publication/home identity, or absent/unsupported helper APIs
produce explicit unavailable/invalid observations; they never reseed a home. The receipt records
the helper module path and distribution version (null when unavailable), exact home and per-file
states. Typed payloads and byte hashes join checkpoint, seat and (for RESTORE) operation; each
restore operation has its own evidence file and cannot overwrite the saved observation.
Corrupt optional profile bytes/hashes/identity joins produce a nonblocking
`profile-evidence-invalid` diagnostic, with the saved comparison unavailable and no invented
change. This does not relax core manifest, ownership, exact-slot or launch validation.

**Deployment limitation:** the current Harness interpreter cannot import `litetui`
(`find_spec('litetui')` is `None`), so changed-digest notices are **inert there**. The real
missing-helper case is tested; positive comparison tests substitute the owner-helper boundary
and do not prove actual LiteTUI effective composition or a watched restart. No source-path
injection, package installation or runtime endpoint is added. Exact unmanaged seed/migration
transport remains separate work; this observation cannot authorize or manufacture a profile.

## Known limits

- Restore refuses to launch a seat that another operation on the same checkpoint started and never
  reported on (`prior-attempt-unknown`); reconcile it first. A seat's `live` needs the registry row
  and a hosting PTY to agree, otherwise it is `unknown` and is not reattached.
- Bare-shell cleanup closes only a leaf that a SAVED seat slot names (where LiteSuite put a
  placeholder back), whose PTY root process is a shell with no child and is not a seat. A leaf no saved
  slot names is never closed, and the PTY's cwd is not consulted. Anything unreadable stays.
- A mid-work (`held`) seat keeps its handoff but is never compacted: `held-not-compacted`.
- Compaction needs `checkpoint.compact_command` for Claude seats; none ships.
- Active-owner layout flush requires its project map loaded. Hidden-owner flush requires one
  hydrated thread-to-project binding plus an exact persisted workspace/project record and existing
  thread layout. The same native write queue merges only captured owned terminal panes, preserving
  unseen panes and other threads. Missing/corrupt/ambiguous ownership stays unsupported; it never
  replaces an unknown map with an empty one. Renderer changes during the write invalidate the ACK.
  Coverage is per recorded exact slot, not a claim that arbitrary non-canvas surfaces were saved.
- An orchestrator caller of any `spawn --split` no longer falls into its own pane by default.
- A launch that never registered can read as dead: if `spawn` ran, no registry row was written, an old
  row for the seat remains, and its process is gone, the seat reads `dead` once the operation is idle
  for longer than spawn timeout plus verify timeout (about 330 s), and a later restore may launch it
  again. The first attempt's seat could in principle still be starting; the wait is the only guard.
- Failed, blocked and unknown seats retain their recorded placeholder sessions through cleanup,
  including when their registry row is absent. Unrelated eligible shell cleanup is unchanged.
- Saved `slot.leaf` and placeholder session ids must survive a LiteSuite restart; no watched
  restart has proven this yet. Missing or changed reservations block exact restore instead of
  silently relocating the seat.
- The shell summary (`N bare shells found, M matched a saved leaf, K closed`) is a journal line on a real
  run (stage `shell-summary`) and an informational `shell-summary` blocker on a dry run; the Result
  contract has no note field, so a final result cannot carry it without turning `complete` into `incomplete`.
- Unknown Claude execution blocks with `model-mismatch`, even if the default's base model matches:
  a transcript does not prove the saved context suffix. The legacy explicit `--allow-default-model`
  exception remains visible as `model-defaulted` and cannot yield a complete result. Native Claude
  effort preservation remains unverified; LiteTUI thinking is asserted against its owned home.
- A registry model that is real but stale (the user switched models with `/model` after registering) is resumed
  on the registry model, not on what the transcript last ran.
- Cancel is not checked while a compact command is executing (up to `compact_timeout`); it is checked in the
  poll that follows, and between seats. A compactor that never returns counts as a timeout and, three in a
  row, abandons the compaction phase.
