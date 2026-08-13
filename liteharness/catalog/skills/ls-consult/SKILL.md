---
name: ls-consult
description: Query external LLM APIs (GPT, Gemini, LM Studio) and Claude CLI for opinions. Only invoke when the user explicitly says 'consult', 'ask GPT', 'ask Gemini', or specifically requests external model comparison. For expert analysis, code review, or architecture opinions, prefer /consult-polymaths instead — it uses internal agents and works without any external services.
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
    "default": ["cc-sonnet", "lms-local"],
    "wide": ["cc-sonnet", "cc-opus", "lms-local"],
    "claude-family": ["cc-haiku", "cc-sonnet", "cc-opus"],
    "full": ["cc-haiku", "cc-sonnet", "cc-opus", "lms-local"]
  },
  "alwaysInclude": [],
  "providers": {
    "claude-cli": {
      "type": "cli",
      "enabled": true
    },
    "lmstudio": {
      "baseUrl": "http://localhost:1234/v1",
      "auth": null,
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
    "lms-local": { "id": "local-model", "name": "LM Studio (local)", "provider": "lmstudio" }
  }
}
```

> **Note:** Edit `.claude/consult-config.json` to add your own API keys and provider URLs (OpenAI, Gemini, etc.). The default config only includes Claude CLI models (uses your Claude subscription) and LM Studio (local, no key needed).

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
4. If `--no-local` is set, remove any models whose provider is `"lmstudio"`.
5. If `--no-cli` is set, remove any models whose provider is `"claude-cli"`.
6. Look up each model key in `config.models` to get `id`, `name`, and `provider`.
7. Look up each provider in `config.providers` to get `baseUrl` and `auth`.

For providers with a `chatPath` field, append that to `baseUrl` for the completions endpoint. Otherwise use the standard `/chat/completions` path. Skip providers where `enabled` is explicitly `false`. Providers with `"type": "cli"` use a completely different invocation path (see Step 5).

## Step 4: Health Check

For each unique **HTTP** provider in the panel, run a quick connectivity check:

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
- {Points where models diverge, with attribution — e.g., "GPT recommends X while Gemini prefers Y"}

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
