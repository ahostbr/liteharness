---
name: ls-local-lens
description: Use when you need to process, summarize, or extract information from large content without bloating context. Triggers on 'summarize this', 'describe this screenshot', 'compress context', 'offload to local model', 'local-lens'. Routes through local LM Studio models. For planning use /plan-w-quizmaster, for expert analysis use /consult-polymaths.
allowed-tools: Bash
---

# Local Lens — LM Studio Model Manager & Context Compressor

> **Requirements:**
> - **LM Studio** must be installed and running locally. Download from https://lmstudio.ai.
> - The `lms` CLI must be available at `%USERPROFILE%\.lmstudio\bin\lms.exe` (installed automatically with LM Studio).
> - At least one model must be downloaded inside LM Studio before loading.
> - VRAM estimates assume a high-end GPU. Run `nvidia-smi` to check yours. If LM Studio is offline, fall back gracefully — do the task yourself and note Local Lens was unavailable.

You are using Local Lens to preprocess content through local models AND dynamically manage which models are loaded. This saves your context window for reasoning, not raw data.

## Why Use This

- Your context window is expensive and finite
- The local 0.8B model runs in ~100ms on a modern GPU (e.g., RTX 4090/5090)
- Devstral 24B handles complex coding/RAG tasks locally in seconds
- 15,000 tokens of raw content becomes 300-500 tokens of structured summary
- You can load/unload models on demand — no need to ask the user

## Model Inventory

These models are installed on disk and available to load:

| Model Key                                        | Params | Quant  | VRAM     | Role                                          |
| ------------------------------------------------ | ------ | ------ | -------- | --------------------------------------------- |
| `qwen3.5-0.8b`                                   | 0.8B   | BF16   | ~2 GB    | Fast summarizer, TTS companion, preprocessing |
| `huihui-qwen3.5-0.8b-abliterated`                | 0.8B   | F16    | ~2 GB    | Uncensored variant of above                   |
| `qwen3.5-0.8b-uncensored-opus-distill`           | 0.8B   | Q4_K_M | ~0.5 GB  | Opus-distilled, uncensored                    |
| `gemma-3-4b-it`                                  | 4B     | Q4_K_S | ~2.4 GB  | Mid-tier general purpose                      |
| `qwen3.5-4b-claude-4.6-opus-reasoning-distilled` | 4B     | Q8_0   | ~4.5 GB  | Opus-distilled reasoning                      |
| `qwen/qwen3.5-9b`                                | 9B     | Q8_0   | ~10.5 GB | Strong general purpose                        |
| `qwen3.5-9b-claude-4.6-opus-reasoning-distilled` | 9B     | Q8_0   | ~9.5 GB  | Best local reasoning                          |
| `mistralai/devstral-small-2-2512`                | 24B    | Q4_K_M | ~15.2 GB | Coding, RAG, complex analysis                 |
| `gemma-3-12b-it-uncensored`                      | 12B    | Q8_0   | ~13.4 GB | Uncensored mid-large                          |

**VRAM budget note:** The table above lists approximate VRAM usage for an RTX 5090 (31.5 GB). Run `nvidia-smi` to check your GPU's available VRAM and adjust which models you load accordingly. Multiple small models can coexist. Large models may auto-evict smaller ones.

## Python Switcher Script (Recommended)

The `lms_switch.py` script at `${CLAUDE_SKILL_DIR}/lms_switch.py` wraps all model management with VRAM safety checks, named profiles, and role presets.

```bash
# Check current status
python "${CLAUDE_SKILL_DIR}/lms_switch.py" status

# Switch profiles (safely unloads first, checks VRAM budget)
python "${CLAUDE_SKILL_DIR}/lms_switch.py" load default      # 0.8B @ 8k
python "${CLAUDE_SKILL_DIR}/lms_switch.py" load litespeak    # 0.8B @ 4k (dictation/voice cleanup)
python "${CLAUDE_SKILL_DIR}/lms_switch.py" load duo          # 0.8B @ 8k + Devstral @ 32k
python "${CLAUDE_SKILL_DIR}/lms_switch.py" load reasoning    # 0.8B @ 8k + Opus 9B @ 16k
python "${CLAUDE_SKILL_DIR}/lms_switch.py" load maxpower     # Devstral solo @ 65k
python "${CLAUDE_SKILL_DIR}/lms_switch.py" load coding       # Devstral solo @ 32k

# Load a specific model only if not already loaded (VRAM-safe)
python "${CLAUDE_SKILL_DIR}/lms_switch.py" ensure "mistralai/devstral-small-2-2512"

# Unload
python "${CLAUDE_SKILL_DIR}/lms_switch.py" unload --all
python "${CLAUDE_SKILL_DIR}/lms_switch.py" unload devstral

# Install/list role presets (system prompts + temperature + stop strings)
python "${CLAUDE_SKILL_DIR}/lms_switch.py" presets
python "${CLAUDE_SKILL_DIR}/lms_switch.py" preset summarizer
python "${CLAUDE_SKILL_DIR}/lms_switch.py" preset rag-strict
python "${CLAUDE_SKILL_DIR}/lms_switch.py" preset tts
python "${CLAUDE_SKILL_DIR}/lms_switch.py" preset coder
python "${CLAUDE_SKILL_DIR}/lms_switch.py" preset extractor
```

