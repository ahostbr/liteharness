---
name: ls-consult
description: Ask several models the same question and compare them — Claude through the Claude CLI, and codex / LM Studio / llama.cpp through a headless LiteTUI child. Only invoke when the user explicitly says 'consult', 'ask the other models', names a model in the panel, or asks for an external model comparison. For expert analysis, code review, or architecture opinions, prefer /consult-polymaths instead — it uses internal agents and works without any external services.
allowed-tools: Bash, Read
---

# /consult — Multi-Model LLM Consultation

You are executing the `/consult` command. Send the user's question to multiple LLM models and synthesize their responses.

## Input

The user's argument is: `$ARGUMENTS`

## Step 1: Load Config

Read the file `.claude/consult-config.json` to get the model panel configuration, provider URLs, and defaults.

**If the file does not exist**, create it at `.claude/consult-config.json` with this default template and continue:

```json
{
  "defaults": {
    "temperature": 0.7,
    "maxTokens": 1024,
    "timeoutSeconds": 60
  },
  "presets": {
    "quick": ["cc-sonnet"],
    "default": ["cc-sonnet", "lt-lmstudio"],
    "wide": ["cc-sonnet", "cc-opus", "lt-codex", "lt-lmstudio"],
    "claude-family": ["cc-haiku", "cc-sonnet", "cc-opus"],
    "full": ["cc-haiku", "cc-sonnet", "cc-opus", "lt-codex", "lt-lmstudio", "lt-llamacpp"]
  },
  "alwaysInclude": [],
  "providers": {
    "claude-cli": {
      "type": "cli",
      "enabled": true
    },
    "litetui": {
      "type": "litetui",
      "command": "litetui",
      "enabled": true
    }
  },
  "models": {
    "cc-haiku": {
      "id": "claude-haiku-4-5",
      "name": "Claude Haiku 4.5 (CLI)",
      "provider": "claude-cli"
    },
    "cc-sonnet": {
      "id": "claude-sonnet-4-6",
      "name": "Claude Sonnet 4.6 (CLI)",
      "provider": "claude-cli"
    },
    "cc-opus": {
      "id": "claude-opus-4-8",
      "name": "Claude Opus 4.8 (CLI)",
      "provider": "claude-cli"
    },
    "lt-codex": {
      "id": "codex",
      "name": "Codex (via LiteTUI)",
      "provider": "litetui",
      "backend": "codex"
    },
    "lt-lmstudio": {
      "id": "",
      "name": "LM Studio (via LiteTUI)",
      "provider": "litetui",
      "backend": "lmstudio"
    },
    "lt-llamacpp": {
      "id": "",
      "name": "llama.cpp (via LiteTUI)",
      "provider": "litetui",
      "backend": "llamacpp"
    }
  }
}
```

> **Note.** Ryan, 2026-09-10: *"i only want claude / codex / lmstudio or our litesuite
> lamma.cpp and everything but claude can go threw litetui now."* The shipped panel is
> Claude through the CLI (your subscription) plus everything else through a headless
> LiteTUI child. There are no HTTP provider entries and no API keys: `cliproxy`,
> `openai`, `gemini` and friends were removed rather than disabled, because a disabled
> entry is one edit away from being back in the panel by accident.
>
> `"id": ""` on the litetui models is deliberate. **The model is whatever is already
> loaded** — see the loading rule in Step 3. A hard-coded id here is exactly what
> caused a second 27B to be loaded beside someone's running model on 2026-09-10.

## Step 2: Parse Arguments

Parse the following flags from the argument string. Everything that isn't a flag is the **question**.

| Flag                 | Default     | Effect                                                            |
| -------------------- | ----------- | ----------------------------------------------------------------- |
| `--preset P`         | `default`   | Panel preset: `quick`, `default`, `wide`, `claude-family`, `full` |
| `--models m1,m2,...` | (none)      | Override preset with specific model keys from config              |
| `--system "..."`     | (see below) | Custom system prompt for all models                               |
| `--no-local`         | off         | Skip LM Studio models even if in panel                            |
| `--no-cli`           | off         | Skip Claude CLI models even if in panel                           |
| `--temp N`           | `0.7`       | Temperature for all models                                        |

