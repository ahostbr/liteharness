# Restart checkpoint contract (v1, card A)

This is a library boundary, **not a save/restore command**. Legacy `sessions
save/restore/list` and terminal snapshot schema 2 are unchanged. There is no
launcher, process enumeration, transport, UI, hold enforcement or automatic
coordinator bootstrap in this card. No public CLI/MCP availability is claimed.

## Modules and owners

- `checkpoint_contract`: closed JSON/dataclass types. `decode(Type, payload)`
  rejects missing/extra fields, wrong scalar types, unknown enum/schema values and
  contradictory identities. `wire(instance)` validates directly constructed
  dataclasses too. Dataclasses alone are not a trust boundary; always decode/wire.
- `checkpoint_census.census`: read-only reconciliation of an **explicit selection**
  against caller-authorized roots, names, registry, owned homes and transcripts.
  It does not select a policy, infer liveness from a PID, repair indexes, write
  files, or turn dormant homes into expected seats.
- `checkpoint_store.CheckpointStore`: atomic publication and operation journal
  primitives under an exclusive writer lock. It has no background worker or
  state-machine policy. Merely constructing/reading a store creates nothing.

The coordinator (cards C/D) freezes the roster, authorizes requests, owns live
state/reconciliation and validates runtime receipts against current reality.
Runtime owners (B/G) produce evidence. Layout owner E produces layout revision
receipts. Host I binds the authenticated caller to a scoped recovery authority;
**a model-provided actor/leaf/from ID is never authorization**. The `actor_id`
argument on `submit` is only an audit/binding field supplied by that trusted
caller. This module does not grant authority or prove the actor is authentic.

## Frozen denominator and authority

A `Manifest` has both:

1. `expected: list[ExpectedSeat]`: name, stable agent UUID, selection provenance.
   These identities remain present even when their detailed contract is blocked.
2. `seats: list[Seat]`: resolved conversation/CLI/execution/root/cwd/role/cognitive/
   parent/owner-profile/disposition/recovery-action/source-revision/slot contracts.

Every unresolved expected seat requires a seat-specific blocker. It cannot
vanish from counts. Expected identities and exclusions are disjoint. Duplicate
agent names, agent IDs and resolved conversation IDs refuse publication. A
coordinator record points at an expected seat, specifies `external-manual-return`
and carries its read-first path/hash. It is not permission to launch that seat.
A `manual-return` receipt is available for the later restore coordinator to
validate against the current runtime; a saved read-first pointer is not proof
that manual return has happened.

Census callers supply selected `Seat` candidates and explicit authorized data
and working-directory roots; there is no machine-specific fallback. Malformed
stable identities/duplicate selections refuse the request. Other evidence
failures yield `CensusRow(expected, seat=None, blockers=...)`. An incomplete
manifest uses all row `expected` values, only non-null `seat` values, and every
blocker. Unsupported CLI evidence therefore remains visible without weakening
the supported-CLI schema. `Census.expected_count` is never the resolved count.

All supplied authorized roots are inspected for owned identity ambiguity; dormant
homes are read, never selected or woken. A corrupt authorized catalog blocks owned
resolution rather than claiming uniqueness. Owned homes use the existing
`AgentStore` contract, including initialization,
link/reparse, schema, identity and duplicate ownership checks. Home execution
wins over stale execution candidates. Existing role normalization recognizes
legacy tier aliases; registry owner evidence cannot silently become a builder.
An owner requires its title/memory profile, with an authorized existing memory
directory. Native execution must agree with its name index. The exact selected conversation must have
an owned transcript and matching `settings.json` seat ID. Names, if present,
must not conflict; absence of a names row does not defeat owned-home authority.
A deliberately offline owned seat may lack registry presence if its explicit
launch context is complete. A selected-current seat without registry evidence
blocks instead of disappearing. Registry rows corroborate context, **not life**.

Native Claude/Codex/Copilot census requires the named original cwd/conversation
and an explicit registry transcript path inside the selected authorized root.
There is no provider-path guessing or assumption that an agent ID is a native
conversation ID. This is census support, not a claim that all three native CLIs
already have governed restore launchers. The later dispatcher must refuse any
unsupported launch path and verify current native identity/execution.

No path is resolved through a symlink/junction/reparse point, including ancestors.
Lexical traversal and unauthorized roots are rejected. These checks are not a
sandbox against a malicious process swapping directories between check and I/O;
writers require trusted local roots and the coordinator's ownership protections.
Census snapshots can become stale immediately; later preflight must revalidate.

## Decisions: four recommendations, zero implicit approvals

`Manifest.decisions` requires all four `Decision(value, basis)` fields. Nothing
fills them automatically. `RECOMMENDED_DECISIONS` is only a named label table:

| Field | Named plan recommendation |
| --- | --- |
| `roster` | `selected-current-plus-explicit-offline` |
| `unsafe_stop` | `incomplete-with-blockers` |
| `backup_scope` | `same-machine-continuity` |
| `placement` | `matching-slot-else-offer-pane` |

`basis` is `unconfirmed-recommendation`, `explicit-selection`, or
`human-confirmed`. Tests explicitly construct unconfirmed recommendations.
Alternative values remain data for the later policy owner to interpret/refuse;
this module never treats the recommendation labels as executed policy. Confirmed
idle/waiting/held dispositions and same-seat handoff/compaction receipts are
separate from those four unconfirmed choices.

## Receipt and completeness contract