**ALWAYS prefer the Python script over raw CLI commands** — it handles VRAM math, safe unloading, and identifier assignment automatically.

## Raw CLI: Model Management (Low-Level)

The `lms` CLI at `$USERPROFILE/.lmstudio/bin/lms.exe` manages models directly.

### Check what's loaded

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" ps
```

### Load a model

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" load "<model-key>" -y -c <context_length> --gpu max --identifier "<short-name>"
```

- `-y` auto-confirms prompts (required for non-interactive use)
- `-c` sets context window (use 4096-8192 for 0.8B, 16384 for 9B, 32768 for Devstral)
- `--gpu max` uses full GPU offload
- `--identifier` sets the API name (use this to reference the model in API calls)
- `--ttl <seconds>` optional auto-unload after idle (e.g., `--ttl 300` = 5 min)

### Unload a model

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" unload <identifier>
```

Note: `unload` does NOT support `-y`. Just pass the identifier directly.

### Unload all models

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" unload --all
```

### Check available models on disk

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" ls
```

### Detailed model info (v0 API)

```bash
curl -s http://localhost:1234/api/v0/models | python -c "
import json,sys
for m in json.load(sys.stdin)['data']:
    print(f\"{m['id']:55s} state={m['state']:12s} quant={m.get('quantization','?'):8s} ctx={m.get('loaded_context_length', m.get('max_context_length','?'))}\")
"
```

## Load Profiles

Pre-configured combos for common scenarios:

### Profile: Always-On (default)

Just the 0.8B @ 8k — fast preprocessing, minimal VRAM.

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" load "qwen3.5-0.8b" -y -c 8192 --gpu max
```

VRAM: ~1.8 GB. Leaves room for everything else.

### Profile: Dictation (voice/dictation cleanup)

0.8B @ 4k — absolute minimum for dictation cleanup. Fastest possible.

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" load "qwen3.5-0.8b" -y -c 4096 --gpu max
```

VRAM: ~1.5 GB. Dictation inputs are short sentences, 4k is plenty.

### Profile: Summarizer + Heavy Hitter (duo)

0.8B @ 8k + Devstral @ 32k for complex analysis. Both fit simultaneously.

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" load "qwen3.5-0.8b" -y -c 8192 --gpu max
"$USERPROFILE/.lmstudio/bin/lms.exe" load "mistralai/devstral-small-2-2512" -y -c 32768 --gpu max --identifier "devstral"
```

VRAM: ~17 GB combined.

### Profile: Reasoning Stack

0.8B @ 8k + Opus-distilled 9B @ 16k for best local reasoning.

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" load "qwen3.5-0.8b" -y -c 8192 --gpu max
"$USERPROFILE/.lmstudio/bin/lms.exe" load "qwen3.5-9b-claude-4.6-opus-reasoning-distilled" -y -c 16384 --gpu max --identifier "opus-9b"
```

VRAM: ~11 GB combined.

### Profile: Max Power (Devstral solo)

Full VRAM to Devstral @ 65k for heavy coding/RAG.

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" unload --all
"$USERPROFILE/.lmstudio/bin/lms.exe" load "mistralai/devstral-small-2-2512" -y -c 65536 --gpu max --identifier "devstral"
```

VRAM: ~15-20 GB depending on context.

## Model Management Protocol

**CRITICAL VRAM SAFETY RULE: ALWAYS unload before loading heavy models.**

The example VRAM budget is 31.5 GB (RTX 5090) — check yours with `nvidia-smi`. These combos will OOM or degrade performance:

- Devstral (15 GB) + any 9B (10 GB) = 25 GB + context overhead = OOM risk
- Devstral (15 GB) + 12B Gemma (13 GB) = absolutely not
- Two 9B models simultaneously = marginal, avoid

**Safe combos:**

- 0.8B (~2 GB) + anything else = always fine
- 0.8B + 4B (~4.5 GB) = fine (~6.5 GB)
- 0.8B + 9B (~10 GB) = fine (~12 GB)
- 0.8B + Devstral (~15 GB) = fine (~17 GB)
- Devstral alone = fine
- 9B alone or 9B + 0.8B = fine

**BEFORE using a model, always:**

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" ps 2>&1
```

**If the model you need is NOT loaded:**

1. Check what's currently loaded and its VRAM usage
2. **UNLOAD models that conflict** before loading — do NOT rely on auto-eviction
3. Load the model you need with appropriate context length
4. Verify with `lms ps` after loading

**Standard unload-then-load pattern:**

```bash
# Example: switching to Devstral — unload everything first
"$USERPROFILE/.lmstudio/bin/lms.exe" unload --all
"$USERPROFILE/.lmstudio/bin/lms.exe" load "mistralai/devstral-small-2-2512" -y -c 32000 --gpu max --identifier "devstral"
```

**Load times** (approximate, high-end GPU such as RTX 5090 — slower on lesser hardware):

- 0.8B models: ~2 seconds
- 4B models: ~4 seconds
- 9B models: ~8 seconds
- Devstral 24B: ~12 seconds

**After heavy work, always restore the default 0.8B:**

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" unload --all
"$USERPROFILE/.lmstudio/bin/lms.exe" load "qwen3.5-0.8b" -y -c 8192 --gpu max
```

## API Usage

### Configuration

```
LM_STUDIO_URL=http://localhost:1234/v1
LMS_CLI="$USERPROFILE/.lmstudio/bin/lms.exe"
```

### Mode 1: Summarize Text (0.8B)

For long text, transcripts, documents, or any content over ~500 words. Ensure `qwen3.5-0.8b` is loaded.

```bash
curl -s http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-0.8b",
    "messages": [
      {"role": "system", "content": "You are a precision summarizer. Extract ALL key facts, names, numbers, decisions, and actionable items. Be thorough but concise. Use bullet points. Never fabricate information not in the source text."},
      {"role": "user", "content": "Summarize the following content:\n\n<CONTENT_HERE>"}
    ],
    "max_tokens": 800,
    "temperature": 0.1
  }'
```

### Mode 2: Targeted Extraction (0.8B)

When you need specific information from a large body of content.

```bash
curl -s http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-0.8b",
    "messages": [
      {"role": "system", "content": "Extract ONLY the information requested. Be precise. If the information is not present, say so. Do not guess."},
      {"role": "user", "content": "From the following content, extract: <WHAT_YOU_NEED>\n\n<CONTENT_HERE>"}
    ],
    "max_tokens": 500,
    "temperature": 0.1
  }'
```

### Mode 3: Describe Image/Screenshot (Vision)

Only models with `type: "vlm"` in the v0 API support vision. Use the OpenAI vision format with base64-encoded images.

**VLM models (support vision):** qwen3.5-0.8b, huihui-qwen3.5-0.8b-abliterated, qwen/qwen3.5-9b, mistralai/devstral-small-2-2512, gemma-3-12b-it-uncensored
**NOT VLM (no vision):** gemma-3-4b-it, all opus-distilled models

**Which vision model to use:**

| Task                                | Model     | Why                                               |
| ----------------------------------- | --------- | ------------------------------------------------- |
| Quick screenshot summary            | qwen-0.8b | 2.3s, 1.8 GB — fast enough for continuous loops   |
| UI/layout description               | qwen-0.8b | Good detail, minimal VRAM, already loaded         |
| Detailed art/photo analysis         | devstral  | Most technical precision, best for complex scenes |
| Natural/conversational descriptions | gemma-12b | Warmest tone, most human-like output              |
| Deep structured analysis            | qwen-9b   | Most thorough but slowest (10s+)                  |

**Default to 0.8B for vision** — it's shockingly good for its size and usually already loaded.

```python
# Python vision helper (works with any VLM model)
import json, urllib.request, base64

with open('image.png', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

payload = json.dumps({
    'model': 'qwen-0.8b',  # or devstral, gemma-12b, qwen-9b
    'messages': [{
        'role': 'user',
        'content': [
            {'type': 'text', 'text': 'Describe this image in detail.'},
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}}
        ]
    }],
    'max_tokens': 500,
    'temperature': 0.1
}).encode()

req = urllib.request.Request('http://localhost:1234/v1/chat/completions',
    data=payload, headers={'Content-Type': 'application/json'})
resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
print(resp['choices'][0]['message']['content'])
```

### Mode 4: Detailed Analysis (Devstral)

When accuracy matters — RAG queries, factual extraction, code analysis, anything where 0.8B might hallucinate. **Load Devstral first if not already loaded.**

```bash
# Ensure Devstral is loaded
"$USERPROFILE/.lmstudio/bin/lms.exe" ps 2>&1 | grep -q devstral || \
  "$USERPROFILE/.lmstudio/bin/lms.exe" load "mistralai/devstral-small-2-2512" -y -c 32000 --gpu max --identifier "devstral"

# Query it
curl -s http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "devstral",
    "messages": [
      {"role": "system", "content": "You are a precise analyst. Answer ONLY from the provided content. If something is not in the content, say so explicitly. Answer directly. Do not use any tools."},
      {"role": "user", "content": "<QUERY>\n\nContent:\n<CONTENT_HERE>"}
    ],
    "max_tokens": 1000,
    "temperature": 0.1
  }'
```

**IMPORTANT for Devstral:** Always include "Do not use any tools." in the system prompt — it's a coding agent model and will try `[TOOL_CALLS]` otherwise.

### Mode 5: Local Reasoning (Opus-Distilled 9B)

For tasks needing stronger reasoning than 0.8B but where you want to stay local. **Load the 9B first if not already loaded.**

```bash
# Ensure opus-9b is loaded
"$USERPROFILE/.lmstudio/bin/lms.exe" ps 2>&1 | grep -q opus || \
  "$USERPROFILE/.lmstudio/bin/lms.exe" load "qwen3.5-9b-claude-4.6-opus-reasoning-distilled" -y -c 32000 --gpu max --identifier "opus-9b"

curl -s http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "opus-9b",
    "messages": [
      {"role": "system", "content": "<SYSTEM_PROMPT>"},
      {"role": "user", "content": "<USER_PROMPT>"}
    ],
    "max_tokens": 1000,
    "temperature": 0.3
  }'
```

## Routing Rules

| Task                               | Model        | Load if needed?              |
| ---------------------------------- | ------------ | ---------------------------- |
| General summarization              | qwen3.5-0.8b | Usually already loaded       |
| Quick gist / TL;DR                 | qwen3.5-0.8b | Usually already loaded       |
| Factual extraction                 | devstral     | Yes — load on demand         |
| Code summarization / review        | devstral     | Yes — load on demand         |
| RAG-style Q&A over content         | devstral     | Yes — load on demand         |
| Complex reasoning                  | opus-9b      | Yes — load on demand         |
| Quick screenshot/image description | qwen3.5-0.8b | Usually already loaded (VLM) |
| Detailed image analysis            | devstral     | Yes — load on demand (VLM)   |
| Natural image descriptions         | gemma-12b    | Yes — load on demand (VLM)   |
| TTS-friendly text generation       | qwen3.5-0.8b | Usually already loaded       |

## Smart Loading Strategy

1. **Keep 0.8B always loaded** — it's tiny (~2 GB) and handles 80% of preprocessing tasks
2. **Load heavy models on demand** — Devstral/9B only when you actually need them
3. **Use `--ttl`** for temporary loads — e.g., `--ttl 300` auto-unloads after 5 min idle
4. **Check before loading** — don't reload what's already there
5. **Restore 0.8B after heavy work** — if a big model evicted it, reload when done

## Implementation Steps

1. **Check what's loaded**: `lms ps`
2. **Load needed model** if not present (see Load Profiles above)
3. **Prepare content**: Escape for JSON. For large content (>30k chars), truncate or chunk.
4. **Send to local model**: Use the appropriate mode above.
5. **Parse response**: Extract `choices[0].message.content`. Check for errors.
6. **Restore default state**: If you loaded a heavy model temporarily, consider unloading it and restoring the 0.8B.

## Python Helper for Complex Content

For content that's hard to escape in bash (quotes, special chars, multiline):

```bash
python -c "
import json, urllib.request

content = open('/path/to/file').read()
payload = json.dumps({
    'model': 'qwen3.5-0.8b',
    'messages': [
        {'role': 'system', 'content': 'You are a precision summarizer. Extract ALL key facts, names, numbers, decisions, and actionable items. Be thorough but concise. Use bullet points.'},
        {'role': 'user', 'content': f'Summarize:\n\n{content}'}
    ],
    'max_tokens': 800,
    'temperature': 0.1
}).encode()

req = urllib.request.Request('http://localhost:1234/v1/chat/completions',
    data=payload, headers={'Content-Type': 'application/json'})
resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
print(resp['choices'][0]['message']['content'])
"
```

## Important Caveats

- The 0.8B model **will lose nuance**. For legal text, exact numbers, or subtle meaning, read the raw content yourself.
- The 0.8B model **may hallucinate** details not in the source. For critical facts, use Devstral or verify yourself.
- If LM Studio is offline, **fall back gracefully** — just do the work yourself and note that Local Lens was unavailable.
- **Never present the local model's summary as your own analysis.** Say "Based on local preprocessing..." or similar.
- Content with special characters needs proper JSON escaping. Use the Python helper above.
- **Devstral will try tool calls** if you don't explicitly tell it not to. Always include "Do not use any tools." in its system prompt.
- **Loading large models evicts small ones.** Always check `lms ps` after loading to see what survived.
