# Named-agent COPY migration (T0308)

This is **COPY-only**, not runtime cutover or activation. Reviewed code must be merged under the orchestrator authority **before** any real invocation. The operator verifies actual merged review independently; a receipt records authority, exact merged SHA, review evidence and approved plan digest as audit evidence, not a magic approval grant.

## Explicit operator boundary

Runtime storage remains opt-in with `--agent NAME`; no feature environment variable is added. Operator migration: `python -m liteharness.agent_migration_cli --copy-agent NAME --root ABSOLUTE_DATA_ROOT --registry-root ABSOLUTE_REGISTRY_ROOT`. This is **dry-run by default**, prints the read-only plan and never invokes COPY. `--apply` additionally requires both `--manifest APPROVED_PLAN_JSON --merge-receipt MERGED_REVIEW_RECEIPT_JSON`. Partial/invalid combinations refuse before mutation. the orchestrator will invoke reviewed merged code after merge, not this implementation session. No command here activates a seat or terminates a process.

## Named selection and authority

Policy **`named-index-conversation-only/v2`** uses the authoritative `~/.liteharness/names.json` name → canonical `agent_id` → singular `convo_id` association. Each named agent selects **exactly one indexed conversation**, not a historical sibling set. There is no list-field inference, latest-event selection or filesystem-mtime selection. Settings and memory come only from that selected source, with required execution fields preserved/normalized without discarding nested preferences. The selected transcript must still have valid identity metadata and a valid last persisted event timestamp; no defaults or guessed identity are supplied.

A missing/unsafe selected source, selected identity mismatch, shared casefold name/agent/conversation association (including partial malformed index rows), known historical name/ID collision, selected reparse path or selected mutation defers that named agent. Unknown or unselected legacy siblings do **not** globally block an otherwise valid selected agent and are never copied or assigned guessed ownership. The manifest explicitly reports each unselected/unknown folder as **“left in legacy, not migrated”** with reasons. It also reports a separate `named-deferral` record for every named agent without a safe selected source, including missing folders; its `indexed_convo_id` is only the catalog claim, not proof of ownership. Archive-entry and named-deferral counts are separate.

Every selected legacy companion byte and empty directory is copied under its one conversation; selected agent memory is an additional copy. Differing memory in an unselected historical archive is left there, never joined or substituted.

## Eligibility — superseding the orchestrator exact-PTY rule

The authoritative registry must be inspectable. An indexed named seat with an explicit **offline/retired** record (including validated retired-registry identity), or a **proven absent** matching record, qualifies only when the authenticated bridge has **no exact live PTY** for that canonical agent UUID. Live/unknown registry semantics, unreadable/malformed records or unavailable registry defer.

Use the existing authenticated bridge client, readonly `GET /pty/list`. Validate response `{sessions: [...]}`, row shape, unique PTY IDs and PID metadata; malformed/unavailable response **defers**, never means no PTY. Match only `harnessAgentId` exact canonical UUID. Do not substitute names, labels, pane/leaf IDs, PID ancestry, or other agent-ID aliases. Rows without `harnessAgentId` are counted and disclosed: “no exact live PTY” means no exact bridge identity match, **not absence of all OS writers**. The policy intentionally relies on bridge identity authority and the source-hash safety rule below.

**Superseded requirements removed:** broad OS-process enumeration, module provenance, executable attribution and file-handle writer proofs are NOT COPY eligibility gates. There is no `no_live_pid` claim, blanket zero-eligibility process gate, launcher gate or maintenance interval. Historical process-proof fixtures/logs do not certify this new rule.

## Full selected-source safety

Hash the full **single indexed conversation** BEFORE; COPY; hash that full selected source AFTER. All selected files and empty directories are covered. Revalidate the current name-index pointer, selected identity/settings/inventory and shared/historical collision guards before and after exclusive rename. Index rewiring or a new known historical name/ID collision defers; a new unknown/unselected sibling does not join the copy or veto it. Receipt includes the policy and exact `index_selection`.

Mark **COPIED** only when BEFORE == AFTER AND destination == source (all bytes, filenames and empty directories). Otherwise DEFER/retry later, never promote a changed copy. Return every copied file’s SHA256/bytes and preserved source inventory; digest integrity alone does not establish current policy selection.

**Caveat:** late writes after final hash may stale copy; no lost source because legacy source is never modified/moved/deleted. Hash observations are not atomic proof that another writer cannot start. COPY-only retention is the safety basis, not a new no-relaunch boundary.

## Inactive publication and recovery

Retain `.agent.initializing` in staging **and** destination, even after verified COPIED. Runtime cannot select that copied seat; **COPY never activates**. Ready catalogs strictly parse inactive metadata and reserve its name/ID for collisions before returning only ready agents. Valid unrelated inactive siblings do not disable ready seats; unreadable inactive metadata and ready/inactive collisions still fail closed. Marker removal/runtime selection are separate explicit activation authority outside this tool.

An inactive audit-intent receipt is not a COPIED claim; only the fully verified result is. Exclusive Windows rename never replaces a destination. Retry verifies exact receipts and whole-tree inventory, including root memory/extra files/directories. Interrupted or changed copies stay blocked. Regenerate a changed plan and defer operator reconciliation of retained inactive copies, never silently delete/overwrite them. Sources are never moved/deleted/chmodded/repaired.

Archived legacy conversations continue existing legacy resume and may write their own legacy folder. “Read-only archive” constrains migration, not legacy use. No fork/new-conversation archive-resume mechanism is added.

## Validation limits

COPY tests use temporary fixture stores only. Passing tests do not establish actual merge approval, agent retirement, real copy eligibility, runtime activation, or the user acceptance. Final exact commits still require the same independent reviewer; remaining fresh named creation, agent-first picker, OSS spawn/resume authority and task-consumer audit continue as explicitly scoped T0332 after T0308 merge.
