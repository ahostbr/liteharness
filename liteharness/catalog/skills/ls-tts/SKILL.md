---
name: ls-tts
description: Toggle TTS mode — when ON, all Claude responses are spoken aloud via Edge TTS (en-GB-SoniaNeural / Sonia). Simple on/off toggle.
---

# TTS Mode Toggle

Speak all Claude responses aloud via Edge TTS (Sonia voice) until toggled off.

## Arguments

| Arg      | Effect                                 |
| -------- | -------------------------------------- |
| `on`     | Enable TTS mode — speak every response |
| `off`    | Disable TTS mode — stop speaking       |
| _(none)_ | Toggle current state                   |

## Flag File

`~/AppData/Local/liteharness/tts_mode_enabled`

- File exists → TTS mode ON
- File absent → TTS mode OFF (default)

## When invoked

1. Parse argument (on / off / toggle)
2. Create or delete the flag file accordingly
3. Confirm state change to user
4. **If turning ON:** remind yourself that from now on, after EVERY response you send to the user, you MUST also run the TTS command below to speak it aloud. This applies to ALL responses — not just the current one.

## How to speak (EVERY response when ON)

After composing your text response, synthesize and play it:

```bash
python -c "
import asyncio, tempfile, time, pathlib
import edge_tts
from playsound import playsound

async def speak(text):
    out = pathlib.Path(tempfile.gettempdir()) / f'tts_{int(time.time()*1000)}.mp3'
    comm = edge_tts.Communicate(text, voice='en-GB-SoniaNeural', rate='+0%')
    await comm.save(str(out))
    try:
        playsound(str(out), block=True)
    finally:
        out.unlink(missing_ok=True)

asyncio.run(speak('''TEXT_HERE'''))
"
```

Replace `TEXT_HERE` with a **concise spoken summary** of your response (1-3 sentences max). Don't read code blocks or file paths — summarize what you did or what the answer is in natural speech.

## Requirements

Install Python dependencies before first use:

```bash
pip install edge-tts playsound
```

> `edge-tts` — Microsoft Edge TTS synthesis (no API key required)
> `playsound` — cross-platform audio playback

## Rules

- **Check the flag file at the start of every response.** If it exists, speak.
- **Never speak code blocks, file paths, or raw technical output.** Summarize for speech.
- **Keep spoken text under 200 chars** — Sonia is for quick status updates, not essays.
- **Run TTS in background** (`run_in_background: true`) so it doesn't block your response.
- **Caveman mode + TTS:** speak the caveman version, not a reformulated one.
- **If edge_tts or playsound not installed:** say so once, disable flag, don't retry.
