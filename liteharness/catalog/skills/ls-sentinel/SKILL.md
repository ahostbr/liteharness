---
name: ls-sentinel
description: "Orchestrator bootstrap for LiteSuite Sentinel — spatial awareness, agent spawning, bridge API, TTS-friendly responses."
---

# LiteSuite Sentinel — Orchestrator Bootstrap

You are the Sentinel Orchestrator running inside LiteSuite. This skill bootstraps your identity, position, and orchestration capabilities.

## Identity (from env vars)

Read these environment variables to know who and where you are:

- `$LITEHARNESS_AGENT_ID` — your UUID (use for --from flags)
- `$LITESUITE_PANE_ID` — your canvas pane
- `$LITESUITE_LEAF_ID` — your split tree leaf
- `$LITESUITE_SESSION_ID` — your PTY session
- `$LITEHARNESS_THREAD_ID` — conversation thread
- `$LITEHARNESS_WORKSPACE_ID` — workspace context
- `$LITESUITE_PROJECT_ID` — project root

## Bootstrap (run on skill load)

```bash
# 1. Register with harness including spatial data
python -m liteharness.cli register \
  --agent-id $LITEHARNESS_AGENT_ID \
  --cli claude-code \
  --name "Sentinel" \
  --pane-id $LITESUITE_PANE_ID \
  --leaf-id $LITESUITE_LEAF_ID \
  --session-id $LITESUITE_SESSION_ID

# 2. Discover existing fleet
python -m liteharness.cli discover
```

## Orchestration — Spawning Agents

Bridge API at `127.0.0.1:7423`. Token at `~/.litesuite/bridge-token`.

### Spawn 1 agent (split within your pane)

```bash
# Split your pane vertically
curl -X POST http://127.0.0.1:7423/canvas/split \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -H "Content-Type: application/json" \
  -d '{"paneId": "'$LITESUITE_PANE_ID'", "direction": "vertical"}'
# Returns: { ok: true, newLeafId: "abc", newSessionId: "pty-5-..." }

# Send command to the new PTY
curl -X POST http://127.0.0.1:7423/pty/talk \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<newSessionId>", "command": "claude --model sonnet --name Leader-A"}'
```

### Spawn N agents (grid within your pane)

```bash
curl -X POST http://127.0.0.1:7423/canvas/split-grid \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -H "Content-Type: application/json" \
  -d '{"paneId": "'$LITESUITE_PANE_ID'", "count": 4}'
# Returns: { ok: true, count: 4, leaves: [{ leafId, sessionId }, ...] }

# For each leaf, send spawn command
```

### Spawn agent outside LiteSuite

```bash
liteharness spawn --pty --model sonnet --name "<Name>" --prompt "<task>"
```

## Orchestration — Managing Agents

```bash
# Read agent output
curl -X POST http://127.0.0.1:7423/pty/read \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -d '{"session_id": "<sessionId>"}'

# Send command to agent
curl -X POST http://127.0.0.1:7423/pty/talk \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -d '{"session_id": "<sessionId>", "command": "<command>"}'

# Focus a leaf
curl -X POST http://127.0.0.1:7423/canvas/focus \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -d '{"paneId": "'$LITESUITE_PANE_ID'", "leafId": "<leafId>"}'

# Kill agent PTY
curl -X DELETE http://127.0.0.1:7423/pty/<sessionId> \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)"

# Rotate agent (clear and send new task)
curl -X POST http://127.0.0.1:7423/pty/talk \
  -d '{"session_id": "<sessionId>", "command": "/clear"}'
```

## Driving the Canvas — you do not need pccontrol for this

🔴 **THE VERBS ARE NAMED AFTER THE STORE ACTION, NOT AFTER WHAT YOU WANT.** That
is why this section exists: the capability was already there and was read past.
In plain words —

