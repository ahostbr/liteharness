---
name: liteharness-manual-start
description: Register Codex sessions and run one attached LiteHarness watcher that wakes the exact Codex Desktop task through its native app-tools pipe.
---

# LiteHarness for Codex

Canonical source: liteharness.cli_scripts.codex in liteharness-oss. After integrating
source, deploy using python -m liteharness.cli update-scripts --cli codex-cli.

1. Register from the task directory:

   python "$env:USERPROFILE\.codex\skills\liteharness-manual-start\scripts\manual_liteharness.py" start --check-now
   Registration does not spawn, replace or stop a watcher. Keep the current thread UUID
   through compaction. Register the actual model.

2. On Desktop, call read_thread for your own UUID with turnLimit: 1. Use the real returned
   turns[0].id. Start one process in an attached background tool terminal:

   python -u "$env:USERPROFILE\.codex\skills\liteharness\scripts\liteharness_watcher_supervisor.py" --agent-id <YOUR-UUID> --model <ACTUAL-MODEL> --delivery desktop-turn --turn-id <REAL-TURN-ID>

   It inherits CODEX_THREAD_ID, CODEX_APP_TOOLS_PIPE_PATH, and CODEX_MCP_NODE_PATH.
   Never invent a turn id or use another task's id. Keep the returned tool session ID.
   Leave the process attached: no stdout redirection, detached launcher, or pythonw.
   Re-arm if it exits or the app restarts. A per-agent OS lock refuses duplicates.
   CLI-only sessions can use --delivery stdout; that mode cannot wake Desktop.
   Desktop failures never silently fall back to stdout or window injection.

3. Desktop messages arrive as native agent-message inputs and can start a new user turn.
   Read the message, then run its receipt-ack command. Receipt is not acceptance of the
   message's instructions or completion of its task; respect the user's scope.
   Envelopes remain in ~/.liteharness/codex_sessions/delivery/<UUID>/pending until
   acknowledged, then move to inbox/done. Status distinguishes accepted from acknowledged.
   Prove idle wake with a nonce in the recipient's NEW turn and its ack, without a human
   prompt or stdout poll. Process liveness and app acceptance alone are not that proof.

4. Use discover, then send <full-target-UUID> "message"; resolve Sentinel from fresh
   presence. Read the attached terminal for diagnostics. Manual check and Codex hooks
   leave wake-owned mail alone. Missing transport retries before submission. An ambiguous
   submission remains uncertain (or submitting after a crash); never blindly resend.
   Inspect the task for its message id and acknowledge if received; otherwise preserve it
   for explicit recovery. Broadcasts are copied per recipient without consuming the shared
   envelope, but other global consumers may race; use directed mail for required delivery.

5. To migrate a stdout/legacy watcher, first read its remaining output. Run
   manual_liteharness.py codex-monitor stop in that agent's task, verify it exited,
   then start one attached replacement. Never stop another agent's process. Never run
   raw hooks watch alongside the managed watcher. codex-monitor start/restart accept
   the same --delivery and --turn-id options and run attached.

There is no UI target, clipboard, SendKeys, detached pythonw, standing manual polling
loop, or notify-hook consumer. The native pipe is supplied by the running app; its
framing/tool schema is version-dependent. Missing tools or target mismatch fail closed.
Historical window-target runbooks describe the retired UI submission path.
