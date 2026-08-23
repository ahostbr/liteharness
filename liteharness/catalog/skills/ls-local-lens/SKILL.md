---
name: ls-local-lens
description: Preprocess bulk content through a LOCAL LM Studio model to save your context window. Discovers what is installed at runtime and asks which model and context to use — it never assumes a lineup.
---

# Local Lens — run bulk work through a local model

> **Requirements**
>
> - **LM Studio** running locally (https://lmstudio.ai), with at least one model downloaded.
> - The `lms` CLI at `%USERPROFILE%\.lmstudio\bin\lms.exe` (ships with LM Studio).
> - If LM Studio is offline, **fall back gracefully** — do the task yourself and say Local Lens was unavailable.

Your context window is expensive and finite. A local model turns 15,000 tokens of raw
transcript, log, or scrape into a few hundred tokens of structured summary. Use it for
bulk reading; keep your own context for reasoning.

---

## 🔴 THE ONE RULE: DISCOVER, THEN ASK. NEVER ASSUME A LINEUP.

**This file names no model as "the best", "the default", or "the heavy tier", and it
must never start.** Ruling, Ryan 2026-08-23:

> _"keep it open ended ... just ask me what model and context i want at the time ...
> dont hardcode models"_

**Why the rule exists, concretely.** An earlier version of this skill shipped a model
inventory table and a routing table built when one 24B coding model was the strongest
thing on the box. Months later that was simply false — a newer 27B beat it at any
quant — but the file still read as a current, confident routing decision, because
**prose that names a ranking carries no expiry date.** On 2026-08-23 an agent following
it was about to `unload --all` the user's live, in-use seat to load a worse model,
mid-task.

⭐ **A model table is a snapshot that keeps presenting itself as a decision.** The
inventory rots at the speed of the field; the *mechanism* below does not. So this skill
keeps the mechanism and gets the inventory **at runtime, every time.**

---

## Step 1 — Discover what is actually there

Never open with a load. Open with a look.

```bash
LMS="$USERPROFILE/.lmstudio/bin/lms.exe"

"$LMS" ps          # what is LOADED right now — identifier, context, size, TTL
"$LMS" ls          # what is on disk and available to load
nvidia-smi --query-gpu=memory.used,memory.total --format=csv    # real headroom
```

Richer view, including per-model context and whether a model does vision:

```bash
curl -s http://localhost:1234/api/v0/models | python -c "
import json,sys
for m in json.load(sys.stdin)['data']:
    if m.get('state') != 'not-loaded':
        print(f\"LOADED  {m['id']:50s} type={m.get('type','?'):5s} ctx={m.get('loaded_context_length','?')}\")
"
```

`type` is `vlm` for vision-capable models and `llm` otherwise — **check it rather than
remembering which models see images.**

## Step 2 — If something suitable is already loaded, USE IT

A resident seat is the cheapest correct answer and it is usually there on purpose.
Loading over the top of it costs the user their working state and a model load.

**Only if nothing suitable is loaded, or the task genuinely needs something else, go to
Step 3.**

## Step 3 — ASK which model and context

Use `AskUserQuestion`. Offer what `lms ls` actually returned — never a remembered list.

Ask for both, because they are one decision:

- **Which model** — from the discovered list.
- **What context length** — this is not a detail. It sets VRAM, and it decides whether
  anything else fits beside it (see below).

If the user has already told you in this session which seat to use, **do not ask
again** — that is what "at the time" means, not "every call".

---

## 🔴 VRAM: `lms ps` SIZE IS WEIGHTS, NOT RESIDENCY

**The single most expensive mistake this skill can cause.** `lms ps` reports the
weights. The real footprint is weights **plus** the KV cache, which scales with
**context length × parallel slots**.

> Measured on a 32 GB card: a 27B seat showed **SIZE 17.74 GB** in `lms ps` while its
> true residency at 120k context × parallel 4 was **~29 GB of 31.5**. Reading SIZE and
> concluding "17.7 + 2.4 < 32, it fits" nearly OOM'd the workstation.

**Therefore:**

1. **Trust `nvidia-smi`, not `lms ps` SIZE**, for what is free.
2. **Never JIT a second model beside a large seat at full context.** Lower the seat's
   context first, or do not load.
3. **Context length is the knob that creates room.** The same seat that leaves nothing
   spare at 120k may leave several GB at 50–60k — enough for a small model to run
   alongside as a sub-agent. Ask the user before making that trade; it changes the
   behaviour of a tool they are using.
4. **Unload explicitly before loading heavy.** Do not rely on auto-eviction.
5. **Verify after loading** with `lms ps` — see what actually survived.

```bash
# Explicit swap, when the user has asked for one
"$LMS" unload --all
"$LMS" load "<model-key-from-lms-ls>" -y -c <context> --gpu max --identifier "<short>"
"$LMS" ps            # confirm
```

Flags: `-y` auto-confirms (required non-interactively) · `-c` context length ·
`--gpu max` full offload · `--identifier` the name you will call in the API ·
`--ttl <seconds>` auto-unload when idle. `unload` takes the identifier and does **not**
accept `-y`.

**Restore what you displaced.** If you evicted the user's seat for a one-off task, put
it back when you are done.

---

## 🧠 REASONING MODELS: BUDGET FOR THE THINKING, OR GET AN EMPTY ANSWER

**Many current local models emit reasoning before answering, and the API returns it in a
SEPARATE field.** `choices[0].message.reasoning_content` is not `content`.

🔴 **When the token budget is spent thinking, `content` comes back `""` with HTTP 200
and `finish_reason: "length"`. There is no error.** A script that writes the result
straight to disk produces a file containing a header and nothing else — which is
indistinguishable from a summary nobody read.

Measured 2026-08-23 on a 27B reasoning seat, 14,150-token prompt:

| `max_tokens` | outcome |
| --- | --- |
| 3,000 | `content` **empty**, `finish_reason=length` |
| 14,000 | `content` **empty** — **49,645 characters** of `reasoning_content` |
| 100,000 | full answer |

**So:**

- **Be generous with `max_tokens`.** The prompt was 14k of a 120k window; the room was
  always there. Under-budgeting fails silently, so err high.
- **ALWAYS guard the empty case.** Never write or report a result without checking.

```python
msg = resp["choices"][0]["message"]
text = (msg.get("content") or "").strip()
if not text:
    reasoning = msg.get("reasoning_content") or ""
    raise SystemExit(
        f"EMPTY CONTENT — finish_reason={resp['choices'][0].get('finish_reason')}, "
        f"reasoning={len(reasoning)} chars. Raise max_tokens. Refusing to write."
    )
```

That guard is the only reason the table above could be measured instead of guessed.

---

## API usage — organised by TASK, not by model

Endpoint `http://localhost:1234/v1/chat/completions`. Set `"model"` to the identifier
you discovered or the user chose. **Low `temperature` (0.1–0.2) for extraction work.**

### Summarize / compress

```json
{
  "model": "<chosen-identifier>",
  "messages": [
    {"role": "system", "content": "You are a precision summarizer. Extract ALL key facts, names, numbers, decisions and actionable items. Be thorough but concise. Never fabricate information not in the source."},
    {"role": "user", "content": "Summarize the following:\n\n<CONTENT>"}
  ],
  "max_tokens": 8000,
  "temperature": 0.1
}
```

### Targeted extraction

Same shape; system prompt becomes *"Extract ONLY what is requested. If it is not
present, say so. Do not guess."*

### Vision

Only models reporting `type: "vlm"` (Step 1). Standard OpenAI vision format:

```python
{"role": "user", "content": [
    {"type": "text", "text": "Describe this image in detail."},
    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
]}
```

### Content that is awkward to escape

Long or quote-heavy content breaks bash quoting. Write a small Python file and run it —
build the payload with `json.dumps`, never string interpolation.

**If the content exceeds the seat's context**, chunk it and summarise per chunk, then
summarise the summaries. Say in your report that you chunked, and how.

---

## `lms_switch.py`

`${CLAUDE_SKILL_DIR}/lms_switch.py` wraps load/unload with VRAM math and named profiles.

```bash
python "${CLAUDE_SKILL_DIR}/lms_switch.py" status
python "${CLAUDE_SKILL_DIR}/lms_switch.py" ensure "<model-key>"
python "${CLAUDE_SKILL_DIR}/lms_switch.py" unload --all
```

⚠️ **Its named profiles (`default`, `duo`, `reasoning`, `coding`, …) hardcode a model
lineup and carry the same staleness this file was rewritten to remove.** Prefer
`status` / `ensure <explicit-key>` with a key from `lms ls`. Treat a profile name as a
suggestion to verify, never as a current routing decision.

---

## Caveats

- **Small models lose nuance and will invent details.** For exact figures, legal text,
  or anything load-bearing, read the source yourself or use a stronger seat.
- **Never present a local model's output as your own analysis.** Say it came from local
  preprocessing, and name the model you used.
- **Transcripts and OCR carry garbled proper nouns.** A faithful summary of a mangled
  source is still mangled — flag names and figures as unverified when the input is ASR
  or OCR.
- **Coding-agent models may emit tool calls** instead of answering. If you see
  `[TOOL_CALLS]` or a `tool_calls` field, add *"Do not use any tools."* to the system
  prompt and retry.
- **Report what you actually ran** — model identifier, context, and whether you chunked.
  A summary whose provenance is unstated cannot be checked later.