If no question is found, ask the user what they'd like to consult about and stop.

## Step 3: Resolve Model Panel

1. If `--models` was provided, use those model keys (comma-separated).
2. Otherwise, use the preset from `--preset` (or `"default"` if not specified) — look up the preset in `config.presets`.
3. Merge in `config.alwaysInclude` models (deduplicate).
4. If `--no-local` is set, remove any models whose provider is `"litetui"`.
5. If `--no-cli` is set, remove any models whose provider is `"claude-cli"`.
6. Look up each model key in `config.models` to get `id`, `name`, and `provider`.
7. Look up each provider in `config.providers` to get `baseUrl` and `auth`.

Skip providers where `enabled` is explicitly `false`. Neither shipped provider type is
HTTP: `"type": "cli"` and `"type": "litetui"` each have their own invocation path
(Step 5). A config that adds an HTTP provider still works — append `chatPath` to
`baseUrl`, or use `/chat/completions` — but none ships.

### The loading rule for `litetui` models (read this before Step 4)

**A consult NEVER causes a model to be loaded into VRAM.** Ryan, 2026-09-10, after a
seat's probe put a second 27B beside the one he was running: *"no model is loaded into
VRAM without first confirming none is loaded and getting explicit approval."*

LM Studio JIT-loads whatever model a completion request names — so naming a model is
itself a load, and nothing in the output says one happened. For `litetui` models:

1. Ask what is resident, at run time, every run: `lms ps` (or
   `GET http://localhost:1234/api/v0/models` and take the entries with a
   `loaded_context_length`).
2. Use the id that comes back. **Never** a stored id and never one from this file —
   the user switches models mid-session, which is why `"id"` is empty in the template.
3. If nothing is resident, **SKIP that model and print why**. Do not load one.

LiteTUI enforces the same rule from its side as of T594, so a `--rpc` child cannot load
a model even if asked: it substitutes the resident one (and says so in `ready` via
`model_note`) or refuses with `{"type": "error", "kind": "model_not_loaded"}`. Treat
that error as a SKIP with its message printed — never a retry, and never a fallback
that names a different model.

## Step 4: Health Check

For each unique **`litetui`** provider in the panel:

```bash
# is the backend up at all, and what is loaded?
lms ps                      # lmstudio  — a row means up; no rows means SKIP
curl -s -m 3 http://localhost:7470/health   # llamacpp — LiteSuite LocalAdapter
```

A backend that does not answer is a **SKIP with the reason printed**, never a hang and
never a retry loop. As measured 2026-09-10 22:2x, nothing was listening on 7470 — the
llama.cpp row is expected to skip on a box where LiteSuite is not running it, and a
panel that silently dropped it would look like the model simply had no opinion.

For `codex`, health is the child itself: it authenticates over OAuth, so the only honest
check is the `ready` line arriving (Step 5). **MEASURED 2026-09-10 23:1x**, end to end,
no VRAM involved: model `gpt-6-astra`, answer `ok`, `turn_end` with `stopReason: "stop"`,
exits rc 0 on stdin close, 6.5 s total — about a third of the LM Studio run.

🔴 **CODEX EMITS NO `reasoning_delta` AT ALL.** The whole turn was `ready`,
`turn_start`, ONE `text_delta`, `turn_end`. A parser that waits for reasoning before
accepting an answer, or that reports "the model did not think", is wrong on this
backend. Reasoning deltas are optional on every backend and absent on this one.

For each unique **HTTP** provider in the panel (none ship), run a quick connectivity check:

```bash
curl -s --max-time 5 -o /dev/null -w "%{http_code}" PROVIDER_BASE_URL/models
```

For `claude-cli` providers, verify the CLI is available:

```bash
claude --version 2>/dev/null
```

Report which providers are up/down. Remove models whose provider is down, but continue with the rest. If ALL providers are down, report the failure and stop.

## Step 5: Query All Models in PARALLEL

**This is critical: fire ALL curl calls as parallel Bash tool calls in a single message.**

