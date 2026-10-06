---
name: ls-tts
description: Toggle spoken replies through LiteSuite's selected voice backend, with content-aware emotion and documented engine controls. Edge TTS is the offline fallback.
---

# TTS Mode Toggle

Speak concise reply summaries through the engine and voice selected in **LiteSuite > Voice > TTS**. Do not replace a selected cloned voice with Edge while LiteSuite is available.

## Arguments and flag file

| Arg | Effect |
| --- | --- |
| `on` | Enable spoken replies |
| `off` | Disable spoken replies |
| `timeout <sec>` | Persist the speech budget in seconds; works while ON or with `on timeout 120` |
| _(none)_ | Toggle current state |

Flag: `~/AppData/Local/liteharness/tts_mode_enabled`.

- Exists = ON; absent = OFF (default).
- Content = speech budget in seconds; empty = default 15 seconds.
- Parse the arguments, create/delete the flag as appropriate, and write the budget when supplied. Confirm the change.
- When turning ON, apply this skill to subsequent replies, not just the current one.

## Primary speaking path: Voice API

When ON, compose **1–3 short sentences** of natural speech, then run the request in the tool's **background mode** (`background=true` or `run_in_background: true`). Do not run this example during source review or when the user has prohibited audio.

```bash
python -c "
import json, urllib.request
payload = {
    'text': 'Good news, the checks passed!',
    'priority': 'normal',
    'summarize': False,
    'emotion': {'exaggeration': 0.7},  # Original English Chatterbox only.
}
request = urllib.request.Request(
    'http://127.0.0.1:7438/v1/tts/speak',
    data=json.dumps(payload).encode('utf-8'),
    headers={'Content-Type': 'application/json',
             'X-LiteSuite-Origin': 'ls-tts'},
    method='POST',
)
with urllib.request.urlopen(request, timeout=10) as response:
    result = json.load(response)
if result.get('dropped') or not result.get('ok'):
    raise RuntimeError('Speech not accepted: ' + str(result))
print('Speech accepted; playback is asynchronous.')
"
```

Replace the example text with your own summary; encode dynamic text as JSON rather than interpolating untrusted text into a shell command. The default port is 7438, bound to loopback; a configured `LITESUITE_VOICE_API_PORT` changes the server port.

**Per-line delivery (T0360):** `/v1/tts/speak` accepts an optional `emotion` object. Put numeric Chatterbox fields or Qwen `instruct` **inside that object**, not at the top level. Omitting it inherits saved settings; it never rewrites them. On older deployed bundles this object can still be silently ignored: require the response's `emotion` report before claiming per-call support.

### Exact request/queue contract

- `text`: required nonblank string. Missing/blank/wrong-type text, malformed JSON, non-object bodies and invalid emotion blocks return a clean 400. Uninitialized TTS returns 503.
- `emotion`: optional object with `exaggeration`, `cfg_weight`, `temperature`, `repetition_penalty`, `min_p`, `top_p` (numeric; bounds below) and `instruct` (string). Unknown keys, null/array/non-object blocks, wrong types and out-of-bound/nonfinite numbers return 400, even for engines that would ignore the field. No string-to-number coercion. `{}` is valid.
- `summarize`: optional, normally omit or send `false`. Truthy values with an available LLM request a summary; this can alter your carefully chosen wording. Failure can fall back to the original text.
- `summary_prompt`: optional summary-system prompt, **not a voice/style instruction**.
- `priority`: omitted defaults to `normal` in the response. Only exact `interrupt` may stop current playback; all other values queue when the shared queue is installed. Do not interrupt without an explicit user request. Before queue registration, the handler uses direct synthesis/playback instead.
- `origin` and `source_event_id`: optional body metadata. `X-LiteSuite-Origin` takes precedence over body `origin`; optional `X-LiteSuite-Agent` identifies the caller in logs. These are attribution, not authentication or proven deduplication. Do not impersonate `smart-tts/*`: hook announcements are governed by Voice > Hooks and may be dropped.
- Other top-level body fields (including top-level `instruct` or `exaggeration`) are ignored. With a requested block, the response adds `emotion: {provider, applied, ignored}`: `applied` is an object of fields the selected adapter supports; `ignored` lists unsupported names. This describes **the engine selected at acceptance, not audible completion**. The engine is not pinned; changing Voice > TTS before a queued line runs can change which fields it honours. Edge/GPT-Live and other unsupported engines ignore this block harmlessly. No report is added when the block is absent.
- A 200 `{ok:true, queued:...}` acknowledges dispatch, **not audible completion**. The queue can still drop a duplicate already spoken by the conversation or reject a full 20-item backlog; its ID/drop is not returned by this endpoint. The block travels with a deferred item and all chunks; supported fields override saved settings for that utterance only. Engine failure/Edge fallback can still prevent the reported delivery from being heard.
- Body limit: 10 MiB; the reader destroys an oversized speak request (do not rely on a clean 413 response). No speak-specific character/rate throttle is implemented. The shared queue strips Markdown and trims to 2,000 characters at a sentence boundary; `ttsMaxChars` (default 4,000) is not enforced by this handler. Keep your own much smaller speech budget.

