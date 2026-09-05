# T130: distinguish watcher activity from agent activity

Measured against oss main `c826032`. No live fleet processes were stopped or
re-registered for this task. Shared-runtime deployment remains T376.

## Reproductions

An isolated record with an OS-verified dead owner PID and a live watcher PID
produced different answers: the roster/name liveness helper returned false and
the stale-agent purge removed the row, but watcher deferral returned true. The
next watcher heartbeat recreated the cached dead-owner row and deferral returned
true again. The card's initial premise that all consumers called it alive was
incorrect; owner-PID checks already protected the purge and roster.

The second reproduction exposed the shared-clock defect directly: a live agent
with a stale live watcher initially did not defer. An **agent-only heartbeat**
updated shared last_seen and made deferral true, with **no watcher heartbeat**.
The watcher process was alive in this case; no claim that it was dead is needed.

The first regression set was **3 failed, 1 passed**. The passing control preserves
the stale-agent purge's veto for a known live idle owner.

## Clock contract

- `watcher_last_seen` advances only from watcher heartbeats.
- `agent_last_seen` advances from registration, turns, CwdChanged, and CLI/manual
  registration/activity. Hook activity is recorded even when delivery defers.
- `last_seen` remains compatibility recency: the later actual instant of the two
  clocks. It is not independent proof of either actor's activity.
- Presence merges recompute that compatibility maximum. Registration retains
  existing watcher clock/PID metadata instead of erasing it.
- Deferral requires a live watcher, a known live owner, and a fresh watcher clock.
  A missing/invalid clock cannot borrow freshness from an agent turn.
- A known-dead owner cannot be recreated by an orphan watcher. Positive discovery
  of a live owning CLI still permits legitimate recovery; the live-owner cache
  recovery control remains green.
- Existing owner-PID checks in purge and roster/name readers are preserved,
  including the purge's live-owner veto.

The old Codex lock-only hook bypass also used the shared rule after this change:
an OS lock proves process ownership, not freshness. The attached watcher's own
duplicate-process lock is unchanged. The check_inbox delivery/archival body is
unchanged; only activity observation and the deferral guard changed.

## Migration

Rows from old/pinned watchers have no watcher_last_seen. The hook treats freshness
as unknown and follows its existing delivery path rather than withholding mail.
The identity log records missing-watcher-last-seen as a metadata-only diagnostic.
The Codex status reader reports **freshness unverified** instead of using agent
activity as evidence about a watcher.

This compatibility behavior is not a new transport acceptance claim: the legacy
hook delivery path still depends on host rendering. Restart pinned watchers onto
the new producer as part of T376's controlled cutover; no current watcher was
silently migrated here.

## Validation

`PYTHONUTF8=1` was inherited for all comparable test runs.

- Final full suite: **333 passed, 20 subtests, 0 failed** in 60.12 seconds.
  Base was 323/20/0; ten new tests account for the increase.
- Both original reproductions fail before the fix. Added controls cover cached
  dead-owner recovery, valid live-owner recovery, independent writer clocks,
  UTC-offset-aware compatibility ordering, migration diagnostics, status output,
  and native-lock migration fallback through the actual hook delivery path.
- Existing T372 fixtures now supply the watcher-specific clock and a live owner.
  A presence test's assumed PID 12345 was replaced with its actual live owner PID;
  that test measures preservation, not acceptance of an orphan.
- Native pipe integration seeds the registration that precedes a real watcher,
  so the common freshness rule can verify its owner.

Ryan ordered FullBit to park after finishing this task. No successor task should
be dispatched until Ryan explicitly releases the seat.
