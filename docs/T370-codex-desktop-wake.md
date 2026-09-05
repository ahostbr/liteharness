# T370: attached Codex Desktop inbox wake

The attached stdout watcher introduced in 1380f18 archived mail after printing it,
without starting a turn in the desktop task. The earlier desktop window submission
path had been removed. Window discovery already accepted both Codex.exe and ChatGPT.exe;
the executable rename was not this regression's cause.

The replacement retains one attached Python process and the existing per-agent OS lock.
Desktop mode uses the app-tools pipe inherited from the calling task and discovers the
native read_thread/send_message_to_thread tools. It verifies the exact task UUID and uses
a real originating turn id, refreshed from read_thread. It does not start another core,
select a foreground window, use the clipboard, or change the task's model/settings.

Directed mail is moved into a durable per-agent spool outside the shared inbox sweeper.
App acceptance produces awaiting_ack; the message moves to done only after the addressed
task runs the receipt acknowledgement. Failed preflight and proven pre-write connection
failures retain/retry mail. An uncertain submission or crash during submission retains it
without automatic resend, preventing duplicate turns. Broadcasts use per-recipient copies;
other global consumers can still race broadcasts, so required delivery should be directed.

The hook change is restricted to Codex runtime plus a live native-watcher OS lock.
Claude hook behavior and the generic watch_inbox/orphan sweep are unchanged.

## Validation

- `python -X utf8 -m pytest tests/test_codex_desktop_delivery.py tests/test_codex_stdout_delivery.py tests/test_codex_hooks.py tests/test_codex_headed_detection.py tests/test_codex_hook_install.py -q`: **23 passed, 3 subtests**.
- Python compileall and git diff --check passed.
- Tests cover real framed pipe traffic, split frames, exact target, duplicate watcher
  refusal, hook exclusion, unchanged Claude delivery, recipient-only acknowledgement,
  retained unavailable/uncertain submissions, restart deduplication, nested message body,
  broadcasts and installed adapter presence.
- An initial live preflight rejected empty discovery params. No mail was lost: it stayed
  pending. Corrected to the actual `threadStartKind: default` schema, strengthened the
  fixture and passed live preflight before the acceptance test.
- Active native self-message probe succeeded and displayed the app's native message header.
  This was explicitly excluded from idle acceptance.
- **Idle acceptance passed** on task 01a071fa-8f65-7ac3-93a6-8f4566d7b777. Setup turn ended
  2026-09-05 18:28:54 UTC; a CLI-only nonce started a NEW turn at 18:29:29 UTC, a 35-second
  idle gap. It appears in that task's own rollout at line 1767 as a native
  send_message_to_thread function_call_output, not a watcher stdout poll. The new turn
  acknowledged receipt at 18:29:40 UTC. Recipient reported no intervening human input or
  manual stdout polling. Metadata witness: [evidence/t370-idle-wake.json](evidence/t370-idle-wake.json).
- The acknowledgement reply itself reached the sender as a native task input and was
  acknowledged. The original nonce envelope was verified in the acknowledged ledger.

## Deployment and limits

Use the updated manual-start skill. Get the real current turn id with the app read_thread
tool, stop only the task's previous watcher, and start the replacement attached with
`--delivery desktop-turn --turn-id <REAL-TURN-ID>`. Keep the returned tool session id.
The main app must remain running. After app restart/process exit, register/re-arm from
the current task so the new pipe environment is inherited. A suspended/offline host
cannot receive a turn. The app-private pipe schema may change; failures retain messages.

The passing candidates run from the isolated T370 worktree. Repository integration and
global deployment are separate: the machine's editable package points at another shared
checkout with unmerged work. Sentinel tracks that deployment decision as T376. Do not
overwrite that checkout or silently repoint global Python. After integration and the
runtime decision, run the canonical installer to synchronize all Codex skill aliases,
then restart each attached watcher in its own task. This report does not claim a fleet
rollout or app-restart acceptance test.