Use the resolved temperature (from `--temp` or `config.defaults.temperature`) and max tokens from `config.defaults.maxTokens`. Use `config.defaults.timeoutSeconds` as the curl `--max-time`.

### Default system prompt (used when `--system` is not provided):

```
You are a technical consultant providing your honest, independent opinion. Be concise but thorough. Focus on practical implications. If you disagree with a premise, say so directly. Provide your reasoning, not just conclusions. Keep your response under 500 words.
```

### Curl pattern for providers WITH auth:

```bash
curl -s --max-time TIMEOUT PROVIDER_BASE_URL/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: AUTH_VALUE" \
  -d '{"model":"MODEL_ID","messages":[{"role":"system","content":"SYSTEM_PROMPT"},{"role":"user","content":"QUESTION"}],"temperature":TEMP,"max_tokens":MAX_TOKENS}'
```

### Curl pattern for providers WITHOUT auth (auth is null):

```bash
curl -s --max-time TIMEOUT PROVIDER_BASE_URL/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"MODEL_ID","messages":[{"role":"system","content":"SYSTEM_PROMPT"},{"role":"user","content":"QUESTION"}],"temperature":TEMP,"max_tokens":MAX_TOKENS}'
```

### Command pattern for `litetui` providers (headless child, JSON lines)

One child per consulted model. **The shape below is MEASURED** (2026-09-10, LiteTUI
0.22.2) — do not infer it from this description, and if it stops matching, re-measure
before changing the parser.

```bash
LITETUI_BACKEND=lmstudio \
litetui --rpc \
  --tool-profile scheduled \
  --model "<the id lms ps reported just now>" \
  --cwd "<a scratch dir, NOT the project>" \
  --prompt "<the consult question>"
```

🔴 **`LITETUI_BACKEND` IS NOT OPTIONAL, AND IT IS THE ONLY LEVER.** The model entries
above carry `"backend"`, and **there is no `--backend` flag** — `cli.py` has `--rpc`,
`--model`, `--prompt`, `--system-prompt`, `--cwd`, `--tool-profile`, `--mode`, `--convo`
and nothing else. Without the env var the child reads `<install>/settings.json`, a file
no consult controls, and talks to whatever THAT says. Measured 2026-09-10 23:0x: a run
gated on "LM Studio is up, model resident" launched a child that went to llama.cpp
(down) and returned `turn_end` with `stopReason: "error"`. **"The service is up" and
"the child is pointed at it" are two conditions**; checking only the first produces
confident findings about a backend nobody was talking to.

`--tool-profile scheduled` is REQUIRED and is not a style choice. Scheduled is the only
profile with an EMPTY confirm set that still refuses: `allow={READ_ONLY, SELF_STORE}`,
and its own source says *"every other authority is refused rather than opening a modal
nobody is present to answer."* Without it a consulted model can reach the tool-approval
branch and the consult parks forever waiting for a human who is not watching.

⚠️ **AND AS OF LiteTUI 0.22.2 THE FLAG DOES NOT REACH THE TURN. PASS IT ANYWAY, BUT
DO NOT RELY ON IT.** Measured 2026-09-10 23:1x: the profile is installed at construction
(`app.py:1209-1215`) and then `_submit_text` overwrites it with
`settings.tool_policy_profile` (`app.py:4272` → `:4296`) before the turn streams — so the
turn runs tools under whatever the install's settings say, `interactive` or
`autonomous`, never `scheduled`. The `ready` line is no witness either: the same
`--tool-profile scheduled` reported `"scheduled"` under lmstudio and `"interactive"`
under codex, depending on which side of that overwrite won the startup race below.

Until that is fixed, **the timeout is the real guard**, not the profile. Bound every
child, kill it on expiry, and treat a consult that asks for a tool as a SKIP rather
than something to wait out. Reported for triage; not a reason to omit the flag, which
is correct and will start working.

#### The lines it emits