Every receipt carries checkpoint and seat UUIDs, kind, source revision,
reference/hash, durability, assurance and timezone-qualified observation time.
`native`, `cooperative` and `unsupported` assurances stay distinct. Cooperative
evidence does not become enforceable hold. Runtime adapters must establish what
the reference/hash and durability assertion actually prove; schema validation
alone cannot prove a flush happened or that a process stayed idle afterward.

Complete publication requires:

- no unresolved blocker or unknown/blocked obligation;
- every expected row resolved;
- matching checkpoint/seat/revision receipts;
- durable, supported handoff, same-seat compaction and final boundary receipts
  ordered handoff <= compaction <= final boundary by observation time
  for every seat, including the externally restored coordinator;
- durable matching layout receipt for every seat with a saved slot.

The final checkpoint source revision is the receipt join key; layout uses its
own saved revision. Adapters must invalidate/reissue evidence when source state
changes. Obligations record owner references/revisions and captured state for
resources, jobs, schedules, flags, drafts, approvals and repositories; they are
not executable recovery scripts. No transcripts, environment dumps, credentials,
lease TTLs, process IDs or shell command strings are restored from this schema.
The caller must still avoid putting secrets in free-text fields/references.

`validate_runtime_versions(manifest, supported)` uses an explicit exact-version
compatibility table. There is no implicit upgrade/downgrade. CLI and schema
versions fail closed. `legacy_snapshot_summary` classifies v2 as
`legacy-partial`, never fabricates a complete v1 checkpoint from process rows.

## Shared request/result wire shape

`Request` fields (all must appear; absent optional values are JSON `null`):

- `schema_version: 1`, `action: save|restore|status|cancel|retry`;
- `request_id`: caller-generated canonical UUID for every mutation;
- `checkpoint_id`: mandatory explicit UUID for restore, optional status selector;
- `operation_id`: required for cancel/retry, optional status selector;
- `seat_ids`: nonempty unique UUID list for retry only;
- `dry_run`: boolean; only save/restore accept true.

Status can list when no selector is supplied or query by exactly one of request,
operation or checkpoint ID. A dry-run request is validated but must never be
submitted to the journal. Save policy/roster inputs are supplied separately by
the coordinator's configuration/selection seam; the request does not silently
choose the four recommendations. Cancel/retry are durable command submissions;
the engine is responsible for reconciling their target operation and rows.

`Result` includes operation/checkpoint IDs, operation state, full expected seat
rows, counts, blockers and allowed next actions. `accepted` is not `complete`.
Each row has evidence/errors and its own allowed actions. `ready` counts both
ready-idle and ready-held; `held` is its subset. Failed includes blocked rows.
Other preparing/starting/saved states still contribute to expected. Counts must
match rows exactly. Unknown rows may offer reconciliation, not automatic retry.
Operation-result transitions and the readiness meaning belong to C/D, not this
storage primitive.

## Publication and crash behavior

Storage is caller-rooted at `<harnessRoot>/sessions/checkpoints/`:

- `<checkpoint UUID>/manifest.json` — incomplete can be replaced while gathering
  evidence; expected/excluded roster, coordinator and creation/host/boot identity
  stay frozen across retries; complete content is immutable;
- `latest-complete.json` — checkpoint ID plus SHA-256 of canonical manifest JSON;
- `operations/<request UUID>.json` — operation ID, bound request/actor, ordered events;
- `.writer-lock` — exclusive writer reservation; no automatic stale deletion.

Publication writes/flushed/fsyncs a temporary file, atomically replaces its
same-directory target, and synchronizes the containing directory where supported.
Only a validated complete manifest advances the pointer, after the manifest is
published. The pointer is monotonic by checkpoint `created_at`; older/tied distinct
checkpoints remain available by explicit ID but do not displace it. Republishing
an older immutable checkpoint cannot regress latest. The coordinator must assign
meaningful creation times and can always restore an explicitly named checkpoint. Previous complete checkpoint directories are never deleted. Failure
before replace leaves the previous target intact; failure between manifest and
pointer can leave an explicitly readable orphan complete manifest. If a call
fails after a rename, outcome is uncertain: read and reconcile, do not assume
nothing was persisted. Digest validation detects a pointer whose content changed.
No retention deletion, transcript copy, Git action or restart is performed.

The lock serializes cooperating writers; a crash can leave it behind and must be
reconciled by its owning coordinator/operator, not reaped by a timer. A locked
store refuses promptly. Filesystem errors propagate (no success after partial
write). On Windows, Python has no portable directory `FlushFileBuffers` here:
file fsync and same-volume replace are tested, **power-loss/OS-reboot durability
is not claimed**. A LiteSuite restart end-to-end proof remains card H's job.

`submit(request, actor_id=...)` persists before returning. Reusing a request UUID
with identical payload and actor returns the original operation; changed payload
or actor refuses. If the response is lost, look up the same request ID. The
journal file is itself the request mapping, so there is no second index to tear.
`operation(operation_id)` searches the small retained journal; indexed status is
not a new storage authority. `append(..., expected_sequence=n)` is a compare-and-
swap whole-document replace; lost/duplicate append responses require reading the
journal before retry. No torn tail is treated as committed. Events are evidence,
not side effects: a recorded launch intent alone is never a successful launch.

## Validation boundary

Run the new three focused pytest files plus existing `test_session_manager.py`
and `test_resume_by_name.py`. Fault injection covers partial writes, ENOSPC at
fsync, manifest/pointer replace failures, journal interruption, stale sequence,
request conflict and orphan publication. Link/reparse tests simulate `lstat`
metadata; they create no machine links and are not an end-to-end junction test.
No full suite, production fleet probe, real restart, provider spend, or installed
MCP smoke test belongs to this card. Required merge review and human-look gates
remain with the leader.
