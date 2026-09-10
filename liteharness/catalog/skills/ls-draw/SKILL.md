---
name: ls-draw
description: Screenshot a monitor and hand the human a paint pill — they highlight, circle, arrow and write on their own screen, and you get the annotated PNG back plus what they drew and exactly where. Use when a screenshot alone would leave you guessing which thing they mean, or when the human says "let me show you". Triggers on 'ls-draw', 'draw on the screen', 'let me draw it', 'let me show you', 'screenshot and let me mark it up', 'annotate my screen', 'circle it for you'.
---

# /ls-draw — the human draws on their own screen, and you get the picture

`ls-mark` gets you a ring around one spot. **This gets you the whole drawing**: the human
highlights three things, circles the broken one, arrows from A to B and writes a word next to
it. You receive the annotated image *and* a machine-readable list of what they drew, in
coordinates you can act on.

## Run it

```bash
python "<this skill's directory>/draw.py"                  # monitor 0, waits up to 600s
python "<this skill's directory>/draw.py" --mon 1
python "<this skill's directory>/draw.py" --mon all        # capture every monitor
python "<this skill's directory>/draw.py" --timeout 120 --note "circle the broken one"
python "<this skill's directory>/draw.py" --get <session>  # re-read a finished session
```

Inside LiteSuite the same thing is `lst run draw mon=0`, and over MCP the `draw` tool returns
the annotated PNG **as an image block** — no Read needed there.

The call **BLOCKS** until the human sends, cancels, or the timeout passes. That is the point:
you are waiting for a person. Say what you are waiting for before you call it.

## What comes back

ONE line of JSON:

```json
{"session":"draw-1789…-1","mon":0,"outcome":"OK",
 "png":"C:\\Users\\…\\.litesuite\\draw\\draw-1789…-1\\annotated.png",
 "original":"C:\\Users\\…\\original.png",
 "text":"the middle one is wrong",
 "monitor":{"x":0,"y":0,"w":3440,"h":1440,"scale":1},
 "annotations":[{"tool":"ellipse","color":"#d4af37","width":4,
                 "mon_bbox":[820,410,240,180],"abs_bbox":[820,410,240,180]}]}
```

…or the bare word `CANCELLED` / `TIMEOUT`, or `LiteSuite is not running`.

## Then act on it

1. **Read the PNG first.** `png` is the annotated image — the capture with their marks on it.
   You are multimodal; the drawing says what the coordinates cannot. Do this before you read
   anything else.
2. **Then the `text` field** — the note they typed. It is usually the actual instruction.
3. **Then `annotations`**, when you need to act on a position rather than understand a
   picture. Each carries two boxes and they are not interchangeable:
   - `mon_bbox` — monitor-local pixels. Use with anything that takes `--mon N` (pccontrol).
   - `abs_bbox` — virtual-desktop pixels. **Negative x on a monitor left of the primary**,
     which is correct and is what a global-coordinate tool needs.
   `original` is the same shot with nothing drawn on it, for a clean before/after.

## Honest failure modes

- **`LiteSuite is not running`** — this skill has no fallback. The capture, the window and the
  pill all live in the app. Say so and stop; do not retry.
- **`CANCELLED`** — they closed it or hit Esc. Ask what they wanted; do not relaunch.
- **`TIMEOUT`** — nobody drew. The window opens on the monitor you asked for, so if you asked
  for `--mon 1` and they were looking at monitor 0, they never saw it. Say which monitor
  before retrying.
- **A session still writes its files when cancelled or timed out.** `original.png` and
  `result.json` are on disk either way, so `--get <session>` can still tell you what was on
  screen when you asked.
- One session at a time. A second call while a window is open is refused.

## When to reach for this

- **"Which one do you mean?"** with more than one candidate — one drawing beats four rounds of
  guessing, and it costs the human three seconds.
- **Bug reports about layout or visuals.** "It looks wrong" becomes a circled element with an
  arrow and the word "overlapping".
- **Teaching you a workflow.** They number the steps on the actual screen: 1 here, 2 here.
- **Design feedback.** They draw the change they want on top of what exists.

Reach for `ls-mark` instead when you need exactly one point and nothing else — it is lighter.