```json
{"type": "ready", "version": "0.22.2", "model": "...", "cwd": "...", "tool_profile": "scheduled"}
{"type": "turn_start", "model": "...", "thinking_level": "xhigh"}
{"type": "reasoning_delta", "text": "The"}
{"type": "text_delta", "text": "ok"}
{"type": "turn_end", "stopReason": "stop"}
```

#### Parsing, and the four ways it goes wrong

1. **The answer is `text_delta` only.** `reasoning_delta` carries a `.text` too, so a
   parser that concatenates every `.text` returns the model's chain of thought as its
   answer. Measured across runs of the SAME one-word prompt: `ok` arrived as a single
   `text_delta` behind 22 reasoning deltas / 86 chars, then 68 chars on the next run,
   and 0 on codex. Treat the volume as unbounded and variable — select on `type`,
   never on the presence of `text`, and never size a buffer from a past run.
2. **Hold stdin OPEN until `turn_end`, then close it.** Measured both failures: with
   stdin at `/dev/null` the child exits 0 having emitted NOTHING — a silent empty
   answer, the worst shape for a panel — and with stdin left open afterwards it never
   exits at all (one probe had to be killed at 150 s).
3. **`ready` may not name the model you asked for.** It can carry `model_note` when
   LiteTUI substituted the resident model, and `{"type": "error", "kind":
   "model_not_loaded"}` means SKIP with the message printed. Post-T594 both `ready`
   and `turn_start` carry the RESOLVED id, so either will do — read whichever arrives,
   and prefer `turn_start` only because it cannot be emitted before resolution.
4. **🔴 `ready` IS NOT RELIABLY THE FIRST LINE. NEVER INDEX `events[0]`.** Measured
   2026-09-10 23:1x, identical flags, three backends:

   | backend | order |
   | --- | --- |
   | lmstudio | `ready` @7.6 s, then `turn_start` |
   | codex | `turn_start` @1.1 s, then `ready` |
   | llamacpp (down) | `turn_start`, then `ready` |

   `--model`/`--prompt` and the `ready` emitter are two workers started back to back,
   each waiting on the model list; which finishes first is decided by how long connect
   takes. A parser that assumed `ready` was line 1 was green on LM Studio purely
   because its connect is slow. Select every event by `type`.

Answer = `"".join(e["text"] for e in events if e["type"] == "text_delta")`.

A child that has not reached `turn_end` within the timeout is a SKIP with the reason
printed, and the process is killed. Never report a partial turn as an opinion.

### Command pattern for `claude-cli` providers (Visible Window Process):

For models whose provider has `"type": "cli"`, spawn each in a **visible PowerShell window** so the user can watch the response stream in. Each CLI model runs as a 3-step process:

#### Step A: Save the prompt to a temp file

For short prompts, write the question directly. For long prompts (transcripts, code, etc.), save the full content:

```bash
cat > "$TEMP/consult-prompt-MODEL_KEY.txt" << 'PROMPT_EOF'
QUESTION_TEXT_HERE
PROMPT_EOF
```

#### Step B: Write a PowerShell launcher script

Write one `.ps1` script per CLI model. The script must:

- Unset `CLAUDECODE` env var (blocks nested sessions otherwise)
- Pipe the prompt file to `claude -p`
- Display the response live in the window
- Save output as **UTF-8** (PowerShell defaults to UTF-16 which corrupts the output)

```bash
cat > "$TEMP/consult-run-MODEL_KEY.ps1" << 'PS_EOF'
$env:CLAUDECODE = $null
$promptFile = "$env:TEMP\consult-prompt-MODEL_KEY.txt"
$outputFile = "$env:TEMP\consult-response-MODEL_KEY.txt"

Write-Host "=== Consulting MODEL_NAME ===" -ForegroundColor Cyan
Write-Host ""

$prompt = Get-Content $promptFile -Raw -Encoding utf8
$response = $prompt | claude -p --model MODEL_ID --system-prompt "SYSTEM_PROMPT" --no-session-persistence --tools "" --output-format text 2>&1

Write-Host $response
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green

[System.IO.File]::WriteAllText($outputFile, $response, [System.Text.UTF8Encoding]::new($false))

Start-Sleep -Seconds 3
PS_EOF
```

Replace these placeholders with actual values:

