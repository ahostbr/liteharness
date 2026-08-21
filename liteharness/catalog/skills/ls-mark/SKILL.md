---
name: ls-mark
description: Ask the HUMAN to mark a spot on their screen — a draggable ring with a send button; you get the coordinates AND a screenshot with the ring still in it. Use whenever "which one do you mean?", "show me where", or a UI element needs pointing at. Triggers on 'mark the screen', 'mark it', 'show me where', 'point at it', 'which one do you mean', 'let me mark', 'screen marker'.
---

# /ls-mark — the human screen-marker channel

The reverse of an agent's click-marker: instead of you showing the human where
you are about to act, **the human shows YOU what they are talking about**. A
draggable ring appears on their screen; they drag it onto the thing and click
**send**; you receive the coordinates in two systems plus a screenshot of that
monitor **with the ring still in the shot** — the ring IS the highlight.

## Run it

```bash
python "<this skill's directory>/mark.py"            # blocks up to 180s
python "<this skill's directory>/mark.py" --timeout 300 --color lime
```

The call BLOCKS until the human clicks send, cancels, or the timeout passes.
Output is ONE line:

```json
{"x": 1745, "y": 724, "mon": 0, "mon_x": 1745, "mon_y": 724, "png": "C:\\...\\mark.png"}
```

or `CANCELLED` / `TIMEOUT after 180s`.

## Then act on it

1. **Read the PNG** with your Read tool — you are multimodal, and the cyan ring
   in the image shows exactly what the human pointed at. Do this FIRST: the
   coordinates tell you where, the image tells you WHAT.
2. Coordinates come in both systems on purpose:
   - `x`, `y` — absolute virtual-desktop pixels (multi-monitor global).
   - `mon`, `mon_x`, `mon_y` — the monitor index and monitor-local pixels, for
     tools that take per-monitor coords (pccontrol's `--mon N`).

## Honest failure modes

- `CANCELLED` — the human closed it (x button or Esc). Ask, don't relaunch.
- `TIMEOUT` — nobody clicked send. The human may not have seen the ring (it
  spawns centred on the PRIMARY monitor); say where it appears before retrying.
- The overlay is a real window: if the screen is locked or a fullscreen
  exclusive app holds the display, it may not be visible. Say so rather than
  polling forever.

## When to reach for this

- Disambiguation: three similar buttons, "which one?" — one mark beats three
  screenshots of guessing.
- Bug reports: "mark the broken widget" turns prose into pixels.
- Driving GUI work: the human marks the field, you click it with confidence.
