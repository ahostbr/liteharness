# T377: Claude hook installation and the third inbox event

Measured 2026-09-05 against main commit `52fb0ca`. No live settings or hook wiring
were changed. The machine's installed editable package is a separate checkout;
this audit uses the assigned isolated worktree. Deployment remains T376.

## Findings

The installed settings run `liteharness.hooks check` on SessionStart,
PostToolUse and UserPromptSubmit. The shipped CLI config supplies the first two.
That difference is a preserved customization, not evidence that install failed.

`_install_claude_hooks` in `liteharness/cli.py:347-356` reads the shipped config
and calls `_merge_claude_hooks` (`:112`). The merge is selective reconciliation:

- `:147-168`: prune LiteHarness actions absent from the current dispatcher.
- `:200-240`: heal the known wrong-event deregister wiring, narrowly by command.
- `:242-272`: add absent LiteHarness commands per event; deduplicate by command
  within that event, preserving valid existing custom placements.
- `:274-282`: write the resulting settings with a trailing newline.

The comments at `:186-199` explicitly describe why an earlier broad reconciliation
was rejected: it removed the user's PreCompact/PostCompact registration hooks and
UserPromptSubmit inbox check. The existing test
`test_our_OWN_commands_on_events_the_config_does_not_ship_are_left_alone` in
`tests/test_deregister_is_session_scoped.py` protects those additions. The code
therefore already implements the required preserve-user-hooks policy.

Command-path correction: this hook setup is invoked by **`liteharness init`**
through `_INSTALLERS` (`cli.py:648-652`). The distinct **`liteharness install`**
command (`cli.py:3889`) installs the skills/agents catalog through
`installers.install_cli`; its adapter table does not include Claude. The merge
measurements above apply to the hook installer, not the catalog command.

## Provenance

Available oss main history contains three revisions each of
`liteharness/hooks_configs/claude_hooks.json` and
`liteharness/catalog/hooks/hooks.json`. Enumerating the inbox-check event mapping
in every available revision found no UserPromptSubmit/check entry.

The inspected plugin-cache versions 1.0.14, 1.0.15 and 1.0.16, plus LiteSuite's
vendored `resources/liteharness-plugin/hooks/hooks.json`, use UserPromptSubmit
for **memory-nudge**, not inbox check. LiteSuite commit `1d2f8632` introduces that
memory-nudge wiring. An event name alone cannot establish the command it runs.

Local dotclaude settings history establishes:

| Revision | Date | Inbox-check events |
|---|---|---|
| `bcdb17d` | 2026-04-15 | SessionStart, UserPromptSubmit |
| `740f639` | 2026-04-15 | SessionStart, UserPromptSubmit, PostToolUse |
| `d193e36` | 2026-05-22 | Same three |
| Current inspected settings | 2026-09-05 | Same three |

This supports a longstanding local customization. It does not identify the
original human or agent who first added it, or rule out unavailable older
distribution history. No inference of original intent is made from commit age.

## What the third event adds

It can supply inbox context at the start of a new user turn, before a tool runs,
including a response that makes no tool calls. It is not the only delivery path
for an unwatched seat: SessionStart and PostToolUse also call check_inbox.

At this base, T372's `_a_live_watcher_is_attached`/`check_inbox` makes hook delivery
defer to a live, fresh watcher; all event bindings share that logic. With no
working watcher, hook delivery resumes. Existing throttling still applies.
UserPromptSubmit therefore remains a useful optional early-delivery trigger;
it is not an idle wake mechanism by itself.

## Executed installer probe

Ran the real `_merge_claude_hooks` against a fresh temporary settings file and
a temporary copy of the inspected settings, then installed each a second time:

| Condition | Check events after install | Second install |
|---|---|---|
| Fresh | SessionStart, PostToolUse | Byte-identical |
| Existing three-event settings | All three retained | Byte-identical |

UserPromptSubmit matcher/hook contents were unchanged; PreCompact and PostCompact
were unchanged. The live settings file's SHA-256 was identical before and after.
The probe operated only on temporary copies and exported no settings contents.

The fresh-file probe had an existing parent directory. A later outer-installer
probe with no `.claude` directory exposed a different defect: hook writing fails
before the status-line installer creates the directory, so the first setup call
returns false and leaves only a status line. This does not invalidate the
preservation result for existing settings, but it limits the fresh-install claim.
The defect was reported separately; this documentation commit does not fix it.

## Recommendation

Document the current contract as **two shipped defaults plus preserved valid
user overrides**. Installer and shipped config already agree on that contract.
Do not silently strip the third hook or make all users inherit this machine's
customization. Adding UserPromptSubmit/check to the defaults is a separate
behavior decision for Ryan; this audit does not make it on his behalf.

No production code change is justified by the observed event-count difference.
The existing targeted preservation/idempotence tests should remain the gate.

## Validation

The first full baseline invocation with only `python -X utf8` yielded
313 passed / 20 subtests / 2 failed. Both failures were Windows subprocess
decoding errors: the interpreter flag does not enable UTF-8 in child interpreters.
An inherited `PYTHONUTF8=1` rerun is the comparable gate used by the leader.
Comparable run: `$env:PYTHONUTF8='1'; python -m pytest tests/ -q` produced
**315 passed, 20 subtests passed, 0 failed** in 34.05 seconds. This matches the
leader's baseline counts. The audit adds documentation only; no test or runtime
behavior was changed to obtain that result.