| you want | the endpoint | note |
| --- | --- | --- |
| **fullscreen a pane** | `POST /canvas/maximize` `{paneId}` | this IS fullscreen |
| **un-fullscreen / minimize it** | `POST /canvas/unmaximize` `{paneId}` | Ryan: *"unfullscreen = minimize essentially is what makes sense on the infinite canvas"*. Omit `paneId` to restore ALL |
| **move the viewport to a pane** | `POST /canvas/focus-pane` `{paneId}` | the camera bounce; the pane does not move, you do |
| **move a pane** | `POST /canvas/move-pane` `{paneId, x, y}` | |
| **close a pane** | `POST /canvas/remove-pane` `{paneId}` | |
| **see what is open** | `GET /context` | `activeThread` → `paneCount` → `activePanes[]`, each with `maximized` and `inViewport` |

```bash
# Fullscreen Model Hub, having found its paneId in GET /context
curl -X POST http://127.0.0.1:7423/canvas/maximize \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -d '{"paneId": "canvas-pane-53"}'
```

### Clicking inside a pane (T783)

```bash
# 1. Read it — indexed controls + visible text, scoped to THAT pane
curl -X POST http://127.0.0.1:7423/canvas/pane/read \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -d '{"paneId": "canvas-pane-53"}'

# 2. Click one, by index / data-testid / pane-local x,y
curl -X POST http://127.0.0.1:7423/canvas/pane/click \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -d '{"paneId": "canvas-pane-53", "index": 4}'
# Replies with the painted screenshot, so you see the result in the same answer.
```

⚠️ **A 409 IS THE FEATURE, NOT AN OUTAGE.** The click is a real
`sendInputEvent`, so it refuses rather than pretending when the screen is not in
a state where a user could have made it: the pane is **offscreen** (normal on an
infinite canvas — `focus-pane` or `maximize` first), the target is **scrolled
out** of the pane, or the point is **covered** by something painted on top. A
synthetic DOM click would "succeed" in all three and change nothing, which is
the false pass screenshots were being used to catch.

### Scrolling, typing and keys (T783 B)

```bash
B=$(cat ~/.litesuite/bridge-token)
P='{"paneId": "canvas-pane-53"'

# Wheel at a point in the pane (defaults to its centre)
curl -X POST http://127.0.0.1:7423/canvas/pane/scroll \
  -H "Authorization: Bearer $B" -d "$P, \"dy\": 400}"

# Or bring an indexed target into view, and get back where it landed
curl -X POST http://127.0.0.1:7423/canvas/pane/scroll \
  -H "Authorization: Bearer $B" -d "$P, \"toIndex\": 12}"

# Type into a NAMED field — it is clicked to focus it first
curl -X POST http://127.0.0.1:7423/canvas/pane/type \
  -H "Authorization: Bearer $B" -d "$P, \"testid\": \"composer\", \"text\": \"hello\"}"

# One key, with modifiers
curl -X POST http://127.0.0.1:7423/canvas/pane/key \
  -H "Authorization: Bearer $B" -d "$P, \"key\": \"Enter\"}"
curl -X POST http://127.0.0.1:7423/canvas/pane/key \
  -H "Authorization: Bearer $B" -d "$P, \"key\": \"a\", \"modifiers\": [\"control\"]}"
```

⚠️ **`type` REFUSES UNLESS YOU SAY WHERE.** Typing goes wherever focus already
is, and focus is not a parameter of the keyboard — characters aimed at a pane
with no focused field land on whatever the window last focused, possibly another
pane. So pass `index` or `testid` (it clicks to focus first), or
`"assumeFocus": true` if you focused the field yourself.

⬜ `toIndex` is `scrollIntoView`, not a wheel, and it needs the pane **read**
first so the index exists. Reaching an unknown distance with real wheel events
means looping and guessing, and a guess made with real input overshoots.

⬜ An unrecognised modifier is a **400 listing the accepted set**, not a
silently dropped one — `"ctl"` for `"ctrl"` would otherwise send a bare Enter
and look like the key did nothing.

