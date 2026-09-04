---
name: liteharness-manual-start
description: Register Codex sessions and manage one attached LiteHarness stdout inbox watcher, including desktop tool terminals.
---

# LiteHarness for Codex

Canonical source: the installed `liteharness.cli_scripts.codex` package. Repair source in
`liteharness-oss`, then run `python -m liteharness.cli update-scripts --cli codex-cli`.

1. Register from the task directory:

   `python "$env:USERPROFILE\.codex\skills\liteharness-manual-start\scripts\manual_liteharness.py" start --check-now`

   Startup registers only. It never spawns, replaces or stops a watcher. Keep the current
   thread UUID; do not rotate it on compaction. Supply the actual model in registration.

2. Start exactly one consumer in an attached background tool terminal:

   `python -u "$env:USERPROFILE\.codex\skills\liteharness-manual-start\scripts\manual_liteharness.py" watch`

   Retain the returned tool session ID. Read that session's stdout after major tool use and
   whenever waiting for a reply. A terminal can capture stdout without exposing a native window.
   Re-arm it if it exits. The per-agent OS lock refuses a duplicate managed watcher.

3. Use `codex-monitor status` for process/heartbeat health. It does not prove delivery;
   prove delivery by reading an actual addressed message from the attached session.
   Stdout delivery does not itself wake an idle desktop task. Never describe it as push
   delivery unless the host independently demonstrates that behavior.

4. Use `discover`, then `send <full-target-UUID> "message"`. Resolve Sentinel from fresh
   presence, not remembered IDs. Read the watcher session for replies. `check` refuses to
   become a second consumer while the managed stdout watcher is alive; without a watcher it
   is a one-shot fallback. `python -m liteharness.cli inbox 5 --agent <UUID>` is read-only
   recovery for already-delivered messages.

5. Migration: if status reports a legacy detached monitor, explicitly run `codex-monitor
   stop` once for this agent, then launch `watch` in an attached tool terminal. `codex-monitor
   start` and `restart` now run attached, so invoke them through the background tool facility.
   Stop never targets other agent IDs. Do not run both raw hooks watch and the managed watcher.

There is no UI target, clipboard paste, SendKeys, desktop injection, detached pythonw,
standing manual polling loop, or automatic notify-hook consumer in this path. Historical
memory runbooks requiring `Target: mode=...` describe the retired delivery path.