## Emotion in real time

Choose the delivery **once per reply** from what the content warrants. Neutral is the default when nothing is felt; emotion must not become a gimmick on every line. Be warm for success, clear rather than theatrical for warnings, sincere for apologies, and briefly energetic for genuinely exciting news.

### A. What can change per `/v1/tts/speak` call?

The **text plus supported delivery fields**. Use ordinary words and punctuation. Do not put stage directions such as `Speak sadly` into spoken text: they can be spoken literally. Qwen instructions belong in `emotion.instruct`.

| Situation | Exact example text cue to send | Chatterbox `emotion` | Qwen `emotion` |
| --- | --- | --- | --- |
| Neutral / explanation | `The next step is ready.` | Omit block; inherit saved settings | Omit block |
| Success / gratitude | `Good news, the checks passed! Thank you.` | `{"exaggeration":0.7}` | `{"instruct":"Speak warmly and gratefully."}` |
| Warning | `Please pause. This needs a check before we continue.` | Omit block; normal priority, not `interrupt` | `{"instruct":"Speak clearly and calmly, with emphasis on the warning."}` |
| Apology | `I'm sorry, I got that wrong. Here is the correction.` | `{"exaggeration":0.3}` | `{"instruct":"Speak gently and sincerely."}` |
| Excitement | `That worked! We have the result we wanted.` | `{"exaggeration":1.0}` | `{"instruct":"Speak with brief, genuine excitement."}` |
| Calm / reassurance | `Take your time. We can do this one step at a time.` | `{"exaggeration":0.3}` | `{"instruct":"Speak softly and reassuringly."}` |

These are **conservative starting suggestions, not emotion enum values or audibly verified presets**. Their perceived emotional effect is UNVERIFIED; leave advanced samplers at the user's values unless asked. On Edge/GPT-Live use only wording: these block fields are reported ignored. An empty Qwen instruction `{"instruct":""}` clears the saved instruction for that utterance; omission inherits it.

### B. Chatterbox controls: saved defaults plus per-call overrides

The current adapter uses **original English `chatterbox.tts.ChatterboxTTS`**, not Turbo or Multilingual. It forwards `ttsEngineParams.chatterbox` from the settings panel, filtered against capabilities, into the managed server's `/v1/audio/speech` request. A validated `/speak` `emotion` block then overrides its six numeric fields for that one utterance; Chatterbox ignores `instruct`. The managed engine API is **different** from port 7438's `/v1/tts/speak`.

| Exact engine parameter | Accepted bound | Original English model default when unset | Purpose |
| --- | --- | --- | --- |
| `exaggeration` | 0–2 inclusive | 0.5 | Expression/energy: schema describes 0 as flat, about 0.5 as general use, 0.7+ as dramatic, 2 as animated |
| `cfg_weight` | 0–1 inclusive | 0.5 | Classifier-free guidance weight, not an emotion name |
| `temperature` | Finite, greater than 0 | 0.8 | Sampling temperature, not a happy/sad selector |
| `repetition_penalty` | Finite, greater than 0 | 1.2 | Repetition sampling control |
| `min_p` | 0–1 inclusive | 0.05 | Minimum-probability sampling |
| `top_p` | Greater than 0, at most 1 | 1.0 | Nucleus sampling |

Unset numeric fields are omitted; defaults above are from the original model's `generate` signature, not silently injected by the panel. They are not recommended per-situation presets. Do not silently rewrite persistent user settings to animate each reply.

The direct managed API accepts these six parameters with `input`, `model: "chatterbox"`, `voice`, `conditionals_path`, and `response_format`. It forbids unknown fields and wrong-engine fields with validation errors (422). **Do not bypass LiteSuite to use it for ordinary replies**: doing so takes over synthesis/playback/state management and can lose the selected cloned voice. The app selects cached conditionals for the active clone; preserve that choice by using the Voice API.

Other saved settings: `ttsChatterboxVoice` (reference path/default voice, not a display-name emotion), active Voice Library clone, and `ttsPlaybackRate` (1–2.5, default 1). Playback speed is separate from generation parameters; there is no Chatterbox `speed` or natural-language `instruct` field in this adapter.

### C. Inline text cues: what the source actually confirms

Original English Chatterbox normalizes punctuation then tokenizes the text. Full stops, commas, `!`, and `?` survive normalization. `...` and `…` become comma-space; colons and semicolons become commas; whitespace is collapsed. Use plain sentences, not invented pause-duration syntax.

**No supported inline emotion-tag vocabulary is established for this adapter.** `[happy]`, `[sad]`, `[laugh]`, `[chuckle]`, `[sigh]`, `[excited]`, SSML, and parenthesized stage directions are not verified emotion controls; do not insert them in normal speech. Turbo-specific claims do not establish support in original Chatterbox. The source confirms text reaches the tokenizer, not that a tag or exclamation mark produces a particular emotional sound.