- `MODEL_KEY` — the config key (e.g., `cc-opus`)
- `MODEL_NAME` — the display name (e.g., `Claude Opus 4.6 (CLI)`)
- `MODEL_ID` — the model id from config (e.g., `opus`, `sonnet`, `haiku`)
- `SYSTEM_PROMPT` — the resolved system prompt (escape double quotes as backtick-double-quote in PS)

#### Step C: Launch the visible window

```bash
cmd.exe //c start "Consulting MODEL_NAME" powershell.exe -ExecutionPolicy Bypass -File "$TEMP/consult-run-MODEL_KEY.ps1"
```

**Launch ALL CLI model windows in parallel** (one `cmd.exe //c start` per model, all in the same Bash tool call block alongside the HTTP curl calls).

#### Step D: Poll for responses

After launching all models (HTTP + CLI), poll for CLI response files. HTTP curl calls return immediately; CLI windows write their output files when done:

```bash
# Poll until response file exists and has content, max ~3 minutes
for i in $(seq 1 36); do
  [ -s "$TEMP/consult-response-MODEL_KEY.txt" ] && break
  sleep 5
done
```

Poll for ALL CLI models in a single Bash call. Then read each response:

```bash
cat "$TEMP/consult-response-MODEL_KEY.txt"
```

The response is plain UTF-8 text — no JSON parsing needed.

#### Cleanup

After presenting results, remove temp files:

```bash
rm -f "$TEMP"/consult-prompt-*.txt "$TEMP"/consult-run-*.ps1 "$TEMP"/consult-response-*.txt
```

**IMPORTANT for CLI models:** These may take longer than HTTP models (10-60s for Opus). They use the user's Claude subscription credits, not API keys. They spawn a separate Claude process with zero context from the current session, giving a truly independent opinion.

**IMPORTANT:** Properly escape the question and system prompt for JSON (for HTTP models) or shell/PowerShell (for CLI models). In PowerShell strings, escape double quotes with backtick-double-quote (\`").

## Step 6: Extract and Present Responses

For each response:

1. **HTTP models:** Parse the JSON and extract `.choices[0].message.content`
2. **CLI models:** Read `$TEMP/consult-response-MODEL_KEY.txt` — it's plain UTF-8 text, no JSON parsing needed. If the file is empty or missing after polling, record `[ERROR: CLI model timed out or failed — check the PowerShell window for details]`.
3. If the call failed, timed out, or returned an error, record `[ERROR: description]`

Present ALL responses with clear attribution using this format:

```
## Consultation Results

**Question:** {the question}
**Models consulted:** {count} ({comma-separated model names})
**Preset:** {preset used}

---

### {Model Name} ({Provider})
> {full response text}

### {Model Name} ({Provider})
> {full response text}

...

---
```

## Step 7: Synthesize

After presenting all raw responses, provide a synthesis:

```
## Synthesis

### Agreement
- {Points where 2+ models converge on the same conclusion}

### Disagreement
- {Points where models diverge, with attribution — e.g., "cc-opus recommends X while lt-lmstudio prefers Y"}

### Unique Insights
- {Points only one model raised that are worth noting}

### My Assessment
{Your own synthesis weighing all the opinions. Be direct about which arguments you find most compelling and why. Add any considerations the models missed.}
```

## Error Handling

| Scenario                                | Action                                                                                                                                 |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| A provider is down (health check fails) | Skip its models, note in output, continue with others                                                                                  |
| A single model errors or times out      | Show `[ERROR: reason]` for that model, continue with others                                                                            |
| All models fail                         | Report failure, suggest checking that LM Studio is running (port 1234) and/or API keys are configured in `.claude/consult-config.json` |
| Claude CLI model fails                  | Show the stderr output as the error. Common causes: not authenticated (`claude auth`), model unavailable, rate limited                 |
| No question provided                    | Ask the user what they want to consult about                                                                                           |
| Unknown model key in `--models`         | Warn and skip that key, continue with valid ones                                                                                       |
| Unknown preset in `--preset`            | Warn and fall back to `"default"` preset                                                                                               |