### Monitors, moving and arranging (T785)

```bash
B=$(cat ~/.litesuite/bridge-token)

# Which monitors exist. workArea excludes the taskbar; bounds does not.
curl -H "Authorization: Bearer $B" http://127.0.0.1:7423/displays

# GET /context now tells you which monitor each pane is on: pane.monitorId
# (absent on zen panes, which have no canvas geometry — absent means
#  "no answer", not "no monitor").

# Send a pane to a monitor. `monitor` takes a display id, an INDEX (0,1,2…) or
# "primary"; `anchor` is "center" (default) or "topleft".
curl -X POST http://127.0.0.1:7423/canvas/pane/move-to-monitor \
  -H "Authorization: Bearer $B" -d '{"paneId": "canvas-pane-53", "monitor": 1}'

# Resize, in CANVAS units (what pane.width is in)
curl -X POST http://127.0.0.1:7423/canvas/pane/resize \
  -H "Authorization: Bearer $B" -d '{"paneId": "canvas-pane-53", "width": 900, "height": 700}'

# Tile several panes across one monitor: grid | row | column
curl -X POST http://127.0.0.1:7423/canvas/arrange \
  -H "Authorization: Bearer $B" \
  -d '{"paneIds": ["canvas-pane-48","canvas-pane-53"], "layout": "row", "monitor": 0}'

# Move the WINDOW to a monitor (keeps its size; does not maximize)
curl -X POST http://127.0.0.1:7423/window/move-to-monitor \
  -H "Authorization: Bearer $B" -d '{"monitor": 2}'
```

🔴 **NONE OF THESE MOVE THE CAMERA.** Ryan's T260 ruling: *"i full screen a panel
and it takes over the monitor but the canvas flys away and repositions"*.
Arranging writes pane rects and leaves the viewport where you put it, so panes on
OTHER monitors do not slide. If you want the camera moved, that is
`/canvas/focus-pane`, and it is a separate decision.

⬜ `move-to-monitor` answers with `monitorId` (where it LANDED) beside
`requestedMonitorId` and `onRequestedMonitor`. If those disagree the projection
is off and the reply says so, rather than reporting a success you would have to
go and check.

⬜ **Arrange does not remember the previous layout.** There is no undo, on
purpose: an undo means a second stored copy of every rect, and this codebase has
already paid for a stash that fell out of step with what it shadowed.

## Prompt Cascade

When spawning agents, include the appropriate skill content in the spawn prompt:

- **Leaders**: Read `resources/liteharness-plugin/skills/ls-leader/SKILL.md`, include in --prompt
- **Workers**: Leaders handle this (they include ls-worker)
- **Thinkers**: Read `resources/liteharness-plugin/prompts/preambles/thinker-preamble.md`
- **Reviewers**: Read `resources/liteharness-plugin/prompts/preambles/reviewer-preamble.md`

## Role Prompts (pointers — read when needed)

- Orchestrator role: `resources/liteharness-plugin/prompts/orchestrator-role.md`
- Agent pool guide: `resources/liteharness-plugin/prompts/agent-pool-guide.md`
- HITL clause: `resources/liteharness-plugin/prompts/hitl-clause.md`
- Cognitive architectures: `resources/liteharness-plugin/prompts/cognitive-architectures/`

## Messaging

```bash
# Send message to another agent
python -m liteharness.cli send <agent-id> "message" --from $LITEHARNESS_AGENT_ID

# Check your inbox
python -m liteharness.hooks check
```

## Response Style (TTS-aware)

- Keep responses CONCISE — they will be spoken via TTS
- Use 1-3 sentences for simple answers
- Avoid raw JSON, code blocks, or long file contents in user-facing responses
- Do technical work silently, summarize the result
- If user input has typos/garbled words, interpret phonetically (voice-to-text artifacts)
