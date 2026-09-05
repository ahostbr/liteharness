# T363: startup and resume registration provenance

The same-process T244 guard protected the first UUID it found, including an
ordinary startup UUID. A subsequent genuine resume could therefore be forced
back onto that startup identity indefinitely.

## Measured incident and replay

The local identity log for 2026-09-05 records:

| UTC time | Environment and payload | Resolved | Incoming id discarded |
|---|---|---|---|
| 15:09:12.549 | Both startup id `1fe83043` | `1fe83043` | None |
| 15:09:23.624 | Both resumed id `2cbc7137` | `1fe83043` | `2cbc7137` |

These are the same hook's inputs at each timestamp, not a later shell's environment.
There was no environment/payload disagreement in this incident. `adopted_from`
names the incoming identity that lost; it does not name the winning identity.
Both events use the same context/config/registration path. Different existing
registry state made the second event take the incorrect adoption branch.

A temporary-root replay reproduced the failure before edits: startup A registered;
resume B entered adoption as B and returned A. The new reverse-order regression
failed on the missing B record. Existing T244 tests seeded a deliberate takeover
first and did not cover an ordinary startup predecessor.

## Change

- Presence records carry `registration_source`: startup, resume, or takeover.
- Explicit CLI registrations and explicit environment overrides are takeover
  choices. Hook refresh preserves that provenance.
- Only a recorded Claude takeover can override a same-process newcomer.
  Unknown legacy records are not guessed to be intentional takeovers.
- A normal resume registers its actual resolved id. Existing roster timestamp
  supersession handles the older ordinary record; no mail, names, or old records
  are deleted or moved, and watchers are not stopped or rebound here.
- Adoption is Claude-specific. Codex desktop tasks can share a backend PID,
  which must not become a shared identity.
- The identity banner names the actual resolution source and whether resume
  supersedes a startup record or an explicit takeover was protected. Identity
  telemetry also records the hook source.

Global environment/payload precedence, check_inbox, and delivery are unchanged.

## Validation and limits

With `PYTHONUTF8=1` inherited:

- New tests before production changes: **6 failed** (including the behavioral
  reverse-order failure and new provenance/banner assertions).
- New tests plus original T244 controls after the fix: **12 passed**.
- Full suite: **323 passed, 20 subtests, 0 failed** in 54.63 seconds; base was
  317/20/0 and the six new tests account for the increase.
- Coverage includes normal startup, real resume and repeated registration,
  explicit CLI and environment takeovers, banner accuracy, legacy unknown
  provenance, and another CLI sharing a backend PID.

This is isolated test/replay evidence, not a live restart of Sentinel. Deployment
and a real resumed-seat acceptance remain separate. Existing intentional overrides
without provenance should be explicitly registered once after cutover; do not
infer takeover history merely from a name or UUID. Shared-runtime cutover is T376.

Zero initial announcement is intentionally out of scope. At first launch both
observed inputs identified A; a hook cannot foresee a later human choice to resume B.
Delaying all first-start registration would be a separate startup-policy change.
