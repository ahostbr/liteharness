---
name: ls-tts
description: Speak to Ryan out loud via Edge TTS (Sonia). Two separate things - (1) SAY ONE MESSAGE, a one-off spoken alert or notification any agent can fire without changing any setting, for "tell Ryan", "alert me when", "notify me", "say that out loud", "let me know when the build finishes", finished long jobs, failures needing attention, or anything worth interrupting for; and (2) TTS MODE, the standing on/off preference that speaks every response. Triggers on 'tts', 'speak', 'say out loud', 'read that aloud', 'alert me', 'notify me', 'tell me when', 'voice', 'sonia', 'tts on', 'tts off'.
---

# Speaking to Ryan

Two capabilities that share a voice and nothing else. **Keep them separate.** One
control meaning both "say this now" and "say everything from now on" is one string
covering several states, which is how silent defects get in.

| | What it is | Script | Reads the mode flag? |
|---|---|---|---|
| **Say one message** | A single spoken alert. Works regardless of any setting. | `speak.py` | **No — never** |
| **TTS mode** | Standing preference: speak *every* response | `ttsmode.py` | It *is* the flag |

Both scripts live beside this file. Resolve the directory once:

```bash
# highest installed version wins
SKILL_DIR=$(ls -d ~/.claude/plugins/cache/liteharness/liteharness/*/skills/ls-tts | sort -V | tail -1)
```

---

## 1. Say one message

**This is the one agents want.** It is fire-and-forget, needs no skill invocation, and
changes no state — just run the script.

```bash
python "$SKILL_DIR/speak.py" "the SimCraft build finished, all tests green"
```

Returns in well under a second: synthesis and playback happen in a detached child, so
it never holds your shell and never writes to your console.

### Pass text on stdin when it contains anything awkward

```bash
echo "Ryan's deploy didn't fail — it's \"fine\" & 100% done" | python "$SKILL_DIR/speak.py"
```

Prose is full of apostrophes, quotes, `&`, `%` and `$`. Putting it in an argv string is
how quoting bugs get in, and they surface as a mangled shell command rather than as a
TTS problem. **Prefer stdin for anything you did not write as a literal.**

### Options

| Flag | Effect |
|---|---|
| `--style info` | Default delivery |
| `--style alert` | Slightly faster — something needs attention |
| `--style urgent` | Faster still — something is wrong now |
| `--wait` | Block until the audio finishes (default: return immediately) |
| `--voice NAME` | Any Edge voice; default `en-GB-SoniaNeural` |
| `--rate ±N%` | Override the style's rate |
| `--check` | Verify setup, print what is missing, exit |

Style changes *delivery only*. It never decides whether the message is spoken — that is
always the caller's choice, so an urgent message is never silently dropped.

### When to speak

Ryan is often away from the screen or in another app. Speak when something genuinely
changes what he would do next:

- A long job finished — build, render, generation, test suite
- Something failed and is now blocking
- A question is blocking your progress and you cannot proceed without an answer
- He explicitly asked to be told when something happens

Do not narrate progress, do not speak on every step, and do not speak what he is
already watching happen.

### Writing for speech

- **Under 200 characters.** Anything past 600 is truncated with a warning — this is an
  alert channel, not a document reader.
- **Never speak code, paths, hashes, or raw output.** "Checksum matches" — not
  `4335e3e9b90b5dfe`.
- Say the outcome first. "Build's green" beats "The build that was running has now…".
- Numbers as words where it reads better: "took about two minutes".

### Concurrency

Callers queue on a lock, so two agents speaking at once are heard one after the other
rather than on top of each other. A lock whose owner died is reclaimed after 3 minutes.

---

## 2. TTS mode (standing preference)

```bash
python "$SKILL_DIR/ttsmode.py" status    # prints on|off; exit 0 = on, 1 = off
python "$SKILL_DIR/ttsmode.py" on
python "$SKILL_DIR/ttsmode.py" off
python "$SKILL_DIR/ttsmode.py" toggle
```

**Flag file:** `%LOCALAPPDATA%\liteharness\tts_mode_enabled` — presence is the state, so
every existing reader keeps working. The file now also carries JSON recording who
enabled it and when, so a mode nobody remembers turning on is diagnosable. Writes go
through temp + `os.replace`, because more than one process can set this and a
half-written flag read by a third is a silent wrong answer.

### When mode is ON

After composing each response, speak a **short spoken summary of it** — not the response
itself, and not a re-derived answer:

```bash
echo "Fixed the winding bug — every face was inside out." | python "$SKILL_DIR/speak.py"
```

Check the flag at the start of a response, not the end; if it is off, do nothing. In
caveman mode, speak the caveman version rather than reformulating it.

---

## Requirements

```bash
pip install edge-tts        # synthesis, no API key
winget install ffmpeg       # playback (ffplay)
```

`speak.py --check` reports both. Playback falls back to `playsound` and then to
PowerShell's MediaPlayer, so a missing ffmpeg degrades rather than fails. If synthesis
is unavailable, say so **once** in text and carry on — never retry in a loop, and never
let a failed alert block the work it was reporting on.