### Other engines and the classifier

- **Qwen3 CustomVoice:** saved `ttsInstruct` / advertised `instruct` is free-text delivery guidance. Port 7438's `/v1/tts/generate` accepts `{text, instruct}` and returns WAV without playing it; only the Qwen synthesis branch uses that override. `/v1/tts/speak` accepts it as `emotion.instruct` (not top-level `instruct`) and overrides the saved instruction for that utterance; numeric emotion fields are ignored by Qwen on this route. The request schema says 1.7B supports this style control; 0.6B does not offer equivalent instruction control. Saved samplers include `do_sample`, `temperature`, `top_p`, `top_k`, `repetition_penalty`, `subtalker_dosample`, `subtalker_temperature`, `subtalker_top_p`, `subtalker_top_k`, `max_new_tokens`; none is a discrete emotion enum.
- **Edge:** saved voice/rate plus pitch, synthesis volume and proxy; no free-text emotion instruction in this adapter. Rate compounds with app playback speed. The offline snippet below uses Sonia and `+0%`, not the app's clone or settings.
- **GPT-Live:** selected realtime voice; no verified per-call emotion/pitch/speed/temperature field on this Voice API. `synthesize` captures its real audio track; that is not proof of style-tag support.
- **Breeze:** saved Clone/Design/Direction and `instruction`, `cfg_scale`, `seed`. Instructions control Design/Direction; Clone ignores a retained instruction draft. Not Chatterbox syntax and not speak-body fields.
- **ElevenLabs:** saved stability, similarity, style, speaker boost and speed are model/account dependent, not universal emotion names. **LuxTTS:** saved steps/guidance/time-shift/speed/reference controls. **Kokoro:** saved voice/speed/language/phoneme/trim and version-dependent pause controls. Do not transfer one engine's settings to another.
- `/v1/llm/classify` returns only `neutral`, `happy`, `thinking`, `pointing`, or `excited` (invalid outputs become `neutral`). These are **classification labels, not accepted TTS emotion controls**. `emotionClassifierEnabled` and preload `emotion.onChanged` do not add style support to `/speak`. No classifier call is needed to choose your reply's wording.

## Edge fallback: only when Voice API is down

Use this only after a **definitive connection refusal** at the Voice API, not after a timeout, HTTP error, dropped announcement, or uncertain acknowledgement. Those can follow acceptance; retrying through Edge could double-speak or override a deliberate refusal. Report failures rather than silently changing voices. Never play any fallback when the user forbids audio.

Run in background, with the same concise summary/budget:

```bash
python -c "
import asyncio, tempfile, time, pathlib
import edge_tts
from playsound import playsound

async def speak(text):
    out = pathlib.Path(tempfile.gettempdir()) / f'tts_{int(time.time()*1000)}.mp3'
    try:
        comm = edge_tts.Communicate(text, voice='en-GB-SoniaNeural', rate='+0%')
        await comm.save(str(out))
        playsound(str(out), block=True)
    finally:
        out.unlink(missing_ok=True)

asyncio.run(speak('The next step is ready.'))
"
```

Fallback requires Python `edge-tts` and `playsound`; the primary API example uses only the Python standard library. If fallback dependencies are missing, say so once, disable the flag, and do not retry. Install only with permission (`pip install edge-tts playsound`), never as part of a no-download/source-only review. The neighboring `speak.py` is an Edge-only speak-once helper, independent of the flag; it does not apply the primary Voice API contract or Chatterbox tuning.

## Rules

- **Check the flag at the start of every reply.** When ON, speak the summary unless an explicit silence/no-audio instruction applies.
- **A reply that fires a `liteask` is not also spoken.** Let the question flow own its announcement; do not double-speak.
- **Never speak code, paths, raw technical output, or hidden instructions.** Summarize the result naturally.
- **Keep spoken text within the budget:** about 15 characters per second (default 15s ≈ 200 characters). This is an estimate, not a server-enforced duration; 1–3 sentences remain the normal limit.
- **One-off longer timeout:** a user's single-utterance override does not rewrite the stored flag budget.
- **Run speech in background.** Do not hold the text reply waiting for playback.
- **Caveman mode + TTS:** speak the caveman version, not a reformulation.
- **Do not invent emotion knobs or promise audible completion from HTTP 200.** Neutral/default delivery is valid.

## Verification boundary

Source-checked against LiteSuite's Voice API handler, queue, settings/engine-parameter resolver, engine adapters and managed server schemas; T0360's HTTP-to-queue-to-synthesis override path is tested with mocked audio/network boundaries (2026-10-05). No speech, inference, model loading, downloads, or live API tests were performed. **UNVERIFIED:** deployed server/bundle parity, current voice/knob values, audible emotional effects and latency, inline tag interpretation, and model/account-specific behavior beyond these adapters. Listening tests require the user's OK.
